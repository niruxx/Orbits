"""Online source browser page (one per source)."""
from orbits.i18n import _ as tr
from gi.repository import Adw, Gdk, GLib, GObject, Gtk

from orbits import net
from orbits.config import settings
from orbits.library import get_library
from orbits.ui.widgets import WallCard, status_page, open_uri
from orbits.theme_sources import web_url
from orbits.ui.item_grid import ItemGrid


class BrowsePage(Adw.Bin):
    def __init__(self, window, source, lib=None, apply_label="Set as Wallpaper",
                 apply_icon="preferences-desktop-wallpaper-symbolic", on_apply=None, describe=None,
                 title_widget=None, wallpaper_extras=True, on_install_error=None):
        """lib/on_apply/describe let other item types (themes) reuse this page.

        on_apply(entry) applies an installed entry; describe(entry) returns the "Installed …" toast text.
        """
        super().__init__()
        self.window, self.source = window, source
        self.title = source.name
        self.lib = lib or get_library()
        self.apply_label, self.apply_icon = apply_label, apply_icon
        self._on_apply, self._describe = on_apply, describe
        self._on_install_error = on_install_error
        self.wallpaper_extras = wallpaper_extras
        self.page = 1
        self.has_more = True
        self.loading = False
        self.generation = 0
        self.items = {}
        self.query = ""
        self.sort = source.sorts[0][0] if source.sorts else ""
        self.category = source.categories[0][0] if source.categories else ""
        self._loaded_once = False

        header = Adw.HeaderBar(title_widget=title_widget or Adw.WindowTitle(title=source.name,
                                                                            subtitle=source.tagline))
        source_url = web_url(getattr(source, "source_url", ""))
        if source_url and not wallpaper_extras:
            origin = Gtk.Button(child=Adw.ButtonContent(label=tr("Browse Source"), icon_name="external-link-symbolic"),
                                tooltip_text=source_url)
            origin.connect("clicked", lambda *_: open_uri(self.window, source_url))
            header.pack_start(origin)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=tr("Refresh"))
        refresh.connect("clicked", lambda *_: self.reload())
        header.pack_end(refresh)
        if source.sorts:
            self.sort_dd = Gtk.DropDown.new_from_strings([label for _, label in source.sorts])
            self.sort_dd.set_tooltip_text(tr("Sort"))
            self.sort_dd.connect("notify::selected", self._on_sort)
            header.pack_end(self.sort_dd)

        self.search_bar = Gtk.SearchBar()
        if source.supports_search:
            self.search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text=tr("Search (Ctrl+F)"))
            header.pack_start(self.search_btn)
            self.search_entry = Gtk.SearchEntry(placeholder_text=tr('Search {value0}…').format(value0=source.name), hexpand=True)
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

        self.grid = ItemGrid(self._make_card, self._open_detail)
        if wallpaper_extras:
            self.grid.set_filter_func(self._wallpaper_filter)
            controls = Gtk.Box(spacing=12, margin_start=24, margin_end=24, margin_top=6, margin_bottom=6)
            self.hide_installed = Gtk.CheckButton(label=tr("Hide Installed"))
            self.fits_screen = Gtk.CheckButton(label=tr("Fits My Screen"))
            for button in (self.hide_installed, self.fits_screen):
                button.connect("toggled", lambda *_: self.grid.invalidate_filter())
                controls.append(button)
        self.more_spinner = Adw.Spinner(height_request=32, margin_bottom=24, visible=False)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                           child=self.grid)
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
        self.offline_banner = Adw.Banner(title=tr("You are offline — showing a cached catalogue"), revealed=False)
        view.add_top_bar(self.offline_banner)
        view.add_top_bar(self.search_bar)
        if wallpaper_extras:
            view.add_top_bar(controls)
        if source.categories:
            view.add_top_bar(chip_scroll)
        view.add_bottom_bar(self.more_spinner)
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
        self.items.clear()
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
                    self.window.toast(tr("Couldn't load more: {value0}").format(value0=err))
                return
            result, cached = result
            self.offline_banner.set_revealed(cached or bool(settings.get("offline", False)))
            items, self.has_more = result
            for item in items:
                if item.key not in self.items:
                    self.items[item.key] = item
                    self.grid.append(item)
            if self.page == 1:
                self.stack.set_visible_child_name("grid" if items else "empty")
            self.page += 1
            # keep filling if the first page doesn't make the view scrollable
            GLib.timeout_add(300, self._maybe_fill, gen)

        net.run_async(net.with_status, done, self.source.fetch, self.query, self.sort, self.category, self.page)

    def _maybe_fill(self, gen):
        adj = self.scroller.get_vadjustment()
        if gen == self.generation and adj.get_upper() <= adj.get_page_size() + 50:
            self._load_page()
        return False

    def _on_edge(self, _sw, pos):
        if pos == Gtk.PositionType.BOTTOM:
            self._load_page()

    # ---------------------------------------------------------------- cards
    def _make_card(self, item):
        sub = item.author or item.stats
        card = WallCard(item.title, sub)
        card.item = item
        card.install_btn = card.add_action_button("folder-download-symbolic", tr("Install"),
                                                  lambda: self.install(item))
        card.add_action_button(self.apply_icon, self.apply_label, lambda: self.install(item, apply=True))
        card.load(url=item.thumb_url or item.preview_url)
        self._update_card(card)
        return card

    def _update_card(self, card):
        key = card.item.key
        installed = self.lib.is_installed(key)
        busy = key in self.lib.busy
        card.set_badge("Installing…" if busy else ("✓ Installed" if installed else ""), accent=installed)
        card.install_btn.set_visible(not installed and not busy)
        card.set_progress(self.lib.busy[key] if busy else None)

    def _wallpaper_filter(self, item):
        if self.hide_installed.get_active() and self.lib.is_installed(item.key):
            return False
        if self.fits_screen.get_active():
            from orbits.wallpaper_tools import fits_resolution
            monitors = Gdk.Display.get_default().get_monitors()
            if monitors.get_n_items():
                monitor = monitors.get_item(0)
                geometry = monitor.get_geometry()
                scale = monitor.get_scale_factor()
                return fits_resolution(item.resolution, geometry.width * scale, geometry.height * scale)
        return True

    def _refresh_badges(self):
        if self.wallpaper_extras:
            self.grid.invalidate_filter()
        self.grid.refresh(self._update_card)

    def install(self, item, download=None, apply=False, on_done=None):
        if self.lib.is_installed(item.key):
            if apply:
                self._apply(self.lib.items[item.key])
            return
        def progress(frac):
            card = self.grid.find_card(lambda card: card.item.key == item.key)
            if card:
                card.set_progress(frac)

        def done(entry, err):
            if err:
                if self._on_install_error:
                    self._on_install_error(err)
                else:
                    self.window.toast(tr('Install failed: {value0}').format(value0=err), timeout=5)
            else:
                if apply:
                    self._apply(entry)
                else:
                    if self._describe:
                        text = self._describe(entry)
                    else:
                        n = len(entry["files"])
                        text = f"Installed “{item.title}”" + (f" · {n} wallpapers" if n > 1 else "")
                    self.window.toast(text, button=self.apply_label, action=lambda: self._apply(entry))
            if on_done:
                on_done(entry, err)

        self.lib.install(item, download, done, progress)

    def _apply(self, entry):
        if self._on_apply:
            self._on_apply(entry)
            return
        self.lib.apply(entry["files"][0], desktop=True, lockscreen=settings.get("apply_lockscreen_too"))
        self.window.toast(tr('Wallpaper set to “{value0}”').format(value0=entry['title']))

    def _open_detail(self, item):
        from orbits.ui.detail import DetailDialog

        DetailDialog(self, item).present(self.window)

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
