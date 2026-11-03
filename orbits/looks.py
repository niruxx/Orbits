"""Looks: save, switch, export and import a whole desktop setup.

A Look captures wallpaper, lock screen, GTK/libadwaita/Shell/icon/cursor themes, fonts, accent color,
style, App Colors and (optionally) the login screen. Store-installed pieces are remembered by their
store key, so applying a Look on another machine downloads whatever is missing.
"""
import json
import re
import shutil
import time
import zipfile
from pathlib import Path

from gi.repository import Gio, GLib, GObject

from orbits.config import DATA_DIR

LOOKS_DIR = DATA_DIR / "looks"
HISTORY_DIR = DATA_DIR / "looks-history"
HISTORY_LIMIT = 10
FORMAT = 1
IFACE_KEYS = ["gtk-theme", "icon-theme", "cursor-theme", "cursor-size", "color-scheme", "accent-color",
              "font-name", "document-font-name", "monospace-font-name", "text-scaling-factor"]
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+ ()-]{0,100}$")
FONT_RE = re.compile(r"^[^\n;{}\"'\\]{1,120}$")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".jxl", ".svg", ".bmp", ".tif", ".tiff", ".heic"}


def _iface():
    return Gio.Settings.new("org.gnome.desktop.interface")


def _iface_keys():
    schema = _iface().get_property("settings-schema")
    return [k for k in IFACE_KEYS if schema.has_key(k)]


def _uri_path(uri):
    if not uri:
        return None
    try:
        return Gio.File.new_for_uri(uri).get_path()
    except GLib.Error:
        return None


# ---------------------------------------------------------------- capture
def capture(name, include_login=False):
    from orbits import colors, themes
    from orbits.fonts import get_font_library

    iface = _iface()
    interface = {}
    for k in _iface_keys():
        v = iface.get_value(k).unpack()
        interface[k] = v

    bg = Gio.Settings.new("org.gnome.desktop.background")
    lock = Gio.Settings.new("org.gnome.desktop.screensaver")
    light, dark = _uri_path(bg.get_string("picture-uri")), _uri_path(bg.get_string("picture-uri-dark"))
    wallpaper = {"light": light, "dark": dark if dark != light else None,
                 "options": bg.get_string("picture-options")}

    # remember where store-installed themes and fonts came from
    assets = {"themes": {}, "fonts": {}}
    used = {"gtk": interface.get("gtk-theme"), "icons": interface.get("icon-theme"),
            "cursors": interface.get("cursor-theme"), "shell": themes.current("shell")}
    lib_theme, _broken = themes.libadwaita_state()
    for kind_id, theme_name in used.items():
        mgr = themes.get_manager(kind_id)
        for key, e in mgr.items.items():
            if theme_name in e.get("names", []):
                assets["themes"][f"{kind_id}:{theme_name}"] = {"key": key, "download": e.get("download")}
    fonts = get_font_library()
    for role_key in ("font-name", "document-font-name", "monospace-font-name"):
        family = re.sub(r"\s+\d+(\.\d+)?$", "", interface.get(role_key, ""))
        for e in fonts.items.values():
            if family in e.get("families", []) or family == e.get("family"):
                assets["fonts"][family] = {"key": e["key"]}

    cstate = colors.load_state() if colors.is_applied() else None
    look = {
        "format": FORMAT, "name": name, "created": time.time(),
        "interface": interface, "wallpaper": wallpaper,
        "lockscreen": _uri_path(lock.get_string("picture-uri")),
        "shell_theme": used["shell"],
        "libadwaita_theme": lib_theme if not cstate else None,
        "app_colors": {"preset": cstate.get("preset"), "palette": cstate.get("palette")} if cstate else None,
        "assets": assets,
    }
    if include_login:
        from orbits.gdm.model import GdmModel

        look["login"] = {k: v for k, v in GdmModel().values.items() if isinstance(v, (str, int, float, bool))}
    return look


# ---------------------------------------------------------------- storage
class LookStore(GObject.Object):
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__()
        LOOKS_DIR.mkdir(parents=True, exist_ok=True)
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    def _list(self, base):
        out = []
        for d in base.iterdir():
            try:
                look = json.loads((d / "look.json").read_text())
            except (OSError, ValueError):
                continue
            look["_dir"] = str(d)
            out.append(look)
        return sorted(out, key=lambda lk: lk.get("created", 0), reverse=True)

    def looks(self):
        return self._list(LOOKS_DIR)

    def history(self):
        return self._list(HISTORY_DIR)

    def _write(self, base, look, slug=None):
        slug = slug or f"{re.sub(r'[^a-z0-9]+', '-', look['name'].lower()).strip('-') or 'look'}-{int(time.time())}"
        d = base / slug
        d.mkdir(parents=True, exist_ok=True)
        look = {k: v for k, v in look.items() if not k.startswith("_")}
        # keep a copy of the wallpapers with the Look so it survives uninstalling them from the Library
        for kind in ("light", "dark"):
            p = look["wallpaper"].get(kind)
            if p and Path(p).is_file() and not str(p).startswith(str(d)):
                dest = d / f"wallpaper-{kind}{Path(p).suffix.lower()}"
                shutil.copy2(p, dest)
                look["wallpaper"][kind] = str(dest)
        (d / "look.json").write_text(json.dumps(look, indent=2))
        self.emit("changed")
        return d

    def save(self, look, existing_dir=None):
        return self._write(LOOKS_DIR, look, Path(existing_dir).name if existing_dir else None)

    def snapshot(self, reason):
        look = capture(reason)
        self._write(HISTORY_DIR, look)
        for old in self.history()[HISTORY_LIMIT:]:
            shutil.rmtree(old["_dir"], ignore_errors=True)
        self.emit("changed")

    def delete(self, look):
        d = Path(look["_dir"])
        if d.parent in (LOOKS_DIR, HISTORY_DIR):
            shutil.rmtree(d, ignore_errors=True)
        self.emit("changed")

    def rename(self, look, name):
        look["name"] = name
        self._write(Path(look["_dir"]).parent, look, Path(look["_dir"]).name)

    # ---- export / import ---------------------------------------------
    def export(self, look, dest: Path):
        data = {k: v for k, v in look.items() if not k.startswith("_")}
        data = json.loads(json.dumps(data))
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for kind in ("light", "dark"):
                p = data["wallpaper"].get(kind)
                if p and Path(p).is_file():
                    arc = f"wallpaper-{kind}{Path(p).suffix.lower()}"
                    z.write(p, arc)
                    data["wallpaper"][kind] = arc
                else:
                    data["wallpaper"][kind] = None
            lock = data.get("lockscreen")
            if lock and Path(lock).is_file():
                arc = f"lockscreen{Path(lock).suffix.lower()}"
                z.write(lock, arc)
                data["lockscreen"] = arc
            else:
                data["lockscreen"] = None
            login = data.get("login")
            if login and login.get("bg_image") and Path(login["bg_image"]).is_file():
                arc = f"login-background{Path(login['bg_image']).suffix.lower()}"
                z.write(login["bg_image"], arc)
                login["bg_image"] = arc
            elif login:
                login["bg_image"] = ""
            if login:
                login.pop("logo", None)  # system path; not portable
            z.writestr("look.json", json.dumps(data, indent=2))

    def import_file(self, src: Path):
        with zipfile.ZipFile(src) as z:
            raw = json.loads(z.read("look.json"))
            look = sanitize(raw)
            d = LOOKS_DIR / f"imported-{int(time.time())}"
            d.mkdir(parents=True)
            names = set(z.namelist())

            def take(arc):
                if not arc or arc not in names or "/" in arc or Path(arc).suffix.lower() not in IMAGE_EXTS:
                    return None
                out = d / arc
                with z.open(arc) as fsrc, open(out, "wb") as fdst:
                    shutil.copyfileobj(fsrc, fdst, 1 << 20)
                return str(out)

            for kind in ("light", "dark"):
                look["wallpaper"][kind] = take(raw.get("wallpaper", {}).get(kind))
            look["lockscreen"] = take(raw.get("lockscreen"))
            if look.get("login") is not None:
                look["login"]["bg_image"] = take((raw.get("login") or {}).get("bg_image")) or ""
        look["created"] = time.time()
        (d / "look.json").write_text(json.dumps(look, indent=2))
        self.emit("changed")
        look["_dir"] = str(d)
        return look


def sanitize(raw):
    """Validate an imported Look; anything unexpected is dropped rather than trusted."""
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise ValueError("This isn't an Orbits Look file (or it's from a newer version)")
    look = {"format": FORMAT, "name": str(raw.get("name") or "Imported Look")[:80], "interface": {},
            "wallpaper": {"options": "zoom"}, "assets": {"themes": {}, "fonts": {}}}
    iface = raw.get("interface") or {}
    for k in IFACE_KEYS:
        v = iface.get(k)
        if k in ("cursor-size",) and isinstance(v, int) and 8 <= v <= 256:
            look["interface"][k] = v
        elif k == "text-scaling-factor" and isinstance(v, (int, float)) and 0.5 <= v <= 3:
            look["interface"][k] = float(v)
        elif k in ("gtk-theme", "icon-theme", "cursor-theme") and isinstance(v, str) and NAME_RE.match(v):
            look["interface"][k] = v
        elif k.endswith("font-name") and isinstance(v, str) and FONT_RE.match(v):
            look["interface"][k] = v
        elif k in ("color-scheme", "accent-color") and isinstance(v, str) and re.match(r"^[a-z-]{1,20}$", v):
            look["interface"][k] = v
    opts = (raw.get("wallpaper") or {}).get("options")
    if opts in ("none", "wallpaper", "centered", "scaled", "stretched", "zoom", "spanned"):
        look["wallpaper"]["options"] = opts
    shell = raw.get("shell_theme")
    look["shell_theme"] = shell if isinstance(shell, str) and (shell == "" or NAME_RE.match(shell)) else ""
    lt = raw.get("libadwaita_theme")
    look["libadwaita_theme"] = lt if isinstance(lt, str) and NAME_RE.match(lt) else None
    ac = raw.get("app_colors")
    look["app_colors"] = None
    if isinstance(ac, dict) and isinstance(ac.get("palette"), dict):
        from orbits.colors import ROLES

        pal = {}
        for variant in ("light", "dark"):
            v = ac["palette"].get(variant)
            if isinstance(v, dict):
                pal[variant] = {r: c for r, c in v.items() if r in ROLES and isinstance(c, str) and HEX_RE.match(c)}
            else:
                pal[variant] = None
        look["app_colors"] = {"preset": str(ac.get("preset") or "Imported")[:40], "palette": pal}
    for section, pattern in (("themes", r"^(gtk|shell|icons|cursors):[A-Za-z0-9][A-Za-z0-9._+ ()-]{0,100}$"),
                             ("fonts", r"^[^\n]{1,120}$")):
        for k, v in ((raw.get("assets") or {}).get(section) or {}).items():
            if re.match(pattern, k) and isinstance(v, dict) and isinstance(v.get("key"), str) \
                    and re.match(r"^[a-z-]+:[A-Za-z0-9._-]{1,80}$", v["key"]):
                look["assets"][section][k] = {"key": v["key"], "download": str(v.get("download") or "")[:200]}
    login = raw.get("login")
    if isinstance(login, dict):
        from orbits.gdm.model import THEME_DEFAULTS, GdmModel

        allowed = set(THEME_DEFAULTS) | {dk.name for dk in GdmModel().keys}
        allowed -= {"logo", "autologin_enable", "autologin_user"}  # never import who logs in automatically
        clean = {}
        for k, v in login.items():
            if k not in allowed or not isinstance(v, (str, int, float, bool)):
                continue
            if k in ("bg_color", "bg_color2", "text_color") and v and not HEX_RE.match(str(v)):
                continue
            if isinstance(v, str) and (len(v) > 200 or any(c in v for c in "\n;{}")):
                continue
            clean[k] = v
        look["login"] = clean
    return look


def summary(look):
    """Short human description: 'WhiteSur-Dark · Papirus · Inter · Nord colors'."""
    i = look.get("interface", {})
    parts = [i.get("gtk-theme"), i.get("icon-theme"),
             re.sub(r"\s+\d+(\.\d+)?$", "", i.get("font-name", "")) or None]
    if look.get("app_colors"):
        parts.append(f"{look['app_colors'].get('preset')} colors")
    if look.get("login"):
        parts.append("login screen")
    return " · ".join(p for p in parts if p)


# ---------------------------------------------------------------- apply
class LookApplier:
    """Applies a Look step by step on the main loop, downloading missing store items first."""

    def __init__(self, look, window, include_login=False, done=None):
        self.look, self.window, self.include_login, self.done = look, window, include_login, done
        self.notes = []

    def start(self):
        get_store().snapshot(f"Before “{self.look.get('name', 'Look')}”")
        self._install_missing(list(self._missing()))

    def _missing(self):
        from orbits import themes
        from orbits.fonts import get_font_library
        from orbits.ui.font_actions import system_families

        installed = {k: {t.name for t in themes.installed(k)} for k in ("gtk", "shell", "icons", "cursors")}
        for spec, ref in self.look.get("assets", {}).get("themes", {}).items():
            kind, name = spec.split(":", 1)
            if name not in installed.get(kind, set()):
                yield ("theme", kind, name, ref)
        families = set(system_families())
        fonts = get_font_library()
        for family, ref in self.look.get("assets", {}).get("fonts", {}).items():
            if family not in families and ref["key"] not in fonts.items:
                yield ("font", None, family, ref)

    def _install_missing(self, queue):
        from orbits import net

        if not queue:
            self._apply_settings()
            return
        what, kind, name, ref = queue.pop(0)
        source_id, item_id = ref["key"].split(":", 1)

        def fetched(item, err):
            if err or item is None:
                self.notes.append(f"couldn't download {name}")
                self._install_missing(queue)
                return
            if what == "theme":
                from orbits import themes

                mgr = themes.get_manager(kind)
                dl = next((d for d in item.downloads if d.name == ref.get("download")), None)
                mgr.install(item, dl, lambda e, er: (er and self.notes.append(f"couldn't install {name}"),
                                                     self._install_missing(queue)))
            else:
                from orbits.fonts import get_font_library

                get_font_library().install(item, lambda e, er: (er and self.notes.append(f"couldn't install {name}"),
                                                                self._install_missing(queue)))

        def fetch():
            if what == "theme":
                from orbits import themes

                return themes.ThemeStoreSource(themes.KINDS[kind]).get_item(item_id)
            from orbits.fonts import FONT_SOURCES_BY_ID

            src = FONT_SOURCES_BY_ID.get(source_id)
            return next((i for i in src.catalog() if i.id == item_id), None) if src else None

        self.window.toast(f"Downloading {name}…", timeout=2)
        net.run_async(fetch, fetched)

    def _apply_settings(self):
        from orbits import colors, themes
        from orbits.ui.font_actions import system_families

        look = self.look
        iface = _iface()
        families = set(system_families())
        keys = set(_iface_keys())
        for k, v in look.get("interface", {}).items():
            if k not in keys:
                continue
            if k.endswith("font-name"):
                family = re.sub(r"\s+\d+(\.\d+)?$", "", v)
                if family not in families:
                    self.notes.append(f"font “{family}” isn't installed")
                    continue
            if k in ("gtk-theme", "icon-theme", "cursor-theme"):
                kind = {"gtk-theme": "gtk", "icon-theme": "icons", "cursor-theme": "cursors"}[k]
                if v not in {t.name for t in themes.installed(kind)} and v not in ("Adwaita", "HighContrast"):
                    self.notes.append(f"{kind} theme “{v}” isn't installed")
                    continue
            try:
                iface.set_value(k, GLib.Variant(iface.get_value(k).get_type_string(), v))
            except (TypeError, GLib.Error):
                self.notes.append(f"couldn't set {k}")

        shell = look.get("shell_theme")
        if shell is not None:
            try:
                if shell == "" or shell in {t.name for t in themes.installed("shell")}:
                    themes.apply("shell", shell)
                else:
                    self.notes.append(f"shell theme “{shell}” isn't installed")
            except RuntimeError as e:
                self.notes.append(str(e))

        if look.get("app_colors"):
            state = colors.load_state()
            state.update(preset=look["app_colors"].get("preset"), palette=look["app_colors"]["palette"])
            colors.apply(state)
        else:
            if colors.is_applied():
                colors.remove()
            lt = look.get("libadwaita_theme")
            match = next((t for t in themes.installed("gtk") if t.name == lt), None) if lt else None
            if match and (match.path / "gtk-4.0" / "gtk.css").exists():
                themes.apply_libadwaita(match.path)
            elif themes.libadwaita_state()[0]:
                themes.remove_libadwaita()

        bg = Gio.Settings.new("org.gnome.desktop.background")
        wp = look.get("wallpaper", {})
        light = wp.get("light")
        if light and Path(light).is_file():
            bg.set_string("picture-options", wp.get("options") or "zoom")
            bg.set_string("picture-uri", GLib.filename_to_uri(light))
            dark = wp.get("dark")
            bg.set_string("picture-uri-dark", GLib.filename_to_uri(dark if dark and Path(dark).is_file() else light))
        lock = look.get("lockscreen")
        if lock and Path(lock).is_file():
            Gio.Settings.new("org.gnome.desktop.screensaver").set_string("picture-uri", GLib.filename_to_uri(lock))
        Gio.Settings.sync()

        if self.include_login and look.get("login"):
            gdm = self.window.pages.get("gdm")
            if gdm:
                gdm.load_values(look["login"])
                self.notes.append("login screen loaded — press Apply on the Login Screen page")
        if self.done:
            self.done(self.notes)


_store = None


def get_store() -> LookStore:
    global _store
    if _store is None:
        _store = LookStore()
    return _store
