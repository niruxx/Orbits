"""GDM settings: what can be customized, how it's read back from the system, and the spec for the helper."""
import json
import os
import pwd
import shutil
from dataclasses import dataclass
from pathlib import Path

from gi.repository import Gio, GLib

from orbits.config import CACHE_DIR, CONFIG_DIR, JsonStore

HELPER = Path(__file__).resolve().parent / "helper.py"
GDM_DCONF_DIR = Path("/etc/dconf/db/gdm.d")
OUR_DCONF = GDM_DCONF_DIR / "99-orbits"
GDM_CONF = next((Path(p) for p in ("/etc/gdm/custom.conf", "/etc/gdm3/custom.conf", "/etc/gdm3/daemon.conf")
                 if os.path.exists(p)), Path("/etc/gdm/custom.conf"))
EXPORT_DIR = CACHE_DIR / "gdm"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class DKey:
    name: str      # our setting name
    schema: str    # gsettings schema id
    key: str       # gsettings key

    @property
    def group(self):
        return self.schema.replace(".", "/")


DCONF_KEYS = [
    # appearance
    DKey("color_scheme", "org.gnome.desktop.interface", "color-scheme"),
    DKey("accent_color", "org.gnome.desktop.interface", "accent-color"),
    DKey("icon_theme", "org.gnome.desktop.interface", "icon-theme"),
    DKey("cursor_theme", "org.gnome.desktop.interface", "cursor-theme"),
    DKey("cursor_size", "org.gnome.desktop.interface", "cursor-size"),
    DKey("font_name", "org.gnome.desktop.interface", "font-name"),
    DKey("text_scaling", "org.gnome.desktop.interface", "text-scaling-factor"),
    DKey("enable_animations", "org.gnome.desktop.interface", "enable-animations"),
    # top bar
    DKey("clock_format", "org.gnome.desktop.interface", "clock-format"),
    DKey("clock_show_date", "org.gnome.desktop.interface", "clock-show-date"),
    DKey("clock_show_weekday", "org.gnome.desktop.interface", "clock-show-weekday"),
    DKey("clock_show_seconds", "org.gnome.desktop.interface", "clock-show-seconds"),
    DKey("show_battery_percentage", "org.gnome.desktop.interface", "show-battery-percentage"),
    # login behaviour
    DKey("banner_enable", "org.gnome.login-screen", "banner-message-enable"),
    DKey("banner_text", "org.gnome.login-screen", "banner-message-text"),
    DKey("disable_user_list", "org.gnome.login-screen", "disable-user-list"),
    DKey("disable_restart_buttons", "org.gnome.login-screen", "disable-restart-buttons"),
    DKey("password_auth", "org.gnome.login-screen", "enable-password-authentication"),
    DKey("fingerprint_auth", "org.gnome.login-screen", "enable-fingerprint-authentication"),
    DKey("smartcard_auth", "org.gnome.login-screen", "enable-smartcard-authentication"),
    # input & power
    DKey("tap_to_click", "org.gnome.desktop.peripherals.touchpad", "tap-to-click"),
    DKey("natural_scroll", "org.gnome.desktop.peripherals.touchpad", "natural-scroll"),
    DKey("mouse_speed", "org.gnome.desktop.peripherals.mouse", "speed"),
    DKey("night_light", "org.gnome.settings-daemon.plugins.color", "night-light-enabled"),
    DKey("power_button", "org.gnome.settings-daemon.plugins.power", "power-button-action"),
    DKey("sleep_ac", "org.gnome.settings-daemon.plugins.power", "sleep-inactive-ac-type"),
    DKey("sleep_battery", "org.gnome.settings-daemon.plugins.power", "sleep-inactive-battery-type"),
    DKey("screen_keyboard", "org.gnome.desktop.a11y.applications", "screen-keyboard-enabled"),
    DKey("screen_reader", "org.gnome.desktop.a11y.applications", "screen-reader-enabled"),
]

# Theme (CSS / gresource) settings, stored only by Orbits
THEME_DEFAULTS = {
    "bg_mode": "default",          # default | image | color | gradient
    "bg_image": "",
    "bg_color": "#1e1e2e",
    "bg_color2": "#45475a",
    "bg_gradient": "vertical",     # vertical | horizontal
    "bg_blur": 0,                  # px
    "bg_dim": 0,                   # percent
    "text_color": "",              # "" = theme default
    "clock_size": 0.0,             # em, 0 = default (6.5em)
    "clock_weight": 0,             # 0 = default (800)
    "logo": "",
    "autologin_enable": False,
    "autologin_user": "",
    "wayland": True,
}

ACCENTS = ["blue", "teal", "green", "yellow", "orange", "red", "pink", "purple", "slate"]
ACCENT_HEX = {"blue": "#3584e4", "teal": "#2190a4", "green": "#3a944a", "yellow": "#c88800",
              "orange": "#ed5b00", "red": "#e62d42", "pink": "#d56199", "purple": "#9141ac",
              "slate": "#6f8396"}


def _schema(schema_id):
    src = Gio.SettingsSchemaSource.get_default()
    return src.lookup(schema_id, True) if src else None


def available_keys():
    """DCONF_KEYS filtered to those that exist on this system's GNOME version."""
    out = []
    for k in DCONF_KEYS:
        s = _schema(k.schema)
        if s and s.has_key(k.key):
            out.append(k)
    return out


def key_info(dk: DKey):
    """(default_value, value_type, range_variant) for a key."""
    sk = _schema(dk.schema).get_key(dk.key)
    return sk.get_default_value(), sk.get_value_type(), sk.get_range()


def enum_choices(dk: DKey):
    _, _, rng = key_info(dk)
    kind, values = rng.unpack()
    return values if kind == "enum" else []


def _read_system_dconf():
    """Merge all keyfiles in /etc/dconf/db/gdm.d in dconf's order (later files win)."""
    merged = {}
    if not GDM_DCONF_DIR.is_dir():
        return merged
    for path in sorted(GDM_DCONF_DIR.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        kf = GLib.KeyFile()
        try:
            kf.load_from_file(str(path), GLib.KeyFileFlags.NONE)
        except GLib.Error:
            continue
        for group in kf.get_groups()[0]:
            for key in kf.get_keys(group)[0]:
                merged[(group, key)] = kf.get_value(group, key)
    return merged


def _read_custom_conf():
    vals, in_daemon = {}, False
    try:
        for line in GDM_CONF.read_text().splitlines():
            s = line.strip()
            if s.startswith("["):
                in_daemon = s == "[daemon]"
            elif in_daemon and "=" in s and not s.startswith("#"):
                k, v = s.split("=", 1)
                vals[k.strip()] = v.strip()
    except OSError:
        pass
    return vals


def login_users():
    users = []
    for p in pwd.getpwall():
        if 1000 <= p.pw_uid < 60000 and not p.pw_shell.endswith(("nologin", "false")):
            users.append((p.pw_name, (p.pw_gecos.split(",")[0] or p.pw_name)))
    return users


def _themes(kind):
    names = set()
    for base in ("/usr/share/icons", "/usr/local/share/icons"):
        if not os.path.isdir(base):
            continue
        for name in os.listdir(base):
            d = os.path.join(base, name)
            if kind == "cursor" and os.path.isdir(os.path.join(d, "cursors")):
                names.add(name)
            elif kind == "icon" and os.path.isfile(os.path.join(d, "index.theme")) \
                    and name not in ("default", "hicolor", "locolor") \
                    and any(os.path.isdir(os.path.join(d, x)) for x in os.listdir(d) if x != "cursors"):
                names.add(name)
    return sorted(names, key=str.lower)


def icon_themes():
    return _themes("icon")


def cursor_themes():
    return _themes("cursor")


class GdmModel:
    """Holds the current choice for every setting; values are Python natives."""

    def __init__(self):
        self.keys = available_keys()
        self.store = JsonStore(CONFIG_DIR / "gdm.json", THEME_DEFAULTS)
        self.values = dict(self.store.data)
        sysvals = _read_system_dconf()
        for dk in self.keys:
            default, vtype, _ = key_info(dk)
            raw = sysvals.get((dk.group, dk.key))
            val = default
            if raw is not None:
                try:
                    val = GLib.Variant.parse(vtype, raw, None, None)
                except GLib.Error:
                    pass
            self.values[dk.name] = val.unpack()
        conf = _read_custom_conf()
        self.values["autologin_enable"] = conf.get("AutomaticLoginEnable", "false").lower() in ("true", "1")
        self.values["autologin_user"] = conf.get("AutomaticLogin", self.values.get("autologin_user", ""))
        self.values["wayland"] = conf.get("WaylandEnable", "true").lower() not in ("false", "0")
        # The logo lives in dconf: the system's current value is the truth.
        raw_logo = sysvals.get(("org/gnome/login-screen", "logo"))
        try:
            self.values["logo"] = GLib.Variant.parse(GLib.VariantType("s"), raw_logo, None, None).unpack() if raw_logo else ""
        except GLib.Error:
            self.values["logo"] = ""

    def has(self, name):
        return name in self.values

    def get(self, name, default=None):
        return self.values.get(name, default)

    def set(self, name, value):
        self.values[name] = value
        if name in THEME_DEFAULTS:
            self.store.data[name] = value
            self.store.save()

    def dkey(self, name):
        return next((k for k in self.keys if k.name == name), None)

    # ---- spec ---------------------------------------------------------
    def build_css(self):
        v, rules = self.values, []
        mode = v["bg_mode"]
        if mode == "image" and v["bg_image"]:
            rules.append("#lockDialogGroup {\n"
                         "  background: #000000 url(\"resource:///org/gnome/shell/theme/orbits-background.jpg\");\n"
                         "  background-size: cover;\n  background-position: center;\n"
                         "  background-repeat: no-repeat;\n}")
        elif mode == "color":
            rules.append(f"#lockDialogGroup {{\n  background: {v['bg_color']};\n}}")
        elif mode == "gradient":
            rules.append("#lockDialogGroup {\n"
                         f"  background-gradient-direction: {v['bg_gradient']};\n"
                         f"  background-gradient-start: {v['bg_color']};\n"
                         f"  background-gradient-end: {v['bg_color2']};\n}}")
        if v["text_color"]:
            c = v["text_color"]
            rules.append(".login-dialog, .login-dialog-banner, .login-dialog-message, .login-dialog-message-hint,\n"
                         ".login-dialog-username, .user-widget-label, .unlock-dialog-clock,\n"
                         f".unlock-dialog-clock-date, .unlock-dialog-clock-time {{\n  color: {c};\n}}")
        clock = []
        if v["clock_size"]:
            clock.append(f"  font-size: {v['clock_size']:.2f}em;")
        if v["clock_weight"]:
            clock.append(f"  font-weight: {int(v['clock_weight'])};")
        if clock:
            rules.append(".unlock-dialog-clock .unlock-dialog-clock-time {\n" + "\n".join(clock) + "\n}")
        return "\n\n".join(rules)

    def build_spec(self, background_png: str | None):
        """background_png: path to the pre-rendered (blurred/dimmed) image, if bg_mode == image."""
        css = self.build_css()
        theme = {"css": css, "background": background_png} if css else None
        dconf = {}
        for dk in self.keys:
            if dk.name == "logo":
                continue
            _, vtype, _ = key_info(dk)
            val = GLib.Variant(vtype.dup_string(), self.values[dk.name])
            dconf.setdefault(dk.group, {})[dk.key] = val.print_(True)
        logo_source = None
        logo = self.values.get("logo", "")
        if logo and not logo.startswith("/usr/share/orbits/"):
            # copy into our cache so the helper only reads files the user owns
            dest = EXPORT_DIR / ("logo" + Path(logo).suffix.lower())
            shutil.copyfile(logo, dest)
            logo_source = str(dest)
        elif logo:
            dconf.setdefault("org/gnome/login-screen", {})["logo"] = GLib.Variant("s", logo).print_(True)
        else:
            dconf.setdefault("org/gnome/login-screen", {})["logo"] = "''"
        custom = {
            "AutomaticLoginEnable": "true" if self.values["autologin_enable"] and self.values["autologin_user"] else None,
            "AutomaticLogin": self.values["autologin_user"] if self.values["autologin_enable"] else None,
            "WaylandEnable": None if self.values["wayland"] else "false",
        }
        return {"theme": theme, "dconf": dconf, "logo_source": logo_source, "custom_conf": custom}

    def write_spec(self, background_png):
        spec = self.build_spec(background_png)
        path = EXPORT_DIR / "spec.json"
        path.write_text(json.dumps(spec, indent=2))
        os.chmod(path, 0o600)
        return path

    def reset_theme_values(self):
        for k, v in THEME_DEFAULTS.items():
            if k not in ("autologin_enable", "autologin_user", "wayland"):
                self.set(k, v)
        for dk in self.keys:
            self.values[dk.name] = key_info(dk)[0].unpack()
