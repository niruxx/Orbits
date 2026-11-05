"""Online font sources + the collection of fonts installed by Orbits (per-user or system-wide)."""
from orbits.undo import reversible, EntryState

import copy
import fcntl
import json
import math
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from gi.repository import Gio, GLib, GObject

from orbits import net
from orbits.config import CACHE_DIR, CONFIG_DIR, DATA_DIR

FONT_EXTS = {".ttf", ".otf", ".ttc", ".otc"}
USER_FONT_DIR = Path(GLib.get_user_data_dir()) / "fonts" / "orbits"
SYSTEM_FONT_DIR = Path("/usr/share/fonts/orbits")
FONT_LIBRARY = DATA_DIR / "fonts.json"
FONT_RULES = CONFIG_DIR / "font-rules.json"
FONTCONFIG = Path(GLib.get_user_config_dir()) / "fontconfig/conf.d/99-orbits.conf"
PREVIEW_DIR = CACHE_DIR / "font-previews"
PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
CATALOG_TTL = 24 * 3600


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "font"


@dataclass
class FontItem:
    source: str
    id: str
    family: str
    category: str = ""
    subtitle: str = ""
    license: str = ""
    styles: int = 0
    preview_url: str = ""   # small TTF used to render the card before install
    page_url: str = ""
    download: str = ""      # source-specific
    extra: dict = field(default_factory=dict)

    @property
    def key(self):
        return f"{self.source}:{self.id}"


# ---------------------------------------------------------------- sources
def _cached_json(name, url, ttl=CATALOG_TTL):
    path = CACHE_DIR / name
    if path.exists() and time.time() - path.stat().st_mtime < ttl:
        try:
            return json.loads(path.read_text())
        except ValueError:
            pass
    try:
        data = net.get_json(url)
    except Exception:
        if path.exists():  # offline: fall back to a stale copy
            return json.loads(path.read_text())
        raise
    path.write_text(json.dumps(data))
    return data


class FontSource:
    id = ""
    name = ""
    icon = "font-x-generic-symbolic"
    tagline = ""
    categories: list[tuple[str, str]] = []
    sorts: list[tuple[str, str]] = []

    def catalog(self) -> list[FontItem]:
        raise NotImplementedError

    def fetch(self, query, sort, category, page, page_size=30):
        items = self.catalog()
        if category and category != "all":
            items = [i for i in items if self._in_category(i, category)]
        if query:
            q = query.lower()
            items = [i for i in items if q in i.family.lower()]
        if sort == "alpha":
            items = sorted(items, key=lambda i: i.family.lower())
        elif sort == "styles":
            items = sorted(items, key=lambda i: -i.styles)
        start = (page - 1) * page_size
        return items[start:start + page_size], start + page_size < len(items)

    def _in_category(self, item, category):
        return item.category == category

    def download_files(self, item: FontItem, dest: Path, progress):
        raise NotImplementedError


class GoogleFontsSource(FontSource):
    id = "google-fonts"
    name = "Google Fonts"
    icon = "font-x-generic-symbolic"
    tagline = "2,000+ open-source families, via Fontsource"
    categories = [("all", "All"), ("sans-serif", "Sans Serif"), ("serif", "Serif"), ("display", "Display"),
                  ("handwriting", "Handwriting"), ("monospace", "Monospace"), ("other-open", "More Open Fonts")]
    sorts = [("popular", "Popular"), ("alpha", "A–Z"), ("styles", "Most Styles"), ("recent", "Recently Updated")]

    def __init__(self):
        self._catalog = None

    def _popularity(self):
        """family -> rank from Google's own metadata (lower is more popular)."""
        path = CACHE_DIR / "google-fonts-popularity.json"
        if path.exists() and time.time() - path.stat().st_mtime < 7 * CATALOG_TTL:
            try:
                return json.loads(path.read_text())
            except ValueError:
                pass
        try:
            raw = net.get_bytes("https://fonts.google.com/metadata/fonts").decode("utf-8")
            meta = json.loads(raw[raw.index("{"):])
            ranks = {f["family"]: f.get("popularity", 9999) for f in meta["familyMetadataList"]}
            path.write_text(json.dumps(ranks))
            return ranks
        except Exception:
            return {}

    def catalog(self):
        if self._catalog is not None:
            return self._catalog
        data = _cached_json("fontsource-catalog.json", "https://api.fontsource.org/v1/fonts")
        ranks = self._popularity()
        data.sort(key=lambda d: ranks.get(d["family"], 99999))
        out = []
        for d in data:
            if d.get("category") == "icons":
                continue
            weights = d.get("weights") or [400]
            weight = 400 if 400 in weights else weights[0]
            style = "normal" if "normal" in (d.get("styles") or ["normal"]) else d["styles"][0]
            subset = d.get("defSubset") or "latin"
            google = d.get("type") == "google"
            out.append(FontItem(
                source=self.id, id=d["id"], family=d["family"], category=d.get("category", ""),
                subtitle=f"{_pretty(d.get('category', ''))} · {len(weights)} weight{'s' if len(weights) != 1 else ''}"
                         + (" · variable" if d.get("variable") else ""),
                license=d.get("license", ""), styles=len(weights) * len(d.get("styles") or [1]),
                preview_url=f"https://cdn.jsdelivr.net/fontsource/fonts/{d['id']}@latest/{subset}-{weight}-{style}.ttf",
                page_url=(f"https://fonts.google.com/specimen/{d['family'].replace(' ', '+')}" if google
                          else f"https://fontsource.org/fonts/{d['id']}"),
                download="google" if google else "fontsource",
                extra={"modified": d.get("lastModified", ""), "google": google},
            ))
        self._catalog = out
        return out

    def fetch(self, query, sort, category, page, page_size=30):
        if sort == "recent":
            items = sorted(self.catalog(), key=lambda i: i.extra.get("modified", ""), reverse=True)
            if category and category != "all":
                items = [i for i in items if self._in_category(i, category)]
            if query:
                items = [i for i in items if query.lower() in i.family.lower()]
            start = (page - 1) * page_size
            return items[start:start + page_size], start + page_size < len(items)
        return super().fetch(query, "" if sort == "popular" else sort, category, page, page_size)

    def _in_category(self, item, category):
        if category == "other-open":
            return not item.extra.get("google")
        return item.category == category and item.extra.get("google")

    def download_files(self, item, dest, progress):
        if item.download == "google":
            raw = net.get_bytes("https://fonts.google.com/download/list?family="
                                + GLib.uri_escape_string(item.family, None, False))
            manifest = json.loads(raw.decode("utf-8").lstrip(")]}'\n"))["manifest"]
            refs = [r for r in manifest["fileRefs"] if Path(r["filename"]).suffix.lower() in FONT_EXTS]
            variable = [r for r in refs if not r["filename"].startswith("static/")]
            refs = variable or refs  # prefer variable fonts: fewer files, every weight
            for n, ref in enumerate(refs):
                net.download(ref["url"], dest / Path(ref["filename"]).name)
                progress((n + 1) / len(refs))
            for f in manifest.get("files", []):
                if f["filename"].upper().startswith(("OFL", "LICENSE")):
                    (dest / Path(f["filename"]).name).write_text(f["contents"])
        else:
            archive = dest / "download.zip"
            net.download(f"https://r2.fontsource.org/fonts/{item.id}@latest/download.zip", archive, progress)
            extract_fonts(archive, dest, prefer=("static/",))
            archive.unlink()


class NerdFontsSource(FontSource):
    id = "nerd-fonts"
    name = "Nerd Fonts"
    icon = "utilities-terminal-symbolic"
    tagline = "Developer fonts patched with thousands of icons"
    categories = []
    sorts = [("alpha", "A–Z")]

    def catalog(self):
        rel = _cached_json("nerd-fonts-release.json",
                           "https://api.github.com/repos/ryanoasis/nerd-fonts/releases/latest", ttl=6 * 3600)
        out = []
        for a in rel.get("assets", []):
            if not a["name"].endswith(".tar.xz"):
                continue
            base = a["name"][:-7]
            out.append(FontItem(
                source=self.id, id=base, family=f"{base} Nerd Font", category="monospace",
                subtitle=f"Nerd Fonts {rel.get('tag_name', '')} · {a['size'] / 1048576:.1f} MB",
                license="MIT / OFL", page_url="https://www.nerdfonts.com/font-downloads",
                download=a["browser_download_url"],
                extra={"modified": rel.get("published_at", ""), "version": rel.get("tag_name", "")},
            ))
        return out

    def download_files(self, item, dest, progress):
        archive = dest / "font.tar.xz"
        net.download(item.download, archive, progress)
        extract_fonts(archive, dest)
        archive.unlink()


def _pretty(s):
    return s.replace("-", " ").title()


def extract_fonts(archive: Path, dest: Path, prefer=()):
    """Pull font files (only) out of a zip/tar into dest, flattening paths."""
    def wanted(name):
        return Path(name).suffix.lower() in FONT_EXTS or Path(name).name.upper().startswith(("OFL", "LICENSE"))

    names = []
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            infos = [i for i in z.infolist() if not i.is_dir() and wanted(i.filename)]
            if prefer:
                pref = [i for i in infos if i.filename.startswith(prefer) or "LICENSE" in i.filename.upper()]
                infos = pref or infos
            for i in infos:
                target = dest / Path(i.filename).name
                with z.open(i) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                names.append(target)
    else:
        with tarfile.open(archive) as t:
            for m in t.getmembers():
                if m.isfile() and wanted(m.name):
                    src = t.extractfile(m)
                    target = dest / Path(m.name).name
                    with open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
                    names.append(target)
    if not any(Path(n).suffix.lower() in FONT_EXTS for n in names):
        raise RuntimeError("No font files found in the download")


FONT_SOURCES = [GoogleFontsSource(), NerdFontsSource()]
FONT_SOURCES_BY_ID = {s.id: s for s in FONT_SOURCES}


# ---------------------------------------------------------------- previews
_preview_lock = threading.Lock()
_registered_previews = set()


def preview_font_file(item: FontItem):
    """Download the small preview TTF (worker thread). Returns its path or None."""
    if not item.preview_url:
        return None
    path = PREVIEW_DIR / (slugify(item.id) + ".ttf")
    if not path.exists():
        data = net.get_bytes(item.preview_url, timeout=20)
        path.write_bytes(data)
    return path


def register_preview(path, fontmap):
    """Make a font file usable by this process' Pango (main thread)."""
    with _preview_lock:
        if str(path) in _registered_previews:
            return True
        try:
            fontmap.add_font_file(str(path))
        except (GLib.Error, AttributeError):
            return False
        _registered_previews.add(str(path))
        return True


def families_in(files):
    """Family names defined by font files, via fc-scan."""
    fams = []
    for f in files:
        try:
            out = subprocess.run(["fc-scan", "--format", "%{family[0]}\n", str(f)], capture_output=True,
                                 text=True, timeout=20).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        for line in out.splitlines():
            if line.strip() and line.strip() not in fams:
                fams.append(line.strip())
    return fams


def system_families():
    """Fontconfig family discovery usable by scheduled Looks without loading the GTK UI."""
    result = subprocess.run(["fc-list", "--format", "%{family[0]}\n"], capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Fontconfig could not list fonts")
    return sorted(set(filter(None, result.stdout.splitlines())), key=str.casefold)


def family_file(family):
    """Resolve an installed family for read-only inspection, without loading a font through GTK."""
    result=subprocess.run(["fc-match","--format","%{file}",family],capture_output=True,text=True,timeout=20)
    path=Path(result.stdout.strip())
    if result.returncode or not path.is_file():raise RuntimeError("Font file could not be located")
    return str(path)


# ---------------------------------------------------------------- library
class FontLibrary(GObject.Object):
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__()
        self.items: dict[str, dict] = {}
        self.busy: dict[str, float] = {}
        try:
            self.items = json.loads(FONT_LIBRARY.read_text())
        except (OSError, ValueError):
            pass
        self._persisted=copy.deepcopy(self.items)
        for key in [k for k, v in self.items.items() if not Path(v["dir"]).is_dir()]:
            del self.items[key]
        from orbits.desktop import interface_settings
        self.iface = interface_settings()

    def _save(self):
        FONT_LIBRARY.parent.mkdir(parents=True,exist_ok=True)
        with FONT_LIBRARY.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            try:
                data=json.loads(FONT_LIBRARY.read_text())
                if not isinstance(data,dict):data={}
            except (OSError,ValueError):data={}
            previous=getattr(self,"_persisted",{})
            for key in previous.keys()-self.items.keys():data.pop(key,None)
            for key,value in self.items.items():
                if key not in previous:data[key]=value
                elif key in data and value!=previous[key]:
                    merged=dict(data[key])
                    for field in previous[key].keys()-value.keys():merged.pop(field,None)
                    for field,content in value.items():
                        if field not in previous[key] or content!=previous[key][field]:merged[field]=content
                    data[key]=merged
            with tempfile.NamedTemporaryFile(mode="w",dir=FONT_LIBRARY.parent,delete=False) as stream:
                json.dump(data,stream,indent=2);tmp=Path(stream.name)
            try:os.replace(tmp,FONT_LIBRARY)
            finally:tmp.unlink(missing_ok=True)
            self.items=data;self._persisted=copy.deepcopy(data)
            from orbits.font_tools import write_fontconfig
            write_fontconfig(FONTCONFIG,self.load_rules(),self._disabled_paths())

    def is_installed(self, key):
        return key in self.items

    def sorted_items(self):
        return sorted(self.items.values(), key=lambda i: i["family"].lower())

    def install(self, item: FontItem, callback, progress=None):
        """Install for the current user. callback(entry, err) on main loop."""
        source = FONT_SOURCES_BY_ID[item.source]
        self.busy[item.key] = 0.0
        self.emit("changed")

        def report(frac):
            self.busy[item.key] = frac
            if progress:
                GLib.idle_add(lambda: (progress(frac), False)[1])

        def work():
            slug = slugify(item.family)
            dest = USER_FONT_DIR / slug
            with tempfile.TemporaryDirectory(prefix="orbits-font-") as tmp:
                tmp = Path(tmp)
                source.download_files(item, tmp, report)
                if dest.exists():
                    shutil.rmtree(dest)
                dest.mkdir(parents=True)
                for f in tmp.iterdir():
                    if f.is_file():
                        shutil.move(str(f), dest / f.name)
            subprocess.run(["fc-cache", "-f", str(dest)], capture_output=True, timeout=120)
            files = [str(f) for f in sorted(dest.iterdir()) if f.suffix.lower() in FONT_EXTS]
            return {"key": item.key, "source": item.source, "id": item.id, "family": item.family,
                    "families": families_in(files) or [item.family], "slug": slug, "dir": str(dest),
                    "files": files, "scope": "user", "license": item.license, "page_url": item.page_url,
                    "category": item.category, "installed_at": time.time(),
                    "remote_changed": item.extra.get("modified", "")}

        def done(entry, err):
            self.busy.pop(item.key, None)
            if entry:
                previous = self.items.get(item.key,{})
                for field in ("collections","enabled"):
                    if field in previous:
                        entry[field] = previous[field]
                self.items[item.key] = entry
                self._save()
            self.emit("changed")
            callback(entry, err)

        net.run_async(work, done)

    @reversible("Font uninstalled", extra=lambda self, key: EntryState(self, [key],
        [self.items[key]["dir"]] if key in self.items and USER_FONT_DIR in Path(self.items[key]["dir"]).parents else []))
    def uninstall_user(self, key):
        entry = self.items.get(key)
        if not entry:
            return
        d = Path(entry["dir"])
        if d.is_dir() and USER_FONT_DIR in d.parents:
            shutil.rmtree(d, ignore_errors=True)
            subprocess.Popen(["fc-cache", "-f"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if entry.get("scope") == "both":
            entry["scope"] = "system"
            entry["dir"] = str(SYSTEM_FONT_DIR / entry["slug"])
            entry["files"] = [str(Path(entry["dir"])/Path(file).name) for file in entry["files"]]
        else:
            self.items.pop(key)
        self._save()
        self.emit("changed")

    def mark_system(self, key, installed: bool):
        """Record the result of the privileged helper's font-install / font-remove."""
        entry = self.items.get(key)
        if not entry:
            return
        if installed:
            entry["scope"] = "both" if entry["scope"] in ("user", "both") else "system"
        elif entry["scope"] == "both":
            entry["scope"] = "user"
        else:
            self.items.pop(key)
        self._save()
        self.emit("changed")

    def collections(self):
        return sorted({name for entry in self.items.values() for name in entry.get("collections",[])})

    def set_collections(self, key, names):
        self.items[key]["collections"] = sorted({name.strip()[:80] for name in names if name.strip()})
        self._save()
        self.emit("changed")

    def load_rules(self):
        try:
            data = json.loads(FONT_RULES.read_text())
            if isinstance(data,dict):
                return data
        except (OSError,ValueError):
            pass
        return {"emoji":"", "hinting":{}, "substitutions":{}}

    def _disabled_paths(self):
        paths = []
        for entry in self.items.values():
            if entry.get("enabled",True):
                continue
            paths.extend(entry["files"])
            if entry["scope"] in ("system","both"):
                paths.extend(str(SYSTEM_FONT_DIR/entry["slug"]/Path(file).name) for file in entry["files"])
        return paths

    def save_rules(self, rules):
        from orbits.font_tools import write_fontconfig
        write_fontconfig(FONTCONFIG,rules,self._disabled_paths())
        FONT_RULES.write_text(json.dumps(rules,indent=2))
        self._refresh_fonts()

    def set_enabled(self, key, enabled):
        from orbits.font_tools import write_fontconfig
        entry = self.items[key]
        previous = entry.get("enabled",True)
        entry["enabled"] = bool(enabled)
        try:
            write_fontconfig(FONTCONFIG,self.load_rules(),self._disabled_paths())
            self._save()
        except Exception:
            entry["enabled"] = previous
            write_fontconfig(FONTCONFIG,self.load_rules(),self._disabled_paths())
            raise
        self._refresh_fonts()
        self.emit("changed")

    @staticmethod
    def _refresh_fonts():
        subprocess.Popen(["fc-cache","-f"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

    def import_files(self, paths, callback):
        """Validate and install local fonts in a worker, then publish metadata on the main loop."""
        from orbits.font_tools import prepare_local
        def work():
            with tempfile.TemporaryDirectory(prefix="orbits-local-font-") as tmp:
                data = prepare_local(paths,tmp)
                key = "local-fonts:" + data["digest"][:24]
                slug = "local-" + data["digest"][:24]
                dest = USER_FONT_DIR / slug
                USER_FONT_DIR.mkdir(parents=True,exist_ok=True)
                created=not dest.exists()
                if created:
                    with tempfile.TemporaryDirectory(prefix=".orbits-font-",dir=USER_FONT_DIR) as staged:
                        tree = Path(staged)/"font"
                        shutil.copytree(tmp,tree)
                        os.rename(tree,dest)
                files = [str(dest/path.name) for path in data["files"]]
                try:
                    subprocess.run(["fc-cache","-f",str(dest)],capture_output=True,timeout=120,check=True)
                except Exception:
                    if created:shutil.rmtree(dest,ignore_errors=True)
                    raise
                family = data["families"][0]
                return {"key":key,"source":"local-fonts","id":data["digest"][:24],"slug":slug,
                        "family":family,"families":data["families"],"dir":str(dest),"files":files,
                        "scope":"user","license":data["metadata"][0]["license"],"page_url":"",
                        "category":"monospace" if data["metadata"][0]["monospace"] else "",
                        "installed_at":time.time(),"enabled":True}
        def done(entry,error):
            if entry:
                previous = self.items.get(entry["key"],{})
                for field in ("collections","enabled","scope"):
                    if field in previous:
                        entry[field] = previous[field]
                self.items[entry["key"]] = entry
                try:self._save()
                except Exception as failure:
                    callback(None,RuntimeError(f"Font files installed, but the library could not be saved: {failure}"))
                    return
                self.emit("changed")
            callback(entry,error)
        net.run_async(work,done)

    # ---- applying ----------------------------------------------------
    KEYS = {"interface": "font-name", "document": "document-font-name", "monospace": "monospace-font-name"}

    def current(self, role):
        return self.iface.get_string(self.KEYS[role])

    @reversible("Font updated", extra=lambda self, family, *args, **kwargs: EntryState(self,
        [key for key, entry in self.items.items() if family in entry.get("families", []) or entry.get("family") == family], []))
    def apply(self, family, role, size=None, variations=None):
        if variations:
            if not re.fullmatch(r"[A-Za-z0-9]{4}=-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:e[+-]?[0-9]+)?(?:,[A-Za-z0-9]{4}=-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:e[+-]?[0-9]+)?)*",variations):
                raise ValueError("Invalid font axis values")
            if not all(math.isfinite(float(part.split("=")[1])) for part in variations.split(",")):
                raise ValueError("Font axis values must be finite")
        for entry in list(self.items.values()):
            if family in entry.get("families",[]) and not entry.get("enabled",True):
                self.set_enabled(entry["key"],True)
        key = self.KEYS[role]
        cur = self.iface.get_string(key)
        m = re.search(r"\s(\d+(?:\.\d+)?)(?:\s+@.*)?$", cur)
        size = size or (m.group(1) if m else "11")
        self.iface.set_string(key, f"{family} {size}" + (f" @{variations}" if variations else ""))
        Gio.Settings.sync()
        self.emit("changed")


_font_library = None


def get_font_library() -> FontLibrary:
    global _font_library
    if _font_library is None:
        _font_library = FontLibrary()
    return _font_library
