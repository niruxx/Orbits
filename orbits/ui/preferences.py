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
        downloads.add(_switch(tr("Scan Downloads for Malware"),
                              tr("Require a successful local ClamAV scan before installation. Requires clamscan and virus definitions. No files are uploaded."),
                              'malware_scan', self._refresh_security_status))
        downloads.add(_switch(tr("Automatically Update Virus Definitions"),
                              tr("Check for updates before scanning, at most once a day. Requires freshclam and a connection. Does not change system ClamAV services."),
                              'malware_auto_update', self._refresh_security_status))
        self.definitions_row = Adw.ButtonRow(title=tr("Update Virus Definitions Now"),
                                            subtitle=tr("Download definitions for Orbits without administrator access"),
                                            start_icon_name="view-refresh-symbolic")
        self.definitions_row.connect("activated", self._update_definitions)
        downloads.add(self.definitions_row)
        self.rescan_row = Adw.ButtonRow(title=tr("Scan Existing Appearance Assets"),
                                       subtitle=tr("Scan user wallpapers, fonts, themes and Looks with ClamAV. Includes assets installed outside Orbits; system assets and symlink targets are excluded."),
                                       start_icon_name="security-high-symbolic")
        self.rescan_row.connect("activated", self._rescan_assets)
        downloads.add(self.rescan_row)
        self.integrity_row = Adw.ButtonRow(title=tr("Check Installed File Integrity"),
                                          subtitle=tr("Compare managed appearance files with their recorded SHA-256 hashes"),
                                          start_icon_name="document-properties-symbolic")
        self.integrity_row.connect("activated", self._check_integrity)
        downloads.add(self.integrity_row)
        self.security_status = Adw.ActionRow(title=tr("Malware Protection Status"), use_markup=False)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER,
                             tooltip_text=tr("Refresh Protection Status"))
        refresh.connect("clicked", self._refresh_security_status)
        self.security_status.add_suffix(refresh)
        downloads.add(self.security_status)
        self._refresh_security_status()
        self.legacy_row = Adw.ButtonRow(title=tr("Validate Older Managed Assets"),
                                       subtitle=tr("Validate files before recording their first integrity baseline. Existing baselines are never replaced; publisher identity remains unverified."))
        self.legacy_row.connect("activated", self._validate_legacy)
        downloads.add(self.legacy_row)
        page.add(downloads)
        self.publisher_group = Adw.PreferencesGroup(title=tr("Trusted Publishers"),
            description=tr("Only add keys obtained through an independently trusted channel. A signature authenticates its publisher, not harmlessness. Hostnames must publish a .minisig beside each download."))
        self.publisher_group.add(_switch(tr("Require Verified Publishers for New Installs"),
            tr("Reject unsigned downloads and imports. Most community stores do not currently provide signatures."), 'require_publisher'))
        self.publisher_host = Adw.EntryRow(title=tr("Publisher Hostname"))
        self.publisher_name = Adw.EntryRow(title=tr("Publisher Name"))
        self.publisher_key = Adw.EntryRow(title=tr("Minisign Public Key"))
        for row in (self.publisher_host, self.publisher_name, self.publisher_key):
            self.publisher_group.add(row)
        add = Adw.ButtonRow(title=tr("Trust This Publisher Key"))
        add.connect("activated", self._add_publisher)
        self.publisher_group.add(add)
        self.publisher_rows = []
        self._publisher_list()
        page.add(self.publisher_group)

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

    def _publisher_list(self):
        from orbits.publishers import keys
        from orbits.config import CONFIG_DIR
        for row in self.publisher_rows:
            self.publisher_group.remove(row)
        self.publisher_rows = []
        try:
            records = keys(CONFIG_DIR)
        except Exception as error:
            self.add_toast(Adw.Toast(title=str(error)))
            return
        for record in records:
            row = Adw.ActionRow(title=record['name'], subtitle=record['host'], use_markup=False)
            remove = Gtk.Button(icon_name='user-trash-symbolic', valign=Gtk.Align.CENTER,
                                tooltip_text=tr("Remove Trusted Publisher"))
            remove.connect('clicked', lambda _button, key=record['key']: self._remove_publisher(key))
            row.add_suffix(remove)
            self.publisher_rows.append(row)
            self.publisher_group.add(row)

    def _add_publisher(self, *_args):
        from orbits.publishers import keys, save_keys
        from orbits.config import CONFIG_DIR
        try:
            record = {'host': self.publisher_host.get_text().strip().lower(),
                      'name': self.publisher_name.get_text().strip(), 'key': self.publisher_key.get_text().strip()}
            records = keys(CONFIG_DIR)
            if any(item['host'] == record['host'] and item['key'] == record['key'] for item in records):
                raise ValueError('This publisher key is already trusted')
            save_keys(CONFIG_DIR, records + [record])
            self.publisher_key.set_text('')
            self._publisher_list()
        except Exception as error:
            self.add_toast(Adw.Toast(title=str(error)))

    def _remove_publisher(self, key):
        from orbits.publishers import keys, save_keys
        from orbits.config import CONFIG_DIR
        try:
            save_keys(CONFIG_DIR, [item for item in keys(CONFIG_DIR) if item['key'] != key])
            self._publisher_list()
        except Exception as error:
            self.add_toast(Adw.Toast(title=str(error)))

    def _validate_legacy(self, *_args):
        from orbits import net, themes
        from orbits.fonts import get_font_library
        from orbits.looks import get_store
        from orbits.legacy_safety import revalidate
        jobs = [('wallpaper', entry['dir']) for entry in get_library().items.values()]
        jobs += [('font', entry['dir']) for entry in get_font_library().items.values() if entry.get('scope') == 'user']
        jobs += [('look', look['_dir']) for look in get_store().looks()]
        for kind in KINDS:
            jobs += [(kind, str(theme.path)) for theme in themes.installed(kind) if theme.managed and theme.scope == 'user']
        self.legacy_row.set_sensitive(False)
        self.legacy_row.set_subtitle(tr("Validating older managed assets…"))
        def done(result, error):
            self.legacy_row.set_sensitive(True)
            self.legacy_row.set_subtitle(str(error) if error else result)
        net.run_async(lambda: revalidate(list(dict.fromkeys(jobs))), done)

    def _asset_roots(self):
        from orbits.fonts import USER_FONT_DIR
        from orbits.looks import LOOKS_DIR
        roots = {download_dir(), USER_FONT_DIR, LOOKS_DIR}
        roots.update(kind.user_dir for kind in KINDS.values() if kind.user_dir)
        return sorted(roots)

    def _refresh_security_status(self, *_args):
        from orbits import net
        from orbits.config import DATA_DIR
        from orbits.security import definition_status
        self.security_status.set_subtitle(tr("Checking protection status…"))
        def done(result, error):
            self.security_status.set_subtitle(str(error) if error else result)
        net.run_async(lambda: definition_status(DATA_DIR/'clamav', settings), done)

    def _check_integrity(self, *_args):
        from orbits import net
        from orbits.integrity import audit_roots
        roots = self._asset_roots()
        self.integrity_row.set_sensitive(False)
        self.integrity_row.set_subtitle(tr("Checking installed file hashes…"))
        def done(result, error):
            self.integrity_row.set_sensitive(True)
            self.integrity_row.set_subtitle(str(error) if error else result)
        net.run_async(lambda: audit_roots(roots), done)

    def _rescan_assets(self, *_args):
        from orbits import net
        from orbits.security import rescan_assets
        roots = self._asset_roots()
        self.rescan_row.set_sensitive(False)
        self.rescan_row.set_subtitle(tr("Scanning existing appearance assets…"))
        def done(result, error):
            self.rescan_row.set_sensitive(True)
            self.rescan_row.set_subtitle(str(error) if error else tr(result))
        net.run_async(lambda: rescan_assets(sorted(roots)), done)

    def _update_definitions(self, *_args):
        from orbits import net
        from orbits.config import DATA_DIR
        from orbits.security import update_definitions
        self.definitions_row.set_sensitive(False)
        self.definitions_row.set_subtitle(tr("Updating virus definitions…"))
        offline = bool(settings.get('offline'))
        def done(result, error):
            self.definitions_row.set_sensitive(True)
            self.definitions_row.set_subtitle(str(error) if error else tr(result))
            self._refresh_security_status()
        net.run_async(lambda: update_definitions(DATA_DIR / 'clamav', offline=offline), done)

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
