"""Looks page: saved desktop setups, history snapshots, import/export."""
import time
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from orbits import looks, net
from orbits.ui.widgets import CoverPicture, local_thumbnail


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
        wp = (look.get("wallpaper") or {}).get("light")
        if wp and Path(wp).is_file():
            net.run_async(local_thumbnail, lambda tex, err: tex and pic.set_paintable(tex), wp, 600)
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
        apply = Gtk.Button(label="Apply", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
        apply.connect("clicked", lambda *_: page.apply(look))
        info.append(apply)
        info.append(self._menu())
        self.append(info)

    def _menu(self):
        btn = Gtk.MenuButton(icon_name="view-more-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        pop = Gtk.Popover(child=box)
        for label, cb in [("Update to Current Desktop", lambda: self.page.overwrite(self.look)),
                          ("Rename…", lambda: self.page.rename(self.look)),
                          ("Export…", lambda: self.page.export(self.look)),
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

        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title="Looks", subtitle="Whole-desktop setups"))
        save = Gtk.Button(css_classes=["suggested-action"])
        save.set_child(Adw.ButtonContent(icon_name="document-save-symbolic", label="Save Current Look"))
        save.connect("clicked", lambda *_: self.save_dialog())
        header.pack_end(save)
        imp = Gtk.Button(icon_name="document-open-symbolic", tooltip_text="Import a Look…")
        imp.connect("clicked", lambda *_: self.import_dialog())
        header.pack_start(imp)

        self.grid = Gtk.FlowBox(homogeneous=True, selection_mode=Gtk.SelectionMode.NONE, min_children_per_line=1,
                                max_children_per_line=5, column_spacing=16, row_spacing=16, valign=Gtk.Align.START,
                                css_classes=["font-grid"])
        self.empty = Adw.StatusPage(icon_name="preferences-desktop-appearance-symbolic", title="No Looks Yet",
                                    description="Save your current wallpaper, themes, fonts and colors as a Look, "
                                                "then switch between setups — or share them as a file.")
        self.history = Adw.PreferencesGroup(title="History",
                                            description="Saved automatically before each Look is applied")
        self._history_rows = []
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_start=24, margin_end=24,
                       margin_top=12, margin_bottom=32)
        body.append(self.empty)
        body.append(self.grid)
        body.append(self.history)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                            child=Adw.Clamp(maximum_size=1800, child=body)))
        self.set_child(view)
        self.store.connect("changed", lambda *_: self.reload())
        self.reload()

    def reload(self):
        self.grid.remove_all()
        items = self.store.looks()
        for lk in items:
            self.grid.append(LookCard(self, lk))
        self.empty.set_visible(not items)
        self.grid.set_visible(bool(items))
        for r in self._history_rows:
            self.history.remove(r)
        self._history_rows = []
        hist = self.store.history()
        self.history.set_visible(bool(hist))
        for lk in hist:
            row = Adw.ActionRow(title=GLib.markup_escape_text(lk.get("name", "Snapshot")),
                                subtitle=f"{_when(lk.get('created', 0))} · {looks.summary(lk)}")
            restore = Gtk.Button(label="Restore", valign=Gtk.Align.CENTER)
            restore.connect("clicked", lambda _b, lk=lk: self.apply(lk))
            keep = Gtk.Button(icon_name="starred-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                              tooltip_text="Keep as a Look")
            keep.connect("clicked", lambda _b, lk=lk: self._keep(lk))
            row.add_suffix(keep)
            row.add_suffix(restore)
            self.history.add(row)
            self._history_rows.append(row)

    # ---------------------------------------------------------------- actions
    def save_dialog(self, existing=None):
        dlg = Adw.AlertDialog(heading="Save Current Look" if not existing else "Update Look",
                              body="Captures your wallpaper, themes, fonts, accent color and app colors.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        entry = Adw.EntryRow(title="Name", text=(existing or {}).get("name", time.strftime("My Look %b %-d")))
        lst = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        lst.append(entry)
        login = Adw.SwitchRow(title="Include Login Screen", active=bool((existing or {}).get("login")))
        lst.append(login)
        box.append(lst)
        dlg.set_extra_child(box)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("save", "Save")
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("save")

        def resp(_d, r):
            if r != "save":
                return
            look = looks.capture(entry.get_text().strip() or "My Look", include_login=login.get_active())
            self.store.save(look, existing["_dir"] if existing else None)
            self.window.toast(f"Saved “{look['name']}”")

        dlg.connect("response", resp)
        dlg.present(self.window)

    def overwrite(self, look):
        self.save_dialog(existing=look)

    def _keep(self, snap):
        snap = dict(snap)
        snap["name"] = snap["name"].replace("Before “", "").rstrip("”") + " (restored)"
        snap.pop("_dir", None)
        self.store.save(snap)
        self.window.toast("Saved as a Look")

    def apply(self, look):
        has_login = bool(look.get("login"))

        def go(include_login):
            def done(notes):
                msg = f"Applied “{look.get('name')}”"
                if notes:
                    msg += " · " + "; ".join(notes[:3]) + ("…" if len(notes) > 3 else "")
                self.window.toast(msg, timeout=6 if notes else 3)

            looks.LookApplier(look, self.window, include_login, done).start()

        if not has_login:
            go(False)
            return
        dlg = Adw.AlertDialog(heading=f"Apply “{look.get('name')}”?",
                              body="This Look also includes login screen settings, which need your password.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("desktop", "Desktop Only")
        dlg.add_response("all", "Include Login Screen")
        dlg.set_response_appearance("all", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda _d, r: r != "cancel" and go(r == "all"))
        dlg.present(self.window)

    def rename(self, look):
        dlg = Adw.AlertDialog(heading="Rename Look")
        entry = Gtk.Entry(text=look.get("name", ""), activates_default=True)
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Rename")
        dlg.set_default_response("ok")
        dlg.connect("response", lambda _d, r: r == "ok" and entry.get_text().strip() and
                    self.store.rename(look, entry.get_text().strip()))
        dlg.present(self.window)

    def delete(self, look):
        dlg = Adw.AlertDialog(heading=f"Delete “{look.get('name')}”?", body="Installed themes and fonts stay.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", "Delete")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "delete" and self.store.delete(look))
        dlg.present(self.window)

    def export(self, look):
        name = "".join(c if c.isalnum() or c in " -_" else "-" for c in look.get("name", "look")).strip() or "look"
        dlg = Gtk.FileDialog(title="Export Look", initial_name=f"{name}.orbits")

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                try:
                    self.store.export(look, Path(f.get_path()))
                    self.window.toast(f"Exported to {Path(f.get_path()).name}")
                except OSError as e:
                    self.window.toast(f"Export failed: {e}")

        dlg.save(self.window, None, done)

    def import_dialog(self):
        filt = Gtk.FileFilter(name="Orbits Looks")
        filt.add_pattern("*.orbits")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(filt)
        dlg = Gtk.FileDialog(title="Import Look", filters=store)

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.import_path(Path(f.get_path()))

        dlg.open(self.window, None, done)

    def import_path(self, path):
        try:
            look = self.store.import_file(path)
        except Exception as e:  # noqa: BLE001 — bad files must not crash the app
            self.window.toast(f"Couldn't import: {e}", timeout=6)
            return
        self.window.toast(f"Imported “{look['name']}”", button="Apply", action=lambda: self.apply(look))
