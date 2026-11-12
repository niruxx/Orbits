"""Installed theme cards and previews."""
from orbits.i18n import _ as tr
from orbits.theme_sources import source_url

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from orbits import net, themes
from orbits.ui.widgets import CoverPicture, load_texture_into

ICON_SAMPLES = [
    ["folder", "inode-directory"],
    ["user-home", "folder-home"],
    ["text-x-generic", "text-plain"],
    ["org.gnome.Nautilus", "system-file-manager", "file-manager"],
    ["utilities-terminal", "org.gnome.Console", "terminal"],
    ["web-browser", "firefox", "org.mozilla.firefox"],
    ["preferences-system", "org.gnome.Settings", "gnome-control-center"],
]
CURSOR_SAMPLES = [["left_ptr", "default", "arrow"], ["hand2", "pointer", "hand1"], ["xterm", "text", "ibeam"],
                  ["watch", "wait"], ["fleur", "move", "grabbing"], ["crosshair", "cross"]]


class ThemeCard(Gtk.Box):
    def __init__(self, page, theme):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["theme-card"], width_request=300)
        self.page, self.theme = page, theme
        self.preview = Gtk.Overlay(css_classes=["theme-preview"], overflow=Gtk.Overflow.HIDDEN, height_request=150)
        self.append(self.preview)
        self._build_preview()

        self.badge = Gtk.Label(label=tr("● Current"), css_classes=["badge", "accent"], halign=Gtk.Align.START,
                               valign=Gtk.Align.START, visible=False)
        self.preview.add_overlay(self.badge)

        info = Gtk.Box(spacing=8, margin_start=14, margin_end=10, margin_top=10, margin_bottom=10)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        text.append(Gtk.Label(label=theme.display, xalign=0, ellipsize=3, max_width_chars=24, css_classes=["heading"]))
        scope = {"user": "Just you", "system": "All users"}.get(theme.scope, theme.scope)
        sub = scope + (" · from Orbits" if theme.managed else "")
        if theme.display != theme.name:
            sub = f"{theme.name} · {sub}"
        text.append(Gtk.Label(label=sub, xalign=0, ellipsize=3, max_width_chars=30,
                              css_classes=["dim-label", "caption"]))
        info.append(text)
        self.apply_btn = Gtk.Button(label="Export…" if theme.kind == "terminal" else "Apply", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
        self.apply_btn.connect("clicked", lambda *_: page.apply(theme))
        info.append(self.apply_btn)
        menu = self._menu()
        if menu:
            info.append(menu)
        self.append(info)

    # ---- preview -------------------------------------------------------
    def _build_preview(self):
        t, kind = self.theme, self.theme.kind
        if kind == "terminal" and t.path:
            from orbits.terminal_schemes import read_scheme
            try:
                scheme = read_scheme(t.path / "terminal.json")
                self.preview.set_child(self._swatches(scheme["colors"][:5]))
                return
            except (ValueError, OSError, KeyError):
                pass
        if kind == "icons" and t.path:
            self.preview.set_child(self._icon_preview())
            return
        if kind == "cursors" and t.path:
            self.preview.set_child(self._cursor_preview())
            return
        img = themes.preview_image(t) if t.path else None
        if img:
            pic = Gtk.Picture(content_fit=Gtk.ContentFit.SCALE_DOWN, halign=Gtk.Align.CENTER,
                              valign=Gtk.Align.CENTER, can_shrink=True,
                              width_request=300, height_request=150)
            load_texture_into(pic, path=str(img), size=600)
            self.preview.set_child(pic)
            return
        if t.meta.get("preview"):
            pic = CoverPicture(300, 150, hexpand=True, vexpand=True)
            load_texture_into(pic, url=t.meta["preview"])
            self.preview.set_child(pic)
            return
        if kind == "gtk" and t.path:
            colors = themes.gtk_colors(t.path)
            if colors:
                self.preview.set_child(self._swatches(colors))
                return
        icon = Gtk.Image(icon_name=themes.KINDS[kind].icon, pixel_size=48, css_classes=["dim-label"],
                         hexpand=True, vexpand=True)
        self.preview.set_child(icon)

    def _icon_preview(self):
        from orbits.theme_preview import icon_samples
        box = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        message = Gtk.Label(label=tr("No raster preview available"), wrap=True,
                            css_classes=["dim-label"])
        box.append(message)

        def done(paths, error):
            if getattr(self, "_grid_released", False) or error:
                return
            available = [path for path in paths if path]
            if not available:
                return
            box.remove(message)
            for path in available[:4]:
                picture = Gtk.Picture(width_request=44, height_request=44,
                                      content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True)
                box.append(picture)
                load_texture_into(picture, path=str(path), size=128)

        net.run_async(lambda: icon_samples(self.theme.path, ICON_SAMPLES), done)
        return box

    def _cursor_preview(self):
        box = Gtk.Box(spacing=18, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, css_classes=["cursor-strip"])
        files = [themes.cursor_file(self.theme.path, names) for names in CURSOR_SAMPLES]
        images = []
        for f in files:
            if f:
                img = Gtk.Picture(can_shrink=False)
                images.append((img, f))
                box.append(img)

        def work():
            out = []
            for _img, f in images:
                try:
                    out.append(themes.xcursor_image(f, 32))
                except (OSError, ValueError):
                    out.append(None)
            return out

        def done(decoded, err):
            if getattr(self, "_grid_released", False):
                return
            for (img, _f), d in zip(images, decoded or []):
                if d:
                    w, h, px = d
                    img.set_paintable(Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                                                            GLib.Bytes.new(px), w * 4))

        net.run_async(work, done)
        if not images:
            box.append(Gtk.Image(icon_name="input-mouse-symbolic", pixel_size=48, css_classes=["dim-label"]))
        return box

    def _swatches(self, colors):
        box = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        for c in colors:
            dot = Gtk.Box(css_classes=["theme-swatch"])
            prov = Gtk.CssProvider()
            prov.load_from_string(f"box {{ background-color: {c}; }}")
            dot.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
            box.append(dot)
        aa = Gtk.Label(label=tr("Aa"), css_classes=["title-1"], margin_start=8)
        box.append(aa)
        return box

    # ---- actions -------------------------------------------------------
    def _menu(self):
        t, kind = self.theme, themes.KINDS[self.theme.kind]
        actions = []
        if t.path and t.kind == "plymouth":
            actions.append(("Preview Boot Splash…", lambda: self.page.preview_plymouth(t)))
        if t.path and t.kind == "gtk":
            actions.append(("Theme Health Check…", lambda: self.page.health_check(t)))
        if t.path and t.scope == "user" and kind.user_dir is not None:
            actions.append(("Install for All Users…", lambda: self.page.install_system(t)))
        if t.path and t.scope == "system" and t.managed:
            actions.append(("Remove for All Users…", lambda: self.page.remove_system(t)))
        if t.path and t.scope == "user":
            actions.append(("Uninstall", lambda: self.page.uninstall(t)))
        origin = source_url(t.meta)
        if origin:
            actions.append((tr("Open Source"), lambda: Gtk.UriLauncher.new(origin).launch(
                self.page.window, None, None)))
        if t.path:
            actions.append(("Show Files", lambda: Gtk.FileLauncher.new(Gio.File.new_for_path(str(t.path))).launch(
                self.page.window, None, None)))
        if not actions:
            return None
        btn = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text=tr("Theme Actions"), css_classes=["flat"], valign=Gtk.Align.CENTER)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        pop = Gtk.Popover(child=box)
        for label, cb in actions:
            b = Gtk.Button(label=label, css_classes=["flat"])
            b.get_child().set_xalign(0)
            if label == "Uninstall":
                b.add_css_class("error")
            b.connect("clicked", lambda _b, cb=cb: (pop.popdown(), cb()))
            box.append(b)
        btn.set_popover(pop)
        return btn

    def set_current(self, is_current):
        self.badge.set_visible(is_current)
        self.apply_btn.set_visible(not is_current)


