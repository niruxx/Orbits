"""Theme pages: Installed (preview, apply, remove, install for all users) + Discover (gnome-look.org)."""
from orbits.i18n import _ as tr
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from orbits import themes
from orbits.ui.browse import BrowsePage

from orbits.ui.theme_card import ThemeCard
from orbits.ui.item_grid import ItemGrid


from orbits.ui.theme_actions import ThemeActions


class ThemesPage(ThemeActions, Adw.Bin):
    def __init__(self, window, kind_id):
        super().__init__()
        self.window = window
        self.kind = themes.KINDS[kind_id]
        self.title = self.kind.title
        self.manager = themes.get_manager(kind_id)
        self.installed_items = []

        self.stack = Adw.ViewStack()
        installed_view = self._installed_view()
        self.stack.add_titled_with_icon(installed_view, "installed", tr("Installed"), "object-select-symbolic")
        self.source = themes.ThemeStoreSource(self.kind)
        self.browse = BrowsePage(
            window, self.source, lib=self.manager, apply_label="Install & Export" if kind_id == "terminal" else "Install & Apply", apply_icon="object-select-symbolic",
            on_apply=self._apply_entry, describe=self._describe,
            title_widget=Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE),
            wallpaper_extras=False)
        self.stack.add_titled_with_icon(self.browse, "discover", tr("Discover"), "web-browser-symbolic")
        self.stack.connect("notify::visible-child-name", lambda *_: self._on_tab())
        self.set_child(self.stack)

        self.manager.connect("changed", lambda *_: self.reload())
        if kind_id in ("gtk", "icons", "cursors"):
            self._iface = themes._iface()
            key = {"gtk": "gtk-theme", "icons": "icon-theme", "cursors": "cursor-theme"}[kind_id]
            self._iface.connect(f"changed::{key}", lambda *_: self._mark_current())
        self._built = False

    # ---------------------------------------------------------------- installed tab
    def _installed_view(self):
        header = Adw.HeaderBar(title_widget=Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=tr("Rescan"))
        refresh.connect("clicked", lambda *_: self.manager.refresh())
        header.pack_end(refresh)
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=tr("Install from File…"))
        add.connect("clicked", lambda *_: self._install_file())
        header.pack_start(add)

        self.info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.grid = ItemGrid(self._make_card, self.apply, max_columns=6,
                             css_class='font-grid', single_click=False)
        self.count_label = Gtk.Label(xalign=0, css_classes=["dim-label"])
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_start=24, margin_end=24,
                       margin_top=12, margin_bottom=32)
        intro = Gtk.Box(spacing=12)
        intro.append(Gtk.Image(icon_name=self.kind.icon, pixel_size=32, css_classes=["accent"]))
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        titles.append(Gtk.Label(label=self.kind.title, xalign=0, css_classes=["title-2"]))
        titles.append(self.count_label)
        intro.append(titles)
        body.append(intro)
        body.append(self.info_box)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                      child=self.grid)
        self.busy_banner = Adw.Banner()
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.add_top_bar(self.busy_banner)
        view.add_top_bar(Adw.Clamp(maximum_size=1800, child=body))
        view.set_content(scroller)
        return view

    def on_shown(self):
        if not self._built:
            self._built = True
            self.reload()
        if self.stack.get_visible_child_name() == "discover":
            self.browse.on_shown()

    def _on_tab(self):
        if self.stack.get_visible_child_name() == "discover":
            self.browse.on_shown()

    def toggle_search(self):
        if self.stack.get_visible_child_name() == "discover":
            self.browse.toggle_search()

    def reload(self):
        if not self._built:
            return
        self._build_info()
        self.grid.remove_all()
        self.installed_items = []
        items = themes.installed(self.kind.id)
        if self.kind.id == "shell":
            items.insert(0, themes.InstalledTheme("shell", "", None, "system", False, "GNOME Default", {}))
        if self.kind.id == "gtk" and not any(t.name == "Adwaita" for t in items):
            items.insert(0, themes.InstalledTheme("gtk", "Adwaita", None, "system", False, "Adwaita (built in)", {}))
        cur = themes.current(self.kind.id)
        items.sort(key=lambda t: (t.name != cur, t.scope != "user", t.display.lower()))
        self.installed_items = items
        self.grid.set_items(items)
        self.count_label.set_label(f"{len(items)} installed · export a palette for your terminal" if self.kind.id == "terminal"
                                   else f"{len(items)} installed · current: {cur or 'GNOME Default'}")
        self._mark_current()

    def _make_card(self, theme):
        card = ThemeCard(self, theme)
        card.set_current(theme.name == themes.current(self.kind.id))
        return card

    def _mark_current(self):
        cur = themes.current(self.kind.id)
        self.grid.refresh(lambda card: card.set_current(card.theme.name == cur))

    def _build_info(self):
        while (c := self.info_box.get_first_child()):
            self.info_box.remove(c)
        kid = self.kind.id
        group = Adw.PreferencesGroup()
        if kid in ("gtk1", "gtk2"):
            group.set_description(tr("Legacy themes use a separate GTK rc file; restart older apps after applying. Some themes require GTK engines supplied by your distribution."))
        elif kid == "sounds":
            group.set_description(tr("Sets GNOME's event-sound theme and enables event sounds. Applications must support sound themes to use it."))
        elif kid == "terminal":
            group.set_description(tr("Imports Konsole, Ptyxis, Kitty, Alacritty, iTerm and terminal.sexy palettes as data. Export for your terminal; downloaded scripts are never executed."))
        elif kid == "gtk":
            name, broken = themes.libadwaita_state()
            row = Adw.SwitchRow(title=tr("Also Theme Libadwaita Apps"),
                                subtitle=tr("Links the theme's GTK4 stylesheet into ~/.config/gtk-4.0. "
                                         "Restart apps to see it. Some themes may look off in newer apps."),
                                active=bool(name) and not broken)
            row.connect("notify::active", self._toggle_libadwaita)
            group.add(row)
            if broken:
                warn = Adw.ActionRow(title=tr("Broken libadwaita theme link"),
                                     subtitle=tr('~/.config/gtk-4.0 points to “{value0}”, which no longer exists').format(value0=name))
                warn.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"]))
                fix = Gtk.Button(label=tr("Clean Up"), valign=Gtk.Align.CENTER)
                fix.connect("clicked", lambda *_: (themes.remove_libadwaita(), self.reload(),
                                                   self.window.toast(tr("Removed broken links from ~/.config/gtk-4.0"))))
                warn.add_suffix(fix)
                group.add(warn)
            elif name:
                group.add(Adw.ActionRow(title=tr("Libadwaita theme"), subtitle=name))
        elif kid == "shell":
            ok, enabled, msg = themes.shell_status()
            row = Adw.ActionRow(title=tr("User Themes extension"),
                                subtitle=msg or ("Enabled" if enabled else "Will be enabled when you apply a theme"))
            row.add_prefix(Gtk.Image(icon_name="object-select-symbolic" if ok and not msg else "dialog-warning-symbolic"))
            group.add(row)
        elif kid == "plymouth":
            problems = themes.plymouth_status()
            for p in problems:
                r = Adw.ActionRow(title=p)
                r.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"]))
                group.add(r)
            group.set_description(tr("Boot splashes are system-wide. Applying one rebuilds your initramfs, which "
                                  "takes about a minute and asks for your password."))
        elif kid in ("icons", "cursors"):
            group.set_description(tr("Themes installed just for you don't show on the login screen; use "
                                  "“Install for All Users” for that."))
        if group.get_first_child() is not None:
            self.info_box.append(group)

    # ---------------------------------------------------------------- actions


    # ---------------------------------------------------------------- store integration
    def _describe(self, entry):
        names = entry.get("names", [])
        if len(names) > 1:
            return f"Installed {len(names)} {self.kind.noun}s from “{entry['title']}”"
        return f"Installed “{names[0] if names else entry['title']}”"

    def _apply_entry(self, entry):
        names = entry.get("names") or []
        if not names:
            return
        found = {t.name: t for t in themes.installed(self.kind.id)}
        theme = found.get(names[0])
        if theme is None:
            self.window.toast(tr("Installed theme not found — try Rescan"))
            return
        if len(names) > 1:
            self.window.toast(tr('Selected “{value0}” — {value1} more variants are in the Installed tab').format(value0=names[0], value1=len(names) - 1))
        self.apply(theme)

    def _install_file(self):
        filt = Gtk.FileFilter(name="Theme archives")
        for pat in (("*.palette", "*.colorscheme", "*.itermcolors", "*.json", "*.toml", "*.conf") if self.kind.id == "terminal" else ()) + ("*.zip", "*.tar", "*.tar.gz", "*.tgz", "*.tar.xz", "*.txz", "*.tar.bz2", "*.tar.zst"):
            filt.add_pattern(pat)
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(filt)
        dlg = Gtk.FileDialog(title=tr('Install {value0} from File').format(value0=self.kind.noun), filters=store)

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            path = f.get_path() if f else None
            if path:
                self._install_local(Path(path))

        dlg.open(self.window, None, done)

    def _install_local(self, path: Path):
        from orbits.sources.base import Download, Item

        item = Item(source="local", id=path.name, title=path.name)
        dl = Download(path.name, path.as_uri())
        self.manager.install(item, dl, lambda e, err: self._after_install(e, err))

    def _after_install(self, entry, err):
        if err:
            self.window.toast(str(err), timeout=6)
            return
        if entry:
            self.window.toast(self._describe(entry))
