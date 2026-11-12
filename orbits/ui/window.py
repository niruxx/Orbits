from orbits.i18n import _ as tr
from gi.repository import Adw, Gio, GLib, Gtk

from orbits.config import settings
from orbits.sources import SOURCES


class OrbitsWindow(Adw.ApplicationWindow):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.set_title(tr("Orbits"))
        self.set_icon_name("io.github.niruxx.Orbits")
        self.set_default_size(settings.get("window_width"), settings.get("window_height"))
        self.set_size_request(380, 500)
        self.connect("close-request", self._on_close)

        self.toasts = Adw.ToastOverlay()
        self.root_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=450)
        self.toasts.set_child(self.root_stack)
        self.set_content(self.toasts)

        from orbits.undo import subscribe
        self._undo_unsubscribe = subscribe(self._offer_undo)
        self._main_built = False
        if settings.get("welcome_done"):
            self._show_main(animate=False)
        else:
            self.show_welcome()

        search = Gio.SimpleAction.new("search", None)
        search.connect("activate", lambda *_: self._current_page_call("toggle_search"))
        self.add_action(search)

    # ---------------------------------------------------------------- welcome
    def show_welcome(self):
        from orbits.ui.welcome import WelcomeView

        old = self.root_stack.get_child_by_name("welcome")
        if old:
            self.root_stack.remove(old)
        view = WelcomeView(on_done=self._welcome_done)
        self.root_stack.add_named(view, "welcome")
        self.root_stack.set_visible_child_name("welcome")

    def _welcome_done(self, start_page=None):
        settings.set("welcome_done", True)
        self._show_main()
        if start_page:
            self.navigate(start_page)

    # ---------------------------------------------------------------- main UI
    def _show_main(self, animate=True):
        if not self._main_built:
            self._build_main()
        self.root_stack.set_visible_child_name("main")

    def _build_main(self):
        from orbits.ui.browse import BrowsePage
        from orbits.ui.gdm_page import GdmPage
        from orbits.fonts import FONT_SOURCES
        from orbits.ui.fonts_browse import FontBrowsePage
        from orbits.ui.fonts_page import FontsPage
        from orbits.ui.library_page import LibraryPage

        self.split = Adw.NavigationSplitView(min_sidebar_width=230, max_sidebar_width=280, sidebar_width_fraction=0.22)
        self.content_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=180)
        self.pages = {}

        # Sidebar
        self.sidebar = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.sidebar.set_header_func(self._sidebar_header)
        self._rows = []
        entries = [("explore", s.id, s.name, s.icon) for s in SOURCES]
        entries += [("collection", "looks", "Looks", "preferences-desktop-appearance-symbolic"),
                    ("collection", "library", "Library", "folder-pictures-symbolic"),
                    ("collection", "updates", "Updates", "software-update-available-symbolic"),
                    ("collection", "downloads", "Downloads", "folder-download-symbolic")]
        entries += [("fonts", s.id, s.name, s.icon) for s in FONT_SOURCES]
        entries += [("fonts", "my-fonts", "My Fonts", "preferences-desktop-font-symbolic")]
        from orbits.themes import KINDS

        entries += [("themes", f"theme-{k.id}", k.title, k.icon) for k in KINDS.values()]
        entries += [("themes", "app-colors", "App Colors", "preferences-color-symbolic")]
        entries += [("themes", "bootloader", "Bootloader Themes", "drive-harddisk-symbolic")]
        entries += [("system", "gdm", "Login Screen", "system-lock-screen-symbolic")]
        font_sources = {s.id: s for s in FONT_SOURCES}
        for section, pid, title, icon in entries:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(spacing=12, margin_start=6, margin_end=6, margin_top=8, margin_bottom=8)
            box.append(Gtk.Image(icon_name=icon))
            box.append(Gtk.Label(label=tr(title), xalign=0, hexpand=True))
            if pid == "library":
                self.library_count = Gtk.Label(css_classes=["dim-label", "caption"])
                box.append(self.library_count)
            elif pid == "my-fonts":
                self.fonts_count = Gtk.Label(css_classes=["dim-label", "caption"])
                box.append(self.fonts_count)
            elif pid == "updates":
                self.updates_count = Gtk.Label(css_classes=["badge-count"], visible=False)
                box.append(self.updates_count)
            row.set_child(box)
            row.page_id, row.section = pid, section
            self.sidebar.append(row)
            self._rows.append(row)

            if pid == "library":
                page = LibraryPage(self)
            elif pid == "gdm":
                page = GdmPage(self)
            elif pid == "my-fonts":
                page = FontsPage(self)
            elif pid == "downloads":
                from orbits.ui.downloads_page import DownloadsPage
                page = DownloadsPage(self)
            elif pid == "updates":
                from orbits.ui.updates_page import UpdatesPage

                page = UpdatesPage(self)
            elif pid in font_sources:
                page = FontBrowsePage(self, font_sources[pid])
            elif pid == "looks":
                from orbits.ui.looks_page import LooksPage

                page = LooksPage(self)
            elif pid == "bootloader":
                from orbits.ui.boot_page import BootPage

                page = BootPage(self)
            elif pid == "app-colors":
                from orbits.ui.colors_page import ColorsPage

                page = ColorsPage(self)
            elif pid.startswith("theme-"):
                from orbits.ui.themes_page import ThemesPage

                page = ThemesPage(self, pid.removeprefix("theme-"))
            else:
                page = BrowsePage(self, next(s for s in SOURCES if s.id == pid))
            self.pages[pid] = page
            self.content_stack.add_named(page, pid)

        self.sidebar.connect("row-activated", self._on_row)

        sb_header = Adw.HeaderBar()
        menu = Gio.Menu()
        menu.append(tr("Search Everywhere"), "app.global-search")
        menu.append(tr("Quick Switcher"), "app.quick-switcher")
        menu.append(tr("Preferences"), "app.preferences")
        menu.append(tr("System Check"), "app.system-check")
        menu.append(tr("Welcome Tour"), "app.welcome")
        menu.append(tr("About Orbits"), "app.about")
        search_button = Gtk.Button(icon_name="system-search-symbolic", tooltip_text=tr("Search Everywhere (Ctrl+K)"))
        search_button.connect("clicked", lambda *_: self.get_application().activate_action("global-search", None))
        sb_header.pack_start(search_button)
        sb_header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text=tr("Main Menu")))
        sb_view = Adw.ToolbarView()
        sb_view.add_top_bar(sb_header)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(self.sidebar)
        sb_view.set_content(scroller)
        self.split.set_sidebar(Adw.NavigationPage(title=tr("Orbits"), child=sb_view))
        self.content_page = Adw.NavigationPage(title=tr("Explore"), child=self.content_stack)
        self.split.set_content(self.content_page)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)

        self.root_stack.add_named(self.split, "main")

        from orbits.library import get_library

        from orbits.fonts import get_font_library

        get_library().connect("changed", lambda *_: self._update_count())
        get_font_library().connect("changed", lambda *_: self._update_count())
        from orbits.updates import get_checker

        checker = get_checker()
        checker.connect("changed", lambda *_: self._update_count())
        checker.check_if_due(self._updates_found)
        self._update_count()
        self._main_built = True
        self.navigate(settings.get("default_source") or SOURCES[0].id)

    def _updates_found(self, updates):
        if updates:
            self.toast(tr('{value0} update{value1} available').format(value0=len(updates), value1='s' if len(updates) != 1 else ''), button="View",
                       action=lambda: self.navigate("updates"))

    def _sidebar_header(self, row, before):
        if before is None or before.section != row.section:
            names = {"explore": "WALLPAPERS", "collection": "COLLECTION", "fonts": "FONTS", "themes": "THEMES",
                     "system": "SYSTEM"}
            row.set_header(Gtk.Label(label=names[row.section], xalign=0, css_classes=["sidebar-heading"]))
        else:
            row.set_header(None)

    def _update_count(self):
        from orbits.library import get_library

        from orbits.fonts import get_font_library

        n = len(get_library().items)
        self.library_count.set_label(str(n) if n else "")
        f = len(get_font_library().items)
        self.fonts_count.set_label(str(f) if f else "")
        from orbits.updates import get_checker

        u = len(get_checker().updates)
        self.updates_count.set_label(str(u))
        self.updates_count.set_visible(u > 0)

    def _on_row(self, _lb, row):
        self.navigate(row.page_id)

    def navigate(self, page_id):
        if not self._main_built:
            return
        row = next((r for r in self._rows if r.page_id == page_id), None)
        if row is None:
            return
        self.sidebar.select_row(row)
        self.content_stack.set_visible_child_name(page_id)
        page = self.pages[page_id]
        self.content_page.set_title(page.title)
        if hasattr(page, "on_shown"):
            page.on_shown()
        self.split.set_show_content(True)

    def _current_page_call(self, method, *args):
        if not self._main_built:
            return
        page = self.content_stack.get_visible_child()
        if hasattr(page, method):
            getattr(page, method)(*args)

    def _offer_undo(self, record):
        def offer():
            def undo():
                try:
                    if record.undo() is False:
                        return
                    for page in getattr(self, 'pages', {}).values():
                        if hasattr(page, 'reload') and not hasattr(page, 'source'):
                            page.reload()
                    self.toast(tr("Change undone"))
                except Exception as error:
                    self.toast(str(error), timeout=6)
            self.toast(record.label, timeout=10, button="Undo", action=undo)
            return False
        GLib.idle_add(offer)

    def toast(self, text, timeout=3, button=None, action=None):
        t = Adw.Toast(title=text, timeout=timeout)
        if button and action:
            t.set_button_label(button)
            t.connect("button-clicked", lambda *_: action())
        self.toasts.add_toast(t)

    def open_search_result(self, record):
        self._show_main()
        self.navigate(record['page'])
        page = self.pages.get(record['page'])
        if record['kind'] == 'wallpaper':
            from orbits.ui.library_page import LibraryDialog
            entry = page.lib.items.get(record['key'])
            if entry:
                LibraryDialog(page, entry).present(self)
        elif record['kind'] == 'font':
            page.search.set_text(record['title'])
            page.search.grab_focus()
        elif record['kind'] == 'look':
            from orbits import looks
            look = next((item for item in looks.get_store().looks() if item['_dir'].endswith('/'+record['key'])), None)
            if look:
                page.apply(look)
        elif record['kind'] == 'theme':
            page.stack.set_visible_child_name('installed')
            page.reload()
            for position, theme in enumerate(page.installed_items):
                if theme.meta.get('key') == record['key'] or theme.name == record.get('theme_name'):
                    if hasattr(page.grid, 'scroll_to'):
                        page.grid.scroll_to(position, Gtk.ListScrollFlags.FOCUS, None)
                    else:
                        page.grid.grab_focus()
                    break

    def use_for_login_screen(self, path):
        self.navigate("gdm")
        self.pages["gdm"].set_background_image(path)

    def _on_close(self, *_):
        self._undo_unsubscribe()
        w, h = self.get_default_size()
        if not self.is_maximized():
            settings.data["window_width"], settings.data["window_height"] = w, h
            settings.save()
        return False
