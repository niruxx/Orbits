"""Installed wallpapers."""
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from orbits.config import settings
from orbits.library import IMAGE_EXTS, get_library
from orbits.sources import BY_ID
from orbits.ui.widgets import CoverPicture, WallCard, load_texture_into, make_grid, open_uri, status_page

FILTERS = [("all", "All")] + [(s.id, s.name) for s in BY_ID.values()] + [("local", "Imported")]


class LibraryPage(Adw.Bin):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.title = "Library"
        self.lib = get_library()
        self.filter = "all"
        self.query = ""

        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title="Library", subtitle="Your installed wallpapers"))
        add = Gtk.Button(tooltip_text="Import Images…")
        add.set_child(Adw.ButtonContent(icon_name="list-add-symbolic", label="Import"))
        add.connect("clicked", self._import)
        header.pack_start(add)
        self.search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text="Search (Ctrl+F)")
        header.pack_end(self.search_btn)
        folder = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text="Open Download Folder")
        folder.connect("clicked", lambda *_: self._open_folder())
        header.pack_end(folder)

        self.search_bar = Gtk.SearchBar()
        self.search_entry = Gtk.SearchEntry(placeholder_text="Filter by name…")
        self.search_entry.connect("search-changed", self._on_search)
        self.search_bar.set_child(Adw.Clamp(maximum_size=520, child=self.search_entry))
        self.search_bar.connect_entry(self.search_entry)
        self.search_btn.connect("toggled", lambda b: self.search_bar.set_search_mode(b.get_active()))
        self.search_bar.connect("notify::search-mode-enabled",
                                lambda sb, _p: self.search_btn.set_active(sb.get_search_mode()))

        chips = Gtk.Box(spacing=8, css_classes=["chip-row"], margin_start=24, margin_end=24, margin_top=6,
                        margin_bottom=6)
        group = None
        for fid, label in FILTERS:
            b = Gtk.ToggleButton(label=label, css_classes=["chip"], active=fid == "all")
            if group:
                b.set_group(group)
            else:
                group = b
            b.connect("toggled", lambda btn, f=fid: btn.get_active() and self._set_filter(f))
            chips.append(b)

        self.grid = make_grid()
        self.grid.connect("child-activated", self._on_activate)
        self.grid.set_filter_func(self._filter_func)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                      child=Adw.Clamp(maximum_size=1800, child=self.grid))

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(scroller, "grid")
        self.stack.add_named(status_page("folder-pictures-symbolic", "No Wallpapers Yet",
                                         "Install some from the KDE Store, Wallhaven or Bing — or import your own.",
                                         "Start Exploring", lambda: self.window.navigate("kde")), "empty")

        view = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT)
        view.add_top_bar(header)
        view.add_top_bar(self.search_bar)
        view.add_top_bar(Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, child=chips,
                                            propagate_natural_height=True))
        view.set_content(self.stack)
        self.set_child(view)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)

        self.cards = {}
        self.lib.connect("changed", lambda *_: self.rebuild())
        self.rebuild()

    def toggle_search(self):
        self.search_btn.set_active(not self.search_btn.get_active())

    def rebuild(self):
        items = self.lib.sorted_items()
        keys = [i["key"] for i in items]
        if keys != list(self.cards):
            self.grid.remove_all()
            old, self.cards = self.cards, {}
            for entry in items:
                card = old.get(entry["key"]) or self._make_card(entry)
                card.entry = entry
                self.cards[entry["key"]] = card
                self.grid.append(card)
        for card in self.cards.values():
            current = self.lib.is_current(card.entry)
            n = len(card.entry["files"])
            card.set_badge("● Current" if current else (f"{n} wallpapers" if n > 1 else ""), accent=current)
        self.stack.set_visible_child_name("grid" if items else "empty")
        self.grid.invalidate_filter()

    def _make_card(self, entry):
        src = BY_ID.get(entry["source"])
        card = WallCard(entry["title"], src.name if src else "Imported")
        card.entry = entry
        card.add_action_button("user-trash-symbolic", "Uninstall", lambda: self.uninstall(card.entry))
        card.add_action_button("preferences-desktop-wallpaper-symbolic", "Set as Wallpaper",
                               lambda: self.apply(card.entry, card.entry["files"][0]))
        card.load(path=entry["files"][0]["light"])
        return card

    def _filter_func(self, child):
        e = child.get_child().entry
        if self.filter != "all" and e["source"] != self.filter:
            return False
        return not self.query or self.query in e["title"].lower()

    def _set_filter(self, f):
        self.filter = f
        self.grid.invalidate_filter()

    def _on_search(self, entry):
        self.query = entry.get_text().strip().lower()
        self.grid.invalidate_filter()

    # ---------------------------------------------------------------- actions
    def apply(self, entry, rec, lockscreen=None):
        lock = settings.get("apply_lockscreen_too") if lockscreen is None else lockscreen
        self.lib.apply(rec, desktop=True, lockscreen=lock)
        self.window.toast(f"Wallpaper set to “{entry['title']}”")

    def uninstall(self, entry):
        dlg = Adw.AlertDialog(heading=f"Uninstall “{entry['title']}”?",
                              body=f"{len(entry['files'])} file(s) will be deleted from disk.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("remove", "Uninstall")
        dlg.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "remove" and self._do_uninstall(entry))
        dlg.present(self.window)

    def _do_uninstall(self, entry):
        self.lib.uninstall(entry["key"])
        self.window.toast(f"Uninstalled “{entry['title']}”")

    def _open_folder(self):
        from orbits.config import download_dir

        Gtk.FileLauncher.new(Gio.File.new_for_path(str(download_dir()))).launch(self.window, None, None)

    def _import(self, *_):
        filt = Gtk.FileFilter(name="Images")
        for ext in IMAGE_EXTS:
            filt.add_suffix(ext.lstrip("."))
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(filt)
        dlg = Gtk.FileDialog(title="Import Wallpapers", filters=store)

        def done(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            self._import_paths([f.get_path() for f in files if f.get_path()])

        dlg.open_multiple(self.window, None, done)

    def _import_paths(self, paths):
        n = self.lib.import_files(paths)
        self.window.toast(f"Imported {n} wallpaper{'s' if n != 1 else ''}" if n else "No supported images found")

    def _on_drop(self, _t, value, _x, _y):
        self._import_paths([f.get_path() for f in value.get_files() if f.get_path()])
        return True

    def _on_activate(self, _grid, child):
        LibraryDialog(self, child.get_child().entry).present(self.window)


class LibraryDialog(Adw.Dialog):
    """Browse the variants of an installed item and apply them."""

    def __init__(self, page, entry):
        super().__init__(title=entry["title"], content_width=940, content_height=720)
        self.page, self.entry = page, entry
        files = entry["files"]

        self.carousel = Adw.Carousel(hexpand=True, spacing=12, height_request=460)
        for rec in files[:60]:
            frame = Gtk.Overlay(css_classes=["detail-hero"], overflow=Gtk.Overflow.HIDDEN)
            pic = CoverPicture(860, 460, hexpand=True)
            frame.set_child(pic)
            load_texture_into(pic, path=rec["light"], size=1600)
            if rec.get("dark"):
                frame.add_overlay(Gtk.Label(label="Light + Dark", css_classes=["badge"], halign=Gtk.Align.START,
                                            valign=Gtk.Align.START))
            self.carousel.append(frame)
        hero_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, css_classes=["wall-card"])
        hero_box.append(self.carousel)
        if len(files) > 1:
            hero_box.append(Adw.CarouselIndicatorDots(carousel=self.carousel))

        src = BY_ID.get(entry["source"])
        title = Gtk.Label(label=entry["title"], xalign=0, wrap=True, css_classes=["detail-title"])
        sub = " · ".join(x for x in [entry.get("author") and f"by {entry['author']}",
                                     src.name if src else "Imported",
                                     f"{len(files)} wallpaper{'s' if len(files) != 1 else ''}"] if x)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        info.append(title)
        info.append(Gtk.Label(label=sub, xalign=0, css_classes=["dim-label"]))

        buttons = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        rm = Gtk.Button(icon_name="user-trash-symbolic", css_classes=["circular", "destructive-action"],
                        tooltip_text="Uninstall")
        rm.connect("clicked", lambda *_: (self.close(), page.uninstall(entry)))
        buttons.append(rm)
        more = Gtk.MenuButton(icon_name="view-more-symbolic", css_classes=["circular"], tooltip_text="More")
        pop = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for label, cb in [("Set as Lock Screen", self._lock), ("Use for Login Screen", self._gdm),
                          ("Show in Files", self._show_file)]:
            b = Gtk.Button(label=label, css_classes=["flat"])
            b.connect("clicked", lambda _b, cb=cb: (more.popdown(), cb()))
            pop.append(b)
        more.set_popover(Gtk.Popover(child=pop))
        buttons.append(more)
        apply = Gtk.Button(css_classes=["pill", "suggested-action"])
        apply.set_child(Adw.ButtonContent(label="Set as Wallpaper", icon_name="preferences-desktop-wallpaper-symbolic"))
        apply.connect("clicked", lambda *_: page.apply(entry, self._rec()))
        buttons.append(apply)

        row = Gtk.Box(spacing=18, margin_top=18)
        row.append(info)
        row.append(buttons)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=24, margin_end=24, margin_bottom=24)
        content.append(hero_box)
        content.append(row)

        header = Adw.HeaderBar()
        if entry.get("page_url"):
            web = Gtk.Button(icon_name="web-browser-symbolic", tooltip_text="Open in Browser")
            web.connect("clicked", lambda *_: open_uri(self, entry["page_url"]))
            header.pack_start(web)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=content,
                                            propagate_natural_height=True))
        self.set_child(view)

    def _rec(self):
        return self.entry["files"][round(self.carousel.get_position())]

    def _lock(self):
        self.page.lib.apply(self._rec(), desktop=False, lockscreen=True)
        self.page.window.toast("Lock screen background updated")

    def _gdm(self):
        path = self._rec()["light"]
        self.close()
        self.page.window.use_for_login_screen(path)

    def _show_file(self):
        Gtk.FileLauncher.new(Gio.File.new_for_path(self._rec()["light"])).open_containing_folder(
            self.page.window, None, None)

