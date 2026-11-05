"""App Colors page: pick or design a palette, preview it live, apply it to libadwaita apps."""
from orbits.i18n import _ as tr
import copy
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from orbits import colors, net
from orbits import palettes

PREVIEW_CLASS = "orbits-color-preview"


def _hex(rgba):
    return "#{:02x}{:02x}{:02x}".format(*(round(c * 255) for c in (rgba.red, rgba.green, rgba.blue)))


def _rgba(h):
    c = Gdk.RGBA()
    c.parse(h or "#000000")
    return c


class ColorsPage(Adw.Bin):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.title = "App Colors"
        self.state = colors.load_state()
        self.variant = "dark" if colors.prefers_dark() else "light"
        self._syncing = False
        self._provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self._provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER)

        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=tr("App Colors"),
                                                            subtitle=tr("Recolor libadwaita apps")))
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_start=24, margin_end=24,
                       margin_top=12, margin_bottom=24)

        toggle = Adw.ToggleGroup(halign=Gtk.Align.CENTER)
        toggle.add(Adw.Toggle(name="light", label=tr("Light"), icon_name="weather-clear-symbolic"))
        toggle.add(Adw.Toggle(name="dark", label=tr("Dark"), icon_name="weather-clear-night-symbolic"))
        toggle.set_active_name(self.variant)
        toggle.connect("notify::active-name", lambda t, _p: self._set_variant(t.get_active_name()))
        body.append(toggle)
        body.append(self._preview())
        wallpaper = Adw.PreferencesGroup(title=tr("Wallpaper Colors"))
        for title, callback in (("Generate from Current Wallpaper", self._current_wallpaper),
                                ("Generate from Image…", self._choose_image)):
            row = Adw.ButtonRow(title=title, start_icon_name="image-x-generic-symbolic")
            row.connect("activated", lambda _r, cb=callback: cb())
            wallpaper.add(row)
        self.accent = Adw.SwitchRow(title=tr("Match GNOME Accent"), active=bool(self.state.get("gnome_accent")),
                                   subtitle=tr("Choose the closest system accent when applying colors"))
        self.accent.connect("notify::active", lambda r,_p: self.state.__setitem__("gnome_accent", r.get_active()))
        wallpaper.add(self.accent)
        body.append(wallpaper)

        self.presets = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=5,
                                   min_children_per_line=2, column_spacing=10, row_spacing=10, homogeneous=True)
        self.available_presets = {**colors.PRESETS, **colors.gallery()}
        for name, pal in self.available_presets.items():
            self.presets.append(self._preset_button(name, pal))
        pgroup = Adw.PreferencesGroup(title=tr("Presets"))
        pgroup.add(self.presets)
        body.append(pgroup)
        community = Adw.PreferencesGroup(title=tr("Community Palettes"),
            description=tr("Import Orbits JSON or a Gradience preset. Imported palettes appear in the gallery above."))
        for title, callback in (("Import Palette…", self._import_palette), ("Save Palette to Gallery", self._save_palette),
                                ("Browse Gradience Presets", self._browse_presets)):
            row = Adw.ButtonRow(title=title)
            row.connect("activated", lambda _r, cb=callback: cb())
            community.add(row)
        body.append(community)

        self.colors_group = Adw.PreferencesGroup(title=tr("Colors"))
        self.use_variant = Adw.SwitchRow(title=tr("Customize This Style"),
                                         subtitle=tr("Off keeps libadwaita's default colors for this style"))
        self.use_variant.connect("notify::active", self._toggle_variant)
        self.colors_group.add(self.use_variant)
        self.buttons = {}
        for role in colors.ROLE_ORDER:
            label = colors.ROLES[role][0]
            row = Adw.ActionRow(title=label)
            btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
            btn.connect("notify::rgba", self._on_color, role)
            row.add_suffix(btn)
            row.set_activatable_widget(btn)
            self.buttons[role] = (row, btn)
            self.colors_group.add(row)
        body.append(self.colors_group)

        opts = Adw.PreferencesGroup(title=tr("Apply To"))
        opts.add(Adw.ActionRow(title=tr("Libadwaita &amp; GTK4 apps"), subtitle=tr("~/.config/gtk-4.0/gtk.css · "
                                                                       "restart apps to see changes")))
        self.gtk3_row = Adw.SwitchRow(title=tr("GTK3 apps (adw-gtk3)"), subtitle=tr("Requires the adw-gtk3 theme; uses "
                                      "the palette of your current style"), active=bool(self.state.get("gtk3")))
        self.gtk3_row.connect("notify::active", lambda r, _p: self.state.__setitem__("gtk3", r.get_active()))
        opts.add(self.gtk3_row)
        self.flatpak_row = Adw.SwitchRow(title=tr("Flatpak apps"), subtitle=tr("Grants Flatpaks read-only access to "
                                         "your GTK config folders"), active=bool(self.state.get("flatpak")))
        self.flatpak_row.connect("notify::active", lambda r, _p: self.state.__setitem__("flatpak", r.get_active()))
        opts.add(self.flatpak_row)
        login = Adw.ActionRow(activatable=True, title=tr("Match Login Screen Colors"), subtitle=tr("Open the login editor with this accent and text color; press Apply there to save"))
        login.connect("activated", lambda *_: self._match_login())
        opts.add(login)
        body.append(opts)

        exports = Adw.PreferencesGroup(title=tr("Export Colors"), description=tr("Create importable color files for your selected style. Existing application settings are kept."))
        self.export_rows = {}
        for target, label in palettes.EXPORT_TARGETS.items():
            row = Adw.SwitchRow(title=label, active=target in self.state.get("export_targets", []))
            exports.add(row)
            self.export_rows[target] = row
        export = Adw.ActionRow(activatable=True, title=tr("Export Selected…"), subtitle=tr("Choose a folder for Kitty, Alacritty, Ptyxis, VS Code and Firefox color files"))
        export.connect("activated", lambda *_: self._export())
        exports.add(export)
        body.append(exports)

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                      child=Adw.Clamp(maximum_size=860, child=body))

        bar = Gtk.Box(spacing=12, css_classes=["apply-bar"])
        self.status = Gtk.Label(xalign=0, hexpand=True, css_classes=["dim-label"], ellipsize=3)
        bar.append(self.status)
        self.remove_btn = Gtk.Button(label=tr("Remove"), css_classes=["pill", "flat"])
        self.remove_btn.connect("clicked", lambda *_: self._remove())
        bar.append(self.remove_btn)
        apply = Gtk.Button(label=tr("Apply"), css_classes=["pill", "suggested-action"])
        apply.connect("clicked", lambda *_: self._apply())
        bar.append(apply)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(scroller)
        view.add_bottom_bar(bar)
        self.set_child(view)

        Gio.Settings.new("org.gnome.desktop.interface").connect(
            "changed::color-scheme", lambda *_: colors.refresh_gtk3_for_scheme())
        self._sync()

    # ---------------------------------------------------------------- preview
    def _preview(self):
        win = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=[PREVIEW_CLASS, "color-preview-frame"],
                      overflow=Gtk.Overflow.HIDDEN, height_request=300)
        hb = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=tr("Preview"), subtitle=tr("Your apps")),
                           show_start_title_buttons=False, show_end_title_buttons=False)
        hb.pack_start(Gtk.Button(icon_name="go-previous-symbolic", tooltip_text=tr("Back")))
        hb.pack_end(Gtk.Button(icon_name="open-menu-symbolic", tooltip_text=tr("Preview Menu")))
        win.append(hb)
        split = Gtk.Box(vexpand=True)
        side = Gtk.ListBox(css_classes=["navigation-sidebar", "color-preview-sidebar"], width_request=170)
        for icon, label in [("user-home-symbolic", "Home"), ("starred-symbolic", "Starred"),
                            ("folder-download-symbolic", "Downloads"), ("user-trash-symbolic", "Trash")]:
            b = Gtk.Box(spacing=10, margin_top=6, margin_bottom=6, margin_start=6)
            b.append(Gtk.Image(icon_name=icon))
            b.append(Gtk.Label(label=label))
            side.append(b)
        side.select_row(side.get_row_at_index(0))
        split.append(side)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True, margin_start=18,
                          margin_end=18, margin_top=14, margin_bottom=14, css_classes=["color-preview-view"])
        lb = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        r1 = Adw.SwitchRow(title=tr("Wi-Fi"), subtitle=tr("Connected"), active=True)
        r2 = Adw.ActionRow(title=tr("Bluetooth"), subtitle=tr("Off"))
        r2.add_suffix(Gtk.CheckButton(active=True, valign=Gtk.Align.CENTER))
        lb.append(r1)
        lb.append(r2)
        content.append(lb)
        content.append(Gtk.Entry(placeholder_text=tr("Search…")))
        btns = Gtk.Box(spacing=8)
        btns.append(Gtk.Button(label=tr("Cancel")))
        btns.append(Gtk.Button(label=tr("Save"), css_classes=["suggested-action"]))
        btns.append(Gtk.Button(label=tr("Delete"), css_classes=["destructive-action"]))
        link = Gtk.Label(label=tr("Accent text"), css_classes=["accent"], hexpand=True, xalign=1)
        btns.append(link)
        content.append(btns)
        split.append(content)
        win.append(split)
        return win

    def _update_preview(self):
        variant = (self.state["palette"] or {}).get(self.variant) or {}
        css = colors.preview_css(f".{PREVIEW_CLASS}", variant)
        css += (f".{PREVIEW_CLASS} .color-preview-sidebar {{ background-color: var(--sidebar-bg-color); }}\n"
                f".{PREVIEW_CLASS} .color-preview-view {{ }}\n"
                f".{PREVIEW_CLASS} headerbar {{ background-color: var(--headerbar-bg-color); "
                f"color: var(--headerbar-fg-color); }}\n")
        self._provider.load_from_string(css)

    def _preset_button(self, name, pal):
        b = Gtk.Button(css_classes=["flat", "preset-chip"], tooltip_text=name)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6)
        dots = Gtk.Box(spacing=3, halign=Gtk.Align.CENTER)
        v = pal.get("dark") or pal.get("light")
        for role in ("window_bg", "view_bg", "accent", "window_fg"):
            d = Gtk.Box(css_classes=["preset-dot"])
            prov = Gtk.CssProvider()
            prov.load_from_string(f"box {{ background-color: {v[role]}; }}")
            d.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
            dots.append(d)
        box.append(dots)
        box.append(Gtk.Label(label=name, css_classes=["caption-heading"]))
        b.set_child(box)
        b.connect("clicked", lambda *_: self._choose_preset(name))
        return b

    # ---------------------------------------------------------------- editing
    def _choose_preset(self, name):
        self.state["preset"] = name
        self.state["palette"] = copy.deepcopy(self.available_presets[name])
        self._sync()

    def _set_variant(self, v):
        self.variant = v
        self._sync()

    def _toggle_variant(self, row, _p):
        if self._syncing:
            return
        pal = self.state["palette"]
        if row.get_active() and not pal.get(self.variant):
            other = pal.get("dark" if self.variant == "light" else "light")
            base = colors.PRESETS["Adwaita"][self.variant]
            pal[self.variant] = dict(base, accent=(other or base)["accent"])
        elif not row.get_active():
            pal[self.variant] = None
        self.state["preset"] = "Custom"
        self._sync()

    def _on_color(self, btn, _p, role):
        if self._syncing:
            return
        pal = self.state["palette"].get(self.variant)
        if pal is None:
            return
        pal[role] = _hex(btn.get_rgba())
        self.state["preset"] = "Custom"
        self._update_preview()
        self._status()

    def _sync(self):
        self._syncing = True
        try:
            variant = self.state["palette"].get(self.variant)
            self.use_variant.set_active(bool(variant))
            self.colors_group.set_description(tr('{value0} style · preset: {value1}').format(value0=self.variant.title(), value1=self.state.get('preset')))
            for role, (row, btn) in self.buttons.items():
                row.set_sensitive(bool(variant))
                if variant and variant.get(role):
                    btn.set_rgba(_rgba(variant[role]))
        finally:
            self._syncing = False
        self._update_preview()
        self._status()

    def _status(self):
        applied = colors.is_applied()
        self.remove_btn.set_visible(applied)
        kind, target = colors.gtk4_conflict()
        if kind == "link":
            self.status.set_label(tr("Applying replaces the GTK4 theme currently linked into ~/.config/gtk-4.0"))
        elif kind == "file":
            self.status.set_label(tr("Your own gtk.css will be backed up to ~/.config/gtk-4.0/orbits-backup"))
        else:
            self.status.set_label("Applied · restart apps to see changes" if applied
                                  else "Not applied yet")

    def _apply(self):
        try:
            notes = colors.apply(self.state)
        except Exception as error:
            self.window.toast(str(error), timeout=6)
            return
        self.window.toast(" · ".join(notes + ["App colors applied — restart apps to see them"]), timeout=5)
        self._status()

    def _remove(self):
        colors.remove()
        self.state = colors.load_state()
        self.window.toast(tr("App colors removed"))
        self._status()

    def from_wallpaper(self, path):
        self.window.toast(tr("Generating a palette from the wallpaper…"))
        def done(palette, error):
            if error:
                self.window.toast(str(error), timeout=6)
                return
            self.state["palette"] = palette
            self.state["preset"] = "Wallpaper"
            self.state["gnome_accent"] = True
            self.accent.set_active(True)
            self._sync()
        net.run_async(lambda: palettes.from_wallpaper(path), done)

    def _current_wallpaper(self):
        from orbits.library import get_library
        lib = get_library()
        dark = colors.prefers_dark()
        uri = Gio.Settings.new("org.gnome.desktop.background").get_string("picture-uri-dark" if dark else "picture-uri")
        path = Gio.File.new_for_uri(uri).get_path() if uri else None
        if path and Path(path).suffix == ".xml":
            path = next((rec.get("preview") for e in lib.items.values() for rec in e["files"] if rec["light"] == path), None)
        if not path:
            self.window.toast(tr("Choose an image to generate a palette"))
            return
        self.from_wallpaper(path)

    def _choose_image(self):
        dialog = Gtk.FileDialog(title=tr("Choose Wallpaper for Colors"))
        def done(d, result):
            try:
                file = d.open_finish(result)
                path = file.get_path() if file else None
                if path:
                    self.from_wallpaper(path)
            except GLib.Error:
                pass
        dialog.open(self.window, None, done)

    def _import_palette(self):
        filt = Gtk.FileFilter(name="Palette JSON")
        filt.add_pattern("*.json")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dialog = Gtk.FileDialog(title=tr("Import Palette"), filters=filters)
        def done(d, result):
            try:
                file = d.open_finish(result)
            except GLib.Error:
                return
            try:
                if not file or not file.get_path():
                    return
                name, palette = palettes.import_palette(file.get_path(), colors.PRESETS["Adwaita"])
                colors.save_palette(name, palette)
                self.available_presets[name] = palette
                self.presets.append(self._preset_button(name, palette))
                self._choose_preset(name)
                self.window.toast(tr('Imported palette “{value0}”').format(value0=name))
            except Exception as error:
                self.window.toast(str(error), timeout=6)
        dialog.open(self.window, None, done)

    def _save_palette(self):
        dialog = Adw.AlertDialog(heading=tr("Save Palette"))
        name = Gtk.Entry(text=self.state.get("preset", "Custom"), placeholder_text=tr("Palette name"))
        dialog.set_extra_child(name)
        dialog.add_response("cancel", tr("Cancel"))
        dialog.add_response("save", tr("Save"))
        def response(_d, result):
            if result != "save":
                return
            try:
                title = name.get_text().strip() or "Custom"
                colors.save_palette(title, self.state["palette"])
                self.available_presets[title] = copy.deepcopy(self.state["palette"])
                self.presets.append(self._preset_button(title, self.state["palette"]))
                self.window.toast(tr("Palette saved to gallery"))
            except Exception as error:
                self.window.toast(str(error), timeout=6)
        dialog.connect("response", response)
        dialog.present(self.window)

    def _browse_presets(self):
        from orbits.ui.widgets import open_uri
        open_uri(self.window, "https://github.com/GradienceTeam/Community")

    def _match_login(self):
        variant = self.state["palette"].get(self.variant)
        if not variant:
            self.window.toast(tr("Customize this style first"))
            return
        self.window.navigate("gdm")
        self.window.pages["gdm"].load_values({"accent_color": palettes.nearest_accent(variant["accent"]),
                                             "text_color": variant["window_fg"]})
        self.window.toast(tr("Palette loaded in the login editor — press Apply to save"))

    def _export(self):
        targets = [target for target, row in self.export_rows.items() if row.get_active()]
        if not targets:
            self.window.toast(tr("Select at least one export target"))
            return
        try:
            palettes.export_files(self.state["palette"], self.variant)
        except ValueError as error:
            self.window.toast(str(error), timeout=6)
            return
        dialog = Gtk.FileDialog(title=tr("Export Palette Colors"))
        def done(d, result):
            try:
                folder = d.select_folder_finish(result)
            except GLib.Error:
                return
            if not folder or not folder.get_path():
                return
            try:
                files = palettes.export_selected(self.state["palette"], self.variant, targets, folder.get_path())
                self.state["export_targets"] = targets
                self.window.toast(tr('Exported {value0} color files').format(value0=len(files)), timeout=6)
            except Exception as error:
                self.window.toast(str(error), timeout=6)
        dialog.select_folder(self.window, None, done)

    def current_state(self):
        return self.state
