"""Keyboard-friendly local search and a compact desktop switcher."""
from gi.repository import Adw, Gdk, GLib, Gtk

from orbits import search
from orbits.config import DATA_DIR
from orbits.i18n import _


class SearchDialog(Adw.Dialog):
    def __init__(self, window, query=''):
        super().__init__(title=_("Search Orbits"), content_width=640, content_height=540)
        self.window = window
        self.records = search.build_index(DATA_DIR)
        from orbits.fonts import system_families
        from orbits import themes
        families = {record['title'] for record in self.records if record['kind']=='font'}
        for family in system_families():
            if family not in families:
                self.records.append(dict(id='font:system:'+family, kind='font', title=family,
                                         page='my-fonts', detail=_("System Font"), key=family))
        managed = {record['key'] for record in self.records if record['kind']=='theme'}
        for kind in themes.KINDS:
            for theme in themes.installed(kind):
                if theme.meta.get('key') not in managed:
                    self.records.append(dict(id=f'theme:{kind}:{theme.name}', kind='theme', title=theme.display,
                                             page='theme-'+kind, detail=kind, key=theme.name, theme_name=theme.name))
        self.entry = Gtk.SearchEntry(placeholder_text=_("Wallpapers, themes, fonts and Looks…"))
        self.entry.update_property([Gtk.AccessibleProperty.LABEL], [_("Search your library")])
        self.entry.connect('search-changed', lambda *_: self.refresh())
        self.entry.connect('activate', self._activate_first)
        self.entry.connect('stop-search', lambda *_: self.close())
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=['boxed-list'])
        self.list.connect('row-activated', self.activate)
        keys = Gtk.EventControllerKey()
        def key_pressed(_controller, key, _code, _state):
            row = self.list.get_row_at_index(0)
            if key == Gdk.KEY_Down and row:
                self.list.select_row(row)
                row.grab_focus()
                return True
            return False
        keys.connect('key-pressed', key_pressed)
        self.entry.add_controller(keys)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_start=18, margin_end=18, margin_top=12, margin_bottom=18)
        box.append(self.entry)
        box.append(Gtk.Label(label=_("Searches installed items. Use each store’s search to discover more."),
                             wrap=True, xalign=0, css_classes=['dim-label']))
        box.append(Gtk.ScrolledWindow(child=self.list, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        view = Adw.ToolbarView(content=box)
        view.add_top_bar(Adw.HeaderBar())
        self.set_child(view)
        self.entry.set_text(query)
        self.refresh()
        self.connect('map', lambda *_: self.entry.grab_focus())

    def refresh(self):
        self.list.remove_all()
        for record in search.search(self.records, self.entry.get_text()):
            row = Adw.ActionRow(title=record['title'], subtitle={'wallpaper':_('Wallpaper'), 'theme':_('Theme'), 'font':_('Font'), 'look':_('Look')}[record['kind']]+' · '+record.get('detail',''),
                                use_markup=False, activatable=True)
            row.record = record
            self.list.append(row)
        first = self.list.get_row_at_index(0)
        if first:
            self.list.select_row(first)

    def _activate_first(self, *_):
        row = self.list.get_selected_row() or self.list.get_row_at_index(0)
        if row:
            self.activate(self.list, row)

    def activate(self, _list, row):
        record = row.record
        self.close()
        self.window.open_search_result(record)


class QuickSwitcher(Adw.Window):
    def __init__(self, application, window):
        super().__init__(application=application, title=_("Quick Switcher"), default_width=420, default_height=520)
        self.window = window
        from orbits import looks
        from orbits.rotation import load_config, configure, rotate_once
        from orbits import net
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_start=18, margin_end=18, margin_top=12, margin_bottom=18)
        group = Adw.PreferencesGroup(title=_("Looks"))
        for look in looks.get_store().looks():
            row = Adw.ActionRow(title=look['name'], use_markup=False)
            button = Gtk.Button(label=_("Apply"), valign=Gtk.Align.CENTER)
            button.connect('clicked', lambda _b, item=look: self.apply_look(item))
            row.add_suffix(button)
            group.add(row)
        box.append(group)
        rotation = Adw.SwitchRow(title=_("Wallpaper Rotation"), active=bool(load_config().get('enabled')))
        def toggled(row, _param):
            try:
                configure(dict(load_config(), enabled=row.get_active()))
            except Exception as error:
                application.notify_error(str(error))
                rotation.handler_block(signal_id)
                rotation.set_active(not row.get_active())
                rotation.handler_unblock(signal_id)
        signal_id = rotation.connect('notify::active', toggled)
        box.append(rotation)
        next_button = Gtk.Button(label=_("Next Wallpaper"))
        def next_wallpaper(*_):
            next_button.set_sensitive(False)
            def done(_record, error):
                next_button.set_sensitive(True)
                if error:
                    application.notify_error(str(error))
            net.run_async(rotate_once, done)
        next_button.connect('clicked', next_wallpaper)
        box.append(next_button)
        view = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=box, hscrollbar_policy=Gtk.PolicyType.NEVER))
        view.add_top_bar(Adw.HeaderBar())
        self.toasts = Adw.ToastOverlay(child=view)
        self.set_content(self.toasts)
        from orbits.undo import subscribe
        self.unsubscribe = subscribe(self.offer_undo)
        self.connect('close-request', lambda *_: self.unsubscribe())

    def offer_undo(self, record):
        import threading
        if threading.current_thread() is not threading.main_thread():
            GLib.idle_add(lambda:(self.offer_undo(record), False)[1])
            return
        toast = Adw.Toast(title=record.label, timeout=10, button_label=_("Undo"))
        def activated(*_args):
            try:
                result = record.undo()
                if result is not False:
                    self.toasts.add_toast(Adw.Toast(title=_("Change undone")))
            except Exception as error:
                self.toasts.add_toast(Adw.Toast(title=str(error)))
        toast.connect('button-clicked', activated)
        self.toasts.add_toast(toast)

    def apply_look(self, look):
        from orbits import looks
        def done(notes):
            self.window.toast(_("Look applied") + (' · '+'; '.join(notes) if notes else ''))
        looks.LookApplier(look, self.window, done=done).start()
