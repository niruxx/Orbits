"""Online font browser (Google Fonts / Fontsource, Nerd Fonts)."""
from orbits.i18n import _ as tr
from gi.repository import Adw, GLib, GObject, Gtk, PangoCairo

from orbits.config import settings
from orbits import net
from orbits.preview_jobs import track
from orbits.fonts import get_font_library, preview_font_file
from orbits.ui.font_preview import load as load_font_preview
from orbits.ui.font_actions import apply_menu_button, font_attrs
from orbits.ui.widgets import open_uri, status_page

from orbits.ui.item_grid import ItemGrid

SAMPLES = {
    "monospace": "fn main() { println!(\"0O 1lI {}\"); }",
    "handwriting": "Dear friend, wish you were here",
    "display": "Bold ideas, big letters",
    "serif": "The quick brown fox jumps over the lazy dog",
}
DEFAULT_SAMPLE = "The quick brown fox jumps over the lazy dog"


class FontCard(Gtk.Box):
    def __init__(self, page, item):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["font-card"],
                         width_request=300)
        self.page, self.item = page, item
        top = Gtk.Box(spacing=8)
        self.big = Gtk.Picture(height_request=48, hexpand=True)
        top.append(self.big)
        self.badge = Gtk.Label(css_classes=["font-badge"], valign=Gtk.Align.START, visible=False)
        top.append(self.badge)
        self.install_btn = Gtk.Button(icon_name="folder-download-symbolic", tooltip_text=tr("Install"),
                                      css_classes=["circular"], valign=Gtk.Align.START)
        self.install_btn.connect("clicked", lambda *_: page.install(item))
        top.append(self.install_btn)
        self.append(top)
        self.sample = Gtk.Picture(height_request=36)
        self.append(self.sample)
        self.name = Gtk.Label(label=item.family, xalign=0, ellipsize=3, max_width_chars=26, css_classes=["heading"],
                              margin_top=4)
        self.append(self.name)
        self.sub = Gtk.Label(label=item.subtitle, xalign=0, ellipsize=3, max_width_chars=30,
                             css_classes=["dim-label", "caption"])
        self.append(self.sub)
        self.progress = Gtk.ProgressBar(visible=False)
        self.append(self.progress)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: page.open_detail(item))
        self.add_controller(click)
        self._load_preview()

    def _load_preview(self):
        if not self.item.preview_url:
            self.big.get_parent().prepend(Gtk.Label(label='Aa', xalign=0, hexpand=True))
            self.big.set_visible(False)
            self.sample.set_visible(False)
            fam = "Monospace" if self.item.category == "monospace" else None
            if fam:
                self.big.set_tooltip_text(tr("No downloadable preview is available"))
            return

        def done(path, err):
            if getattr(self, "_grid_released", False):
                return
            if path:
                load_font_preview(self.big, path, 'Aa', 30)
                load_font_preview(self.sample, path, SAMPLES.get(self.item.category, DEFAULT_SAMPLE), 13)
            elif err:
                self.big.set_tooltip_text(str(err))

        track(self, net.run_preview(lambda: None if getattr(self, "_grid_released", False) else preview_font_file(self.item), done))

    def update(self, lib):
        key = self.item.key
        installed, busy = lib.is_installed(key), key in lib.busy
        self.badge.set_visible(installed or busy)
        self.badge.set_label("Installing…" if busy else "✓ Installed")
        self.install_btn.set_visible(not installed and not busy)
        self.progress.set_visible(busy)
        if busy:
            self.progress.set_fraction(lib.busy[key])


class FontBrowsePage(Adw.Bin):
    def __init__(self, window, source):
        super().__init__()
        self.window, self.source = window, source
        self.title = source.name
        self.lib = get_font_library()
        self.query, self.page, self.has_more, self.loading, self.generation = "", 1, True, False, 0
        self.sort = source.sorts[0][0] if source.sorts else ""
        self.category = source.categories[0][0] if source.categories else ""
        self.items = {}
        self._loaded_once = False

        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=source.name, subtitle=source.tagline))
        self.search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text=tr("Search (Ctrl+F)"))
        header.pack_start(self.search_btn)
        if len(source.sorts) > 1:
            dd = Gtk.DropDown.new_from_strings([label for _, label in source.sorts])
            dd.connect("notify::selected", self._on_sort)
            header.pack_end(dd)
        self.search_bar = Gtk.SearchBar()
        self.search_entry = Gtk.SearchEntry(placeholder_text=tr('Search {value0}…').format(value0=source.name))
        self.search_entry.connect("search-changed", self._on_search)
        self.search_bar.set_child(Adw.Clamp(maximum_size=520, child=self.search_entry))
        self.search_bar.connect_entry(self.search_entry)
        self.search_btn.bind_property("active", self.search_bar, "search-mode-enabled",
                                      GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)

        chips = Gtk.Box(spacing=8, css_classes=["chip-row"], margin_start=24, margin_end=24, margin_top=6,
                        margin_bottom=6)
        group = None
        for cid, label in source.categories:
            b = Gtk.ToggleButton(label=label, css_classes=["chip"], active=cid == self.category)
            if group:
                b.set_group(group)
            else:
                group = b
            b.connect("toggled", lambda btn, c=cid: btn.get_active() and self._set_category(c))
            chips.append(b)

        self.grid = ItemGrid(self._make_card, self.open_detail, max_columns=6,
                             css_class='font-grid', single_click=False)
        self.more = Adw.Spinner(height_request=32, margin_bottom=24, visible=False)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                           child=self.grid)
        self.scroller.connect("edge-reached", lambda _s, pos: pos == Gtk.PositionType.BOTTOM and self._load())

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(Adw.Spinner(width_request=48, height_request=48, halign=Gtk.Align.CENTER,
                                         valign=Gtk.Align.CENTER), "loading")
        self.stack.add_named(self.scroller, "grid")
        self.error = status_page("network-offline-symbolic", "Couldn't Load Fonts", "", "Try Again", self.reload)
        self.stack.add_named(self.error, "error")
        self.stack.add_named(status_page("system-search-symbolic", "No Fonts Found",
                                         "Try another search or category."), "empty")

        view = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT)
        view.add_top_bar(header)
        self.offline_banner = Adw.Banner(title=tr("You are offline — showing a cached catalogue"), revealed=False)
        view.add_top_bar(self.offline_banner)
        view.add_top_bar(self.search_bar)
        if source.categories:
            view.add_top_bar(Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, child=chips,
                                                propagate_natural_height=True))
        view.add_bottom_bar(self.more)
        view.set_content(self.stack)
        self.set_child(view)
        self.lib.connect("changed", lambda *_: self.grid.refresh(lambda card: card.update(self.lib)))

    def _make_card(self, item):
        card = FontCard(self, item)
        card.update(self.lib)
        return card

    def on_shown(self):
        if not self._loaded_once:
            self._loaded_once = True
            self.reload()

    def toggle_search(self):
        self.search_btn.set_active(not self.search_btn.get_active())

    def reload(self):
        self.generation += 1
        self.page, self.has_more, self.loading = 1, True, False
        self.items.clear()
        self.grid.remove_all()
        self.stack.set_visible_child_name("loading")
        self._load()

    def _load(self):
        if self.loading or not self.has_more:
            return
        self.loading = True
        gen = self.generation
        self.more.set_visible(self.page > 1)

        def done(res, err):
            if gen != self.generation:
                return
            self.loading = False
            self.more.set_visible(False)
            if err:
                if self.page == 1:
                    self.error.set_description(str(err))
                    self.stack.set_visible_child_name("error")
                return
            res, cached = res
            self.offline_banner.set_revealed(cached or bool(settings.get("offline", False)))
            items, self.has_more = res
            for it in items:
                if it.key not in self.items:
                    self.items[it.key] = it
                    self.grid.append(it)
            if self.page == 1:
                self.stack.set_visible_child_name("grid" if items else "empty")
            self.page += 1
            GLib.timeout_add(300, self._fill, gen)

        net.run_async(net.with_status, done, self.source.fetch, self.query, self.sort, self.category, self.page)

    def _fill(self, gen):
        adj = self.scroller.get_vadjustment()
        if gen == self.generation and adj.get_upper() <= adj.get_page_size() + 50:
            self._load()
        return False

    def _on_sort(self, dd, _p):
        self.sort = self.source.sorts[dd.get_selected()][0]
        self.reload()

    def _set_category(self, c):
        if c != self.category:
            self.category = c
            self.reload()

    def _on_search(self, entry):
        self.query = entry.get_text().strip()
        if getattr(self, "_search_timer", None):
            GLib.source_remove(self._search_timer)
        self._search_timer = GLib.timeout_add(300, lambda: (setattr(self, "_search_timer", None),
                                                            self.reload(), False)[2])

    def install(self, item, on_done=None):
        if self.lib.is_installed(item.key):
            return

        def done(entry, err):
            if err:
                self.window.toast(tr('Font install failed: {value0}').format(value0=err), timeout=6)
            else:
                fams = entry["families"]
                self.window.toast(f"Installed “{entry['family']}”" + (f" · {len(fams)} families" if len(fams) > 1
                                                                      else ""))
            if on_done:
                on_done(entry, err)

        self.lib.install(item, done, lambda f: (c := self.grid.find_card(lambda card: card.item.key == item.key)) and c.update(self.lib))

    def open_detail(self, item):
        FontDetailDialog(self, item).present(self.window)


class FontDetailDialog(Adw.Dialog):
    def __init__(self, page, item):
        super().__init__(title=item.family, content_width=760, content_height=640)
        self.page, self.item, self.lib = page, item, page.lib
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=24, margin_end=24,
                      margin_bottom=24)
        self.display = Gtk.Picture(height_request=60)
        box.append(self.display)
        box.append(Gtk.Label(label=" · ".join(x for x in [item.subtitle, item.license] if x), xalign=0,
                             css_classes=["dim-label"]))
        self.entry = Gtk.Entry(text=SAMPLES.get(item.category, DEFAULT_SAMPLE), placeholder_text=tr("Type to preview…"),
                               margin_top=8)
        box.append(self.entry)
        self.samples = []
        for size in (40, 28, 18, 12):
            lbl = Gtk.Picture(height_request=size + 20)
            self.samples.append((lbl, size))
            box.append(lbl)
        alpha = Gtk.Picture(height_request=100, margin_top=12)
        self.samples.append((alpha, 16))
        box.append(alpha)
        self.entry.connect("changed", lambda *_: self._update_text())
        self._update_text()
        self._apply_font()

        self.buttons = Gtk.Box(spacing=8)
        header = Adw.HeaderBar()
        header.pack_end(self.buttons)
        if item.page_url:
            web = Gtk.Button(icon_name="web-browser-symbolic", tooltip_text=tr("Open in Browser"))
            web.connect("clicked", lambda *_: open_uri(self, item.page_url))
            header.pack_start(web)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=box,
                                            propagate_natural_height=True))
        self.set_child(view)
        self._h = self.lib.connect("changed", lambda *_: self._sync())
        self.connect("closed", lambda *_: self.lib.disconnect(self._h))
        self._sync()

    def _update_text(self):
        text = self.entry.get_text() or DEFAULT_SAMPLE
        if getattr(self, '_preview_path', None):
            for picture, size in self.samples[:-1]:
                load_font_preview(picture, self._preview_path, text, size)

    def _apply_font(self):
        def done(path, error):
            if error or not path:
                self.display.set_tooltip_text(str(error or 'No preview available'))
                return
            self._preview_path = path
            load_font_preview(self.display, path, self.item.family, 34)
            self._update_text()
            load_font_preview(self.samples[-1][0], path, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ\nabcdefghijklmnopqrstuvwxyz\n0123456789 !?&@#%(){}', 16)
        if self.item.preview_url:
            track(self, net.run_preview(lambda: None if getattr(self, "_grid_released", False) else preview_font_file(self.item), done))

    def _sync(self):
        while (c := self.buttons.get_first_child()):
            self.buttons.remove(c)
        key = self.item.key
        if key in self.lib.busy:
            self.buttons.append(Gtk.ProgressBar(fraction=self.lib.busy[key], width_request=140, show_text=True,
                                                text="Installing…", valign=Gtk.Align.CENTER))
        elif self.lib.is_installed(key):
            entry = self.lib.items[key]
            rm = Gtk.Button(css_classes=["destructive-action"])
            rm.set_child(Adw.ButtonContent(label=tr("Uninstall"), icon_name="user-trash-symbolic"))
            rm.connect("clicked", lambda *_: self.lib.uninstall_user(key))
            self.buttons.append(rm)
            self.buttons.append(apply_menu_button(self.page.window, lambda: entry["families"][0],
                                                  lambda: entry, label=tr("Apply")))
        else:
            b = Gtk.Button(css_classes=["suggested-action"])
            b.set_child(Adw.ButtonContent(label=tr("Install"), icon_name="folder-download-symbolic"))
            b.connect("clicked", lambda *_: self.page.install(self.item))
            self.buttons.append(b)
