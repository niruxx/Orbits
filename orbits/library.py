"""Installed wallpaper collection: download, unpack, register with GNOME, apply, uninstall."""
import json
import os
import re
import shutil
import tarfile
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from gi.repository import Gio, GLib, GObject

from orbits import net
from orbits.config import BG_PROPERTIES_DIR, DATA_DIR, download_dir, settings
from orbits.sources.base import Download, Item

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".jxl", ".heic", ".svg", ".bmp", ".tif", ".tiff"}
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
    total = 0
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                target = (dest / info.filename).resolve()
                if not str(target).startswith(str(dest.resolve()) + os.sep) or info.is_dir():
                    continue
                total += info.file_size
                if total > MAX_UNPACKED:
                    raise RuntimeError("Archive is too large")
                if not (_is_image(info.filename) or info.filename.endswith(("metadata.json", "metadata.desktop"))):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        return
    with tarfile.open(archive) as t:
        members = []
        for m in t.getmembers():
            if not m.isfile():
                continue
            total += m.size
            if total > MAX_UNPACKED:
                raise RuntimeError("Archive is too large")
            if _is_image(m.name):
                members.append(m)
        t.extractall(dest, members=members, filter="data")


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
        # Drop entries whose files were deleted behind our back
        for key in [k for k, v in self.items.items() if not any(Path(f["light"]).exists() for f in v["files"])]:
            del self.items[key]
        self._bg = Gio.Settings.new(BG_SCHEMA)
        self._lockscreen = Gio.Settings.new(LOCK_SCHEMA)
        self._bg.connect("changed::picture-uri", lambda *_: self.emit("changed"))

    # ---- persistence -------------------------------------------------
    def _save(self):
        tmp = LIBRARY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, indent=2))
        os.replace(tmp, LIBRARY_FILE)
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
                out.append("    <options>zoom</options>")
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
        download = download or (item.downloads[0] if item.downloads else None)
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
                self.items[item.key] = entry
                self._save()
            self.emit("changed")
            callback(entry, err)

        net.run_async(work, done)

    def _install_blocking(self, item: Item, download: Download, report):
        dest_dir = download_dir() / f"{item.source}-{_slug(item.id)}"
        with tempfile.TemporaryDirectory(prefix="orbits-dl-") as tmp:
            tmp = Path(tmp)
            name = _slug(download.name) or "download"
            raw = tmp / name
            content_type, _ = net.download(download.url, raw, report)
            unpack = tmp / "unpacked"
            unpack.mkdir()
            looks_image = _is_image(name) or content_type.startswith("image/")
            if not looks_image and (_is_archive(name) or zipfile.is_zipfile(raw) or tarfile.is_tarfile(raw)):
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
            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            dest_dir.mkdir(parents=True)
            files = []
            for i, v in enumerate(variants[:200]):
                rec = {"name": v["name"]}
                for kind in ("light", "dark"):
                    src = v[kind]
                    if src is None:
                        rec[kind] = None
                        continue
                    target = dest_dir / f"{i:03d}-{kind}-{_slug(v['name'])}{src.suffix.lower()}"
                    shutil.move(src, target)
                    rec[kind] = str(target)
                files.append(rec)
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
        """Add local image files to the library (copied into the wallpapers folder)."""
        added = 0
        for p in paths:
            src = Path(p)
            if not (src.is_file() and _is_image(src.name)):
                continue
            key = f"local:{_slug(src.stem)}-{int(time.time() * 1000) % 10**8}"
            dest_dir = download_dir() / key.replace(":", "-")
            dest_dir.mkdir(parents=True, exist_ok=True)
            target = dest_dir / (_slug(src.stem) + src.suffix.lower())
            shutil.copy2(src, target)
            self.items[key] = {"key": key, "source": "local", "id": key, "title": src.stem,
                               "author": "", "page_url": "", "thumb_url": "", "dir": str(dest_dir),
                               "files": [{"name": src.stem, "light": str(target), "dark": None}],
                               "installed_at": time.time()}
            added += 1
        if added:
            self._save()
            self.emit("changed")
        return added

    # ---- uninstall ----------------------------------------------------
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
    def apply(self, file_rec: dict, desktop=True, lockscreen=False):
        light = GLib.filename_to_uri(file_rec["light"])
        dark = GLib.filename_to_uri(file_rec["dark"]) if file_rec.get("dark") else light
        if desktop:
            self._bg.set_string("picture-options", "zoom")
            self._bg.set_string("picture-uri", light)
            self._bg.set_string("picture-uri-dark", dark)
        if lockscreen:
            self._lockscreen.set_string("picture-options", "zoom")
            self._lockscreen.set_string("picture-uri", light)
        Gio.Settings.sync()
        self.emit("changed")

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
                for kind in ("light", "dark"):
                    if f.get(kind):
                        f[kind] = str(target / Path(f[kind]).name)
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
