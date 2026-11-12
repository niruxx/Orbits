"""Login Screen (GDM) customization page."""
from orbits.i18n import _ as tr
import os
import shutil
import copy
import tempfile
from pathlib import Path

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from orbits.gdm import model as gm
from orbits.library import IMAGE_EXTS, get_library
from orbits.ui.gdm_preview import GdmPreview
from orbits.gdm.backgrounds import render_backgrounds
from orbits import net
from orbits.ui.widgets import CoverPicture, local_thumbnail, load_optional_texture_into

from orbits.ui.gdm_controls import GdmControls


class GdmPage(GdmControls, Adw.Bin):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.title = "Login Screen"
        self.model = gm.GdmModel()
        self.dirty = False
        self._syncing = False
        self._rows_by_key = {}

        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=tr("Login Screen"),
                                                            subtitle=tr("Customize GDM & the lock screen")))
        menu = Gio.Menu()
        menu.append(tr("Reset Everything to Defaults…"), "gdm.reset")
        menu.append(tr("Open Theme Backup Folder"), "gdm.backup")
        self.greeter_btn = Gtk.Button(label=tr("Preview Greeter"), tooltip_text=tr("Open the real greeter in a nested window"))
        self.greeter_btn.connect("clicked", lambda *_: self._preview_greeter())
        header.pack_start(self.greeter_btn)
        header.pack_end(Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, tooltip_text=tr("More")))
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
        mode_toggle.add(Adw.Toggle(name="login", label=tr("Login Screen")))
        mode_toggle.add(Adw.Toggle(name="lock", label=tr("Lock Screen")))
        mode_toggle.set_active_name("login")
        mode_toggle.connect("notify::active-name", lambda t, _p: self.preview.set_mode(t.get_active_name()))

        # ---- settings tabs
        self.stack = Adw.ViewStack(vhomogeneous=False)
        self._add_tab("background", tr("Background"), "preferences-desktop-wallpaper-symbolic", self._background_tab())
        self._add_tab("style", tr("Style"), "preferences-desktop-appearance-symbolic", self._style_tab())
        self._add_tab("clock", tr("Clock"), "preferences-system-time-symbolic", self._clock_tab())
        self._add_tab("login", tr("Login"), "system-users-symbolic", self._login_tab())
        self._add_tab("system", tr("System"), "preferences-system-symbolic", self._system_tab())
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
        self.revert_btn = Gtk.Button(label=tr("Revert"), css_classes=["pill", "flat"], sensitive=False)
        self.revert_btn.connect("clicked", lambda *_: self._revert())
        bar.append(self.revert_btn)
        self.apply_btn = Gtk.Button(css_classes=["pill", "suggested-action"])
        self.apply_spinner = Adw.Spinner(visible=False)
        apply_box = Gtk.Box(spacing=8)
        apply_box.append(self.apply_spinner)
        apply_box.append(Gtk.Label(label=tr("Apply")))
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


    # ================================================================ row builders


    # ================================================================ images
    def _choose_image(self, key):
        filt = Gtk.FileFilter(name="Images")
        exts = IMAGE_EXTS if key.startswith("bg_image") else {".png", ".svg", ".jpg", ".jpeg", ".webp"}
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

    def load_values(self, values):
        """Load settings from a Look into the editor (the user still presses Apply)."""
        for k, v in values.items():
            if k in gm.THEME_DEFAULTS or self.model.dkey(k) is not None:
                self.model.set(k, v)
        self._sync_rows()
        self._changed()

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
            self.lib_popover.set_child(Gtk.Label(label=tr("Your library is empty.\nInstall some wallpapers first!"),
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
            self.status.set_label(tr("Unsaved changes — applying requires your password"))
        else:
            self.status_icon.set_from_icon_name("security-high-symbolic")
            self.status.set_label(tr("Originals are backed up and can be restored at any time"))

    def _update_visibility(self):
        mode = self.model.get("bg_mode")
        for r in (self.image_row, self.blur_row, self.dim_row):
            r.set_visible(mode == "image")
        self.color_row.set_visible(mode in ("color", "gradient"))
        self.color_row.set_title("Start Color" if mode == "gradient" else "Color")
        self.color2_row.set_visible(mode == "gradient")
        self.dir_row.set_visible(mode == "gradient")
        for key, row in self.variant_rows:
            row.set_visible(mode == "image")
            row.set_subtitle(Path(self.model.get(key)).name if self.model.get(key) else "Use the main image")
        self.monitor_group.set_visible(mode == "image")
        img = self.model.get("bg_image")
        self.image_row.set_subtitle(Path(img).name if img else "No image selected")
        if img and os.path.exists(img) and getattr(self, "_thumb_for", None) != img:
            self._thumb_for = img
            net.run_async(local_thumbnail, lambda tex, err: tex and self.image_thumb.set_paintable(tex), img, 256)
        logo = self.model.get("logo")
        self.logo_row.set_subtitle(Path(logo).name if logo else "No logo")
        load_optional_texture_into(self.logo_thumb, logo, size=256)

    def _sync_rows(self):
        self._syncing = True
        try:
            for key, (_row, setter) in self._rows_by_key.items():
                if self.model.has(key) or key == "panel_opacity_enabled":
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
            self.banner.set_title(tr("pkexec is not available — changes can't be applied"))
            self.banner.set_revealed(True)
        elif __import__("orbits.system", fromlist=["usr_read_only"]).usr_read_only():
            self.banner.set_title(tr("This system's /usr is read-only, so the login screen can't be changed"))
            self.banner.set_revealed(True)
        elif not (shutil.which("gdm") or os.path.exists("/usr/sbin/gdm3") or os.path.exists("/usr/sbin/gdm")):
            self.banner.set_title(tr("GDM doesn't appear to be installed on this system"))
            self.banner.set_revealed(True)
        elif self.model.store.get("applied_theme") and not self._theme_is_ours():
            self.banner.set_title(tr("A system update reset your login screen theme"))
            self.banner.set_button_label("Re-apply")
            self.banner.set_revealed(True)

    @staticmethod
    def _theme_is_ours():
        from orbits.gdm import helper

        try:
            return helper.is_ours(helper.active_theme_file())
        except (OSError, GLib.Error):
            return False

    # ================================================================ apply
    def apply(self):
        # Snapshot before rendering so subsequent edits cannot change an in-flight apply.
        model = copy.copy(self.model)
        model.values = copy.deepcopy(self.model.values)
        self.apply_btn.set_sensitive(False)
        self.apply_spinner.set_visible(True)

        def work():
            backgrounds = render_backgrounds(model.values, gm.EXPORT_DIR)
            return model.write_spec(backgrounds)

        def ready(spec, err):
            if err:
                self.apply_btn.set_sensitive(True)
                self.apply_spinner.set_visible(False)
                self.window.toast(tr("Couldn't prepare login settings: {value0}").format(value0=err), timeout=6)
                return
            self._applied_values = model.values
            self._run_helper(["apply", str(spec)], self._applied)

        net.run_async(work, ready)

    def _preview_greeter(self):
        from orbits.gdm.preview import PreviewSession

        self.greeter_btn.set_sensitive(False)
        model = copy.copy(self.model)
        model.values = copy.deepcopy(self.model.values)

        def work():
            directory = tempfile.TemporaryDirectory(prefix="orbits-greeter-")
            try:
                images = render_backgrounds(model.values, directory.name)
                spec = model.build_spec(images)
                return PreviewSession(spec, directory)
            except Exception:
                directory.cleanup()
                raise

        def ready(session, err):
            if err:
                self.greeter_btn.set_sensitive(True)
                self.window.toast(str(err), timeout=8)
                return
            dialog = Adw.AlertDialog(heading=tr("Greeter Preview"),
                body=tr("The greeter is running in a separate window using your draft settings. "
                     "Authentication is unavailable in this preview. Close it here when finished."))
            dialog.add_response("stop", tr("Close Preview"))
            dialog.connect("response", lambda *_: session.stop())
            app = self.window.get_application()
            shutdown = app.connect("shutdown", lambda *_: session.stop())
            dialog.present(self.window)

            def finished(_result, error):
                app.disconnect(shutdown)
                dialog.close()
                self.greeter_btn.set_sensitive(True)
                if error:
                    self.window.toast(str(error), timeout=8)
            net.run_async(session.wait, finished)

        net.run_async(work, ready)

    def _configure_monitors(self):
        dialog = Adw.Dialog(title=tr("Login Backgrounds by Monitor"), content_width=600, content_height=520)
        group = Adw.PreferencesGroup(description=tr("Choose light and dark images for each connected display."))
        records = []
        layouts = set()
        saved = {r.get("connector"): r for r in self.model.get("bg_monitors", [])}
        monitors = Gdk.Display.get_default().get_monitors()
        for i in range(monitors.get_n_items()):
            monitor = monitors.get_item(i)
            geometry = monitor.get_geometry()
            layout = (geometry.x, geometry.y, geometry.width, geometry.height)
            if layout in layouts:  # Mirrored outputs share a single background.
                continue
            layouts.add(layout)
            connector = monitor.get_connector() or f"monitor-{i}"
            old = saved.get(connector, {})
            record = {"connector": connector, "name": monitor.get_model() or connector,
                      "x": geometry.x, "y": geometry.y, "width": geometry.width, "height": geometry.height,
                      "light": old.get("light", ""), "dark": old.get("dark", "")}
            records.append(record)
            for variant in ("light", "dark"):
                row = Adw.ActionRow(title=tr('{value0} — {value1}').format(value0=record['name'], value1=variant.title()),
                                    subtitle=Path(record[variant]).name if record[variant] else "Use global image")
                choose = Gtk.Button(label=tr("Choose…"), valign=Gtk.Align.CENTER)

                def select(_button, rec=record, key=variant, target=row):
                    filt = Gtk.FileFilter(name="Images")
                    for ext in IMAGE_EXTS:
                        filt.add_suffix(ext.lstrip("."))
                    filters = Gio.ListStore.new(Gtk.FileFilter)
                    filters.append(filt)
                    picker = Gtk.FileDialog(title=tr("Choose Login Background"), filters=filters)
                    def picked(d, result):
                        try:
                            file = d.open_finish(result)
                            if file and file.get_path():
                                rec[key] = file.get_path()
                                target.set_subtitle(Path(rec[key]).name)
                        except GLib.Error:
                            pass
                    picker.open(self.window, None, picked)
                choose.connect("clicked", select)
                clear = Gtk.Button(icon_name="edit-clear-symbolic", tooltip_text=tr("Use global image"),
                                   valign=Gtk.Align.CENTER)
                clear.connect("clicked", lambda _b, rec=record, key=variant, r=row:
                              (rec.update({key: ""}), r.set_subtitle(tr("Use global image"))))
                row.add_suffix(clear)
                row.add_suffix(choose)
                group.add(row)
        save = Gtk.Button(label=tr("Use This Layout"), css_classes=["suggested-action", "pill"])
        save.connect("clicked", lambda *_: (self._set("bg_monitors", records), dialog.close()))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_start=18, margin_end=18,
                      margin_top=18, margin_bottom=18)
        box.append(group)
        box.append(save)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(Gtk.ScrolledWindow(child=box, hscrollbar_policy=Gtk.PolicyType.NEVER))
        dialog.set_child(view)
        dialog.present(self.window)

    def _applied(self, ok, msg):
        if ok:
            applied_theme = bool(self._applied_values.get("bg_mode") != "default" or
                                 self._applied_values.get("text_color") or
                                 self._applied_values.get("clock_size") or
                                 self._applied_values.get("clock_weight") or
                                 self._applied_values.get("clock_font") or
                                 self._applied_values.get("panel_opacity", -1) >= 0)
            self.model.set("applied_theme", applied_theme)
            self._applied_values["applied_theme"] = applied_theme
            self._set_dirty(self.model.values != self._applied_values)
            self.banner.set_revealed(False)
            self.window.toast(tr("Login screen updated — log out to see it"), timeout=5)

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
            heading=tr("Reset Login Screen?"),
            body=tr("This restores the original GNOME Shell theme, removes all Orbits GDM settings and disables "
                 "automatic login. Settings made by other tools are left alone."))
        dlg.add_response("cancel", tr("Cancel"))
        dlg.add_response("reset", tr("Reset"))
        dlg.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "reset" and self._run_helper(["reset"], self._reset_done))
        dlg.present(self.window)

    def _reset_done(self, ok, _msg):
        if ok:
            self.model.reset_theme_values()
            self.model.set("applied_theme", False)
            self._revert()
            self.window.toast(tr("Login screen restored to defaults"))

    def _open_backup(self):
        Gtk.FileLauncher.new(Gio.File.new_for_path("/usr/share/gnome-shell")).launch(self.window, None, None)
