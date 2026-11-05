"""GTK, GNOME Shell, icon, cursor and Plymouth themes: discovery (gnome-look.org), install, apply, remove."""
from orbits.undo import reversible, EntryState

import json
import os
import re
import shutil
import struct
import tarfile
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from gi.repository import Gio, GLib, GObject

from orbits import net
from orbits.config import DATA_DIR
from orbits.sources.kde import OcsSource

HOME = Path.home()
DATA_HOME = Path(GLib.get_user_data_dir())
USER_THEMES = DATA_HOME / "themes"
USER_ICONS = DATA_HOME / "icons"
USER_SOUNDS = DATA_HOME / "sounds"
USER_TERMINALS = DATA_DIR / "terminal-themes"
GTK4_CONFIG = Path(GLib.get_user_config_dir()) / "gtk-4.0"
LIBADWAITA_MARKER = GTK4_CONFIG / ".orbits-libadwaita"
LIBADWAITA_FILES = ("gtk.css", "gtk-dark.css", "assets", "thumbnail.png")
PLYMOUTH_DIR = Path("/usr/share/plymouth/themes")
STAGING = DATA_DIR / "staging"
INDEX_FILE = DATA_DIR / "themes.json"
MARKER = ".orbits.json"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+ ()-]{0,100}$")
MAX_UNPACKED = 2 << 30
USER_THEME_UUID = "user-theme@gnome-shell-extensions.gcampax.github.com"
USER_THEME_SCHEMA = "org.gnome.shell.extensions.user-theme"
IFACE = "org.gnome.desktop.interface"


# ---------------------------------------------------------------- theme kinds
@dataclass(frozen=True)
class Kind:
    id: str
    title: str          # "GTK Themes"
    noun: str           # "GTK theme"
    icon: str
    category: str       # gnome-look.org OCS category id
    tagline: str
    user_dir: Path | None
    search_dirs: tuple
    system_base: str    # helper destination key: themes | icons | plymouth
    default: str        # value meaning "GNOME default"

    def matches(self, d: Path) -> bool:
        return _MATCHERS[self.id](d)


def _is_gtk(d):
    return (d / "gtk-3.0").is_dir() or (d / "gtk-4.0").is_dir()


def _is_gtk2(d):
    return (d / "gtk-2.0" / "gtkrc").is_file()


def _is_gtk1(d):
    return (d / "gtk" / "gtkrc").is_file()


def _is_sounds(d):
    kf = _index_theme(d)
    return bool(kf and kf.has_group("Sound Theme"))


def _is_terminal(d):
    from orbits.terminal_schemes import find_schemes
    return (d / "terminal.json").is_file() and bool(find_schemes(d))


def _is_shell(d):
    return (d / "gnome-shell" / "gnome-shell.css").is_file()


def _index_theme(d):
    kf = GLib.KeyFile()
    try:
        kf.load_from_file(str(d / "index.theme"), GLib.KeyFileFlags.NONE)
        return kf
    except GLib.Error:
        return None


def _is_icons(d):
    kf = _index_theme(d)
    if not kf or d.name in ("hicolor", "locolor", "default"):
        return False
    try:
        return bool(kf.get_string("Icon Theme", "Directories").strip())
    except GLib.Error:
        return False


def _is_cursors(d):
    return (d / "cursors").is_dir()


def _is_plymouth(d):
    return any(d.glob("*.plymouth"))


_MATCHERS = {"gtk": _is_gtk, "shell": _is_shell, "icons": _is_icons, "cursors": _is_cursors,
             "plymouth": _is_plymouth, "gtk2": _is_gtk2, "gtk1": _is_gtk1, "sounds": _is_sounds, "terminal": _is_terminal}

THEME_DIRS = (USER_THEMES, HOME / ".themes", Path("/usr/local/share/themes"), Path("/usr/share/themes"))
ICON_DIRS = (USER_ICONS, HOME / ".icons", Path("/usr/local/share/icons"), Path("/usr/share/icons"))

SOUND_DIRS = (USER_SOUNDS, Path("/usr/local/share/sounds"), Path("/usr/share/sounds"))

KINDS = {
    "gtk2": Kind("gtk2", "GTK2 Themes", "GTK2 theme", "applications-graphics-symbolic", "136",
                 "Themes for legacy GTK2 applications", USER_THEMES, THEME_DIRS, "themes", "Raleigh"),
    "gtk1": Kind("gtk1", "GTK1 Themes", "GTK1 theme", "applications-graphics-symbolic", "284",
                 "Themes for older GTK1 applications", USER_THEMES, THEME_DIRS, "themes", ""),
    "sounds": Kind("sounds", "Sound Themes", "sound theme", "audio-volume-high-symbolic", "316",
                   "Desktop event sounds", USER_SOUNDS, SOUND_DIRS, "sounds", "freedesktop"),
    "terminal": Kind("terminal", "Terminal Colors", "terminal scheme", "utilities-terminal-symbolic", "462",
                     "Terminal palettes imported as data", USER_TERMINALS,
                     (USER_TERMINALS, Path("/usr/share/orbits/terminal-themes")), "terminal", ""),
    "gtk": Kind("gtk", "GTK Themes", "GTK theme", "applications-graphics-symbolic", "135",
                "Restyle GTK3 and libadwaita apps", USER_THEMES, THEME_DIRS, "themes", "Adwaita"),
    "shell": Kind("shell", "Shell Themes", "Shell theme", "user-desktop-symbolic", "134",
                  "Top bar, overview and popups", USER_THEMES, THEME_DIRS, "themes", ""),
    "icons": Kind("icons", "Icon Themes", "icon theme", "view-app-grid-symbolic", "132",
                  "App, folder and symbolic icons", USER_ICONS, ICON_DIRS, "icons", "Adwaita"),
    "cursors": Kind("cursors", "Cursor Themes", "cursor theme", "input-mouse-symbolic", "107",
                    "Pointers for every occasion", USER_ICONS, ICON_DIRS, "icons", "Adwaita"),
    "plymouth": Kind("plymouth", "Boot Splash", "boot splash", "system-reboot-symbolic", "108",
                     "Plymouth animations shown while booting", None, (PLYMOUTH_DIR,), "plymouth", "bgrt"),
}


class ThemeStoreSource(OcsSource):
    """gnome-look.org listing for one theme kind."""

    api = "https://api.gnome-look.org/ocs/v1/content/data"
    store_name = "gnome-look.org"
    sorts = [("down", "Most Downloaded"), ("new", "Newest"), ("high", "Top Rated"), ("alpha", "A–Z")]
    categories = []

    def __init__(self, kind: Kind):
        self.kind = kind
        self.source_url = "https://www.gnome-look.org"
        if kind.id in ("gtk1", "gtk2", "sounds", "terminal"):
            self.api = "https://api.opendesktop.org/ocs/v1/content/data"
            self.source_url = "https://www.opendesktop.org"
        self.id = f"theme-{kind.id}"
        self.name = kind.title
        self.icon = kind.icon
        self.tagline = f"{kind.tagline} · gnome-look.org"
        self.default_category = kind.category


# ---------------------------------------------------------------- installed themes
@dataclass
class InstalledTheme:
    kind: str
    name: str
    path: Path
    scope: str          # user | system
    managed: bool       # installed by Orbits (and so safe to remove)
    display: str
    meta: dict

    @property
    def removable(self):
        return self.scope == "user" or self.managed


def _read_marker(d: Path):
    try:
        return json.loads((d / MARKER).read_text())
    except (OSError, ValueError):
        return None


def installed(kind_id) -> list[InstalledTheme]:
    kind = KINDS[kind_id]
    seen, out = set(), []
    for base in kind.search_dirs:
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            try:
                if not d.is_dir() or d.name in seen or not kind.matches(d):
                    continue
            except OSError:
                continue
            seen.add(d.name)
            marker = _read_marker(d)
            display = d.name
            if kind_id in ("icons", "cursors"):
                kf = _index_theme(d)
                if kf:
                    try:
                        display = kf.get_string("Icon Theme", "Name") or d.name
                    except GLib.Error:
                        pass
            elif kind_id == "plymouth":
                display = _plymouth_name(d) or d.name
            user = str(d).startswith(str(HOME))
            out.append(InstalledTheme(kind_id, d.name, d, "user" if user else "system", marker is not None,
                                      display, marker or {}))
    return out


def _plymouth_name(d):
    for f in d.glob("*.plymouth"):
        kf = GLib.KeyFile()
        try:
            kf.load_from_file(str(f), GLib.KeyFileFlags.NONE)
            return kf.get_string("Plymouth Theme", "Name")
        except GLib.Error:
            return None
    return None


# ---------------------------------------------------------------- current / apply
def _iface():
    return Gio.Settings.new(IFACE)


def _user_theme_settings():
    src = Gio.SettingsSchemaSource.get_default()
    schema = src.lookup(USER_THEME_SCHEMA, True) if src else None
    if schema is None:
        for base in (DATA_HOME / "gnome-shell/extensions", Path("/usr/share/gnome-shell/extensions")):
            d = base / USER_THEME_UUID / "schemas"
            if d.is_dir():
                try:
                    s2 = Gio.SettingsSchemaSource.new_from_directory(str(d), src, False)
                    schema = s2.lookup(USER_THEME_SCHEMA, False)
                except GLib.Error:
                    schema = None
                if schema:
                    break
    return Gio.Settings.new_full(schema, None, None) if schema else None


def plymouth_current():
    try:
        for line in Path("/etc/plymouth/plymouthd.conf").read_text().splitlines():
            if line.strip().startswith("Theme="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return KINDS["plymouth"].default


def current(kind_id):
    if kind_id in ("gtk1", "gtk2"):
        return legacy_current(kind_id)
    if kind_id == "sounds":
        return Gio.Settings.new("org.gnome.desktop.sound").get_string("theme-name")
    if kind_id == "terminal":
        return ""
    if kind_id == "gtk":
        return _iface().get_string("gtk-theme")
    if kind_id == "icons":
        return _iface().get_string("icon-theme")
    if kind_id == "cursors":
        return _iface().get_string("cursor-theme")
    if kind_id == "shell":
        s = _user_theme_settings()
        return s.get_string("name") if s else ""
    if kind_id == "plymouth":
        return plymouth_current()
    return ""


def shell_status():
    """(available, enabled, message) for the User Themes extension."""
    from orbits.desktop import detect
    if detect() != "gnome":
        return False, False, "Shell themes require a GNOME Shell session"
    if _user_theme_settings() is None:
        return False, False, "Install the “User Themes” GNOME extension (gnome-shell-extensions package)"
    try:
        shell = Gio.Settings.new("org.gnome.shell")
        enabled = USER_THEME_UUID in shell.get_strv("enabled-extensions")
        if shell.get_boolean("disable-user-extensions"):
            return True, False, "Extensions are turned off — enable them in the Extensions app"
    except GLib.Error:
        enabled = False
    return True, enabled, ""


def _enable_user_theme_extension():
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync("org.gnome.Shell.Extensions", "/org/gnome/Shell/Extensions", "org.gnome.Shell.Extensions",
                      "EnableExtension", GLib.Variant("(s)", (USER_THEME_UUID,)), GLib.VariantType("(b)"),
                      Gio.DBusCallFlags.NONE, 5000, None)
        return True
    except GLib.Error:
        shell = Gio.Settings.new("org.gnome.shell")
        exts = shell.get_strv("enabled-extensions")
        if USER_THEME_UUID not in exts:
            shell.set_strv("enabled-extensions", exts + [USER_THEME_UUID])
        return True


@reversible("Theme applied")
def apply(kind_id, name):
    """Apply a user-session theme. Plymouth goes through the privileged helper instead."""
    if kind_id in ("gtk1", "gtk2"):
        apply_legacy(kind_id, name)
    elif kind_id == "sounds":
        sound = Gio.Settings.new("org.gnome.desktop.sound")
        sound.set_string("theme-name", name)
        sound.set_boolean("event-sounds", True)
    elif kind_id == "gtk":
        _iface().set_string("gtk-theme", name)
    elif kind_id == "icons":
        _iface().set_string("icon-theme", name)
    elif kind_id == "cursors":
        _iface().set_string("cursor-theme", name)
    elif kind_id == "shell":
        from orbits.desktop import detect
        if detect() != "gnome":
            raise RuntimeError("Shell themes require a GNOME Shell session")
        s = _user_theme_settings()
        if s is None:
            raise RuntimeError(shell_status()[2])
        if name:
            _enable_user_theme_extension()
        s.set_string("name", name)
    else:
        raise ValueError(kind_id)
    Gio.Settings.sync()


# Legacy GTK rc files are independent of the current GTK3/GTK4 theme.
LEGACY_BEGIN = "# >>> Orbits legacy theme"
LEGACY_END = "# <<< Orbits legacy theme"


def legacy_current(kind_id):
    path = HOME / (".gtkrc-2.0" if kind_id == "gtk2" else ".gtkrc")
    try:
        text = path.read_text()
    except OSError:
        return KINDS[kind_id].default
    if kind_id == "gtk2":
        matches = re.findall(r'^\s*gtk-theme-name\s*=\s*"([^"\n]+)"', text, re.M)
    else:
        matches = re.findall(r'^# Orbits GTK1 name: (.*)$', text, re.M)
    return matches[-1] if matches else KINDS[kind_id].default


def apply_legacy(kind_id, name):
    if name and not NAME_RE.fullmatch(name):
        raise ValueError("Invalid theme name")
    path = HOME / (".gtkrc-2.0" if kind_id == "gtk2" else ".gtkrc")
    if path.is_symlink():
        raise RuntimeError(f"{path} is a symbolic link; edit it manually")
    original = path.read_text() if path.exists() else ""
    text = re.sub(r"(?ms)^" + re.escape(LEGACY_BEGIN) + r"\n.*?^" + re.escape(LEGACY_END) + r"\n?", "", original)
    # Applying the default undoes only our managed block.
    if name != KINDS[kind_id].default:
        theme = next((t for t in installed(kind_id) if t.name == name), None)
        if theme is None:
            raise RuntimeError("Theme is not installed")
        if kind_id == "gtk2":
            directive = f'gtk-theme-name = "{name}"'
        else:
            include = str(theme.path / "gtk" / "gtkrc").replace("\\", "\\\\").replace('"', '\\"')
            directive = f'# Orbits GTK1 name: {name}\ninclude "{include}"'
        text = text.rstrip("\n") + f"\n{LEGACY_BEGIN}\n{directive}\n{LEGACY_END}\n"
    if path.exists() and not path.with_name(path.name + ".orbits-backup").exists():
        shutil.copyfile(path, path.with_name(path.name + ".orbits-backup"))
    tmp = path.with_name(path.name + ".orbits-tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------- libadwaita (GTK4) theming
def libadwaita_state():
    """(theme_name or None, broken: bool) for files linked into ~/.config/gtk-4.0."""
    link = GTK4_CONFIG / "gtk.css"
    name = None
    if LIBADWAITA_MARKER.exists():
        name = LIBADWAITA_MARKER.read_text().strip() or None
    if link.is_symlink():
        target = Path(os.readlink(link))
        guess = target.parent.parent.name if target.parent.name == "gtk-4.0" else None
        return name or guess, not link.exists()
    if link.exists():
        return name or "custom gtk.css", False
    return None, False


@reversible("GTK4 theme applied")
def apply_libadwaita(theme_dir: Path):
    src = theme_dir / "gtk-4.0"
    if not (src / "gtk.css").exists():
        raise RuntimeError(f"{theme_dir.name} has no GTK4 / libadwaita stylesheet")
    GTK4_CONFIG.mkdir(parents=True, exist_ok=True)
    backup = GTK4_CONFIG / "orbits-backup"
    for name in LIBADWAITA_FILES:
        target = GTK4_CONFIG / name
        if target.is_symlink():
            target.unlink()
        elif target.exists():  # a real file the user made — keep it safe
            backup.mkdir(exist_ok=True)
            dest = backup / name
            if dest.exists():
                dest = backup / f"{name}.{int(time.time())}"
            shutil.move(str(target), dest)
        if (src / name).exists():
            target.symlink_to(src / name)
    LIBADWAITA_MARKER.write_text(theme_dir.name + "\n")


@reversible("GTK4 theme removed")
def remove_libadwaita():
    for name in LIBADWAITA_FILES:
        target = GTK4_CONFIG / name
        if target.is_symlink():
            target.unlink()
    backup = GTK4_CONFIG / "orbits-backup"
    if backup.is_dir():
        for f in backup.iterdir():
            dest = GTK4_CONFIG / f.name
            if f.name in LIBADWAITA_FILES and not dest.exists():
                shutil.move(str(f), dest)
        if not any(backup.iterdir()):
            backup.rmdir()
    LIBADWAITA_MARKER.unlink(missing_ok=True)


# ---------------------------------------------------------------- plymouth environment
def plymouth_status():
    """List of human-readable problems that would stop a boot splash from showing."""
    problems = []
    if not shutil.which("plymouth-set-default-theme"):
        return ["Plymouth isn't installed"]
    try:
        cmdline = Path("/proc/cmdline").read_text().split()
        if "splash" not in cmdline:
            problems.append("Your kernel command line is missing “splash”")
    except OSError:
        pass
    conf = Path("/etc/mkinitcpio.conf")
    if conf.exists() and shutil.which("mkinitcpio"):
        hooks = ""
        for path in [conf, *sorted(Path("/etc/mkinitcpio.conf.d").glob("*.conf"))]:
            try:
                for line in path.read_text().splitlines():
                    if line.strip().startswith("HOOKS="):
                        hooks = line
            except OSError:
                pass
        if "plymouth" not in hooks:
            problems.append("The “plymouth” hook is missing from mkinitcpio HOOKS")
    return problems


# ---------------------------------------------------------------- previews
def preview_image(theme: InstalledTheme):
    """A local image that represents the theme, if any."""
    d = theme.path
    candidates = ["gtk-4.0/thumbnail.png", "gtk-3.0/thumbnail.png", "thumbnail.png", "preview.png",
                  "screenshot.png", "gnome-shell/preview.png"]
    for c in candidates:
        if (d / c).is_file():
            return d / c
    if theme.kind == "plymouth":
        pngs = [p for p in d.glob("*.png") if p.is_file()]
        if pngs:
            return max(pngs, key=lambda p: p.stat().st_size)
    return None


def gtk_colors(theme_dir: Path):
    """A few defining colors parsed from the GTK3 stylesheet."""
    css = ""
    for f in ("gtk-3.0/gtk.css", "gtk-3.0/gtk-dark.css", "gtk-4.0/gtk.css"):
        try:
            css = (theme_dir / f).read_text(errors="ignore")[:400_000]
            break
        except OSError:
            continue
    colors = []
    for key in ("theme_bg_color", "window_bg_color", "theme_base_color", "theme_selected_bg_color",
                "accent_bg_color", "theme_fg_color", "headerbar_bg_color"):
        m = re.search(rf"@define-color\s+{key}\s+(#[0-9a-fA-F]{{3,8}}|rgba?\([^)]*\))", css)
        if m and m.group(1) not in colors:
            colors.append(m.group(1))
    return colors[:5]


def xcursor_image(path: Path, size=32):
    """Decode one image from an Xcursor file: returns (width, height, BGRA premultiplied bytes)."""
    data = path.read_bytes()
    if data[:4] != b"Xcur":
        return None
    _hdr, _ver, ntoc = struct.unpack_from("<III", data, 4)
    best = None
    for i in range(ntoc):
        typ, subtype, pos = struct.unpack_from("<III", data, 16 + i * 12)
        if typ == 0xFFFD0002 and (best is None or abs(subtype - size) < abs(best[0] - size)):
            best = (subtype, pos)
    if not best:
        return None
    pos = best[1]
    _h, _t, _s, _v, w, h, _xh, _yh, _d = struct.unpack_from("<IIIIIIIII", data, pos)
    px = data[pos + 36: pos + 36 + w * h * 4]
    return (w, h, px) if len(px) == w * h * 4 else None


def cursor_file(theme_dir: Path, names):
    for n in names:
        f = theme_dir / "cursors" / n
        if f.exists():
            try:
                return f.resolve()
            except OSError:
                continue
    return None


# ---------------------------------------------------------------- install from archives
def _safe_unpack(archive: Path, dest: Path):
    total = 0
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            root = dest.resolve()
            for info in z.infolist():
                total += info.file_size
                if total > MAX_UNPACKED:
                    raise RuntimeError("Archive is too large")
                target = (dest / info.filename).resolve()
                if not str(target).startswith(str(root) + os.sep):
                    continue
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
        return
    with tarfile.open(archive) as t:
        for m in t.getmembers():
            total += m.size
            if total > MAX_UNPACKED:
                raise RuntimeError("Archive is too large")
        t.extractall(dest, filter="data")


def find_themes(root: Path, kind: Kind, fallback_name="theme"):
    """(dir, target_name) pairs for every theme of `kind` inside an unpacked archive."""
    found = []
    if kind.id == "terminal":
        from orbits.terminal_schemes import find_schemes
        used = set()
        for path, scheme in find_schemes(root):
            name = re.sub(r"[^A-Za-z0-9._+ ()-]", "-", path.stem).strip(" .-")[:90] or "terminal"
            base, index = name, 2
            while name in used:
                name = f"{base}-{index}"
                index += 1
            used.add(name)
            found.append((path, name))
        return found
    candidates = [root] + sorted(p for p in root.rglob("*") if p.is_dir() and not p.is_symlink())
    for d in candidates:
        try:
            if not kind.matches(d):
                continue
        except OSError:
            continue
        if any(f[0] in d.parents for f in found):
            continue  # nested inside a theme we already took
        name = d.name if d != root else fallback_name
        if kind.id == "plymouth":
            name = _plymouth_target_name(d) or name
        if NAME_RE.match(name):
            found.append((d, name))
    return found


def _plymouth_target_name(d):
    for f in d.glob("*.plymouth"):
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        m = re.search(r"^\s*ImageDir\s*=\s*\S*?/plymouth/themes/([^/\s]+)", text, re.M)
        if m:  # the theme hard-codes where it must live, e.g. /usr/share/plymouth/themes/<name>/images
            return m.group(1)
        return f.stem
    return None


class ThemeManager(GObject.Object):
    """Installs gnome-look items for one kind. Mirrors the wallpaper Library interface for BrowsePage."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, kind: Kind):
        super().__init__()
        self.kind = kind
        self.busy: dict[str, float] = {}
        self._lock = threading.Lock()
        self.items = self._load_index()
        self._prune()

    # ---- index (key -> installed names) --------------------------------
    def _load_index(self):
        try:
            return json.loads(INDEX_FILE.read_text()).get(self.kind.id, {})
        except (OSError, ValueError):
            return {}

    def _save(self):
        try:
            data = json.loads(INDEX_FILE.read_text())
        except (OSError, ValueError):
            data = {}
        data[self.kind.id] = self.items
        tmp = INDEX_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, INDEX_FILE)

    def _prune(self):
        present = {t.name for t in installed(self.kind.id)}
        changed = False
        for key, e in list(self.items.items()):
            names = [n for n in e["names"] if n in present]
            if not names and not e.get("pending_system"):
                del self.items[key]
                changed = True
            elif names != e["names"]:
                e["names"] = names
                changed = True
        if changed:
            self._save()

    def refresh(self):
        self._prune()
        self.emit("changed")

    def is_installed(self, key):
        return key in self.items

    # ---- install ---------------------------------------------------------
    def install(self, item, download, callback, progress=None):
        """Download & unpack. User kinds land in ~/.local/share; Plymouth is staged for the root helper.

        callback(entry, err): entry has "names" and, for Plymouth, "staged" (dirs the helper must copy).
        """
        if download is None:
            archives = [d for d in item.downloads if re.search(r"\.(zip|tar(\.\w+)?|tgz|txz|tbz2?)$", d.name, re.I)]
            download = (archives or item.downloads or [None])[-1 if archives else 0]
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
            if entry and self.kind.id == "plymouth":
                self._install_staged(entry, callback, item.key)
                return
            self.busy.pop(item.key, None)
            if entry:
                self.items[item.key] = entry
                self._save()
            self.emit("changed")
            callback(entry, err)

        net.run_async(work, done)

    def _install_staged(self, entry, callback, key):
        """Plymouth themes live in /usr/share: copy the staged folders in with the root helper."""
        from orbits.privileged import run_helper

        staged = list(entry["staged"])

        def next_one(ok=True, msg=""):
            if not ok:
                self.busy.pop(key, None)
                for d, _n in entry["staged"]:
                    shutil.rmtree(d, ignore_errors=True)
                self.emit("changed")
                callback(None, RuntimeError(msg))
                return
            if not staged:
                self.busy.pop(key, None)
                self.record_system_install(entry)
                callback(entry, None)
                return
            d, name = staged.pop(0)
            run_helper(["theme-install", "plymouth", d, name], next_one)

        next_one()

    def _install_blocking(self, item, download, report):
        kind = self.kind
        with tempfile.TemporaryDirectory(prefix="orbits-theme-") as tmp:
            tmp = Path(tmp)
            archive = tmp / (re.sub(r"[^A-Za-z0-9._-]", "_", download.name) or "download")
            net.download(download.url, archive, report, checksum=getattr(download, "md5", ""))
            unpack = tmp / "unpacked"
            unpack.mkdir()
            try:
                if kind.id == "terminal" and not zipfile.is_zipfile(archive) and not tarfile.is_tarfile(archive):
                    shutil.copyfile(archive, unpack / archive.name)
                else:
                    _safe_unpack(archive, unpack)
            except (tarfile.TarError, zipfile.BadZipFile) as e:
                raise RuntimeError(f"Couldn't unpack {download.name}: {e}")
            fallback = re.sub(r"\.(zip|tar(\.\w+)?|tgz|txz|tbz2?)$", "", download.name, flags=re.I) or item.title
            themes = find_themes(unpack, kind, re.sub(r"[^A-Za-z0-9._+ ()-]", "-", fallback).strip("-") or "theme")
            if not themes:
                hint = ""
                if any(unpack.rglob("install.sh")):
                    hint = " It only ships an install script — open its page for instructions."
                raise RuntimeError(f"No {kind.noun} found in {download.name}.{hint}")

            marker = {"key": item.key, "title": item.title, "preview": item.preview_url or item.thumb_url,
                      "page": item.page_url, "installed_at": time.time()}
            if kind.id == "plymouth":
                staged = []
                STAGING.mkdir(parents=True, exist_ok=True)
                for src, name in themes:
                    dest = STAGING / f"plymouth-{name}"
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(src, dest, symlinks=True)
                    (dest / MARKER).write_text(json.dumps(marker))
                    staged.append((str(dest), name))
                return {"key": item.key, "title": item.title, "names": [n for _, n in staged], "staged": staged,
                        "preview": marker["preview"], "download": download.name, "remote_changed": item.updated,
                        "installed_at": time.time()}

            kind.user_dir.mkdir(parents=True, exist_ok=True)
            names = []
            for src, name in themes:
                dest = kind.user_dir / name
                if dest.exists():
                    if not _read_marker(dest):
                        raise RuntimeError(f"A theme named “{name}” is already installed (not by Orbits)")
                    shutil.rmtree(dest)
                if kind.id == "terminal":
                    from orbits.terminal_schemes import read_scheme
                    dest.mkdir()
                    (dest / "terminal.json").write_text(json.dumps(read_scheme(src), indent=2))
                else:
                    shutil.copytree(src, dest, symlinks=True)
                (dest / MARKER).write_text(json.dumps(marker))
                names.append(name)
        return {"key": item.key, "title": item.title, "names": names, "preview": marker["preview"],
                "download": download.name, "remote_changed": item.updated, "installed_at": time.time()}

    def record_system_install(self, entry):
        self.items[entry["key"]] = {k: v for k, v in entry.items() if k != "staged"}
        self._save()
        for d, _n in entry.get("staged", []):
            shutil.rmtree(d, ignore_errors=True)
        self.emit("changed")

    # ---- uninstall ---------------------------------------------------------
    @reversible("Theme uninstalled", extra=lambda self, name: EntryState(self,
        [key for key, entry in self.items.items() if name in entry.get("names", [])],
        [base/name for base in self.kind.search_dirs if str(base).startswith(str(HOME)) and (base/name).exists()]))
    def uninstall_user(self, name):
        """Remove a theme from the user's folders (any user theme, Orbits-installed or not)."""
        kind = self.kind
        for base in kind.search_dirs:
            d = base / name
            if d.is_dir() and str(d).startswith(str(HOME)) and kind.matches(d):
                shutil.rmtree(d)
                break
        else:
            raise RuntimeError(f"{name} isn't a user-installed {kind.noun}")
        if current(kind.id) == name and kind.id != "plymouth":
            apply(kind.id, kind.default)
        self.refresh()

    @reversible("Theme uninstalled", extra=lambda self, key: EntryState(self, [key],
        [self.kind.user_dir/name for name in self.items.get(key, {}).get("names", [])
         if self.kind.user_dir is not None and (self.kind.user_dir/name).exists()]))
    def uninstall(self, key):
        """Used by the store detail dialog: remove everything an item installed (user kinds)."""
        e = self.items.get(key)
        if not e:
            return
        for name in e["names"]:
            try:
                self.uninstall_user(name)
            except (RuntimeError, OSError):
                pass
        self.items.pop(key, None)
        self._save()
        self.emit("changed")


_managers = {}


def get_manager(kind_id) -> ThemeManager:
    if kind_id not in _managers:
        _managers[kind_id] = ThemeManager(KINDS[kind_id])
    return _managers[kind_id]
