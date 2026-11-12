"""Installed wallpapers."""
from orbits.i18n import _ as tr
from pathlib import Path
from orbits import net
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from orbits.config import settings
from orbits.library import IMAGE_EXTS, get_library
from orbits.sources import BY_ID
from orbits.ui.widgets import CoverPicture, WallCard, load_texture_into, open_uri, status_page

from orbits.ui.item_grid import ItemGrid

FILTERS = [("all", "All"), ("favorites", "Favorites")] + [(s.id, s.name) for s in BY_ID.values()] + [("local", "Imported")]


class LibraryPage(Adw.Bin):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.title = "Library"
        self.lib = get_library()
        self.filter = "all"
        self.query = ""
        self.collection = ""

        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=tr("Library"), subtitle=tr("Your installed wallpapers")))
        add = Gtk.Button(tooltip_text=tr("Import Images…"))
        add.set_child(Adw.ButtonContent(icon_name="list-add-symbolic", label=tr("Import")))
        add.connect("clicked", self._import)
        header.pack_start(add)
        self.search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text=tr("Search (Ctrl+F)"))
        header.pack_end(self.search_btn)
        folder = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=tr("Open Download Folder"))
        folder.connect("clicked", lambda *_: self._open_folder())
        header.pack_end(folder)
        tools = Gtk.Button(icon_name="document-properties-symbolic", tooltip_text=tr("Wallpaper Tools"))
        tools.connect("clicked", self._tools)
        header.pack_end(tools)

        self.search_bar = Gtk.SearchBar()
        self.search_entry = Gtk.SearchEntry(placeholder_text=tr("Filter by name…"))
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

        self.grid = ItemGrid(self._make_card, self._open_detail)
        self.grid.set_filter_func(self._filter_func)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                      child=self.grid)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(scroller, "grid")
        self.stack.add_named(status_page("folder-pictures-symbolic", "No Wallpapers Yet",
                                         "Install some from the KDE Store, Wallhaven or Bing — or import your own.",
                                         "Start Exploring", lambda: self.window.navigate("kde")), "empty")

        view = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT)
        view.add_top_bar(header)
        view.add_top_bar(self.search_bar)
        self.collection_row = Adw.ComboRow(title=tr("Collection"), model=Gtk.StringList.new(["All Collections"]))
        self.collection_names = [""]
        self.collection_row.connect("notify::selected", self._collection_changed)
        view.add_top_bar(self.collection_row)
        view.add_top_bar(Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, child=chips,
                                            propagate_natural_height=True))
        view.set_content(self.stack)
        self.set_child(view)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)

        self.lib.connect("changed", lambda *_: self.rebuild())
        self.rebuild()

    def on_shown(self):
        self.lib.reload_index()
        self.rebuild()

    def _tools(self, *_):
        from orbits.ui.wallpaper_tools import WallpaperToolsDialog
        WallpaperToolsDialog(self).present(self.window)

    def _collection_changed(self, row, _p):
        index = row.get_selected()
        self.collection = self.collection_names[index] if index < len(self.collection_names) else ""
        self.grid.invalidate_filter()

    def toggle_search(self):
        self.search_btn.set_active(not self.search_btn.get_active())

    def rebuild(self):
        collection_names = [""] + self.lib.collections()
        if collection_names != self.collection_names:
            self.collection_names = collection_names
            current = self.collection if self.collection in collection_names else ""
            self.collection_row.set_model(Gtk.StringList.new(["All Collections"] + collection_names[1:]))
            self.collection_row.set_selected(collection_names.index(current))
        items = self.lib.sorted_items()
        self.grid.set_items(items)
        self.stack.set_visible_child_name("grid" if items else "empty")
        self.grid.invalidate_filter()

    def _make_card(self, entry):
        src = BY_ID.get(entry["source"])
        card = WallCard(entry["title"], src.name if src else "Imported")
        card.entry = entry
        card.add_action_button("starred-symbolic", tr("Toggle Favorite"), lambda: self.lib.set_favorite(card.entry["key"], not card.entry.get("favorite")))
        card.add_action_button("user-trash-symbolic", tr("Uninstall"), lambda: self.uninstall(card.entry))
        card.add_action_button("preferences-desktop-wallpaper-symbolic", tr("Set as Wallpaper"),
                               lambda: self.apply(card.entry, card.entry["files"][0]))
        card.load(path=entry["files"][0].get("preview") or entry["files"][0]["light"])
        current = self.lib.is_current(entry)
        n = len(entry["files"])
        card.set_badge("● Current" if current else ("★ Favorite" if entry.get("favorite") else (f"{n} wallpapers" if n > 1 else "")), accent=current)
        return card

    def _filter_func(self, e):
        if self.filter == "favorites" and not e.get("favorite"):
            return False
        if self.collection and self.collection not in e.get("collections", []):
            return False
        if self.filter not in ("all", "favorites") and e["source"] != self.filter:
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
        try:
            self.lib.apply(rec, desktop=True, lockscreen=lock)
        except Exception as error:
            self.window.toast(str(error), timeout=6)
            return
        self.window.toast(tr('Wallpaper set to “{value0}”').format(value0=entry['title']))

    def uninstall(self, entry):
        dlg = Adw.AlertDialog(heading=tr('Uninstall “{value0}”?').format(value0=entry['title']),
                              body=tr('{value0} file(s) will be deleted from disk.').format(value0=len(entry['files'])))
        dlg.add_response("cancel", tr("Cancel"))
        dlg.add_response("remove", tr("Uninstall"))
        dlg.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "remove" and self._do_uninstall(entry))
        dlg.present(self.window)

    def _do_uninstall(self, entry):
        self.lib.uninstall(entry["key"])
        self.window.toast(tr('Uninstalled “{value0}”').format(value0=entry['title']))

    def _open_folder(self):
        from orbits.config import download_dir

        Gtk.FileLauncher.new(Gio.File.new_for_path(str(download_dir()))).launch(self.window, None, None)

    def _import(self, *_):
        filt = Gtk.FileFilter(name="Images")
        for ext in IMAGE_EXTS:
            filt.add_suffix(ext.lstrip("."))
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(filt)
        dlg = Gtk.FileDialog(title=tr("Import Wallpapers"), filters=store)

        def done(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            self._import_paths([f.get_path() for f in files if f.get_path()])

        dlg.open_multiple(self.window, None, done)

    def _import_paths(self, paths):
        def done(n, error):
            if error:
                self.window.toast(tr('Import failed: {value0}').format(value0=error), timeout=6)
            else:
                self.window.toast(f"Imported {n} wallpaper{'s' if n != 1 else ''}" if n else "No supported images found")
        # Decoding/scanning happens off the UI thread; library publication stays on it.
        import tempfile
        from contextlib import ExitStack
        from orbits.image_safety import normalize_images
        stack = ExitStack()
        def work():
            prepared = []
            try:
                for value in paths:
                    if Path(value).suffix.lower() not in IMAGE_EXTS:
                        continue
                    directory = stack.enter_context(tempfile.TemporaryDirectory(prefix='orbits-import-ui-'))
                    count = 200 if Path(value).suffix.lower() in ('.heic', '.heif') else 1
                    frames = normalize_images(value, directory, frames=count, publisher=True)
                    prepared.append((Path(value).stem, frames))
                return prepared
            except Exception:
                stack.close()
                raise
        def publish(prepared, error):
            try:
                if error:
                    done(None, error)
                    return
                for title, frames in prepared:
                    if len(frames) > 1:
                        self.lib.create_slideshow([(i * 86400 // len(frames), image)
                                                   for i, image in enumerate(frames)], title)
                    else:
                        self.lib.add_generated(frames[0], title)
                done(len(prepared), None)
            except Exception as failure:
                done(None, failure)
            finally:
                stack.close()
        net.run_async(work, publish)

    def _on_drop(self, _t, value, _x, _y):
        self._import_paths([f.get_path() for f in value.get_files() if f.get_path()])
        return True

    def _open_detail(self, entry):
        LibraryDialog(self, entry).present(self.window)


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
            load_texture_into(pic, path=rec.get("preview") or rec["light"], size=3840)
            if rec.get("dark"):
                frame.add_overlay(Gtk.Label(label=tr("Light + Dark"), css_classes=["badge"], halign=Gtk.Align.START,
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
                        tooltip_text=tr("Uninstall"))
        rm.connect("clicked", lambda *_: (self.close(), page.uninstall(entry)))
        buttons.append(rm)
        more = Gtk.MenuButton(icon_name="view-more-symbolic", css_classes=["circular"], tooltip_text=tr("More"))
        pop = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for label, cb in [("Set as Lock Screen", self._lock), ("Use for Login Screen", self._gdm),
                          ("Edit Before Applying", self._edit), ("Use for App Colors", self._colors),
                          ("Collections…", self._collections), ("Show in Files", self._show_file)]:
            b = Gtk.Button(label=label, css_classes=["flat"])
            b.connect("clicked", lambda _b, cb=cb: (more.popdown(), cb()))
            pop.append(b)
        more.set_popover(Gtk.Popover(child=pop))
        buttons.append(more)
        apply = Gtk.Button(css_classes=["pill", "suggested-action"])
        apply.set_child(Adw.ButtonContent(label=tr("Set as Wallpaper"), icon_name="preferences-desktop-wallpaper-symbolic"))
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
            web = Gtk.Button(icon_name="web-browser-symbolic", tooltip_text=tr("Open in Browser"))
            web.connect("clicked", lambda *_: open_uri(self, entry["page_url"]))
            header.pack_start(web)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=content,
                                            propagate_natural_height=True))
        self.set_child(view)

    def _rec(self):
        return self.entry["files"][round(self.carousel.get_position())]

    def _edit(self):
        from orbits.ui.wallpaper_tools import WallpaperEditor
        WallpaperEditor(self.page, self._rec()).present(self.page.window)

    def _colors(self):
        path = self._rec().get("preview") or self._rec()["light"]
        self.close()
        self.page.window.navigate("app-colors")
        self.page.window.pages["app-colors"].from_wallpaper(path)

    def _collections(self):
        dialog = Adw.AlertDialog(heading=tr("Wallpaper Collections"), body="Separate collection names with commas. Existing: " + ", ".join(self.page.lib.collections()))
        field = Gtk.Entry(text=", ".join(self.entry.get("collections", [])), placeholder_text=tr("Nature, Favorites, Work"))
        dialog.set_extra_child(field)
        dialog.add_response("cancel", tr("Cancel"))
        dialog.add_response("save", tr("Save"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda _d, r: r == "save" and self.page.lib.set_collections(self.entry["key"], field.get_text().split(",")))
        dialog.present(self)

    def _lock(self):
        try:
            self.page.lib.apply(self._rec(), desktop=False, lockscreen=True)
        except Exception as error:
            self.page.window.toast(str(error), timeout=6)
            return
        self.page.window.toast(tr("Lock screen background updated"))

    def _gdm(self):
        path = self._rec()["light"]
        self.close()
        self.page.window.use_for_login_screen(path)

    def _show_file(self):
        Gtk.FileLauncher.new(Gio.File.new_for_path(self._rec()["light"])).open_containing_folder(
            self.page.window, None, None)

