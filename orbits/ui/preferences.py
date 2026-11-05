"""Application preferences: downloads, library behaviour, sources, appearance, storage."""
from orbits.i18n import _ as tr
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from orbits.config import THUMB_CACHE, WALLPAPER_DIR, cache_size, download_dir, settings, trim_cache
from orbits.library import get_library
from orbits.fonts import FONT_SOURCES
from orbits.themes import KINDS
from orbits.sources import SOURCES

SCHEMES = [("default", "Follow System"), ("light", "Light"), ("dark", "Dark")]
MIN_RES = [("", "Any"), ("1280x720", "1280 × 720"), ("1920x1080", "1920 × 1080 (Full HD)"),
           ("2560x1440", "2560 × 1440 (QHD)"), ("3840x2160", "3840 × 2160 (4K)")]
TOP_RANGE = [("1d", "Day"), ("3d", "3 Days"), ("1w", "Week"), ("1M", "Month"), ("3M", "3 Months"),
             ("6M", "6 Months"), ("1y", "Year")]


def apply_color_scheme():
    scheme = {"light": Adw.ColorScheme.FORCE_LIGHT, "dark": Adw.ColorScheme.FORCE_DARK}.get(
        settings.get("color_scheme"), Adw.ColorScheme.DEFAULT)
    Adw.StyleManager.get_default().set_color_scheme(scheme)


def _combo(title, subtitle, choices, key, on_change=None):
    row = Adw.ComboRow(title=title, subtitle=subtitle or "",
                       model=Gtk.StringList.new([label for _, label in choices]))
    values = [v for v, _ in choices]
    cur = settings.get(key)
    row.set_selected(values.index(cur) if cur in values else 0)

    def changed(r, _p):
        settings.set(key, values[r.get_selected()])
        if on_change:
            on_change()

    row.connect("notify::selected", changed)
    return row


def _switch(title, subtitle, key, on_change=None):
    row = Adw.SwitchRow(title=title, subtitle=subtitle or "", active=bool(settings.get(key)))

    def changed(r, _p):
        settings.set(key, r.get_active())
        if on_change:
            on_change()

    row.connect("notify::active", changed)
    return row


def _offline_changed():
    app = Gio.Application.get_default()
    if app and app.window:
        for page in getattr(app.window, 'pages', {}).values():
            browser = page if hasattr(page, 'offline_banner') else getattr(page, 'browse', None)
            if browser and hasattr(browser, 'offline_banner'):
                browser.offline_banner.set_revealed(bool(settings.get('offline')))
    if settings.get('offline'):
        from orbits import downloads
        if downloads._manager:
            for job in list(downloads._manager.jobs.values()):
                downloads._manager.pause(job)


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self):
        super().__init__(title=tr("Preferences"), search_enabled=True)
        self.add(self._general_page())
        self.add(self._sources_page())
        self.add(self._storage_page())

    # ---------------------------------------------------------------- general
    def _general_page(self):
        page = Adw.PreferencesPage(title=tr("General"), icon_name="emblem-system-symbolic")

        network = Adw.PreferencesGroup(title=tr("Network"), description=tr("Cached catalogues and installed items remain available offline."))
        network.add(_switch(tr("Offline Mode"), tr("Use cached catalogues without making network requests"), 'offline', _offline_changed))
        page.add(network)
        downloads = Adw.PreferencesGroup(title=tr("Downloads"),
                                         description=tr("Where installed wallpapers are stored on disk."))
        self.dir_row = Adw.ActionRow(title=tr("Download Location"), subtitle=str(download_dir()),
                                     subtitle_selectable=True)
        self.dir_row.add_prefix(Gtk.Image(icon_name="folder-download-symbolic"))
        choose = Gtk.Button(label=tr("Change…"), valign=Gtk.Align.CENTER)
        choose.connect("clicked", self._choose_dir)
        open_btn = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER,
                              tooltip_text=tr("Open Folder"), css_classes=["flat"])
        open_btn.connect("clicked", lambda *_: Gtk.FileLauncher.new(
            Gio.File.new_for_path(str(download_dir()))).launch(self.get_root(), None, None))
        self.dir_row.add_suffix(open_btn)
        self.dir_row.add_suffix(choose)
        downloads.add(self.dir_row)
        self.reset_dir = Adw.ButtonRow(title=tr("Use Default Location"), start_icon_name="edit-undo-symbolic",
                                       visible=bool(settings.get("download_dir")))
        self.reset_dir.connect("activated", lambda *_: self._set_dir(WALLPAPER_DIR))
        downloads.add(self.reset_dir)
        page.add(downloads)

        lib = Adw.PreferencesGroup(title=tr("Library"))
        lib.add(_switch(tr("Show in GNOME Settings"),
                        tr("List installed wallpapers under Settings → Appearance"),
                        'register_in_gnome', lambda: get_library().refresh_gnome_registration()))
        lib.add(_switch(tr("Also Set Lock Screen"),
                        tr("“Set as Wallpaper” updates the lock screen background too"),
                        'apply_lockscreen_too'))
        lib.add(_combo(tr("Start Page"), tr("Shown when Orbits opens"),
                       [(s.id, s.name) for s in SOURCES] + [("library", "Library")]
                       + [(s.id, s.name) for s in FONT_SOURCES] + [("my-fonts", "My Fonts")]
                       + [(f"theme-{k.id}", k.title) for k in KINDS.values()] + [("gdm", "Login Screen")],
                       "default_source"))
        page.add(lib)

        look = Adw.PreferencesGroup(title=tr("Appearance"))
        look.add(_combo(tr("Style"), None, SCHEMES, "color_scheme", apply_color_scheme))
        tour = Adw.ButtonRow(title=tr("Show Welcome Tour"), start_icon_name="starred-symbolic")
        tour.connect("activated", self._show_tour)
        look.add(tour)
        page.add(look)
        return page

    def _choose_dir(self, *_):
        dlg = Gtk.FileDialog(title=tr("Choose Download Location"), initial_folder=Gio.File.new_for_path(str(download_dir())))

        def done(d, res):
            try:
                folder = d.select_folder_finish(res)
            except GLib.Error:
                return
            if folder and folder.get_path():
                self._set_dir(Path(folder.get_path()))

        dlg.select_folder(self.get_root(), None, done)

    def _set_dir(self, new: Path):
        old = download_dir()
        if new == old:
            return
        lib = get_library()
        settings.set("download_dir", "" if new == WALLPAPER_DIR else str(new))
        self.dir_row.set_subtitle(str(download_dir()))
        self.reset_dir.set_visible(bool(settings.get("download_dir")))
        if not lib.items:
            return
        alert = Adw.AlertDialog(heading=tr("Move Existing Wallpapers?"),
                                body=tr('{value0} installed wallpapers are in\n{value1}\n\nMove them to the new location, or leave them where they are?').format(value0=len(lib.items), value1=old))
        alert.add_response("keep", tr("Leave Them"))
        alert.add_response("move", tr("Move"))
        alert.set_response_appearance("move", Adw.ResponseAppearance.SUGGESTED)
        alert.set_default_response("move")

        def resp(_a, r):
            if r == "move":
                try:
                    n = lib.relocate(download_dir())
                    self.add_toast(Adw.Toast(title=tr('Moved {value0} wallpapers').format(value0=n)))
                except OSError as e:
                    self.add_toast(Adw.Toast(title=tr('Could not move wallpapers: {value0}').format(value0=e.strerror)))

        alert.connect("response", resp)
        alert.present(self)

    def _show_tour(self, *_):
        self.close()
        app = Gtk.Application.get_default()
        app.activate_action("welcome", None)

    # ---------------------------------------------------------------- sources
    def _sources_page(self):
        page = Adw.PreferencesPage(title=tr("Sources"), icon_name="web-browser-symbolic")

        kde = Adw.PreferencesGroup(title=tr("KDE Store"))
        kde.add(_combo(tr("Results per Page"), None, [(12, "12"), (24, "24"), (48, "48"), (96, "96")],
                       "kde_page_size"))
        page.add(kde)

        wh = Adw.PreferencesGroup(title=tr("Wallhaven"))
        wh.add(_combo(tr("Minimum Resolution"), tr("Hide wallpapers smaller than this"), MIN_RES, "wallhaven_min_res"))
        wh.add(_combo(tr("“Top” Time Range"), None, TOP_RANGE, "wallhaven_top_range"))
        key = Adw.PasswordEntryRow(title=tr("API Key (optional)"), text=settings.get("wallhaven_api_key") or "",
                                   show_apply_button=True)
        key.connect("apply", lambda r: settings.set("wallhaven_api_key", r.get_text().strip()))
        wh.add(key)
        color = Adw.EntryRow(title=tr("Color filter (#RRGGBB; blank for any)"), text=settings.get("wallhaven_color") or "",
                             show_apply_button=True)
        def save_color(row):
            import re
            value = row.get_text().strip()
            if value and not re.fullmatch(r"#?[0-9a-fA-F]{6}", value):
                self.add_toast(Adw.Toast(title=tr("Use a six-digit hex color")))
                return
            settings.set("wallhaven_color", value.lstrip("#"))
        color.connect("apply", save_color)
        wh.add(color)
        page.add(wh)

        more = Adw.PreferencesGroup(title=tr("Additional Sources"))
        for key, title in (("nasa_api_key", "NASA API Key (optional; defaults to DEMO_KEY)"),
                           ("reddit_token", "Reddit OAuth Access Token")):
            row = Adw.PasswordEntryRow(title=title, text=settings.get(key) or "", show_apply_button=True)
            row.connect("apply", lambda r, k=key: settings.set(k, r.get_text().strip()))
            more.add(row)
        more.set_description(tr("Openverse includes license and attribution details. Reddit tokens expire; renew them here."))
        page.add(more)

        note = Adw.PreferencesGroup(description=tr("Changes take effect the next time a page is refreshed."))
        page.add(note)
        return page

    # ---------------------------------------------------------------- storage
    def _storage_page(self):
        page = Adw.PreferencesPage(title=tr("Storage"), icon_name="drive-harddisk-symbolic")
        grp = Adw.PreferencesGroup(title=tr("Thumbnail Cache"),
                                   description=tr("Previews downloaded while browsing. Safe to clear."))
        self.cache_row = Adw.ActionRow(title=tr("Cache Size"), subtitle=_human(cache_size()))
        clear = Gtk.Button(label=tr("Clear"), valign=Gtk.Align.CENTER, css_classes=["destructive-action"])
        clear.connect("clicked", self._clear_cache)
        self.cache_row.add_suffix(clear)
        grp.add(self.cache_row)
        grp.add(_combo(tr("Maximum Cache Size"), tr("Oldest previews are removed past this limit"),
                       [(100, "100 MB"), (300, "300 MB"), (1000, "1 GB"), (3000, "3 GB")],
                       "thumb_cache_mb", lambda: (trim_cache(), self._refresh_cache())))
        page.add(grp)

        lib = get_library()
        total = sum(f.stat().st_size for e in lib.items.values() for f in Path(e["dir"]).glob("*") if f.is_file())
        grp2 = Adw.PreferencesGroup(title=tr("Library"))
        grp2.add(Adw.ActionRow(title=tr("Installed Wallpapers"), subtitle=tr('{value0} items · {value1}').format(value0=len(lib.items), value1=_human(total))))
        page.add(grp2)
        return page

    def _clear_cache(self, *_):
        for f in THUMB_CACHE.iterdir():
            if f.is_file():
                f.unlink(missing_ok=True)
        self._refresh_cache()
        self.add_toast(Adw.Toast(title=tr("Thumbnail cache cleared")))

    def _refresh_cache(self):
        self.cache_row.set_subtitle(_human(cache_size()))
