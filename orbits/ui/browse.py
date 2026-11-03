"""Online source browser page (one per source)."""
from gi.repository import Adw, GLib, GObject, Gtk

from orbits import net
from orbits.config import settings
from orbits.library import get_library
from orbits.ui.widgets import WallCard, make_grid, status_page


class BrowsePage(Adw.Bin):
    def __init__(self, window, source):
        super().__init__()
        self.window, self.source = window, source
        self.title = source.name
        self.lib = get_library()
        self.page = 1
        self.has_more = True
        self.loading = False
        self.generation = 0
        self.cards = {}
        self.query = ""
        self.sort = source.sorts[0][0] if source.sorts else ""
        self.category = source.categories[0][0] if source.categories else ""
        self._loaded_once = False

        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=source.name, subtitle=source.tagline))
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh")
        refresh.connect("clicked", lambda *_: self.reload())
        header.pack_end(refresh)
        if source.sorts:
            self.sort_dd = Gtk.DropDown.new_from_strings([label for _, label in source.sorts])
            self.sort_dd.set_tooltip_text("Sort")
            self.sort_dd.connect("notify::selected", self._on_sort)
            header.pack_end(self.sort_dd)

        self.search_bar = Gtk.SearchBar()
        if source.supports_search:
            self.search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text="Search (Ctrl+F)")
            header.pack_start(self.search_btn)
            self.search_entry = Gtk.SearchEntry(placeholder_text=f"Search {source.name}…", hexpand=True)
            self.search_entry.connect("activate", self._on_search)
            self.search_entry.connect("stop-search", lambda *_: self.search_btn.set_active(False))
            clamp = Adw.Clamp(maximum_size=520, child=self.search_entry)
            self.search_bar.set_child(clamp)
            self.search_bar.connect_entry(self.search_entry)
            self.search_btn.bind_property("active", self.search_bar, "search-mode-enabled",
                                          GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)

        chips = Gtk.Box(spacing=8, css_classes=["chip-row"], margin_start=24, margin_end=24,
                        margin_top=6, margin_bottom=6)
        group = None
        for cid, label in source.categories:
            b = Gtk.ToggleButton(label=label, css_classes=["chip"], active=cid == self.category)
            if group:
                b.set_group(group)
            else:
                group = b
            b.connect("toggled", self._on_chip, cid)
            chips.append(b)
        chip_scroll = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, child=chips)
        chip_scroll.set_propagate_natural_height(True)

        self.grid = make_grid()
        self.grid.connect("child-activated", self._on_activate)
        self.more_spinner = Adw.Spinner(height_request=32, margin_bottom=24, visible=False)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.grid)
        body.append(self.more_spinner)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                           child=Adw.Clamp(maximum_size=1800, child=body))
        self.scroller.connect("edge-reached", self._on_edge)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(Adw.Spinner(width_request=48, height_request=48, halign=Gtk.Align.CENTER,
                                         valign=Gtk.Align.CENTER), "loading")
        self.stack.add_named(self.scroller, "grid")
        self.error_page = status_page("network-offline-symbolic", "Couldn't Load Wallpapers", "",
                                      "Try Again", self.reload)
        self.stack.add_named(self.error_page, "error")
        self.stack.add_named(status_page("system-search-symbolic", "No Results",
                                         "Try a different search or category."), "empty")

        view = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT)
        view.add_top_bar(header)
        view.add_top_bar(self.search_bar)
        if source.categories:
            view.add_top_bar(chip_scroll)
        view.set_content(self.stack)
        self.set_child(view)

        self.lib.connect("changed", lambda *_: self._refresh_badges())

    # ---------------------------------------------------------------- loading
    def on_shown(self):
        if not self._loaded_once:
            self._loaded_once = True
            self.reload()

    def toggle_search(self):
        if self.source.supports_search:
            self.search_btn.set_active(not self.search_btn.get_active())

    def reload(self):
        self.generation += 1
        self.page, self.has_more, self.loading = 1, True, False
        self.cards.clear()
        self.grid.remove_all()
        self.stack.set_visible_child_name("loading")
        self._load_page()

    def _load_page(self):
        if self.loading or not self.has_more:
            return
        self.loading = True
        gen = self.generation
        if self.page > 1:
            self.more_spinner.set_visible(True)

        def done(result, err):
            if gen != self.generation:
                return
            self.loading = False
            self.more_spinner.set_visible(False)
            if err:
                if self.page == 1:
                    self.error_page.set_description(str(err))
                    self.stack.set_visible_child_name("error")
                else:
                    self.window.toast(f"Couldn't load more: {err}")
                return
            items, self.has_more = result
            for item in items:
                if item.key not in self.cards:
                    self._add_card(item)
            if self.page == 1:
                self.stack.set_visible_child_name("grid" if items else "empty")
            self.page += 1
            # keep filling if the first page doesn't make the view scrollable
            GLib.timeout_add(300, self._maybe_fill, gen)

        net.run_async(self.source.fetch, done, self.query, self.sort, self.category, self.page)

    def _maybe_fill(self, gen):
        adj = self.scroller.get_vadjustment()
        if gen == self.generation and adj.get_upper() <= adj.get_page_size() + 50:
            self._load_page()
        return False

    def _on_edge(self, _sw, pos):
        if pos == Gtk.PositionType.BOTTOM:
            self._load_page()

    # ---------------------------------------------------------------- cards
    def _add_card(self, item):
        sub = item.author or item.stats
        card = WallCard(item.title, sub)
        card.item = item
        card.install_btn = card.add_action_button("folder-download-symbolic", "Install",
                                                  lambda: self.install(item))
        card.add_action_button("preferences-desktop-wallpaper-symbolic", "Set as Wallpaper",
                               lambda: self.install(item, apply=True))
        card.load(url=item.thumb_url or item.preview_url)
        self.cards[item.key] = card
        self.grid.append(card)
        self._update_card(card)

    def _update_card(self, card):
        key = card.item.key
        installed = self.lib.is_installed(key)
        busy = key in self.lib.busy
        card.set_badge("Installing…" if busy else ("✓ Installed" if installed else ""), accent=installed)
        card.install_btn.set_visible(not installed and not busy)
        card.set_progress(self.lib.busy[key] if busy else None)

    def _refresh_badges(self):
        for card in self.cards.values():
            self._update_card(card)

    def install(self, item, download=None, apply=False, on_done=None):
        if self.lib.is_installed(item.key):
            if apply:
                self._apply(self.lib.items[item.key])
            return
        card = self.cards.get(item.key)

        def progress(frac):
            if card:
                card.set_progress(frac)

        def done(entry, err):
            if err:
                self.window.toast(f"Install failed: {err}", timeout=5)
            else:
                n = len(entry["files"])
                if apply:
                    self._apply(entry)
                else:
                    self.window.toast(f"Installed “{item.title}”" + (f" · {n} wallpapers" if n > 1 else ""),
                                      button="Set as Wallpaper", action=lambda: self._apply(entry))
            if on_done:
                on_done(entry, err)

        self.lib.install(item, download, done, progress)

    def _apply(self, entry):
        self.lib.apply(entry["files"][0], desktop=True, lockscreen=settings.get("apply_lockscreen_too"))
        self.window.toast(f"Wallpaper set to “{entry['title']}”")

    def _on_activate(self, _grid, child):
        from orbits.ui.detail import DetailDialog

        DetailDialog(self, child.get_child().item).present(self.window)

    # ---------------------------------------------------------------- filters
    def _on_sort(self, dd, _p):
        self.sort = self.source.sorts[dd.get_selected()][0]
        self.reload()

    def _on_chip(self, btn, cid):
        if btn.get_active() and cid != self.category:
            self.category = cid
            self.reload()

    def _on_search(self, entry):
        self.query = entry.get_text().strip()
        self.reload()
