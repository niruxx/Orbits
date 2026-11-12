"""Looks page: saved desktop setups, history snapshots, import/export."""
from orbits.i18n import _ as tr
import time
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from orbits import looks, net
from orbits.ui.widgets import CoverPicture, load_texture_into
from orbits.ui.item_grid import ItemGrid


def _when(ts):
    d = time.time() - ts
    if d < 3600:
        return f"{max(1, int(d // 60))} min ago"
    if d < 86400:
        return f"{int(d // 3600)} h ago"
    return time.strftime("%b %-d, %Y", time.localtime(ts))


class LookCard(Gtk.Box):
    def __init__(self, page, look):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["theme-card"], width_request=300)
        self.page, self.look = page, look
        hero = Gtk.Overlay(css_classes=["theme-preview"], overflow=Gtk.Overflow.HIDDEN, height_request=170)
        pic = CoverPicture(300, 170, hexpand=True, vexpand=True)
        hero.set_child(pic)
        wp = look.get("screenshot") or looks.preview_path(look)
        if wp and Path(wp).is_file():
            load_texture_into(pic, path=wp, size=600)
        dots = Gtk.Box(spacing=4, halign=Gtk.Align.END, valign=Gtk.Align.END, margin_end=10, margin_bottom=10)
        ac = look.get("app_colors")
        variant = ac and (ac["palette"].get("dark") or ac["palette"].get("light"))
        if variant:
            for role in ("window_bg", "accent", "window_fg"):
                if variant.get(role):
                    d = Gtk.Box(css_classes=["preset-dot"])
                    prov = Gtk.CssProvider()
                    prov.load_from_string(f"box {{ background-color: {variant[role]}; }}")
                    d.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
                    dots.append(d)
            hero.add_overlay(dots)
        self.append(hero)

        info = Gtk.Box(spacing=8, margin_start=14, margin_end=10, margin_top=10, margin_bottom=10)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        text.append(Gtk.Label(label=look.get("name", "Look"), xalign=0, ellipsize=3, max_width_chars=24,
                              css_classes=["heading"]))
        text.append(Gtk.Label(label=looks.summary(look) or _when(look.get("created", 0)), xalign=0, ellipsize=3,
                              max_width_chars=34, css_classes=["dim-label", "caption"]))
        info.append(text)
        apply = Gtk.Button(label=tr("Apply"), valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
        apply.connect("clicked", lambda *_: page.apply(look))
        info.append(apply)
        info.append(self._menu())
        self.append(info)

    def _menu(self):
        btn = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text=tr("Look Actions"), css_classes=["flat"], valign=Gtk.Align.CENTER)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        pop = Gtk.Popover(child=box)
        for label, cb in [("Update to Current Desktop", lambda: self.page.overwrite(self.look)),
                          ("Rename…", lambda: self.page.rename(self.look)),
                          ("Export…", lambda: self.page.export(self.look)),
                          ("Share Gallery Bundle…",lambda:self.page.share(self.look)),
                          ("Delete", lambda: self.page.delete(self.look))]:
            b = Gtk.Button(label=label, css_classes=["flat"])
            b.get_child().set_xalign(0)
            if label == "Delete":
                b.add_css_class("error")
            b.connect("clicked", lambda _b, cb=cb: (pop.popdown(), cb()))
            box.append(b)
        btn.set_popover(pop)
        return btn


class LooksPage(Adw.Bin):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.title = "Looks"
        self.store = looks.get_store()

        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=tr("Looks"), subtitle=tr("Whole-desktop setups")))
        save = Gtk.Button(css_classes=["suggested-action"])
        save.set_child(Adw.ButtonContent(icon_name="document-save-symbolic", label=tr("Save Current Look")))
        save.connect("clicked", lambda *_: self.save_dialog())
        header.pack_end(save)
        imp = Gtk.Button(icon_name="document-open-symbolic", tooltip_text=tr("Import a Look…"))
        imp.connect("clicked", lambda *_: self.import_dialog())
        header.pack_start(imp)
        gallery=Gtk.Button(label=tr("Community"))
        gallery.connect("clicked",lambda *_:self.gallery());header.pack_start(gallery)
        schedule=Gtk.Button(icon_name="alarm-symbolic",tooltip_text=tr("Schedule Looks"))
        schedule.connect("clicked",lambda *_:self.schedule());header.pack_start(schedule)

        self.grid = ItemGrid(lambda look: LookCard(self, look), self.apply, max_columns=5,
                             css_class='font-grid', single_click=False)
        self.empty = Adw.StatusPage(icon_name="preferences-desktop-appearance-symbolic", title=tr("No Looks Yet"),
                                    description=tr("Save your current wallpaper, themes, fonts and colors as a Look, "
                                                "then switch between setups — or share them as a file."))
        self.history = Adw.PreferencesGroup(title=tr("History"),
                                            description=tr("Saved automatically before each Look is applied"))
        self._history_rows = []
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_start=24, margin_end=24,
                       margin_top=12, margin_bottom=32)
        body.append(self.empty)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.add_top_bar(body)
        view.add_bottom_bar(Gtk.ScrolledWindow(child=self.history,
                            hscrollbar_policy=Gtk.PolicyType.NEVER,
                            max_content_height=200, propagate_natural_height=True))
        view.set_content(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                            child=self.grid))
        self.set_child(view)
        self.store.connect("changed", lambda *_: self.reload())
        self.reload()

    def reload(self):
        self.grid.remove_all()
        items = self.store.looks()
        self.grid.set_items(items)
        self.empty.set_visible(not items)
        self.grid.set_visible(bool(items))
        for r in self._history_rows:
            self.history.remove(r)
        self._history_rows = []
        hist = self.store.history()
        self.history.set_visible(bool(hist))
        for lk in hist:
            row = Adw.ActionRow(title=GLib.markup_escape_text(lk.get("name", "Snapshot")),
                                subtitle=tr('{value0} · {value1}').format(value0=_when(lk.get('created', 0)), value1=looks.summary(lk)))
            restore = Gtk.Button(label=tr("Restore"), valign=Gtk.Align.CENTER)
            restore.connect("clicked", lambda _b, lk=lk: self.apply(lk))
            keep = Gtk.Button(icon_name="starred-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                              tooltip_text=tr("Keep as a Look"))
            keep.connect("clicked", lambda _b, lk=lk: self._keep(lk))
            row.add_suffix(keep)
            row.add_suffix(restore)
            self.history.add(row)
            self._history_rows.append(row)

    # ---------------------------------------------------------------- actions
    def save_dialog(self, existing=None):
        dlg = Adw.AlertDialog(heading="Save Current Look" if not existing else "Update Look",
                              body=tr("Captures your wallpaper, themes, fonts, accent color and app colors."))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        entry = Adw.EntryRow(title=tr("Name"), text=(existing or {}).get("name", time.strftime("My Look %b %-d")))
        lst = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        lst.append(entry)
        login = Adw.SwitchRow(title=tr("Include Login Screen"), active=bool((existing or {}).get("login")))
        lst.append(login)
        screenshot=Adw.SwitchRow(title=tr("Capture Desktop Screenshot"),subtitle=tr("Uses the desktop screenshot portal; included when this Look is exported"))
        lst.append(screenshot)
        box.append(lst)
        dlg.set_extra_child(box)
        dlg.add_response("cancel", tr("Cancel"))
        dlg.add_response("save", tr("Save"))
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("save")

        def resp(_d, r):
            if r != "save":
                return
            look = looks.capture(entry.get_text().strip() or "My Look", include_login=login.get_active())
            def save(path=None,error=None):
                try:
                    if path:look["screenshot"]=path
                    self.store.save(look, existing["_dir"] if existing else None)
                    message=f"Saved “{look['name']}”"
                    if error:message+=f" without a screenshot: {error}"
                    self.window.toast(message,timeout=6 if error else 3)
                except Exception as error:self.window.toast(str(error),timeout=6)
            if screenshot.get_active():
                from orbits.screenshot import capture
                capture(save)
            else:save()

        dlg.connect("response", resp)
        dlg.present(self.window)

    def overwrite(self, look):
        self.save_dialog(existing=look)

    def _keep(self, snap):
        snap = dict(snap)
        snap["name"] = snap["name"].replace("Before “", "").rstrip("”") + " (restored)"
        snap.pop("_dir", None)
        self.store.save(snap)
        self.window.toast(tr("Saved as a Look"))

    def apply(self, look):
        dialog=Adw.AlertDialog(heading=tr('Apply “{value0}”?').format(value0=look.get('name')),body=tr("Choose which parts of this Look to apply. Login settings are loaded into the editor for a separate Apply action."))
        choices={}
        group=Gtk.ListBox(css_classes=["boxed-list"],selection_mode=Gtk.SelectionMode.NONE)
        for key,label in looks.COMPONENTS.items():
            if key=="login" and not look.get("login"):continue
            row=Adw.SwitchRow(title=label,active=key!="login")
            choices[key]=row;group.append(row)
        dialog.set_extra_child(group)
        dialog.add_response("cancel",tr("Cancel"));dialog.add_response("apply",tr("Apply Selected"))
        dialog.set_response_appearance("apply",Adw.ResponseAppearance.SUGGESTED)
        def response(_dialog,result):
            if result!="apply":return
            selected={key for key,row in choices.items() if row.get_active()}
            if not selected:self.window.toast(tr("Select at least one part to apply"));return
            def done(notes):
                message=f"Applied selected parts of “{look.get('name')}”"
                if notes:message+=" · "+"; ".join(notes[:3])
                self.window.toast(message,timeout=6)
            try:looks.LookApplier(look,self.window,"login" in selected,done,components=selected).start()
            except Exception as error:self.window.toast(str(error),timeout=6)
        dialog.connect("response",response);dialog.present(self.window)

    def gallery(self):
        from orbits.ui.look_tools import LooksGallery
        LooksGallery(self).present(self.window)

    def schedule(self):
        from orbits.ui.look_tools import LookSchedule
        LookSchedule(self).present(self.window)

    def share(self,look):
        from orbits.look_gallery import export_bundle
        dialog=Gtk.FileDialog(title=tr("Export Community Gallery Bundle"))
        def done(d,result):
            try:folder=d.select_folder_finish(result)
            except GLib.Error:return
            if not folder or not folder.get_path():return
            try:
                path=export_bundle(self.store,look,folder.get_path())
                self.window.toast(tr('Saved Look, screenshot and {value0}; share the folder or host it over HTTPS').format(value0=path.name),timeout=7)
            except Exception as error:self.window.toast(str(error),timeout=6)
        dialog.select_folder(self.window,None,done)

    def rename(self, look):
        dlg = Adw.AlertDialog(heading=tr("Rename Look"))
        entry = Gtk.Entry(text=look.get("name", ""), activates_default=True)
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", tr("Cancel"))
        dlg.add_response("ok", tr("Rename"))
        dlg.set_default_response("ok")
        dlg.connect("response", lambda _d, r: r == "ok" and entry.get_text().strip() and
                    self.store.rename(look, entry.get_text().strip()))
        dlg.present(self.window)

    def delete(self, look):
        dlg = Adw.AlertDialog(heading=tr('Delete “{value0}”?').format(value0=look.get('name')), body=tr("Installed themes and fonts stay."))
        dlg.add_response("cancel", tr("Cancel"))
        dlg.add_response("delete", tr("Delete"))
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "delete" and self.store.delete(look))
        dlg.present(self.window)

    def export(self, look):
        name = "".join(c if c.isalnum() or c in " -_" else "-" for c in look.get("name", "look")).strip() or "look"
        dlg = Gtk.FileDialog(title=tr("Export Look"), initial_name=f"{name}.orbits")

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                try:
                    self.store.export(look, Path(f.get_path()))
                    self.window.toast(tr('Exported to {value0}').format(value0=Path(f.get_path()).name))
                except Exception as e:
                    self.window.toast(tr('Export failed: {value0}').format(value0=e))

        dlg.save(self.window, None, done)

    def import_dialog(self):
        filt = Gtk.FileFilter(name="Orbits Looks")
        filt.add_pattern("*.orbits")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(filt)
        dlg = Gtk.FileDialog(title=tr("Import Look"), filters=store)

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.import_path(Path(f.get_path()))

        dlg.open(self.window, None, done)

    def import_path(self, path):
        def done(look, error):
            if error:
                self.window.toast(tr("Couldn't import: {value0}").format(value0=error), timeout=6)
                return
            self.store.emit("changed")
            self.window.toast(tr('Imported “{value0}”').format(value0=look['name']), button="Apply", action=lambda: self.apply(look))
        net.run_async(lambda: self.store.import_file(path, notify=False), done)
