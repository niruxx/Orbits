"""Login Screen (GDM) customization page."""
import os
import shutil
from pathlib import Path

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from orbits.gdm import model as gm
from orbits.library import IMAGE_EXTS, get_library
from orbits.ui.gdm_preview import GdmPreview, export_background
from orbits import net
from orbits.ui.widgets import CoverPicture, local_thumbnail

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


class GdmPage(Adw.Bin):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.title = "Login Screen"
        self.model = gm.GdmModel()
        self.dirty = False
        self._syncing = False
        self._rows_by_key = {}

        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title="Login Screen",
                                                            subtitle="Customize GDM & the lock screen"))
        menu = Gio.Menu()
        menu.append("Reset Everything to Defaults…", "gdm.reset")
        menu.append("Open Theme Backup Folder", "gdm.backup")
        header.pack_end(Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, tooltip_text="More"))
        group = Gio.SimpleActionGroup()
        for name, cb in [("reset", self._confirm_reset), ("backup", self._open_backup)]:
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_a, cb=cb: cb())
            group.add_action(a)
        self.insert_action_group("gdm", group)

        self.banner = Adw.Banner(revealed=False)
        self.banner.connect("button-clicked", lambda *_: self.apply())
        self._check_environment()

        # ---- preview
        self.preview = GdmPreview(self.model)
        frame = Adw.Clamp(maximum_size=780, child=self.preview)
        mode_toggle = Adw.ToggleGroup(halign=Gtk.Align.CENTER, css_classes=["round"])
        mode_toggle.add(Adw.Toggle(name="login", label="Login Screen"))
        mode_toggle.add(Adw.Toggle(name="lock", label="Lock Screen"))
        mode_toggle.set_active_name("login")
        mode_toggle.connect("notify::active-name", lambda t, _p: self.preview.set_mode(t.get_active_name()))

        # ---- settings tabs
        self.stack = Adw.ViewStack(vhomogeneous=False)
        self._add_tab("background", "Background", "preferences-desktop-wallpaper-symbolic", self._background_tab())
        self._add_tab("style", "Style", "preferences-desktop-appearance-symbolic", self._style_tab())
        self._add_tab("clock", "Clock", "preferences-system-time-symbolic", self._clock_tab())
        self._add_tab("login", "Login", "system-users-symbolic", self._login_tab())
        self._add_tab("system", "System", "preferences-system-symbolic", self._system_tab())
        switcher = Adw.InlineViewSwitcher(stack=self.stack, halign=Gtk.Align.CENTER, margin_top=12,
                                          display_mode=Adw.InlineViewSwitcherDisplayMode.BOTH)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_start=24, margin_end=24,
                       margin_top=12, margin_bottom=24)
        body.append(mode_toggle)
        body.append(frame)
        body.append(switcher)
        body.append(self.stack)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                      child=Adw.Clamp(maximum_size=980, child=body))

        # ---- apply bar
        bar = Gtk.Box(spacing=12, css_classes=["apply-bar"])
        self.status_icon = Gtk.Image(icon_name="security-high-symbolic")
        self.status = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END, wrap=False,
                                css_classes=["dim-label"])
        bar.append(self.status_icon)
        bar.append(self.status)
        self.revert_btn = Gtk.Button(label="Revert", css_classes=["pill", "flat"], sensitive=False)
        self.revert_btn.connect("clicked", lambda *_: self._revert())
        bar.append(self.revert_btn)
        self.apply_btn = Gtk.Button(css_classes=["pill", "suggested-action"])
        self.apply_spinner = Adw.Spinner(visible=False)
        apply_box = Gtk.Box(spacing=8)
        apply_box.append(self.apply_spinner)
        apply_box.append(Gtk.Label(label="Apply"))
        self.apply_btn.set_child(apply_box)
        self.apply_btn.connect("clicked", lambda *_: self.apply())
        bar.append(self.apply_btn)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.add_top_bar(self.banner)
        view.set_content(scroller)
        view.add_bottom_bar(bar)
        self.set_child(view)
        self._set_dirty(False)
        self._update_visibility()

    # ================================================================ tabs
    def _add_tab(self, name, title, icon, widget):
        self.stack.add_titled_with_icon(widget, name, title, icon)

    def _tab_box(self, *groups):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=12)
        for g in groups:
            if g is not None:
                box.append(g)
        return box

    def _background_tab(self):
        g = Adw.PreferencesGroup(title="Login Background",
                                 description="Shown behind the GDM login dialog.")
        self.mode_row = self._combo("bg_mode", "Type", BG_MODES)
        g.add(self.mode_row)

        self.image_row = Adw.ActionRow(title="Image", subtitle="No image selected")
        self.image_thumb = CoverPicture(72, 44, css_classes=["card"], valign=Gtk.Align.CENTER)
        self.image_row.add_prefix(self.image_thumb)
        lib_btn = Gtk.MenuButton(icon_name="folder-pictures-symbolic", tooltip_text="Choose from Library",
                                 valign=Gtk.Align.CENTER, css_classes=["flat"])
        self.lib_popover = Gtk.Popover()
        self.lib_popover.connect("show", lambda *_: self._fill_library_popover())
        lib_btn.set_popover(self.lib_popover)
        file_btn = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        file_btn.connect("clicked", lambda *_: self._choose_image("bg_image"))
        self.image_row.add_suffix(lib_btn)
        self.image_row.add_suffix(file_btn)
        g.add(self.image_row)
        self.blur_row = self._scale("bg_blur", "Blur", "Soften the image", 0, 100, 1)
        self.dim_row = self._scale("bg_dim", "Dim", "Darken for better contrast", 0, 90, 1)
        g.add(self.blur_row)
        g.add(self.dim_row)

        self.color_row = self._color("bg_color", "Color")
        self.color2_row = self._color("bg_color2", "Second Color")
        self.dir_row = self._combo("bg_gradient", "Direction", [("vertical", "Vertical"), ("horizontal", "Horizontal")])
        for r in (self.color_row, self.color2_row, self.dir_row):
            g.add(r)
        return self._tab_box(g)

    def _style_tab(self):
        g = Adw.PreferencesGroup(title="Appearance")
        if self.model.has("color_scheme"):
            dk = self.model.dkey("color_scheme")
            g.add(self._combo("color_scheme", "Style", [(v, _pretty(v)) for v in gm.enum_choices(dk)]))
        if self.model.has("accent_color"):
            g.add(self._accent_row())
        g.add(self._optional_color("text_color", "Custom Text Color", "Login dialog and lock screen clock"))

        t = Adw.PreferencesGroup(title="Themes &amp; Fonts",
                                 description="Only themes installed system-wide (in /usr/share) are available to GDM.")
        if self.model.has("font_name"):
            t.add(self._font("font_name", "Interface Font"))
        if self.model.has("text_scaling"):
            t.add(self._spin("text_scaling", "Text Scaling", 0.5, 3.0, 0.05, 2))
        if self.model.has("icon_theme"):
            t.add(self._combo("icon_theme", "Icon Theme", [(n, n) for n in gm.icon_themes()]))
        if self.model.has("cursor_theme"):
            t.add(self._combo("cursor_theme", "Cursor Theme", [(n, n) for n in gm.cursor_themes()]))
        if self.model.has("cursor_size"):
            t.add(self._combo("cursor_size", "Cursor Size", [(n, f"{n} px") for n in CURSOR_SIZES]))
        if self.model.has("enable_animations"):
            t.add(self._switch("enable_animations", "Animations"))
        return self._tab_box(g, t)

    def _clock_tab(self):
        g = Adw.PreferencesGroup(title="Top Bar")
        if self.model.has("clock_format"):
            g.add(self._combo("clock_format", "Time Format", [("24h", "24-hour"), ("12h", "AM / PM")]))
        for key, title in [("clock_show_date", "Show Date"), ("clock_show_weekday", "Show Weekday"),
                           ("clock_show_seconds", "Show Seconds"),
                           ("show_battery_percentage", "Show Battery Percentage")]:
            if self.model.has(key):
                g.add(self._switch(key, title))

        lock = Adw.PreferencesGroup(title="Lock Screen Clock",
                                    description="Applies to the big clock on the lock screen. "
                                                "Switch the preview to “Lock Screen” to see it.")
        self.clock_size_row = Adw.SpinRow.new_with_range(3.0, 14.0, 0.25)
        self.clock_size_row.set_title("Size")
        self.clock_size_row.set_subtitle("In em — GNOME's default is 6.5")
        self.clock_size_row.set_digits(2)
        self.clock_size_row.set_value(self.model.get("clock_size") or 6.5)
        self.clock_size_row.connect("notify::value", lambda r, _p: self._custom_clock_changed())
        self.clock_weight_row = Adw.ComboRow(title="Weight", model=Gtk.StringList.new(
            ["Thin", "Light", "Regular", "Medium", "Bold", "Heavy", "Black"]))
        self._weights = [200, 300, 400, 500, 700, 800, 900]
        w = self.model.get("clock_weight") or 800
        self.clock_weight_row.set_selected(self._weights.index(w) if w in self._weights else 5)
        self.clock_weight_row.connect("notify::selected", lambda *_: self._custom_clock_changed())
        self.clock_expander = Adw.ExpanderRow(title="Custom Clock Style", show_enable_switch=True,
                                              enable_expansion=bool(self.model.get("clock_size")))
        self.clock_expander.add_row(self.clock_size_row)
        self.clock_expander.add_row(self.clock_weight_row)
        self.clock_expander.connect("notify::enable-expansion", lambda *_: self._custom_clock_changed())
        lock.add(self.clock_expander)
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
        g = Adw.PreferencesGroup(title="Branding")
        self.logo_row = Adw.ActionRow(title="Logo", subtitle="Shown at the bottom of the login screen")
        self.logo_thumb = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, width_request=72, height_request=40,
                                      valign=Gtk.Align.CENTER)
        self.logo_row.add_prefix(self.logo_thumb)
        clear = Gtk.Button(icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                           tooltip_text="Remove Logo")
        clear.connect("clicked", lambda *_: (self.model.set("logo", ""), self._changed()))
        choose = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        choose.connect("clicked", lambda *_: self._choose_image("logo"))
        self.logo_row.add_suffix(clear)
        self.logo_row.add_suffix(choose)
        g.add(self.logo_row)

        if self.model.has("banner_enable"):
            exp = Adw.ExpanderRow(title="Welcome Message", subtitle="Text shown above the login prompt",
                                  show_enable_switch=True, enable_expansion=bool(self.model.get("banner_enable")))
            text = Adw.EntryRow(title="Message", text=self.model.get("banner_text") or "")
            text.connect("changed", lambda e: self._set("banner_text", e.get_text()))
            exp.add_row(text)
            exp.connect("notify::enable-expansion", lambda e, _p: self._set("banner_enable", e.get_enable_expansion()))
            self._rows_by_key["banner_enable"] = (exp, lambda v: exp.set_enable_expansion(bool(v)))
            self._rows_by_key["banner_text"] = (text, lambda v: text.set_text(v or ""))
            g.add(exp)

        b = Adw.PreferencesGroup(title="Behaviour")
        for key, title, sub in [("disable_user_list", "Hide User List", "Users must type their username"),
                                ("disable_restart_buttons", "Hide Power Buttons",
                                 "Remove restart and shut down from the login screen")]:
            if self.model.has(key):
                b.add(self._switch(key, title, sub))

        auth = Adw.PreferencesGroup(title="Authentication Methods")
        for key, title in [("password_auth", "Password"), ("fingerprint_auth", "Fingerprint"),
                           ("smartcard_auth", "Smart Card")]:
            if self.model.has(key):
                auth.add(self._switch(key, title))

        auto = Adw.PreferencesGroup(title="Automatic Login",
                                    description="Skips the login screen entirely on boot. Use with care.")
        users = gm.login_users()
        self.auto_exp = Adw.ExpanderRow(title="Log In Automatically", show_enable_switch=True,
                                        enable_expansion=bool(self.model.get("autologin_enable")))
        user_row = Adw.ComboRow(title="User", model=Gtk.StringList.new([f"{full} ({u})" if full != u else u
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
        auto.add(self._switch("wayland", "Use Wayland", "Turn off to force the X11 login screen"))
        return self._tab_box(g, b, auth, auto)

    def _system_tab(self):
        tp = Adw.PreferencesGroup(title="Touchpad &amp; Mouse")
        for key, title in [("tap_to_click", "Tap to Click"), ("natural_scroll", "Natural Scrolling")]:
            if self.model.has(key):
                tp.add(self._switch(key, title))
        if self.model.has("mouse_speed"):
            tp.add(self._scale("mouse_speed", "Mouse Speed", None, -1.0, 1.0, 0.05, digits=2))

        pw = Adw.PreferencesGroup(title="Power")
        for key, title in [("power_button", "Power Button Action"), ("sleep_ac", "When Idle (Plugged In)"),
                           ("sleep_battery", "When Idle (On Battery)")]:
            if self.model.has(key):
                pw.add(self._combo(key, title, [(v, _pretty(v)) for v in gm.enum_choices(self.model.dkey(key))]))
        if self.model.has("night_light"):
            pw.add(self._switch("night_light", "Night Light"))

        a11y = Adw.PreferencesGroup(title="Accessibility")
        for key, title in [("screen_keyboard", "Screen Keyboard"), ("screen_reader", "Screen Reader")]:
            if self.model.has(key):
                a11y.add(self._switch(key, title))
        return self._tab_box(tp, pw, a11y)

    # ================================================================ row builders
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
        inner = Adw.ActionRow(title="Color")
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
        row = Adw.ActionRow(title="Accent Color")
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

    # ================================================================ images
    def _choose_image(self, key):
        filt = Gtk.FileFilter(name="Images")
        exts = IMAGE_EXTS if key == "bg_image" else {".png", ".svg", ".jpg", ".jpeg", ".webp"}
        for ext in exts:
            filt.add_suffix(ext.lstrip("."))
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(filt)
        dlg = Gtk.FileDialog(title="Choose Logo" if key == "logo" else "Choose Background", filters=store)
        if key == "logo" and os.path.isdir("/usr/share/pixmaps"):
            dlg.set_initial_folder(Gio.File.new_for_path("/usr/share/pixmaps"))

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                if key == "bg_image":
                    self.set_background_image(f.get_path())
                else:
                    self.model.set("logo", f.get_path())
                    self._changed()

        dlg.open(self.window, None, done)

    def set_background_image(self, path):
        self.model.set("bg_image", path)
        self.model.set("bg_mode", "image")
        self.stack.set_visible_child_name("background")
        self._sync_rows()
        self._changed()

    def _fill_library_popover(self):
        lib = get_library()
        flow = Gtk.FlowBox(max_children_per_line=4, min_children_per_line=3, selection_mode=Gtk.SelectionMode.NONE,
                           column_spacing=8, row_spacing=8, margin_start=8, margin_end=8, margin_top=8,
                           margin_bottom=8, homogeneous=True)
        recs = [(e["title"], f["light"]) for e in lib.sorted_items() for f in e["files"][:4]][:48]
        if not recs:
            self.lib_popover.set_child(Gtk.Label(label="Your library is empty.\nInstall some wallpapers first!",
                                                 justify=Gtk.Justification.CENTER, margin_start=18, margin_end=18,
                                                 margin_top=18, margin_bottom=18))
            return
        for title, path in recs:
            pic = CoverPicture(128, 80, css_classes=["card"])
            net.run_async(local_thumbnail, lambda tex, err, p=pic: tex and p.set_paintable(tex), path, 256)
            btn = Gtk.Button(child=pic, css_classes=["flat"], tooltip_text=title)
            btn.connect("clicked", lambda _b, p=path: (self.lib_popover.popdown(), self.set_background_image(p)))
            flow.append(btn)
        self.lib_popover.set_child(Gtk.ScrolledWindow(child=flow, min_content_width=600, min_content_height=320,
                                                      hscrollbar_policy=Gtk.PolicyType.NEVER))

    # ================================================================ state
    def _changed(self):
        self._update_visibility()
        self.preview.refresh()
        self._set_dirty(True)

    def _set_dirty(self, dirty):
        self.dirty = dirty
        self.revert_btn.set_sensitive(dirty)
        if dirty:
            self.status_icon.set_from_icon_name("document-edit-symbolic")
            self.status.set_label("Unsaved changes — applying requires your password")
        else:
            self.status_icon.set_from_icon_name("security-high-symbolic")
            self.status.set_label("Originals are backed up and can be restored at any time")

    def _update_visibility(self):
        mode = self.model.get("bg_mode")
        for r in (self.image_row, self.blur_row, self.dim_row):
            r.set_visible(mode == "image")
        self.color_row.set_visible(mode in ("color", "gradient"))
        self.color_row.set_title("Start Color" if mode == "gradient" else "Color")
        self.color2_row.set_visible(mode == "gradient")
        self.dir_row.set_visible(mode == "gradient")
        img = self.model.get("bg_image")
        self.image_row.set_subtitle(Path(img).name if img else "No image selected")
        if img and os.path.exists(img) and getattr(self, "_thumb_for", None) != img:
            self._thumb_for = img
            net.run_async(local_thumbnail, lambda tex, err: tex and self.image_thumb.set_paintable(tex), img, 256)
        logo = self.model.get("logo")
        self.logo_row.set_subtitle(Path(logo).name if logo else "No logo")
        self.logo_thumb.set_file(Gio.File.new_for_path(logo) if logo else None)

    def _sync_rows(self):
        self._syncing = True
        try:
            for key, (_row, setter) in self._rows_by_key.items():
                if self.model.has(key):
                    setter(self.model.get(key))
            self.auto_exp.set_enable_expansion(bool(self.model.get("autologin_enable")))
            self.clock_expander.set_enable_expansion(bool(self.model.get("clock_size")))
        finally:
            self._syncing = False

    def _revert(self):
        self.model = gm.GdmModel()
        self.preview.model = self.model
        self._sync_rows()
        self._update_visibility()
        self.preview.refresh()
        self._set_dirty(False)

    def _check_environment(self):
        if not os.path.exists(gm.HELPER) or not shutil.which("pkexec"):
            self.banner.set_title("pkexec is not available — changes can't be applied")
            self.banner.set_revealed(True)
        elif not (shutil.which("gdm") or os.path.exists("/usr/sbin/gdm3") or os.path.exists("/usr/sbin/gdm")):
            self.banner.set_title("GDM doesn't appear to be installed on this system")
            self.banner.set_revealed(True)
        elif self.model.store.get("applied_theme") and not self._theme_is_ours():
            self.banner.set_title("A system update reset your login screen theme")
            self.banner.set_button_label("Re-apply")
            self.banner.set_revealed(True)

    @staticmethod
    def _theme_is_ours():
        try:
            res = Gio.Resource.load("/usr/share/gnome-shell/gnome-shell-theme.gresource")
            res.lookup_data("/org/gnome/shell/theme/orbits.marker", Gio.ResourceLookupFlags.NONE)
            return True
        except GLib.Error:
            return False

    # ================================================================ apply
    def apply(self):
        bg_png = None
        if self.model.get("bg_mode") == "image":
            src = self.model.get("bg_image")
            if not src or not os.path.exists(src):
                self.window.toast("Choose a background image first")
                return
            bg_png = str(gm.EXPORT_DIR / "background.jpg")
            try:
                try:
                    tex = Gdk.Texture.new_from_filename(src)
                except GLib.Error:  # formats only gdk-pixbuf loaders understand
                    tex = Gdk.Texture.new_for_pixbuf(GdkPixbuf.Pixbuf.new_from_file(src))
                export_background(self.window.get_renderer(), tex, self.model.get("bg_blur", 0),
                                  self.model.get("bg_dim", 0), bg_png)
            except GLib.Error as e:
                self.window.toast(f"Couldn't render background: {e.message}")
                return
        try:
            spec = self.model.write_spec(bg_png)
        except OSError as e:
            self.window.toast(f"Couldn't prepare settings: {e}")
            return
        self._run_helper(["apply", str(spec)], self._applied)

    def _applied(self, ok, msg):
        if ok:
            self.model.set("applied_theme", bool(self.model.build_css()))
            self._set_dirty(False)
            self.banner.set_revealed(False)
            self.window.toast("Login screen updated — log out to see it", timeout=5)

    def _run_helper(self, args, on_done):
        from orbits.privileged import run_helper

        self.apply_btn.set_sensitive(False)
        self.apply_spinner.set_visible(True)
        run_helper(args, lambda ok, msg: self._helper_done(ok, msg, on_done))

    def _helper_done(self, ok, msg, on_done):
        self.apply_btn.set_sensitive(True)
        self.apply_spinner.set_visible(False)
        if not ok:
            self.window.toast(msg, timeout=6)
        on_done(ok, msg)

    def _confirm_reset(self):
        dlg = Adw.AlertDialog(
            heading="Reset Login Screen?",
            body="This restores the original GNOME Shell theme, removes all Orbits GDM settings and disables "
                 "automatic login. Settings made by other tools are left alone.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("reset", "Reset")
        dlg.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "reset" and self._run_helper(["reset"], self._reset_done))
        dlg.present(self.window)

    def _reset_done(self, ok, _msg):
        if ok:
            self.model.reset_theme_values()
            self.model.set("applied_theme", False)
            self._revert()
            self.window.toast("Login screen restored to defaults")

    def _open_backup(self):
        Gtk.FileLauncher.new(Gio.File.new_for_path("/usr/share/gnome-shell")).launch(self.window, None, None)
