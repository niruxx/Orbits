"""Looks: save, switch, export and import a whole desktop setup.

A Look captures wallpaper, lock screen, GTK/libadwaita/Shell/icon/cursor themes, fonts, accent color,
style, App Colors and (optionally) the login screen. Store-installed pieces are remembered by their
store key, so applying a Look on another machine downloads whatever is missing.
"""
from orbits.undo import reversible, EntryState

import copy
import json
import uuid
import tempfile
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
COMPONENTS = {"wallpaper":"Wallpaper", "lockscreen":"Lock Screen", "themes":"GTK, Shell, Icons & Cursors",
              "fonts":"Fonts & Text Scaling", "colors":"App Colors, GTK4 Styling & Accent", "login":"Login Screen Draft"}
DESKTOP_COMPONENTS = set(COMPONENTS) - {"login"}


def interface_component(key):
    if key.endswith("font-name") or key == "text-scaling-factor":
        return "fonts"
    if key in ("accent-color", "color-scheme"):
        return "colors"
    return "themes"


def font_family(description):
    try:
        from gi.repository import Pango
        family=Pango.FontDescription.from_string(description).get_family()
        if family:return family
    except (ImportError,AttributeError):pass
    return re.sub(r"\s+\d+(?:\.\d+)?(?:\s+@.*)?$", "", description)
IFACE_KEYS = ["gtk-theme", "icon-theme", "cursor-theme", "cursor-size", "color-scheme", "accent-color",
              "font-name", "document-font-name", "monospace-font-name", "text-scaling-factor"]
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+ ()-]{0,100}$")
FONT_RE = re.compile(r"^[^\n;{}\"'\\]{1,120}$")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".jxl", ".svg", ".bmp", ".tif", ".tiff", ".heic"}


def _iface():
    from orbits.desktop import interface_settings
    return interface_settings()


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

    from orbits.desktop import background_settings
    bg = background_settings()
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
        family = font_family(interface.get(role_key, ""))
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

        look["login"] = {k: v for k, v in GdmModel().values.items()
                         if isinstance(v, (str, int, float, bool)) and k not in ("auto_reapply", "applied_theme")}
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
        slug = slug or f"{re.sub(r'[^a-z0-9]+', '-', look['name'].lower()).strip('-') or 'look'}-{uuid.uuid4().hex[:10]}"
        d = base / slug
        d.mkdir(parents=True, exist_ok=True)
        look = copy.deepcopy({k: v for k, v in look.items() if not k.startswith("_")})
        from orbits.look_assets import copy_background
        for kind in ("light", "dark"):
            source=look["wallpaper"].get(kind)
            if source and Path(source).is_file():
                look["wallpaper"][kind]=copy_background(source,d,f"wallpaper-{kind}")
        for field,stem in (("screenshot","screenshot"),("lockscreen","lockscreen")):
            source=look.get(field)
            if source and Path(source).is_file():
                if field=="lockscreen":look[field]=copy_background(source,d,stem)
                else:
                    target=d/(stem+Path(source).suffix.lower())
                    if Path(source).resolve()!=target.resolve():shutil.copy2(source,target)
                    look[field]=str(target)
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
            from orbits.look_assets import archive_background
            for kind in ("light", "dark"):
                source=data["wallpaper"].get(kind)
                data["wallpaper"][kind]=archive_background(z,source,f"wallpaper-{kind}") if source and Path(source).is_file() else None
            source=data.get("lockscreen")
            data["lockscreen"]=archive_background(z,source,"lockscreen") if source and Path(source).is_file() else None
            screenshot=data.get("screenshot")
            if screenshot and Path(screenshot).is_file():
                arc="screenshot"+Path(screenshot).suffix.lower()
                z.write(screenshot,arc);data["screenshot"]=arc
            else:data.pop("screenshot",None)
            login = data.get("login")
            if login:
                for key in ("bg_image", "bg_image_light", "bg_image_dark"):
                    source = login.get(key)
                    if source and Path(source).is_file():
                        arc = f"login-{key}{Path(source).suffix.lower()}"
                        z.write(source, arc)
                        login[key] = arc
                    else:
                        login[key] = ""
                for key in ("logo", "auto_reapply", "applied_theme", "bg_monitors"):
                    login.pop(key, None)  # System policy and monitor layouts stay on this machine.
            z.writestr("look.json", json.dumps(data, indent=2))

    def import_file(self, src: Path):
        from orbits.look_assets import rewrite_slideshow
        d=LOOKS_DIR/("imported-"+uuid.uuid4().hex)
        d.mkdir(parents=True)
        try:
            with zipfile.ZipFile(src) as z:
                infos=z.infolist()
                if len(infos)>5000 or sum(info.file_size for info in infos)>512<<20:
                    raise ValueError("Look archive is too large")
                if z.getinfo("look.json").file_size>2<<20:raise ValueError("Look metadata is too large")
                raw=json.loads(z.read("look.json"));look=sanitize(raw)
                names=set(z.namelist());taken={}
                def take(arc,allow_xml=False):
                    if not isinstance(arc,str) or not arc or arc not in names or "/" in arc or "\\" in arc:
                        return None
                    suffix=Path(arc).suffix.lower()
                    if suffix not in IMAGE_EXTS and not (allow_xml and suffix==".xml"):return None
                    if arc in taken:return taken[arc]
                    out=d/arc
                    if suffix==".xml":
                        def image(value):
                            result=take(value)
                            if not result:raise ValueError("Slideshow frame is missing from the Look archive")
                            return result
                        out.write_text(rewrite_slideshow(z.read(arc).decode("utf-8"),image))
                    else:
                        with z.open(arc) as source,out.open("wb") as output:shutil.copyfileobj(source,output)
                    taken[arc]=str(out)
                    return str(out)
                for kind in ("light","dark"):
                    look["wallpaper"][kind]=take((raw.get("wallpaper") or {}).get(kind),True)
                look["lockscreen"]=take(raw.get("lockscreen"),True)
                screenshot=take(raw.get("screenshot"))
                if screenshot:look["screenshot"]=screenshot
                if look.get("login") is not None:
                    for key in ("bg_image", "bg_image_light", "bg_image_dark"):
                        look["login"][key]=take((raw.get("login") or {}).get(key)) or ""
            look["created"]=time.time()
            (d/"look.json").write_text(json.dumps(look,indent=2))
        except Exception:
            shutil.rmtree(d,ignore_errors=True)
            raise
        self.emit("changed");look["_dir"]=str(d)
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
        allowed -= {"logo", "autologin_enable", "autologin_user", "auto_reapply", "applied_theme", "bg_monitors"}  # never import who logs in automatically
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


def preview_path(look):
    path=(look.get("wallpaper") or {}).get("light")
    if not path or not Path(path).is_file():return None
    if Path(path).suffix.lower()!=".xml":return path
    import xml.etree.ElementTree as ET
    try:
        tree=ET.parse(path)
        for node in tree.iter():
            if node.tag in ("file","size") and not len(node) and node.text:
                candidate=Path(node.text.strip())
                if not candidate.is_absolute():candidate=Path(path).parent/candidate
                if candidate.is_file():return str(candidate)
    except (OSError,ET.ParseError):pass
    return None


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

    def __init__(self, look, window, include_login=False, done=None, components=None):
        self.look, self.window, self.include_login, self.done = look, window, include_login, done
        self.notes = []
        self.components=set(components) if components is not None else DESKTOP_COMPONENTS | ({"login"} if include_login else set())
        if not self.components <= set(COMPONENTS):raise ValueError("Unknown Look component")

    def start(self):
        if not self.components:
            if self.done:self.done(["No components selected"])
            return
        get_store().snapshot(f"Before “{self.look.get('name', 'Look')}”")
        self._install_missing(list(self._missing()))

    def _missing(self):
        from orbits import themes
        from orbits.fonts import get_font_library
        from orbits.fonts import system_families

        installed = {k: {t.name for t in themes.installed(k)} for k in ("gtk", "shell", "icons", "cursors")}
        for spec, ref in self.look.get("assets", {}).get("themes", {}).items():
            kind, name = spec.split(":", 1)
            selected = "themes" in self.components or ("colors" in self.components and kind == "gtk" and name == self.look.get("libadwaita_theme"))
            if selected and name not in installed.get(kind, set()):
                yield ("theme", kind, name, ref)
        families = set(system_families()) if "fonts" in self.components else set()
        fonts = get_font_library()
        for family, ref in (self.look.get("assets", {}).get("fonts", {}) if "fonts" in self.components else {}).items():
            entry=fonts.items.get(ref["key"])
            if entry and not entry.get("enabled",True):fonts.set_enabled(ref["key"],True)
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
        try:
            self._apply_selected_settings()
        except Exception as error:
            self.notes.append(str(error))
            if self.done:self.done(self.notes)

    @reversible("Look applied")
    def _apply_selected_settings(self):
        from orbits import colors, themes
        from orbits.fonts import system_families

        look = self.look
        iface = _iface()
        families = set(system_families()) if "fonts" in self.components else set()
        keys = set(_iface_keys())
        for k, v in look.get("interface", {}).items():
            if k not in keys or interface_component(k) not in self.components:
                continue
            if k.endswith("font-name"):
                family = font_family(v)
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
        if "themes" in self.components and shell is not None:
            try:
                if shell == "" or shell in {t.name for t in themes.installed("shell")}:
                    themes.apply("shell", shell)
                else:
                    self.notes.append(f"shell theme “{shell}” isn't installed")
            except RuntimeError as e:
                self.notes.append(str(e))

        if "colors" in self.components:
            if look.get("app_colors"):
                state = colors.load_state()
                palette={tone:({**colors.PRESETS["Adwaita"][tone],**values} if values else None)
                         for tone,values in look["app_colors"]["palette"].items()}
                state.update(preset=look["app_colors"].get("preset"), palette=palette, gnome_accent=False)
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

        from orbits.desktop import background_settings, detect, xfce_wallpaper
        wp = look.get("wallpaper", {})
        light = wp.get("light")
        if "wallpaper" in self.components and light and Path(light).is_file():
            if detect() == "xfce":
                xfce_wallpaper(light,wp.get("options") or "zoom")
            else:
                bg=background_settings()
                bg.set_string("picture-options",wp.get("options") or "zoom")
                bg.set_string("picture-uri",GLib.filename_to_uri(light))
                dark=wp.get("dark")
                bg.set_string("picture-uri-dark",GLib.filename_to_uri(dark if dark and Path(dark).is_file() else light))
        lock = look.get("lockscreen")
        if "lockscreen" in self.components and lock and Path(lock).is_file():
            if detect() in ('gnome','budgie'):
                Gio.Settings.new("org.gnome.desktop.screensaver").set_string("picture-uri",GLib.filename_to_uri(lock))
            else:
                self.notes.append("Separate lock-screen backgrounds are not supported on this desktop")
        Gio.Settings.sync()

        if "login" in self.components and self.include_login and look.get("login"):
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
