"""Manage fonts: desktop font roles, rendering options, Orbits-installed fonts, every font on the system."""
from gi.repository import Adw, Gio, GLib, Gtk, Pango

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
        self.iface = Gio.Settings.new("org.gnome.desktop.interface")
        self.query = ""

        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title="My Fonts", subtitle="Manage and apply fonts"))
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Rescan Fonts")
        refresh.connect("clicked", lambda *_: self._rebuild_all_fonts())
        header.pack_end(refresh)

        page = Adw.PreferencesPage()
        page.add(self._desktop_group())
        page.add(self._rendering_group())
        self.installed_group = Adw.PreferencesGroup(title="Installed with Orbits")
        browse = Gtk.Button(label="Get More Fonts", css_classes=["flat"], valign=Gtk.Align.CENTER)
        browse.connect("clicked", lambda *_: window.navigate("google-fonts"))
        self.installed_group.set_header_suffix(browse)
        page.add(self.installed_group)
        self._installed_rows = []

        self.all_group = Adw.PreferencesGroup(title="All Fonts on This System")
        self.search = Gtk.SearchEntry(placeholder_text="Filter fonts…", valign=Gtk.Align.CENTER, width_chars=22)
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
        g = Adw.PreferencesGroup(title="Desktop Fonts", description="Applied instantly to your session.")
        self.role_buttons = {}
        for role, title in ROLES:
            row = Adw.ActionRow(title=f"{title} Font")
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
        g = Adw.PreferencesGroup(title="Rendering")
        schema = self.iface.get_property("settings-schema")
        if schema.has_key("text-scaling-factor"):
            row = Adw.SpinRow.new_with_range(0.5, 3.0, 0.05)
            row.set_title("Text Scaling")
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
        items = self.lib.sorted_items()
        if not items:
            row = Adw.ActionRow(title="No fonts installed yet",
                                subtitle="Browse Google Fonts or Nerd Fonts in the sidebar")
            self.installed_group.add(row)
            self._installed_rows.append(row)
            return
        for e in items:
            fam = e["families"][0]
            row = Adw.ActionRow(title=GLib.markup_escape_text(e["family"]),
                                subtitle=f"{SCOPE_LABEL.get(e['scope'], e['scope'])} · {len(e['files'])} file"
                                         f"{'s' if len(e['files']) != 1 else ''}"
                                         + (f" · {', '.join(e['families'][:3])}" if len(e["families"]) > 1 else ""))
            sample = Gtk.Label(label="Aa", width_chars=3, css_classes=["font-row-sample"])
            sample.set_attributes(font_attrs(fam, 20))
            row.add_prefix(sample)
            row.add_suffix(apply_menu_button(self.window, lambda f=fam: f, lambda e=e: e))
            more = Gtk.MenuButton(icon_name="view-more-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            pop = Gtk.Popover(child=box)
            actions = []
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

    def _confirm_uninstall(self, e):
        dlg = Adw.AlertDialog(heading=f"Uninstall “{e['family']}”?",
                              body="Apps using this font will fall back to another one.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("remove", "Uninstall")
        dlg.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "remove" and (self.lib.uninstall_user(e["key"]),
                                                                self.window.toast(f"Uninstalled “{e['family']}”")))
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
            sample = Gtk.Label(label="The quick brown fox", xalign=1, ellipsize=Pango.EllipsizeMode.END,
                               hexpand=True, max_width_chars=26, css_classes=["dim-label"])
            sample.set_attributes(font_attrs(fam, 13))
            row.add_suffix(sample)
            row.add_suffix(apply_menu_button(self.window, lambda f=fam: f))
            self.all_list.append(row)
        extra = len(matches) - MAX_ROWS
        self.all_more.set_visible(extra > 0 or not matches)
        self.all_more.set_label(f"{extra} more — refine the filter to see them" if extra > 0 else "No matching fonts")
        self.all_group.set_description(f"{len(self._families)} font families available")


class _MonoFilter(Gtk.Filter):
    def do_match(self, item):
        fam = item if isinstance(item, Pango.FontFamily) else item.get_family()
        return fam.is_monospace()

    def do_get_strictness(self):
        return Gtk.FilterMatch.SOME
