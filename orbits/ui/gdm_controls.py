"""GDM preference controls and tab construction."""
from gi.repository import Adw, Gdk, Gtk, Pango
from orbits.i18n import _ as tr
from orbits.gdm import model as gm
from orbits.ui.widgets import CoverPicture

BG_MODES = [("default", "GNOME Default"), ("image", "Image"), ("color", "Solid Color"), ("gradient", "Gradient")]
CURSOR_SIZES = [24, 32, 48, 64, 96]
ACCENT_LABELS = {a: a.title() for a in gm.ACCENTS}


def _pretty(value):
    return str(value).replace("-", " ").replace("_", " ").title()


def _hex(rgba):
    return "#{:02x}{:02x}{:02x}".format(*(round(c * 255) for c in (rgba.red, rgba.green, rgba.blue)))


def _rgba(hex_):
    c = Gdk.RGBA()
    c.parse(hex_ or "#000000")
    return c


class GdmControls:
    def _tab_box(self, *groups):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=12)
        for g in groups:
            if g is not None:
                box.append(g)
        return box


    def _background_tab(self):
        g = Adw.PreferencesGroup(title=tr("Login Background"),
                                 description=tr("Shown behind the GDM login dialog."))
        self.mode_row = self._combo("bg_mode", tr("Type"), BG_MODES)
        g.add(self.mode_row)

        self.image_row = Adw.ActionRow(title=tr("Image"), subtitle=tr("No image selected"))
        self.image_thumb = CoverPicture(72, 44, css_classes=["card"], valign=Gtk.Align.CENTER)
        self.image_row.add_prefix(self.image_thumb)
        lib_btn = Gtk.MenuButton(icon_name="folder-pictures-symbolic", tooltip_text=tr("Choose from Library"),
                                 valign=Gtk.Align.CENTER, css_classes=["flat"])
        self.lib_popover = Gtk.Popover()
        self.lib_popover.connect("show", lambda *_: self._fill_library_popover())
        lib_btn.set_popover(self.lib_popover)
        file_btn = Gtk.Button(label=tr("Choose…"), valign=Gtk.Align.CENTER)
        file_btn.connect("clicked", lambda *_: self._choose_image("bg_image"))
        self.image_row.add_suffix(lib_btn)
        self.image_row.add_suffix(file_btn)
        g.add(self.image_row)
        self.blur_row = self._scale("bg_blur", tr("Blur"), tr("Soften the image"), 0, 100, 1)
        self.dim_row = self._scale("bg_dim", tr("Dim"), tr("Darken for better contrast"), 0, 90, 1)
        g.add(self.blur_row)
        g.add(self.dim_row)

        self.color_row = self._color("bg_color", "Color")
        self.color2_row = self._color("bg_color2", "Second Color")
        self.dir_row = self._combo("bg_gradient", tr("Direction"), [("vertical", "Vertical"), ("horizontal", "Horizontal")])
        for r in (self.color_row, self.color2_row, self.dir_row):
            g.add(r)
        self.variant_rows = []
        for variant in ("light", "dark"):
            key = f"bg_image_{variant}"
            row = Adw.ActionRow(title=tr('{value0} Background').format(value0=variant.title()))
            choose = Gtk.Button(label=tr("Choose…"), valign=Gtk.Align.CENTER)
            choose.connect("clicked", lambda _b, k=key: self._choose_image(k))
            clear = Gtk.Button(icon_name="edit-clear-symbolic", tooltip_text=tr("Use the main image"),
                               valign=Gtk.Align.CENTER, css_classes=["flat"])
            clear.connect("clicked", lambda _b, k=key: self._set(k, ""))
            row.add_suffix(clear)
            row.add_suffix(choose)
            g.add(row)
            self.variant_rows.append((key, row))
        monitors = Adw.PreferencesGroup(title=tr("Per-monitor Backgrounds"),
            description=tr("Compose images for the current display layout. GDM must use the same layout; "
                        "reapply after changing Displays settings. Empty choices use the global images."))
        row = Adw.ActionRow(title=tr("Display Layout"))
        configure = Gtk.Button(label=tr("Configure…"), valign=Gtk.Align.CENTER)
        configure.connect("clicked", lambda *_: self._configure_monitors())
        clear = Gtk.Button(label=tr("Clear"), valign=Gtk.Align.CENTER)
        clear.connect("clicked", lambda *_: self._set("bg_monitors", []))
        row.add_suffix(clear)
        row.add_suffix(configure)
        monitors.add(row)
        self.monitor_group = monitors
        return self._tab_box(g, monitors)


    def _style_tab(self):
        g = Adw.PreferencesGroup(title=tr("Appearance"))
        if self.model.has("color_scheme"):
            dk = self.model.dkey("color_scheme")
            g.add(self._combo("color_scheme", tr("Style"), [(v, _pretty(v)) for v in gm.enum_choices(dk)]))
        if self.model.has("accent_color"):
            g.add(self._accent_row())
        panel = Adw.ExpanderRow(title=tr("Custom Panel Opacity"), show_enable_switch=True,
                                 enable_expansion=self.model.get("panel_opacity", -1) >= 0)
        panel.add_row(self._scale("panel_opacity", tr("Opacity"), tr("0 is transparent; 100 is opaque"), 0, 100, 1))
        panel.connect("notify::enable-expansion", self._panel_toggled)
        self._register("panel_opacity_enabled", panel, lambda v: panel.set_enable_expansion(self.model.get("panel_opacity", -1) >= 0))
        g.add(panel)
        g.add(self._optional_color("text_color", tr("Custom Text Color"), tr("Login dialog and lock screen clock")))

        t = Adw.PreferencesGroup(title=tr("Themes &amp; Fonts"),
                                 description=tr("Only themes installed system-wide (in /usr/share) are available to GDM."))
        if self.model.has("font_name"):
            t.add(self._font("font_name", tr("Interface Font")))
        if self.model.has("text_scaling"):
            t.add(self._spin("text_scaling", tr("Text Scaling"), 0.5, 3.0, 0.05, 2))
        if self.model.has("icon_theme"):
            t.add(self._combo("icon_theme", tr("Icon Theme"), [(n, n) for n in gm.icon_themes()]))
        if self.model.has("cursor_theme"):
            t.add(self._combo("cursor_theme", tr("Cursor Theme"), [(n, n) for n in gm.cursor_themes()]))
        if self.model.has("cursor_size"):
            t.add(self._combo("cursor_size", tr("Cursor Size"), [(n, f"{n} px") for n in CURSOR_SIZES]))
        if self.model.has("enable_animations"):
            t.add(self._switch("enable_animations", tr("Animations")))
        return self._tab_box(g, t)


    def _panel_toggled(self, row, _param):
        if self._syncing:
            return
        value = 100 if row.get_enable_expansion() else -1
        self._set("panel_opacity", value)
        if value >= 0:
            self._rows_by_key["panel_opacity"][1](value)


    def _clock_tab(self):
        g = Adw.PreferencesGroup(title=tr("Top Bar"))
        if self.model.has("clock_format"):
            g.add(self._combo("clock_format", tr("Time Format"), [("24h", "24-hour"), ("12h", "AM / PM")]))
        for key, title in [("clock_show_date", "Show Date"), ("clock_show_weekday", "Show Weekday"),
                           ("clock_show_seconds", "Show Seconds"),
                           ("show_battery_percentage", "Show Battery Percentage")]:
            if self.model.has(key):
                g.add(self._switch(key, title))

        lock = Adw.PreferencesGroup(title=tr("Lock Screen Clock"),
                                    description=tr("Applies to the big clock on the lock screen. "
                                                "Switch the preview to “Lock Screen” to see it."))
        self.clock_size_row = Adw.SpinRow.new_with_range(3.0, 14.0, 0.25)
        self.clock_size_row.set_title(tr("Size"))
        self.clock_size_row.set_subtitle(tr("In em — GNOME's default is 6.5"))
        self.clock_size_row.set_digits(2)
        self.clock_size_row.set_value(self.model.get("clock_size") or 6.5)
        self.clock_size_row.connect("notify::value", lambda r, _p: self._custom_clock_changed())
        self.clock_weight_row = Adw.ComboRow(title=tr("Weight"), model=Gtk.StringList.new(
            ["Thin", "Light", "Regular", "Medium", "Bold", "Heavy", "Black"]))
        self._weights = [200, 300, 400, 500, 700, 800, 900]
        w = self.model.get("clock_weight") or 800
        self.clock_weight_row.set_selected(self._weights.index(w) if w in self._weights else 5)
        self.clock_weight_row.connect("notify::selected", lambda *_: self._custom_clock_changed())
        self.clock_expander = Adw.ExpanderRow(title=tr("Custom Clock Style"), show_enable_switch=True,
                                              enable_expansion=bool(self.model.get("clock_size")))
        self.clock_expander.add_row(self.clock_size_row)
        self.clock_expander.add_row(self.clock_weight_row)
        self.clock_expander.connect("notify::enable-expansion", lambda *_: self._custom_clock_changed())
        lock.add(self.clock_expander)
        font = Adw.EntryRow(title=tr("Clock Font Family"), text=self.model.get("clock_font") or "",
                            tooltip_text=tr("Use a system-wide font family; leave empty for the default"))
        font.connect("changed", lambda r: self._set("clock_font", r.get_text()))
        self._register("clock_font", font, lambda v: font.set_text(v or ""))
        lock.add(font)
        return self._tab_box(g, lock)


    def _custom_clock_changed(self):
        if self._syncing:
            return
        if self.clock_expander.get_enable_expansion():
            self.model.set("clock_size", self.clock_size_row.get_value())
            self.model.set("clock_weight", self._weights[self.clock_weight_row.get_selected()])
        else:
            self.model.set("clock_size", 0.0)
            self.model.set("clock_weight", 0)
        self._changed()


    def _login_tab(self):
        g = Adw.PreferencesGroup(title=tr("Branding"))
        self.logo_row = Adw.ActionRow(title=tr("Logo"), subtitle=tr("Shown at the bottom of the login screen"))
        self.logo_thumb = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, width_request=72, height_request=40,
                                      valign=Gtk.Align.CENTER)
        self.logo_row.add_prefix(self.logo_thumb)
        clear = Gtk.Button(icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                           tooltip_text=tr("Remove Logo"))
        clear.connect("clicked", lambda *_: (self.model.set("logo", ""), self._changed()))
        choose = Gtk.Button(label=tr("Choose…"), valign=Gtk.Align.CENTER)
        choose.connect("clicked", lambda *_: self._choose_image("logo"))
        self.logo_row.add_suffix(clear)
        self.logo_row.add_suffix(choose)
        g.add(self.logo_row)

        if self.model.has("banner_enable"):
            exp = Adw.ExpanderRow(title=tr("Welcome Message"), subtitle=tr("Text shown above the login prompt"),
                                  show_enable_switch=True, enable_expansion=bool(self.model.get("banner_enable")))
            text = Adw.EntryRow(title=tr("Message"), text=self.model.get("banner_text") or "")
            text.connect("changed", lambda e: self._set("banner_text", e.get_text()))
            exp.add_row(text)
            exp.connect("notify::enable-expansion", lambda e, _p: self._set("banner_enable", e.get_enable_expansion()))
            self._rows_by_key["banner_enable"] = (exp, lambda v: exp.set_enable_expansion(bool(v)))
            self._rows_by_key["banner_text"] = (text, lambda v: text.set_text(v or ""))
            g.add(exp)

        b = Adw.PreferencesGroup(title=tr("Behaviour"))
        for key, title, sub in [("disable_user_list", "Hide User List", "Users must type their username"),
                                ("disable_restart_buttons", "Hide Power Buttons",
                                 "Remove restart and shut down from the login screen")]:
            if self.model.has(key):
                b.add(self._switch(key, title, sub))

        auth = Adw.PreferencesGroup(title=tr("Authentication Methods"))
        for key, title in [("password_auth", "Password"), ("fingerprint_auth", "Fingerprint"),
                           ("smartcard_auth", "Smart Card")]:
            if self.model.has(key):
                auth.add(self._switch(key, title))

        auto = Adw.PreferencesGroup(title=tr("Automatic Login"),
                                    description=tr("Skips the login screen entirely on boot. Use with care."))
        users = gm.login_users()
        self.auto_exp = Adw.ExpanderRow(title=tr("Log In Automatically"), show_enable_switch=True,
                                        enable_expansion=bool(self.model.get("autologin_enable")))
        user_row = Adw.ComboRow(title=tr("User"), model=Gtk.StringList.new([f"{full} ({u})" if full != u else u
                                                                         for u, full in users]))
        names = [u for u, _ in users]
        cur = self.model.get("autologin_user")
        if cur in names:
            user_row.set_selected(names.index(cur))
        elif names:
            self.model.set("autologin_user", names[0])

        def user_changed(r, _p):
            if names:
                self._set("autologin_user", names[r.get_selected()])

        user_row.connect("notify::selected", user_changed)
        self.auto_exp.add_row(user_row)
        self.auto_exp.connect("notify::enable-expansion",
                              lambda e, _p: self._set("autologin_enable", e.get_enable_expansion()))
        auto.add(self.auto_exp)
        auto.add(self._switch("wayland", tr("Use Wayland"), tr("Turn off to force the X11 login screen")))
        return self._tab_box(g, b, auth, auto)


    def _system_tab(self):
        tp = Adw.PreferencesGroup(title=tr("Touchpad &amp; Mouse"))
        for key, title in [("tap_to_click", "Tap to Click"), ("natural_scroll", "Natural Scrolling")]:
            if self.model.has(key):
                tp.add(self._switch(key, title))
        if self.model.has("mouse_speed"):
            tp.add(self._scale("mouse_speed", tr("Mouse Speed"), None, -1.0, 1.0, 0.05, digits=2))

        pw = Adw.PreferencesGroup(title=tr("Power"))
        for key, title in [("power_button", "Power Button Action"), ("sleep_ac", "When Idle (Plugged In)"),
                           ("sleep_battery", "When Idle (On Battery)")]:
            if self.model.has(key):
                pw.add(self._combo(key, title, [(v, _pretty(v)) for v in gm.enum_choices(self.model.dkey(key))]))
        if self.model.has("night_light"):
            pw.add(self._switch("night_light", tr("Night Light")))

        a11y = Adw.PreferencesGroup(title=tr("Accessibility"))
        for key, title in [("screen_keyboard", "Screen Keyboard"), ("screen_reader", "Screen Reader")]:
            if self.model.has(key):
                a11y.add(self._switch(key, title))
        updates = Adw.PreferencesGroup(title=tr("System Updates"),
            description=tr("Keep the applied login theme after package updates. Uses pacman/apt hooks "
                        "and a systemd watcher for DNF/RPM updates. Enabling or disabling takes effect on Apply."))
        updates.add(self._switch("auto_reapply", tr("Reapply Theme After Updates")))
        return self._tab_box(tp, pw, a11y, updates)


    def _register(self, key, row, setter):
        self._rows_by_key[key] = (row, setter)


    def _set(self, key, value):
        if self._syncing:
            return
        self.model.set(key, value)
        self._changed()


    def _switch(self, key, title, subtitle=None):
        row = Adw.SwitchRow(title=title, subtitle=subtitle or "", active=bool(self.model.get(key)))
        row.connect("notify::active", lambda r, _p: self._set(key, r.get_active()))
        self._register(key, row, lambda v: row.set_active(bool(v)))
        return row


    def _combo(self, key, title, choices):
        values = [v for v, _ in choices]
        cur = self.model.get(key)
        if cur not in values:  # e.g. a theme that's set but not installed — keep it selectable
            values.append(cur)
            choices = list(choices) + [(cur, str(cur))]
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new([label for _, label in choices]),
                           enable_search=len(choices) > 10)
        row.set_selected(values.index(cur))
        row.connect("notify::selected", lambda r, _p: self._set(key, values[r.get_selected()]))
        self._register(key, row, lambda v: v in values and row.set_selected(values.index(v)))
        return row


    def _spin(self, key, title, lo, hi, step, digits=0):
        row = Adw.SpinRow.new_with_range(lo, hi, step)
        row.set_title(title)
        row.set_digits(digits)
        row.set_value(float(self.model.get(key) or 0))
        row.connect("notify::value", lambda r, _p: self._set(key, float(r.get_value())))
        self._register(key, row, lambda v: row.set_value(float(v)))
        return row


    def _scale(self, key, title, subtitle, lo, hi, step, digits=0):
        row = Adw.ActionRow(title=title, subtitle=subtitle or "")
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
        scale.set_size_request(240, -1)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.LEFT)
        scale.set_digits(digits)
        scale.set_value(float(self.model.get(key) or 0))
        scale.set_valign(Gtk.Align.CENTER)
        is_int = digits == 0 and isinstance(gm.THEME_DEFAULTS.get(key), int)
        scale.connect("value-changed",
                      lambda s: self._set(key, int(round(s.get_value())) if is_int else float(s.get_value())))
        row.add_suffix(scale)
        self._register(key, row, lambda v: scale.set_value(float(v)))
        return row


    def _color(self, key, title):
        row = Adw.ActionRow(title=title)
        btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER,
                                    rgba=_rgba(self.model.get(key)))
        btn.connect("notify::rgba", lambda b, _p: self._set(key, _hex(b.get_rgba())))
        row.add_suffix(btn)
        row.set_activatable_widget(btn)
        self._register(key, row, lambda v: btn.set_rgba(_rgba(v)))
        return row


    def _optional_color(self, key, title, subtitle):
        exp = Adw.ExpanderRow(title=title, subtitle=subtitle, show_enable_switch=True,
                              enable_expansion=bool(self.model.get(key)))
        inner = Adw.ActionRow(title=tr("Color"))
        btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER,
                                    rgba=_rgba(self.model.get(key) or "#ffffff"))
        inner.add_suffix(btn)
        exp.add_row(inner)

        def update(*_):
            self._set(key, _hex(btn.get_rgba()) if exp.get_enable_expansion() else "")

        btn.connect("notify::rgba", update)
        exp.connect("notify::enable-expansion", update)
        self._register(key, exp, lambda v: (exp.set_enable_expansion(bool(v)), v and btn.set_rgba(_rgba(v))))
        return exp


    def _font(self, key, title):
        row = Adw.ActionRow(title=title)
        btn = Gtk.FontDialogButton(dialog=Gtk.FontDialog(), valign=Gtk.Align.CENTER, use_font=True,
                                   level=Gtk.FontLevel.FONT,
                                   font_desc=Pango.FontDescription.from_string(self.model.get(key) or "Sans 11"))
        btn.connect("notify::font-desc", lambda b, _p: self._set(key, b.get_font_desc().to_string()))
        row.add_suffix(btn)
        self._register(key, row, lambda v: btn.set_font_desc(Pango.FontDescription.from_string(v)))
        return row


    def _accent_row(self):
        row = Adw.ActionRow(title=tr("Accent Color"))
        box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        group = None
        buttons = {}
        for name in gm.ACCENTS:
            b = Gtk.ToggleButton(css_classes=["circular", "accent-swatch"], tooltip_text=ACCENT_LABELS[name],
                                 width_request=26, height_request=26)
            prov = Gtk.CssProvider()
            prov.load_from_string(
                f"button {{ background: {gm.ACCENT_HEX[name]}; min-width: 22px; min-height: 22px; padding: 0; }}"
                "button:checked { box-shadow: 0 0 0 3px alpha(@window_fg_color, 0.85); }")
            b.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
            if group:
                b.set_group(group)
            else:
                group = b
            b.set_active(self.model.get("accent_color") == name)
            b.connect("toggled", lambda btn, n=name: btn.get_active() and self._set("accent_color", n))
            buttons[name] = b
            box.append(b)
        row.add_suffix(box)
        self._register("accent_color", row, lambda v: v in buttons and buttons[v].set_active(True))
        return row


