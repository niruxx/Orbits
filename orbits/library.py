"""Installed wallpaper collection: download, unpack, register with GNOME, apply, uninstall."""
from orbits.undo import reversible, EntryState

import json
import copy
import fcntl
import os
import re
import shutil
import tarfile
import tempfile
import threading
import time
import zipfile
import uuid
from pathlib import Path
from xml.sax.saxutils import escape

from gi.repository import Gio, GLib, GObject

from orbits.rejections import guarded
from orbits import net
from orbits.config import BG_PROPERTIES_DIR, DATA_DIR, download_dir, settings
from orbits.sources.base import Download, Item

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".jxl", ".heic", ".heif", ".svg", ".bmp", ".tif", ".tiff"}
ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".tar.zst")
MAX_UNPACKED = 1 << 30  # refuse archives that expand past 1 GiB
LIBRARY_FILE = DATA_DIR / "library.json"
BG_XML = BG_PROPERTIES_DIR / "orbits-wallpapers.xml"

BG_SCHEMA = "org.gnome.desktop.background"
LOCK_SCHEMA = "org.gnome.desktop.screensaver"


def _slug(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")[:60] or "wallpaper"


def _is_image(name):
    return Path(name).suffix.lower() in IMAGE_EXTS


def _is_archive(name):
    return name.lower().endswith(ARCHIVE_SUFFIXES)


def _pixel_hint(path: Path):
    """Sort key: prefer larger images using the WIDTHxHEIGHT name Plasma packages use, else file size."""
    m = re.search(r"(\d{3,5})x(\d{3,5})", path.name)
    if m:
        return int(m.group(1)) * int(m.group(2))
    return path.stat().st_size


def _collect_variants(root: Path):
    """Turn an unpacked archive into a list of {light, dark} variants.

    Plasma wallpaper packages look like  <Name>/contents/images/<WxH>.png  plus optional images_dark/.
    Each package becomes one variant (best resolution, with dark pair); loose images become one each.
    """
    variants, claimed = [], set()
    for images_dir in sorted(root.rglob("contents/images")):
        if not images_dir.is_dir():
            continue
        imgs = [p for p in images_dir.iterdir() if p.is_file() and _is_image(p.name)]
        if not imgs:
            continue
        light = max(imgs, key=_pixel_hint)
        dark = None
        dark_dir = images_dir.parent / "images_dark"
        if dark_dir.is_dir():
            darks = [p for p in dark_dir.iterdir() if p.is_file() and _is_image(p.name)]
            if darks:
                dark = max(darks, key=_pixel_hint)
                claimed.update(darks)
        claimed.update(imgs)
        variants.append({"light": light, "dark": dark, "name": images_dir.parent.parent.name})
    loose = [p for p in sorted(root.rglob("*"))
             if p.is_file() and _is_image(p.name) and p not in claimed
             and not re.search(r"preview|scree?nshot|thumb", p.stem, re.I)]
    # Pair loose "<name>-dark" / "<name>-light" / "<name>" files into one light+dark variant
    by_base = {}
    for p in loose:
        m = re.match(r"(.*?)[-_ ]?(dark|light|night|day)$", p.stem, re.I)
        base, tone = (m.group(1), m.group(2).lower()) if m else (p.stem, "")
        by_base.setdefault((p.parent, base.lower()), []).append((tone, p))
    for (_parent, _base), group in by_base.items():
        darks = [p for t, p in group if t in ("dark", "night")]
        lights = [p for t, p in group if t not in ("dark", "night")]
        if len(darks) == 1 and len(lights) == 1:
            variants.append({"light": lights[0], "dark": darks[0], "name": lights[0].stem})
        else:
            variants += [{"light": p, "dark": None, "name": p.stem} for _t, p in group]
    return variants


def _safe_extract(archive: Path, dest: Path):
    from orbits.safe_archive import extract
    return extract(archive, dest, wanted=lambda name: _is_image(name))


class Library(GObject.Object):
    """Singleton store of installed wallpapers. Emits 'changed' on install/uninstall/apply."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self.items: dict[str, dict] = {}
        self.busy: dict[str, float] = {}  # key -> progress fraction while installing
        try:
            self.items = json.loads(LIBRARY_FILE.read_text())
        except (OSError, ValueError):
            pass
        self._persisted = copy.deepcopy(self.items)
        # Drop entries whose files were deleted behind our back
        for key in [k for k, v in self.items.items() if not any(Path(f["light"]).exists() for f in v["files"])]:
            del self.items[key]
        from orbits.desktop import background_settings
        self._bg = background_settings()
        self._lockscreen = Gio.Settings.new(LOCK_SCHEMA)
        self._bg.connect("changed::picture-uri", self._background_changed)

    def _background_changed(self, *_):
        # Defer until a current apply/relocate operation has finished saving its paths.
        GLib.idle_add(self._refresh_background)

    def _refresh_background(self):
        self.reload_index()
        self.emit("changed")
        return False

    def reload_index(self):
        """Refresh after the timer process has installed wallpapers."""
        try:
            data = json.loads(LIBRARY_FILE.read_text())
            if isinstance(data, dict):
                self.items = data
                self._persisted = copy.deepcopy(data)
        except (OSError, ValueError):
            pass

    def set_favorite(self, key, favorite):
        self.items[key]["favorite"] = bool(favorite)
        self._save()
        self.emit("changed")

    def set_collections(self, key, names):
        self.items[key]["collections"] = sorted({n.strip()[:80] for n in names if n.strip()})
        self._save()
        self.emit("changed")

    def collections(self):
        return sorted({name for entry in self.items.values() for name in entry.get("collections", [])})

    def add_generated(self, path, title, options="zoom", preview=None):
        path = Path(path).resolve()
        key = f"local:{uuid.uuid4().hex}"
        dest_dir = download_dir() / key.replace(":", "-")
        dest_dir.mkdir(parents=True)
        target = dest_dir / path.name
        shutil.copyfile(path, target)
        rec = {"name": title, "light": str(target), "dark": None, "options": options}
        if preview:
            thumb = dest_dir / "preview.png"
            shutil.copyfile(preview, thumb)
            rec["preview"] = str(thumb)
        entry = {"key": key, "source": "local", "id": key, "title": title, "author": "", "page_url": "",
                 "thumb_url": "", "dir": str(dest_dir), "files": [rec], "installed_at": time.time()}
        from orbits.integrity import record_tree
        record_tree(dest_dir)
        self.items[key] = entry
        self._save()
        self.emit("changed")
        return entry

    def create_slideshow(self, frames, title="Daily Wallpaper", transition=5):
        from orbits.wallpaper_tools import slideshow_xml, open_image
        key = f"local:{uuid.uuid4().hex}"
        folder = download_dir() / key.replace(":", "-")
        folder.mkdir(parents=True)
        try:
            copied = []
            for i, (seconds, path) in enumerate(frames):
                target = folder / f"frame-{i:03d}.png"
                open_image(path).save(target)
                copied.append((seconds, target))
            xml = folder / "background.xml"
            xml.write_text(slideshow_xml(copied, transition))
            rec = {"name": title, "light": str(xml), "dark": None, "preview": str(copied[0][1])}
            entry = {"key": key, "source": "local", "id": key, "title": title, "author": "", "page_url": "",
                     "thumb_url": "", "dir": str(folder), "files": [rec], "installed_at": time.time()}
            from orbits.integrity import record_tree
            record_tree(folder)
            self.items[key] = entry
            self._save()
        except Exception:
            self.items.pop(key, None)
            shutil.rmtree(folder, ignore_errors=True)
            raise
        self.emit("changed")
        return entry

    # ---- persistence -------------------------------------------------
    def _save(self):
        # Merge local changes with timer-process changes under a shared file lock.
        LIBRARY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LIBRARY_FILE.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                disk = json.loads(LIBRARY_FILE.read_text())
                if not isinstance(disk, dict):
                    disk = {}
            except (OSError, ValueError):
                disk = {}
            previous = getattr(self, "_persisted", {})
            for key in previous.keys() - self.items.keys():
                disk.pop(key, None)
            for key, value in self.items.items():
                if key not in previous:
                    disk[key] = value
                elif key in disk and value != previous[key]:
                    # Two processes can change different metadata on the same wallpaper.
                    merged = dict(disk[key])
                    for field in previous[key].keys() - value.keys():
                        merged.pop(field, None)
                    for field, content in value.items():
                        if field not in previous[key] or content != previous[key][field]:
                            merged[field] = content
                    disk[key] = merged
            with tempfile.NamedTemporaryFile(mode="w", dir=LIBRARY_FILE.parent, delete=False) as stream:
                json.dump(disk, stream, indent=2)
                tmp = stream.name
            try:
                os.replace(tmp, LIBRARY_FILE)
            finally:
                Path(tmp).unlink(missing_ok=True)
            self.items = disk
            self._persisted = copy.deepcopy(disk)
            self._write_gnome_xml()

    def _write_gnome_xml(self):
        """Register wallpapers so they also show up in GNOME Settings → Appearance."""
        if not settings.get("register_in_gnome"):
            BG_XML.unlink(missing_ok=True)
            return
        BG_PROPERTIES_DIR.mkdir(parents=True, exist_ok=True)
        out = ['<?xml version="1.0"?>', '<!DOCTYPE wallpapers SYSTEM "gnome-wp-list.dtd">', "<wallpapers>"]
        for item in self.items.values():
            for i, f in enumerate(item["files"]):
                name = item["title"] if len(item["files"]) == 1 else f"{item['title']} ({i + 1})"
                out.append("  <wallpaper deleted=\"false\">")
                out.append(f"    <name>{escape(name)}</name>")
                out.append(f"    <filename>{escape(f['light'])}</filename>")
                if f.get("dark"):
                    out.append(f"    <filename-dark>{escape(f['dark'])}</filename-dark>")
                out.append(f"    <options>{escape(f.get('options', 'zoom'))}</options>")
                out.append("  </wallpaper>")
        out.append("</wallpapers>")
        BG_XML.write_text("\n".join(out) + "\n")

    # ---- queries ------------------------------------------------------
    def is_installed(self, key):
        return key in self.items

    def sorted_items(self):
        return sorted(self.items.values(), key=lambda i: i.get("installed_at", 0), reverse=True)

    def current_uri(self):
        return self._bg.get_string("picture-uri")

    def is_current(self, entry):
        cur = self.current_uri()
        return any(GLib.filename_to_uri(f["light"]) == cur for f in entry["files"])

    # ---- install ------------------------------------------------------
    def install(self, item: Item, download: Download | None, callback, progress=None):
        """Download & register item in a worker. callback(entry, error) on main loop."""
        from orbits.download_choice import select_download
        download = select_download(item, download)
        if download is None:
            callback(None, RuntimeError("This item has nothing to download"))
            return
        self.busy[item.key] = 0.0
        self.emit("changed")

        def report(frac):
            self.busy[item.key] = frac
            if progress:
                GLib.idle_add(lambda: (progress(frac), False)[1])

        def work():
            return self._install_blocking(item, download, report)

        def done(entry, err):
            self.busy.pop(item.key, None)
            if entry:
                previous = self.items.get(item.key, {})
                for field in ("favorite", "collections"):
                    if field in previous:
                        entry[field] = previous[field]
                self.items[item.key] = entry
                self._save()
            self.emit("changed")
            callback(entry, err)

        net.run_async(work, done)

    @guarded
    def _install_blocking(self, item: Item, download: Download, report):
        # App workers and the timer must not replace the same download directory together.
        with (DATA_DIR / "wallpaper-downloads.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._download_blocking(item, download, report)

    def _download_blocking(self, item: Item, download: Download, report):
        dest_dir = download_dir() / f"{item.source}-{_slug(item.id)}"
        with tempfile.TemporaryDirectory(prefix="orbits-dl-") as tmp:
            tmp = Path(tmp)
            name = _slug(download.name) or "download"
            raw = tmp / name
            content_type, _ = net.download(download.url, raw, report, checksum=getattr(download, "md5", ""))
            unpack = tmp / "unpacked"
            unpack.mkdir()
            from orbits.safe_archive import is_archive
            looks_image = _is_image(name) or content_type.startswith("image/")
            if not looks_image and (_is_archive(name) or is_archive(raw)):
                _safe_extract(raw, unpack)
            elif looks_image:
                if not _is_image(name):
                    name += "." + content_type.split("/")[1].replace("jpeg", "jpg")
                shutil.move(raw, unpack / name)
            else:
                raise RuntimeError(f"Unsupported file type: {download.name}")
            variants = _collect_variants(unpack)
            if not variants:
                raise RuntimeError("No images were found in this download")
            dest_dir.parent.mkdir(parents=True, exist_ok=True)
            files = []
            with tempfile.TemporaryDirectory(prefix=".orbits-wallpaper-", dir=dest_dir.parent) as staged:
                staged = Path(staged)
                tree = staged / "ready"
                tree.mkdir()
                from orbits.image_safety import normalize_images
                for i, v in enumerate(variants[:200]):
                    rec = {"name": v["name"]}
                    for kind in ("light", "dark"):
                        src = v[kind]
                        if src is None:
                            rec[kind] = None
                            continue
                        normalized = normalize_images(src, staged / f'pixels-{i}-{kind}', scan=False)[0]
                        name = f"{i:03d}-{kind}-{_slug(v['name'])}.png"
                        shutil.move(normalized, tree / name)
                        rec[kind] = str(dest_dir / name)
                    files.append(rec)
                from orbits.integrity import record_tree
                record_tree(tree)
                backup = staged / "previous"
                if dest_dir.exists():
                    os.rename(dest_dir, backup)
                try:
                    os.rename(tree, dest_dir)
                except Exception:
                    if backup.exists():
                        os.rename(backup, dest_dir)
                    raise

        return {
            "key": item.key,
            "source": item.source,
            "id": item.id,
            "title": item.title,
            "author": item.author,
            "page_url": item.page_url,
            "thumb_url": item.thumb_url,
            "dir": str(dest_dir),
            "files": files,
            "installed_at": time.time(),
            "download": download.name,
            "remote_changed": item.updated,
        }

    def import_files(self, paths: list[str]):
        """Scan and normalize local images before publishing them to the library."""
        from orbits.image_safety import normalize_images
        added = 0
        for value in paths:
            source = Path(value)
            if not _is_image(source.name):
                continue
            with tempfile.TemporaryDirectory(prefix='orbits-import-') as temporary:
                count = 200 if source.suffix.lower() in ('.heic', '.heif') else 1
                frames = normalize_images(source, temporary, frames=count, publisher=True)
                if len(frames) > 1:
                    self.create_slideshow([(i * 86400 // len(frames), image)
                                           for i, image in enumerate(frames)], source.stem)
                else:
                    self.add_generated(frames[0], source.stem)
            added += 1
        return added

    # ---- uninstall ----------------------------------------------------
    @reversible("Wallpaper uninstalled", extra=lambda self, key: EntryState(self, [key], [self.items[key]["dir"]] if key in self.items else []))
    def uninstall(self, key):
        entry = self.items.pop(key, None)
        if not entry:
            return
        d = Path(entry["dir"])
        # Only ever delete the per-item folder Orbits created (named "<source>-<id>")
        if d.is_dir() and d.name.startswith(f"{entry['source']}-") and d.name != entry["source"] + "-":
            shutil.rmtree(d, ignore_errors=True)
        self._save()
        self.emit("changed")

    # ---- apply --------------------------------------------------------
    @reversible("Wallpaper updated")
    def apply(self, file_rec: dict, desktop=True, lockscreen=False):
        from orbits.integrity import verify_tree
        for kind in ('light', 'dark'):
            if file_rec.get(kind):
                verify_tree(Path(file_rec[kind]).parent)
        from orbits.desktop import detect, xfce_wallpaper
        if lockscreen and detect() == "cinnamon":
            raise RuntimeError("Cinnamon manages the lock-screen background separately")
        if detect() == "xfce":
            if lockscreen:
                raise RuntimeError("Separate lock-screen backgrounds are not supported on XFCE")
            if desktop:
                xfce_wallpaper(file_rec["light"], file_rec.get("options", "zoom"))
            self._notify_apply()
            return
        light = GLib.filename_to_uri(file_rec["light"])
        dark = GLib.filename_to_uri(file_rec["dark"]) if file_rec.get("dark") else light
        if desktop:
            self._bg.set_string("picture-options", file_rec.get("options", "zoom"))
            self._bg.set_string("picture-uri", light)
            self._bg.set_string("picture-uri-dark", dark)
        if lockscreen:
            self._lockscreen.set_string("picture-options", "zoom")
            self._lockscreen.set_string("picture-uri", light)
        Gio.Settings.sync()
        self._notify_apply()

    def _notify_apply(self):
        if threading.current_thread() is threading.main_thread():
            self.emit("changed")
        else:
            GLib.idle_add(lambda:(self.emit("changed"), False)[1])

    def relocate(self, new_root: Path):
        """Move every installed item folder into new_root and rewrite stored paths. Returns count moved."""
        new_root.mkdir(parents=True, exist_ok=True)
        cur = GLib.filename_to_uri  # keep wallpapers that are currently applied pointing at the new place
        bg_uri, moved = self.current_uri(), 0
        for entry in self.items.values():
            old = Path(entry["dir"])
            if not old.is_dir() or old.parent == new_root:
                continue
            target = new_root / old.name
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(old), str(target))
            for f in entry["files"]:
                was_current = cur(f["light"]) == bg_uri
                for kind in ("light", "dark", "preview"):
                    if f.get(kind):
                        f[kind] = str(target / Path(f[kind]).name)
                for xml in target.glob("*.xml"):
                    import xml.etree.ElementTree as ET
                    tree = ET.parse(xml)
                    for node in tree.iter():
                        if node.text and node.text.startswith(str(old) + os.sep):
                            node.text = str(target / Path(node.text).relative_to(old))
                    tree.write(xml, encoding="unicode", xml_declaration=True)
                if was_current:
                    self.apply(f)
            entry["dir"] = str(target)
            moved += 1
        self._save()
        self.emit("changed")
        return moved

    def refresh_gnome_registration(self):
        self._write_gnome_xml()


_library = None


def get_library() -> Library:
    global _library
    if _library is None:
        _library = Library()
    return _library
