"""Bootloader themes: detect the bootloader, browse themes, install/apply/remove via the root helper.

Supported: GRUB (gnome-look.org) and rEFInd (GitHub topic "refind-theme").
Detected but without theme support: systemd-boot, Limine, others.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from gi.repository import GLib, GObject

from orbits import net
from orbits.config import DATA_DIR
from orbits.sources.base import Download, Item, Source
from orbits.sources.kde import OcsSource

INDEX = DATA_DIR / "boot-themes.json"
STAGING = DATA_DIR / "staging"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+ ()-]{0,100}$")


@dataclass
class Loader:
    id: str          # grub | refind | systemd-boot | limine | unknown
    name: str
    supported: bool
    booted: bool     # what the firmware says started this boot
    detail: str = ""


LOADER_NAMES = {"grub": "GRUB", "refind": "rEFInd", "systemd-boot": "systemd-boot", "limine": "Limine"}


def _classify(label, path):
    text = f"{label} {path}".lower()
    if "refind" in text:
        return "refind"
    if "grub" in text or "shim" in text or re.search(r"\\(fedora|ubuntu|debian|opensuse|centos|rocky)\\", text):
        return "grub"
    if "systemd-boot" in text or "linux boot manager" in text or "systemd" in text:
        return "systemd-boot"
    if "limine" in text:
        return "limine"
    return None


def detect():
    """Best-effort list of bootloaders on this machine, booted one first. Works without root."""
    found = {}
    booted = None
    try:
        out = subprocess.run(["efibootmgr"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    cur = re.search(r"^BootCurrent:\s*(\w+)", out, re.M)
    for m in re.finditer(r"^Boot(\w{4})\*?\s+(.+?)(?:\t|\s{2,})(.*)$", out, re.M):
        num, label, path = m.groups()
        kind = _classify(label, path)
        if not kind:
            continue
        is_cur = bool(cur and cur.group(1) == num)
        if is_cur:
            booted = kind
        if kind not in found or is_cur:
            found[kind] = f"UEFI entry “{label.strip()}”" + (" — booted now" if is_cur else "")
    # BIOS systems or extra hints from the installed system
    if any(shutil.which(c) for c in ("grub-mkconfig", "grub2-mkconfig", "update-grub")) and \
            os.path.exists("/etc/default/grub"):
        found.setdefault("grub", "GRUB tools and /etc/default/grub are installed")
        if not out:
            booted = booted or "grub"
    if shutil.which("refind-install"):
        found.setdefault("refind", "rEFInd is installed")
    loaders = [Loader(k, LOADER_NAMES[k], k in ("grub", "refind"), k == booted, d) for k, d in found.items()]
    loaders.sort(key=lambda lo: (not lo.booted, not lo.supported, lo.name))
    return loaders


# ---------------------------------------------------------------- stores
class GrubStoreSource(OcsSource):
    api = "https://api.gnome-look.org/ocs/v1/content/data"
    store_name = "gnome-look.org"
    id = "boot-grub"
    name = "GRUB Themes"
    source_url = "https://www.gnome-look.org"
    icon = "drive-harddisk-symbolic"
    tagline = "GRUB themes · gnome-look.org"
    default_category = "109"
    sorts = [("down", "Most Downloaded"), ("new", "Newest"), ("high", "Top Rated"), ("alpha", "A–Z")]
    categories = []


class GitHubTopicSource(Source):
    """Repositories tagged with a GitHub topic, newest code as a zip."""

    sorts = [("stars", "Most Stars"), ("updated", "Recently Updated")]
    categories = []

    def __init__(self, id_, name, topic, tagline):
        self.id, self.name, self.topic, self.tagline = id_, name, topic, tagline
        self.icon = "drive-harddisk-symbolic"
        self.source_url = f"https://github.com/topics/{topic}"

    def fetch(self, query, sort, category, page):
        q = f"topic:{self.topic}" + (f" {query}" if query else "")
        data = net.get_json("https://api.github.com/search/repositories",
                            {"q": q, "sort": sort or "stars", "order": "desc", "per_page": 24, "page": page})
        if "items" not in data:
            raise RuntimeError(data.get("message") or "GitHub search failed")
        items = []
        for r in data["items"]:
            full = r["full_name"]
            items.append(Item(
                source=self.id, id=full.replace("/", "~"), title=r["name"], author=r["owner"]["login"],
                thumb_url=f"https://opengraph.githubassets.com/1/{full}",
                preview_url=f"https://opengraph.githubassets.com/1/{full}", page_url=r["html_url"],
                description=r.get("description") or "", stats=f"★ {r['stargazers_count']:,} · {r.get('license') and r['license'].get('spdx_id') or 'no license'}",
                downloads=[Download(f"{r['name']}.zip", f"https://codeload.github.com/{full}/zip/HEAD")],
                updated=r.get("pushed_at") or "",
            ))
        return items, page * 24 < min(int(data.get("total_count") or 0), 1000)


SOURCES = {
    "grub": GrubStoreSource(),
    "refind": GitHubTopicSource("boot-refind", "rEFInd Themes", "refind-theme", "rEFInd themes · GitHub"),
}


# ---------------------------------------------------------------- archive scanning
def find_boot_themes(root: Path, loader, fallback):
    """(dir, name) for every theme folder (containing theme.txt / theme.conf) in an unpacked archive."""
    marker = "theme.txt" if loader == "grub" else "theme.conf"
    found = []
    for f in sorted(root.rglob(marker)):
        d = f.parent
        if any(p == d or p in d.parents for p, _ in found):
            continue
        name = d.name if d != root else fallback
        # GitHub zips unpack to "<repo>-<branch>"; drop the branch suffix
        name = re.sub(r"-(main|master|HEAD|[0-9a-f]{7,40})$", "", name)
        name = re.sub(r"[^A-Za-z0-9._+ ()-]", "-", name).strip("- .")[:101] or "theme"
        if any(existing == name for _, existing in found):
            raise ValueError(f"Archive contains multiple themes named {name!r}; install them separately")
        if NAME_RE.match(name):
            found.append((d, name))
    return found


def preview_in(d: Path):
    for cand in ("preview.png", "screenshot.png", "background.png", "background.jpg", "bg.png", "background.svg"):
        hits = sorted(d.rglob(cand))
        if hits:
            return hits[0]
    pngs = sorted((p for p in d.rglob("*.png") if p.is_file()), key=lambda p: -p.stat().st_size)
    return pngs[0] if pngs else None


# ---------------------------------------------------------------- manager (mirrors Library for BrowsePage)
class BootThemeManager(GObject.Object):
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, loader):
        super().__init__()
        self.loader = loader
        self.busy: dict[str, float] = {}
        self.items = self._load()
        self.system = None   # last result of boot-list for this loader

    def _load(self):
        try:
            return json.loads(INDEX.read_text()).get(self.loader, {})
        except (OSError, ValueError):
            return {}

    def _save(self):
        try:
            data = json.loads(INDEX.read_text())
        except (OSError, ValueError):
            data = {}
        data[self.loader] = self.items
        INDEX.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=INDEX.parent, delete=False) as f:
            json.dump(data, f, indent=2)
            tmp = f.name
        os.replace(tmp, INDEX)

    def is_installed(self, key):
        return key in self.items

    def install(self, item, download, callback, progress=None):
        from orbits.privileged import run_helper
        from orbits.themes import _safe_unpack

        if self.busy:
            callback(None, RuntimeError("Another boot theme installation is in progress"))
            return
        download = download or (item.downloads[-1] if item.downloads else None)
        if download is None:
            callback(None, RuntimeError("Nothing to download"))
            return
        self.busy[item.key] = 0.0
        self.emit("changed")

        def report(frac):
            self.busy[item.key] = frac
            if progress:
                GLib.idle_add(lambda: (progress(frac), False)[1])

        stage_paths = []

        def work():
            STAGING.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="orbits-boot-") as tmp:
                tmp = Path(tmp)
                archive = tmp / (re.sub(r"[^A-Za-z0-9._-]", "_", download.name) or "theme")
                net.download(download.url, archive, report)
                unpack = tmp / "u"
                unpack.mkdir()
                _safe_unpack(archive, unpack)
                fallback = re.sub(r"\.(zip|tar(\.\w+)?|tgz|txz)$", "", download.name, flags=re.I)
                themes = find_boot_themes(unpack, self.loader, re.sub(r"[^A-Za-z0-9._+-]", "-", fallback) or "theme")
                if not themes:
                    what = "theme.txt" if self.loader == "grub" else "theme.conf"
                    raise RuntimeError(f"No {LOADER_NAMES[self.loader]} theme ({what}) found in {download.name}")
                staged = []
                for src, name in themes:
                    dest = Path(tempfile.mkdtemp(prefix=f"boot-{self.loader}-", dir=STAGING))
                    stage_paths.append(dest)
                    shutil.copytree(src, dest, symlinks=False, dirs_exist_ok=True)
                    (dest / ".orbits.json").write_text(json.dumps({"key": item.key, "title": item.title, "page": item.page_url}))
                    staged.append((str(dest), name))
                return staged

        def done(staged, err):
            if err:
                for path in stage_paths:
                    shutil.rmtree(path, ignore_errors=True)
                self.busy.pop(item.key, None)
                self.emit("changed")
                callback(None, err)
                return
            queue = list(staged)
            installed_names = []

            def next_one(ok=True, msg=""):
                if not ok:
                    for d, _n in staged:
                        shutil.rmtree(d, ignore_errors=True)
                    if installed_names:
                        self.items[item.key] = make_entry(installed_names)
                        try:
                            self._save()
                        except OSError as error:
                            msg += f" (Could not save installed variants: {error})"
                    self.busy.pop(item.key, None)
                    self.emit("changed")
                    callback(None, RuntimeError(msg))
                    return
                if not queue:
                    for d, _n in staged:
                        shutil.rmtree(d, ignore_errors=True)
                    entry = make_entry(installed_names)
                    self.items[item.key] = entry
                    try:
                        self._save()
                    except OSError as error:
                        self.busy.pop(item.key, None)
                        self.emit("changed")
                        callback(None, RuntimeError(f"Themes installed, but the library could not be saved: {error}"))
                        return
                    self.busy.pop(item.key, None)
                    self.emit("changed")
                    callback(entry, None)
                    return
                d, name = queue.pop(0)
                def installed(ok, msg):
                    if ok:
                        installed_names.append(name)
                    next_one(ok, msg)

                run_helper(["boot-theme-install", self.loader, d, name], installed)

            def make_entry(names):
                return {"key": item.key, "title": item.title, "names": list(names),
                        "preview": item.thumb_url, "page": item.page_url, "download": download.name,
                        "remote_changed": item.updated, "installed_at": time.time()}

            next_one()

        net.run_async(work, done)

    def forget(self, name):
        for key, e in list(self.items.items()):
            if name in e["names"]:
                e["names"].remove(name)
                if not e["names"]:
                    del self.items[key]
        self._save()
        self.emit("changed")

    def uninstall(self, key, callback=None):
        from orbits.privileged import run_helper

        e = self.items.get(key)
        if not e:
            return
        names = list(e["names"])

        def next_one(ok=True, msg=""):
            if not ok or not names:
                if callback:
                    callback(ok, msg)
                return
            if names:
                name = names.pop(0)
                run_helper(["boot-theme-remove", self.loader, name],
                           lambda ok2, m2, n=name: (ok2 and self.forget(n), next_one(ok2, m2)))

        next_one()

    def scan(self, callback):
        """Ask the helper for every installed theme (the ESP / /boot is usually root-only)."""
        from orbits.privileged import run_helper

        def done(ok, out):
            if ok:
                try:
                    data = json.loads(out.strip().splitlines()[-1])
                    self.system = data.get(self.loader)
                except (ValueError, IndexError, AttributeError, TypeError):
                    ok, out = False, "Couldn't read the helper's answer"
            self.emit("changed")
            callback(ok, out)

        run_helper(["boot-list"], done)


_managers = {}


def get_manager(loader) -> BootThemeManager:
    if loader not in _managers:
        _managers[loader] = BootThemeManager(loader)
    return _managers[loader]
