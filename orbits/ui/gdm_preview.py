"""Live mock-up of the GDM login screen / lock screen, and the background renderer used for export."""
from orbits.i18n import _ as tr
import time
import json
import tempfile
from pathlib import Path

from orbits.gdm.backgrounds import background_path
from orbits.wallpaper_tools import compose_monitors

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Graphene, Gsk, Gtk, Pango

from orbits.ui.widgets import load_optional_texture_into
from orbits import net
from orbits.gdm.model import ACCENT_HEX, login_users

REF_WIDTH = 1920.0  # blur values are expressed in pixels at 1080p
REAL_EM_PX = 14.67  # ~11pt at 96 dpi


def _rgba(hex_):
    c = Gdk.RGBA()
    c.parse(hex_ or "#000000")
    return c


def draw_background(snapshot, width, height, mode, texture=None, color="#000", color2="#000",
                    direction="vertical", blur=0.0, dim=0.0):
    """Paint a GDM-style background into snapshot. blur is in output pixels; dim in 0..1."""
    rect = Graphene.Rect().init(0, 0, width, height)
    snapshot.push_clip(rect)
    if mode == "image" and texture is not None:
        tw, th = texture.get_width(), texture.get_height()
        scale = max(width / tw, height / th)
        w, h = tw * scale, th * scale
        pad = blur * 2
        x, y = (width - w) / 2 - pad, (height - h) / 2 - pad
        snapshot.append_color(_rgba("#000"), rect)
        if blur > 0:
            snapshot.push_blur(blur)
        snapshot.append_scaled_texture(texture, Gsk.ScalingFilter.TRILINEAR,
                                       Graphene.Rect().init(x, y, w + 2 * pad, h + 2 * pad))
        if blur > 0:
            snapshot.pop()
    elif mode == "gradient":
        end = Graphene.Point().init(0, height) if direction == "vertical" else Graphene.Point().init(width, 0)
        stops = []
        for off, c in ((0.0, color), (1.0, color2)):
            s = Gsk.ColorStop()
            s.offset, s.color = off, _rgba(c)
            stops.append(s)
        snapshot.append_linear_gradient(rect, Graphene.Point().init(0, 0), end, stops)
    elif mode == "color":
        snapshot.append_color(_rgba(color), rect)
    else:  # GNOME default: dark grey
        snapshot.append_color(_rgba("#222226"), rect)
    if dim > 0:
        snapshot.append_color(Gdk.RGBA(red=0, green=0, blue=0, alpha=dim), rect)
    snapshot.pop()


def export_background(renderer, texture, blur_ref, dim_pct, out_path, max_width=3840):
    """Render the image with blur/dim at (up to) 4K and save as JPEG for the shell theme."""
    tw, th = texture.get_width(), texture.get_height()
    scale = min(1.0, max_width / tw)
    w, h = max(1, int(tw * scale)), max(1, int(th * scale))
    snap = Gtk.Snapshot()
    draw_background(snap, w, h, "image", texture, blur=blur_ref * w / REF_WIDTH, dim=dim_pct / 100)
    node = snap.to_node()
    out = renderer.render_texture(node, Graphene.Rect().init(0, 0, w, h))
    dl = Gdk.TextureDownloader.new(out)
    dl.set_format(Gdk.MemoryFormat.R8G8B8)
    data, stride = dl.download_bytes()
    pb = GdkPixbuf.Pixbuf.new_from_bytes(data, GdkPixbuf.Colorspace.RGB, False, 8, w, h, stride)
    pb.savev(str(out_path), "jpeg", ["quality"], ["93"])


class BackgroundArea(Gtk.Widget):
    def __init__(self, on_resize):
        super().__init__(hexpand=True, vexpand=True)
        self.params = {"mode": "default"}
        self.on_resize = on_resize
        self._last_w = 0
        self.aspect = 16 / 9

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return (240, 940, -1, -1)
        h = int((for_size if for_size > 0 else 940) / self.aspect)
        return (h, h, -1, -1)

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        p = dict(self.params)
        blur = p.pop("blur_ref", 0) * w / REF_WIDTH
        draw_background(snapshot, w, h, blur=blur, **p)

    def do_size_allocate(self, width, height, baseline):
        if width != self._last_w:
            self._last_w = width
            GLib.idle_add(lambda: (self.on_resize(width), False)[1])


class GdmPreview(Gtk.Overlay):
    """Mock of the GNOME Shell greeter, redrawn from GdmModel values."""

    def __init__(self, model):
        super().__init__(css_classes=["gdm-preview", "gdm-preview-frame"], overflow=Gtk.Overflow.HIDDEN)
        self.model = model
        self.mode = "login"
        self.scale = 0.45
        self.bg_texture = None
        self.bg_texture_path = None
        self.lock_texture = None
        self._css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self._css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER)

        self._users = login_users()
        self.bg = BackgroundArea(self._on_resize)
        self.set_child(self.bg)

        # top bar
        top = self.topbar = Gtk.CenterBox(valign=Gtk.Align.START, css_classes=["topbar"])
        self.clock = Gtk.Label()
        top.set_center_widget(self.clock)
        icons = Gtk.Box(spacing=6)
        for name in ("network-wireless-signal-excellent-symbolic", "audio-volume-high-symbolic"):
            icons.append(Gtk.Image(icon_name=name, pixel_size=11))
        self.battery = Gtk.Label(label="")
        icons.append(Gtk.Image(icon_name="battery-level-80-symbolic", pixel_size=11))
        icons.append(self.battery)
        icons.append(Gtk.Image(icon_name="system-shutdown-symbolic", pixel_size=11))
        top.set_end_widget(icons)
        a11y = Gtk.Image(icon_name="preferences-desktop-accessibility-symbolic", pixel_size=11)
        top.set_start_widget(a11y)
        self.add_overlay(top)

        # login dialog
        self.login = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER,
                             valign=Gtk.Align.CENTER)
        self.banner = Gtk.Label(css_classes=["banner"], wrap=True, max_width_chars=40,
                                justify=Gtk.Justification.CENTER, margin_bottom=10)
        self.login.append(self.banner)
        self.avatar = Gtk.Label(label=tr("N"), css_classes=["avatar-circle"], halign=Gtk.Align.CENTER)
        self.login.append(self.avatar)
        self.user_name = Gtk.Label(css_classes=["user-name"])
        self.login.append(self.user_name)
        self.entry = Gtk.Box(css_classes=["fake-entry", "focused"], halign=Gtk.Align.CENTER, margin_top=4)
        self.entry_label = Gtk.Label(label=tr("Password"), css_classes=["fake-entry-label"], valign=Gtk.Align.CENTER)
        self.entry.append(self.entry_label)
        self.login.append(self.entry)
        self.restart_hint = Gtk.Label(label=tr("Not listed?"), css_classes=["lock-hint"], margin_top=6)
        self.login.append(self.restart_hint)
        self.add_overlay(self.login)

        self.logo = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, height_request=34, valign=Gtk.Align.END,
                                halign=Gtk.Align.CENTER, margin_bottom=16, can_shrink=True, width_request=140)
        self.add_overlay(self.logo)

        # lock screen
        self.lock = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER,
                            valign=Gtk.Align.CENTER)
        self.lock_time = Gtk.Label(css_classes=["lock-clock-time"])
        self.lock_date = Gtk.Label(css_classes=["lock-clock-date"])
        self.lock.append(self.lock_time)
        self.lock.append(self.lock_date)
        self.add_overlay(self.lock)
        self.lock_hint = Gtk.Label(label=tr("Click or press a key to unlock"), css_classes=["pill-hint"],
                                   valign=Gtk.Align.END, halign=Gtk.Align.CENTER, margin_bottom=22)
        self.add_overlay(self.lock_hint)

        GLib.timeout_add_seconds(1, self._tick)
        self._load_lock_wallpaper()
        self.refresh()

    # ---------------------------------------------------------------- inputs
    def set_mode(self, mode):
        self.mode = mode
        self.refresh()

    def _on_resize(self, width):
        self.scale = width / REF_WIDTH
        self.refresh()

    def _load_lock_wallpaper(self):
        uri = Gio.Settings.new("org.gnome.desktop.screensaver").get_string("picture-uri") or \
            Gio.Settings.new("org.gnome.desktop.background").get_string("picture-uri")

        def work():
            path = Gio.File.new_for_uri(uri).get_path()
            import tempfile
            from orbits.image_safety import normalize_images
            with tempfile.TemporaryDirectory(prefix='orbits-gdm-image-') as temporary:
                normalized = normalize_images(path, temporary, size=1280)[0]
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(normalized), 1280, 1280, True)
            return Gdk.Texture.new_for_pixbuf(pb)

        if uri:
            net.run_preview(work, lambda tex, err: tex and (setattr(self, "lock_texture", tex), self.refresh()))

    def _ensure_bg_texture(self):
        variant = "light" if self.model.get("color_scheme") == "prefer-light" else "dark"
        path = background_path(self.model.values, variant)
        monitors = self.model.get("bg_monitors", [])
        key = json.dumps([path, monitors, variant], sort_keys=True)
        if key == self.bg_texture_path:
            return
        self.bg_texture_path = key
        self.bg_texture = None
        if not path and not monitors:
            return

        def work():
            with tempfile.TemporaryDirectory(prefix="orbits-login-layout-") as tmp:
                source = path
                if monitors:
                    assignments = [dict(rec, path=rec.get(variant) or path) for rec in monitors]
                    source = str(compose_monitors(assignments, Path(tmp) / "layout.png"))
                from orbits.image_safety import normalize_images
                with tempfile.TemporaryDirectory(prefix='orbits-gdm-image-') as temporary:
                    normalized = normalize_images(source, temporary, size=2560)[0]
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(normalized), 2560, 2560, True)
                return Gdk.Texture.new_for_pixbuf(pb)

        def done(tex, err):
            if key == self.bg_texture_path:
                self.bg_texture = tex
                self.refresh()

        net.run_preview(work, done)

    # ---------------------------------------------------------------- render
    def _tick(self):
        self._update_clock()
        return True

    def _clock_text(self):
        m = self.model
        h24 = m.get("clock_format", "24h") == "24h"
        fmt = "%H:%M" if h24 else "%-I:%M"
        if m.get("clock_show_seconds"):
            fmt += ":%S"
        if not h24:
            fmt += " %p"
        prefix = ""
        if m.get("clock_show_weekday"):
            prefix += "%a "
        if m.get("clock_show_date", True):
            prefix += "%b %-d  "
        return time.strftime(prefix + fmt)

    def _update_clock(self):
        self.clock.set_label(self._clock_text())
        h24 = self.model.get("clock_format", "24h") == "24h"
        self.lock_time.set_label(time.strftime("%H:%M" if h24 else "%-I:%M"))
        self.lock_date.set_label(time.strftime("%A, %B %-d"))

    def refresh(self):
        m, s = self.model, self.scale
        login = self.mode == "login"
        for w in (self.login, self.logo):
            w.set_visible(login)
        for w in (self.lock, self.lock_hint):
            w.set_visible(not login)

        if login:
            mode = m.get("bg_mode")
            self._ensure_bg_texture()
            self.bg.params = {"mode": mode, "texture": self.bg_texture, "color": m.get("bg_color"),
                              "color2": m.get("bg_color2"), "direction": m.get("bg_gradient"),
                              "blur_ref": m.get("bg_blur", 0) if mode == "image" else 0,
                              "dim": (m.get("bg_dim", 0) / 100) if mode == "image" else 0}
        else:
            # user-session lock screen: blurred desktop wallpaper
            self.bg.params = {"mode": "image" if self.lock_texture else "default", "texture": self.lock_texture,
                              "blur_ref": 60, "dim": 0.35}
        monitors = m.get("bg_monitors", []) if login else []
        aspect = 16 / 9
        if monitors:
            width = max(r["x"] + r["width"] for r in monitors) - min(r["x"] for r in monitors)
            height = max(r["y"] + r["height"] for r in monitors) - min(r["y"] for r in monitors)
            if width > 0 and height > 0:
                aspect = width / height
        if self.bg.aspect != aspect:
            self.bg.aspect = aspect
            self.bg.queue_resize()
        self.bg.queue_draw()

        users = self._users
        name = users[0][1] if users else "User"
        list_hidden = m.get("disable_user_list")
        self.avatar.set_visible(not list_hidden)
        self.avatar.set_label(name[:1].upper())
        self.user_name.set_visible(not list_hidden)
        self.user_name.set_label(name)
        self.entry_label.set_label("Username" if list_hidden else "Password")
        self.restart_hint.set_visible(not list_hidden)
        self.banner.set_visible(bool(m.get("banner_enable")) and bool(m.get("banner_text")))
        self.banner.set_label(m.get("banner_text") or "")
        self.battery.set_label("80%" if m.get("show_battery_percentage") else "")

        logo = m.get("logo")
        load_optional_texture_into(self.logo, logo, size=512)

        # dynamic styling
        text = m.get("text_color") or "#f6f5f4"
        accent = ACCENT_HEX.get(m.get("accent_color", "blue"), "#3584e4")
        size_em = m.get("clock_size") or 6.546
        weight = int(m.get("clock_weight") or 800)
        font = m.get("font_name") or "Sans 11"
        family = Pango.FontDescription.from_string(font).get_family() or "Sans"
        clock_family = (m.get("clock_font") or family).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")
        panel_opacity = m.get("panel_opacity", -1)
        panel_css = (f".gdm-preview .topbar {{ background-color: rgba(0, 0, 0, {panel_opacity / 100:.2f}); }}\n"
                     if panel_opacity >= 0 else "")
        self._css.load_from_string(
            panel_css +
            f".gdm-preview label {{ color: {text}; font-family: \"{family}\"; }}\n"
            f".gdm-preview .topbar label {{ color: #ffffff; }}\n"
            f".gdm-preview .fake-entry.focused {{ box-shadow: inset 0 0 0 2px {accent}; }}\n"
            f".gdm-preview .lock-clock-time {{ font-size: {max(8, size_em * REAL_EM_PX * s):.1f}px;"
            f' font-weight: {weight}; font-family: "{clock_family}"; }}\n'
            f".gdm-preview .lock-clock-date {{ font-size: {max(6, 1.818 * REAL_EM_PX * s):.1f}px; }}\n"
        )
        self._update_clock()

