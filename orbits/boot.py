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
REPOSITORY_METADATA = frozenset({
    '.git', '.github', '.gitlab', '.hg', '.svn', '.gitattributes', '.gitignore',
    '.gitmodules', '.gitkeep', '.gittattribute', '.gittattributes', '.editorconfig',
})


def _repository_metadata(path):
    return any(part.lower() in REPOSITORY_METADATA for part in Path(path).parts)


def _boot_theme_name(value):
    value = re.sub(r"-(main|master|HEAD|[0-9a-f]{7,40})$", "", value)
    return re.sub(r"[^A-Za-z0-9._+ ()-]", "-", value).strip("- .")[:101] or "theme"


def _named_refind_config(path):
    """Recognize standalone appearance configs, excluding the main boot-manager config."""
    if path.name.lower() == 'refind.conf' or path.is_symlink() or not path.is_file():
        return False
    if path.stat().st_size > 65536:
        return False
    try:
        with path.open(encoding='utf-8-sig') as stream:
            text = stream.read(65537)
        if len(text) > 65536:
            return False
    except (OSError, UnicodeError):
        return False
    if re.search(r'^\s*(include|menuentry|loader|volume)\s', text, re.M | re.I):
        return False
    return bool(re.search(r'^\s*(icons_dir|banner|selection_big|selection_small|font|banner_scale|hideui)\s+\S',
                          text, re.M | re.I))


def find_boot_themes(root: Path, loader, fallback):
    """Return directory/name pairs, or config-file/name pairs for named rEFInd flavours."""
    root = Path(root)
    marker = "theme.txt" if loader == "grub" else "theme.conf"
    candidates = [path for path in root.rglob('*')
                  if path.name.lower() == marker and not path.is_symlink() and path.is_file()
                  and not _repository_metadata(path.relative_to(root))]
    candidates.sort(key=lambda path: (len(path.parts), str(path).lower()))
    found, directories = [], set()
    for config in candidates:
        directory = config.parent
        if directory in directories:
            raise ValueError(f"Archive contains ambiguous {marker} filenames in {directory.name!r}")
        directories.add(directory)
    for config in candidates:
        directory = config.parent
        if any(parent == directory or parent in directory.parents for parent, _ in found):
            continue
        name = directory.name if directory != root else fallback
        name = _boot_theme_name(name)
        if any(existing.casefold() == name.casefold() for _, existing in found):
            raise ValueError(f"Archive contains multiple themes named {name!r}; install them separately")
        if NAME_RE.match(name):
            found.append((directory, name))
    if loader == 'refind':
        alternatives = sorted(path for path in root.rglob('*') if path.suffix.lower() == '.conf'
                              and path.name.lower() != marker
                              and not _repository_metadata(path.relative_to(root)))
        for config in alternatives:
            if any(parent == config.parent or parent in config.parent.parents for parent, _ in found
                   if parent.is_dir()):
                continue
            if not _named_refind_config(config):
                continue
            base = config.parent.name if config.parent != root else fallback
            name = _boot_theme_name(_boot_theme_name(base)+'-'+config.stem)
            if any(existing.casefold() == name.casefold() for _, existing in found):
                raise ValueError(f"Archive contains multiple themes named {name!r}; install them separately")
            found.append((config, name))
    return found


def missing_boot_theme_message(root, loader, filename):
    marker = "theme.txt" if loader == "grub" else "theme.conf"
    paths = [path for path in Path(root).rglob('*') if path.is_file()
             and not _repository_metadata(path.relative_to(root))]
    message = (f"No ready-to-use {LOADER_NAMES[loader]} theme found in {filename}. "
               f"All nested folders were searched for {marker}.")
    if any(path.name.lower() in ('theme_studio.py', 'build_and_deploy_theme.py', 'build_theme.py', 'generate_theme.py')
           for path in paths):
        message += " This archive contains a theme editor or generator; export/build a finished static theme before importing."
    if any(path.is_file() and not path.is_symlink() for path in Path(root).rglob('.gitmodules')):
        message += " It references Git submodules, whose contents are not bundled in GitHub source ZIPs."
    if any(path.name.lower() in (marker+'.in', marker+'.example', marker+'.sample', marker+'.template')
           for path in paths):
        message += " This archive contains a configuration template, which must be completed before importing."
    if any(path.name.lower() in ('install.sh', 'setup.sh', 'install.py', 'install') for path in paths):
        message += " An installer script is present; Orbits does not execute downloaded scripts."
    return message + f" Import an archive containing {marker} and its theme assets."


def stage_boot_theme(source, destination, loader):
    """Discard repository metadata; validate only the assets copied for installation."""
    def ignore(_directory, names):
        return [name for name in names if name.lower() in REPOSITORY_METADATA]
    source = Path(source)
    selected = None
    if source.is_symlink():
        raise ValueError('Theme links are not supported')
    if source.is_file():
        if loader != 'refind' or source.suffix.lower() != '.conf' or not _named_refind_config(source):
            raise ValueError('Not a standalone rEFInd appearance configuration')
        selected = source.name
        source = source.parent
    shutil.copytree(source, destination, ignore=ignore, symlinks=True, dirs_exist_ok=True)
    destination = Path(destination)
    if selected:
        if any(path.name.lower() == 'theme.conf' for path in destination.iterdir()):
            raise ValueError('Named theme config conflicts with an existing theme.conf')
        shutil.copyfile(destination/selected, destination/'theme.conf')
    marker = 'theme.txt' if loader == 'grub' else 'theme.conf'
    configs = [path for path in destination.iterdir() if path.name.lower() == marker]
    if len(configs) != 1 or configs[0].is_symlink() or not configs[0].is_file():
        raise ValueError(f'Boot theme must contain one regular {marker} file')
    if configs[0].name != marker:
        configs[0].rename(destination/marker)
    from orbits.asset_validation import validate_theme_tree
    validate_theme_tree(destination, loader)


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
                net.download(download.url, archive, report, allow_local=item.source == f"local-{self.loader}")
                unpack = tmp / "u"
                unpack.mkdir()
                _safe_unpack(archive, unpack)
                fallback = re.sub(r"\.(zip|tar(\.\w+)?|tgz|txz)$", "", download.name, flags=re.I)
                themes = find_boot_themes(unpack, self.loader, re.sub(r"[^A-Za-z0-9._+-]", "-", fallback) or "theme")
                if not themes:
                    raise RuntimeError(missing_boot_theme_message(unpack, self.loader, download.name))
                staged = []
                for src, name in themes:
                    dest = Path(tempfile.mkdtemp(prefix=f"boot-{self.loader}-", dir=STAGING))
                    stage_paths.append(dest)
                    stage_boot_theme(src, dest, self.loader)
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
                from orbits.theme_cleanup import cache_identity
                return {"cache": cache_identity(item, download, self.items.get(item.key)), "key": item.key, "title": item.title, "names": list(names),
                        "preview": item.thumb_url, "page": item.page_url, "download": download.name,
                        "remote_changed": item.updated, "installed_at": time.time()}

            next_one()

        net.run_async(work, done)

    def forget(self, name):
        from orbits.theme_cleanup import cleanup
        removed=[]
        for key,entry in list(self.items.items()):
            if name in entry['names']:
                entry['names'].remove(name)
                if not entry['names']:
                    removed.append(entry)
                    del self.items[key]
        self._save()
        for entry in removed:cleanup(entry)
        self.emit('changed')

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
                def removed(ok2, message, removed_name=name):
                    if ok2:
                        try:self.forget(removed_name)
                        except (OSError,RuntimeError) as error:
                            next_one(False,str(error))
                            return
                    next_one(ok2,message)
                run_helper(["boot-theme-remove", self.loader, name],removed)

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
