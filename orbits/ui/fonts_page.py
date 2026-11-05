"""Manage fonts: desktop font roles, rendering options, Orbits-installed fonts, every font on the system."""
from orbits.i18n import _ as tr
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from orbits import net
from orbits.fonts import get_font_library
from orbits.ui.font_actions import (ROLES, apply_menu_button, font_attrs, install_system_wide, remove_system_wide,
                                    system_families)

SCOPE_LABEL = {"user": "Just You", "system": "All Users", "both": "You + All Users"}
MAX_ROWS = 250


class FontsPage(Adw.Bin):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.title = "My Fonts"
        self.lib = get_font_library()
        self.iface = self.lib.iface
        self.query = ""
        self.collection = ""
        self._syncing_collections=False

        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=tr("My Fonts"), subtitle=tr("Manage and apply fonts")))
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=tr("Rescan Fonts"))
        refresh.connect("clicked", lambda *_: self._rebuild_all_fonts())
        header.pack_end(refresh)
        add = Gtk.Button(icon_name="list-add-symbolic",tooltip_text=tr("Import Font Files or ZIP…"))
        add.connect("clicked",lambda *_:self.import_dialog());header.pack_start(add)
        compare = Gtk.Button(label=tr("Compare"))
        compare.connect("clicked",lambda *_:self.compare());header.pack_end(compare)
        rules = Gtk.Button(icon_name="preferences-system-symbolic",tooltip_text=tr("Emoji, Hinting and Substitutions"))
        rules.connect("clicked",lambda *_:self.rules());header.pack_end(rules)

        page = Adw.PreferencesPage()
        page.add(self._desktop_group())
        page.add(self._rendering_group())
        collections=Adw.PreferencesGroup(title=tr("Font Collection"))
        self.collection_names=[""]+self.lib.collections()
        self.collection_row=Adw.ComboRow(title=tr("Show Collection"),model=Gtk.StringList.new(["All Fonts"]+self.collection_names[1:]))
        self.collection_row.connect("notify::selected",self._collection_changed)
        collections.add(self.collection_row);page.add(collections)
        self.installed_group = Adw.PreferencesGroup(title=tr("Installed with Orbits"))
        browse = Gtk.Button(label=tr("Get More Fonts"), css_classes=["flat"], valign=Gtk.Align.CENTER)
        browse.connect("clicked", lambda *_: window.navigate("google-fonts"))
        self.installed_group.set_header_suffix(browse)
        page.add(self.installed_group)
        self._installed_rows = []

        self.all_group = Adw.PreferencesGroup(title=tr("All Fonts on This System"))
        self.search = Gtk.SearchEntry(placeholder_text=tr("Filter fonts…"), valign=Gtk.Align.CENTER, width_chars=22)
        self.search.connect("search-changed", self._on_search)
        self.all_group.set_header_suffix(self.search)
        self.all_list = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        self.all_group.add(self.all_list)
        self.all_more = Gtk.Label(css_classes=["dim-label", "caption"], margin_top=8)
        self.all_group.add(self.all_more)
        page.add(self.all_group)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(page)
        self.set_child(view)
        drop=Gtk.DropTarget.new(Gdk.FileList,Gdk.DragAction.COPY)
        drop.connect("drop",lambda _target,value,_x,_y:(self.import_paths([f.get_path() for f in value.get_files() if f.get_path()]),True)[1])
        self.add_controller(drop)

        self.lib.connect("changed", lambda *_: self._rebuild_installed())
        self.iface.connect("changed", self._on_iface_changed)
        self._families = []
        self._rebuild_installed()
        self._built_all = False

    def on_shown(self):
        if not self._built_all:
            self._built_all = True
            self._rebuild_all_fonts()

    def toggle_search(self):
        self.search.grab_focus()

    # ---------------------------------------------------------------- desktop roles
    def _desktop_group(self):
        g = Adw.PreferencesGroup(title=tr("Desktop Fonts"), description=tr("Applied instantly to your session."))
        self.role_buttons = {}
        for role, title in ROLES:
            row = Adw.ActionRow(title=tr('{value0} Font').format(value0=title))
            btn = Gtk.FontDialogButton(dialog=Gtk.FontDialog(), valign=Gtk.Align.CENTER, use_font=True,
                                       level=Gtk.FontLevel.FONT)
            if role == "monospace":
                btn.get_dialog().set_filter(_MonoFilter())
            btn.set_font_desc(Pango.FontDescription.from_string(self.lib.current(role)))
            btn.connect("notify::font-desc", self._on_role_font, role)
            row.add_suffix(btn)
            self.role_buttons[role] = btn
            g.add(row)
        return g

    def _on_role_font(self, btn, _p, role):
        val = btn.get_font_desc().to_string()
        if val != self.lib.current(role):
            self.iface.set_string(self.lib.KEYS[role], val)

    def _on_iface_changed(self, _s, key):
        for role, k in self.lib.KEYS.items():
            if k == key:
                btn = self.role_buttons[role]
                desc = Pango.FontDescription.from_string(self.iface.get_string(k))
                if btn.get_font_desc() is None or not btn.get_font_desc().equal(desc):
                    btn.set_font_desc(desc)

    def _rendering_group(self):
        g = Adw.PreferencesGroup(title=tr("Rendering"))
        schema = self.iface.get_property("settings-schema")
        if schema.has_key("text-scaling-factor"):
            row = Adw.SpinRow.new_with_range(0.5, 3.0, 0.05)
            row.set_title(tr("Text Scaling"))
            row.set_digits(2)
            self.iface.bind("text-scaling-factor", row, "value", Gio.SettingsBindFlags.DEFAULT)
            g.add(row)
        for key, title in [("font-antialiasing", "Antialiasing"), ("font-hinting", "Hinting")]:
            if not schema.has_key(key):
                continue
            kind, values = schema.get_key(key).get_range().unpack()
            if kind != "enum":
                continue
            row = Adw.ComboRow(title=title, model=Gtk.StringList.new([v.title() for v in values]))
            row.set_selected(values.index(self.iface.get_string(key)) if self.iface.get_string(key) in values else 0)
            row.connect("notify::selected", lambda r, _p, k=key, vs=values: self.iface.set_string(k, vs[r.get_selected()]))
            g.add(row)
        return g

    # ---------------------------------------------------------------- installed by Orbits
    def _rebuild_installed(self):
        for r in self._installed_rows:
            self.installed_group.remove(r)
        self._installed_rows = []
        names=[""]+self.lib.collections()
        if names!=self.collection_names:
            selected=self.collection if self.collection in names else ""
            self.collection_names=names
            self._syncing_collections=True
            self.collection=selected
            self.collection_row.set_model(Gtk.StringList.new(["All Fonts"]+names[1:]))
            self.collection_row.set_selected(names.index(selected))
            self._syncing_collections=False
        items = [entry for entry in self.lib.sorted_items() if not self.collection or self.collection in entry.get("collections",[])]
        if not items:
            row = Adw.ActionRow(title=tr("No fonts installed yet"),
                                subtitle=tr("Browse Google Fonts or Nerd Fonts in the sidebar"))
            self.installed_group.add(row)
            self._installed_rows.append(row)
            return
        for e in items:
            fam = e["families"][0]
            row = Adw.ActionRow(title=GLib.markup_escape_text(e["family"]),
                                subtitle=f"{SCOPE_LABEL.get(e['scope'], e['scope'])} · {len(e['files'])} file"
                                         f"{'s' if len(e['files']) != 1 else ''}"
                                         + (f" · {', '.join(e['families'][:3])}" if len(e["families"]) > 1 else ""))
            sample = Gtk.Label(label=tr("Aa"), width_chars=3, css_classes=["font-row-sample"])
            sample.set_attributes(font_attrs(fam, 20))
            row.add_prefix(sample)
            enabled=Gtk.Switch(active=e.get("enabled",True),valign=Gtk.Align.CENTER,tooltip_text=tr("Enable for Your User"))
            enabled.connect("state-set",lambda switch,state,key=e["key"]:self._enabled(key,state))
            row.add_suffix(enabled)
            row.add_suffix(apply_menu_button(self.window, lambda f=fam: f, lambda e=e: e))
            more = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text=tr("Font Actions"), css_classes=["flat"], valign=Gtk.Align.CENTER)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            pop = Gtk.Popover(child=box)
            actions = [("Font Details & Glyphs…",lambda e=e:self.inspect(e)),("Collections…",lambda e=e:self.edit_collections(e))]
            if e["scope"] == "user":
                actions.append(("Install for All Users…", lambda e=e: install_system_wide(self.window, e)))
            if e["scope"] in ("system", "both"):
                actions.append(("Remove for All Users…", lambda e=e: remove_system_wide(self.window, e)))
            if e["scope"] in ("user", "both"):
                actions.append(("Uninstall", lambda e=e: self._confirm_uninstall(e)))
            actions.append(("Show Files", lambda e=e: Gtk.FileLauncher.new(
                Gio.File.new_for_path(e["dir"])).launch(self.window, None, None)))
            for label, cb in actions:
                b = Gtk.Button(label=label, css_classes=["flat"])
                b.get_child().set_xalign(0)
                b.connect("clicked", lambda _b, cb=cb, p=pop: (p.popdown(), cb()))
                box.append(b)
            more.set_popover(pop)
            row.add_suffix(more)
            self.installed_group.add(row)
            self._installed_rows.append(row)

    def _collection_changed(self,row,_property):
        if self._syncing_collections:return
        index=row.get_selected()
        self.collection=self.collection_names[index] if index<len(self.collection_names) else ""
        self._rebuild_installed()

    def _enabled(self,key,state):
        try:
            self.lib.set_enabled(key,state)
            self._rebuild_all_fonts()
            self.window.toast("Font enabled" if state else "Font disabled for your user; files are retained")
        except Exception as error:self.window.toast(str(error),timeout=6)
        return True

    def inspect(self,entry):
        from orbits.ui.font_tools import FontInspector
        FontInspector(self.window,entry).present(self.window)

    def inspect_system(self,family):
        from orbits.fonts import family_file
        def done(path,error):
            if error:self.window.toast(str(error),timeout=6)
            else:self.inspect({"family":family,"files":[path],"license":""})
        net.run_async(lambda:family_file(family),done)

    def compare(self):
        from orbits.ui.font_tools import FontCompare
        FontCompare(self.window).present(self.window)

    def rules(self):
        from orbits.ui.font_tools import FontRules
        FontRules(self.window).present(self.window)

    def edit_collections(self,entry):
        dialog=Adw.AlertDialog(heading=tr("Font Collections"),body=tr("Separate collection names with commas."))
        field=Gtk.Entry(text=", ".join(entry.get("collections",[])))
        dialog.set_extra_child(field);dialog.add_response("cancel",tr("Cancel"));dialog.add_response("save",tr("Save"))
        dialog.connect("response",lambda _d,result:result=="save" and self.lib.set_collections(entry["key"],field.get_text().split(",")))
        dialog.present(self.window)

    def import_dialog(self):
        filter_=Gtk.FileFilter(name="Font Files and ZIP Archives")
        for suffix in ("ttf","otf","ttc","otc","zip"):filter_.add_suffix(suffix)
        filters=Gio.ListStore.new(Gtk.FileFilter);filters.append(filter_)
        dialog=Gtk.FileDialog(title=tr("Import Fonts"),filters=filters)
        def done(d,result):
            try:
                files=d.open_multiple_finish(result)
            except GLib.Error:return
            self.import_paths([file.get_path() for file in files if file.get_path()])
        dialog.open_multiple(self.window,None,done)

    def import_paths(self,paths):
        if not paths:return
        self.window.toast(tr("Validating and installing fonts…"))
        def done(entry,error):
            self.window.toast(str(error) if error else f"Installed {len(entry['families'])} font families",timeout=6)
            if entry:self._rebuild_all_fonts()
        self.lib.import_files(paths,done)

    def _confirm_uninstall(self, e):
        dlg = Adw.AlertDialog(heading=tr('Uninstall “{value0}”?').format(value0=e['family']),
                              body=tr("Apps using this font will fall back to another one."))
        dlg.add_response("cancel", tr("Cancel"))
        dlg.add_response("remove", tr("Uninstall"))
        dlg.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "remove" and (self.lib.uninstall_user(e["key"]),
                                                                self.window.toast(tr('Uninstalled “{value0}”').format(value0=e['family']))))
        dlg.present(self.window)

    # ---------------------------------------------------------------- all fonts
    def _rebuild_all_fonts(self):
        self._families = system_families()
        self._fill_all()

    def _on_search(self, entry):
        self.query = entry.get_text().strip().lower()
        self._fill_all()

    def _fill_all(self):
        self.all_list.remove_all()
        matches = [f for f in self._families if self.query in f.lower()]
        for fam in matches[:MAX_ROWS]:
            row = Adw.ActionRow(title=GLib.markup_escape_text(fam))
            sample = Gtk.Label(label=tr("The quick brown fox"), xalign=1, ellipsize=Pango.EllipsizeMode.END,
                               hexpand=True, max_width_chars=26, css_classes=["dim-label"])
            sample.set_attributes(font_attrs(fam, 13))
            row.add_suffix(sample)
            detail=Gtk.Button(icon_name="document-properties-symbolic",tooltip_text=tr("Font Details & Glyph Map"),valign=Gtk.Align.CENTER)
            detail.connect("clicked",lambda _button,f=fam:self.inspect_system(f))
            row.add_suffix(detail)
            row.add_suffix(apply_menu_button(self.window, lambda f=fam: f))
            self.all_list.append(row)
        extra = len(matches) - MAX_ROWS
        self.all_more.set_visible(extra > 0 or not matches)
        self.all_more.set_label(f"{extra} more — refine the filter to see them" if extra > 0 else "No matching fonts")
        self.all_group.set_description(tr('{value0} font families available').format(value0=len(self._families)))


class _MonoFilter(Gtk.Filter):
    def do_match(self, item):
        fam = item if isinstance(item, Pango.FontFamily) else item.get_family()
        return fam.is_monospace()

    def do_get_strictness(self):
        return Gtk.FilterMatch.SOME
