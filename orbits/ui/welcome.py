"""First-launch welcome carousel."""
from orbits.i18n import _ as tr
from gi.repository import Adw, Gtk

PAGES = [
    {
        "app_icon": True,
        "title": "Welcome to Orbits",
        "body": "Your desktop, beautifully curated.\nDiscover wallpapers, build a collection and make your login screen your own.",
    },
    {
        "icon": "web-browser-symbolic",
        "title": "Discover",
        "body": "Browse thousands of wallpapers from the community.",
        "features": [
            ("application-x-addon-symbolic", "KDE Store", "The same catalogue Plasma's “Get New Wallpapers” uses"),
            ("camera-photo-symbolic", "Wallhaven", "Hand-picked, high resolution, searchable"),
            ("weather-clear-symbolic", "Bing Daily", "A stunning new photograph every day"),
        ],
    },
    {
        "icon": "folder-pictures-symbolic",
        "title": "Your Library",
        "body": "Install and uninstall with one click.",
        "features": [
            ("folder-download-symbolic", "Install", "Packages are unpacked, light & dark variants detected"),
            ("preferences-desktop-wallpaper-symbolic", "Apply", "Set desktop and lock screen backgrounds instantly"),
            ("emblem-system-symbolic", "Integrated", "Everything also appears in GNOME Settings → Appearance"),
        ],
    },
    {
        "icon": "preferences-desktop-font-symbolic",
        "title": "Fonts",
        "body": "Find the perfect typeface and use it everywhere.",
        "features": [
            ("font-x-generic-symbolic", "Google Fonts", "2,000+ open-source families with live previews"),
            ("utilities-terminal-symbolic", "Nerd Fonts", "Icon-patched developer fonts for your terminal"),
            ("object-select-symbolic", "Apply", "Interface, documents, monospace — and the login screen"),
        ],
    },
    {
        "icon": "applications-graphics-symbolic",
        "title": "Themes",
        "body": "Restyle every corner of your desktop — from boot to apps.",
        "features": [
            ("applications-graphics-symbolic", "GTK & Shell", "App themes, libadwaita too, and GNOME Shell themes"),
            ("view-app-grid-symbolic", "Icons & Cursors", "Previewed with real icons and pointers before you apply"),
            ("system-reboot-symbolic", "Boot Splash", "Plymouth animations, with the initramfs rebuilt for you"),
        ],
    },
    {
        "icon": "system-lock-screen-symbolic",
        "title": "Login Screen",
        "body": "Customize GDM like never before — with a live preview.",
        "features": [
            ("image-x-generic-symbolic", "Backgrounds", "Images with blur & dim, solid colors or gradients"),
            ("preferences-desktop-appearance-symbolic", "Style", "Accent color, fonts, icons, cursors and clock"),
            ("security-high-symbolic", "Safe", "Originals are backed up; one click restores them"),
        ],
    },
    {
        "icon": "starred-symbolic",
        "title": "You're All Set",
        "body": "Where would you like to start?",
        "start": True,
    },
]


class WelcomeView(Adw.Bin):
    def __init__(self, on_done):
        super().__init__(css_classes=["welcome"])
        self.on_done = on_done

        self.carousel = Adw.Carousel(hexpand=True, vexpand=True, allow_scroll_wheel=True, spacing=24)
        for spec in PAGES:
            self.carousel.append(self._make_page(spec))
        self.carousel.connect("page-changed", self._on_page_changed)

        header = Adw.HeaderBar(show_title=False, css_classes=["flat"])
        self.skip = Gtk.Button(label=tr("Skip"), css_classes=["flat"])
        self.skip.connect("clicked", lambda *_: self.on_done())
        header.pack_end(self.skip)
        self.back = Gtk.Button(icon_name="go-previous-symbolic", css_classes=["circular", "flat"],
                               tooltip_text=tr("Back"), sensitive=False)
        self.back.connect("clicked", lambda *_: self._go(-1))
        header.pack_start(self.back)

        dots = Adw.CarouselIndicatorDots(carousel=self.carousel)
        self.next = Gtk.Button(label=tr("Next"), css_classes=["pill", "suggested-action", "welcome-next"],
                               halign=Gtk.Align.CENTER)
        self.next.connect("clicked", lambda *_: self._go(1))
        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_bottom=32, margin_top=8)
        bottom.append(self.next)
        bottom.append(dots)

        view = Adw.ToolbarView(extend_content_to_top_edge=True)
        view.add_top_bar(header)
        view.set_content(self.carousel)
        view.add_bottom_bar(bottom)
        self.set_child(view)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    def _make_page(self, spec):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, valign=Gtk.Align.CENTER,
                      halign=Gtk.Align.CENTER, margin_start=24, margin_end=24, margin_top=48)
        if spec.get("app_icon"):
            icon = Gtk.Image(icon_name="io.github.niruxx.Orbits", pixel_size=192, css_classes=["welcome-app-icon"])
            box.append(icon)
        else:
            circle = Gtk.Box(halign=Gtk.Align.CENTER, css_classes=["welcome-icon-circle"])
            circle.append(Gtk.Image(icon_name=spec["icon"], pixel_size=56, hexpand=True,
                                    halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER))
            box.append(circle)
        box.append(Gtk.Label(label=spec["title"], css_classes=["welcome-title"], wrap=True,
                             justify=Gtk.Justification.CENTER))
        box.append(Gtk.Label(label=spec["body"], css_classes=["welcome-body"], wrap=True,
                             justify=Gtk.Justification.CENTER, max_width_chars=48))
        if spec.get("features"):
            grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12)
            clamp = Adw.Clamp(maximum_size=460, child=grid)
            for icon, title, sub in spec["features"]:
                row = Gtk.Box(spacing=16, css_classes=["welcome-feature"])
                row.append(Gtk.Image(icon_name=icon, pixel_size=24))
                text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                text.append(Gtk.Label(label=title, xalign=0, css_classes=["heading"]))
                text.append(Gtk.Label(label=sub, xalign=0, wrap=True, css_classes=["dim-label", "caption"]))
                row.append(text)
                grid.append(row)
            box.append(clamp)
        if spec.get("start"):
            choices = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=12, homogeneous=True)
            for label, icon, page in [("Wallpapers", "preferences-desktop-wallpaper-symbolic", "kde"),
                                      ("Fonts", "preferences-desktop-font-symbolic", "google-fonts"),
                                      ("Login Screen", "system-lock-screen-symbolic", "gdm")]:
                b = Gtk.Button(css_classes=["welcome-feature", "flat"])
                inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8, margin_bottom=8,
                                margin_start=18, margin_end=18)
                inner.append(Gtk.Image(icon_name=icon, pixel_size=32))
                inner.append(Gtk.Label(label=label, css_classes=["heading"]))
                b.set_child(inner)
                b.connect("clicked", lambda _b, p=page: self.on_done(p))
                choices.append(b)
            box.append(choices)
        page = Gtk.ScrolledWindow(hexpand=True, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                  child=box)
        return page

    def _go(self, delta):
        n = self.carousel.get_n_pages()
        pos = round(self.carousel.get_position())
        if delta > 0 and pos >= n - 1:
            self.on_done()
            return
        target = max(0, min(n - 1, pos + delta))
        self.carousel.scroll_to(self.carousel.get_nth_page(target), True)

    def _on_page_changed(self, carousel, index):
        last = index == carousel.get_n_pages() - 1
        self.next.set_label("Get Started" if last else "Next")
        self.back.set_sensitive(index > 0)
        self.skip.set_visible(not last)

    def _on_key(self, _ctl, keyval, _code, _state):
        from gi.repository import Gdk

        if keyval in (Gdk.KEY_Right, Gdk.KEY_Return):
            self._go(1)
            return True
        if keyval == Gdk.KEY_Left:
            self._go(-1)
            return True
        return False
