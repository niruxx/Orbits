from orbits.i18n import _ as tr
import sys
import warnings

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from orbits import APP_ID, APP_NAME, VERSION  # noqa: E402
from orbits.config import PACKAGE_DIR, THUMB_CACHE, settings  # noqa: E402

warnings.filterwarnings("ignore", category=DeprecationWarning)


class OrbitsApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        GLib.set_application_name(APP_NAME)
        self.window = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        display = Gdk.Display.get_default()
        Gtk.IconTheme.get_for_display(display).add_search_path(str(PACKAGE_DIR.parent / "data" / "icons"))
        css = Gtk.CssProvider()
        css.load_from_path(str(PACKAGE_DIR / "style.css"))
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        from orbits.ui.preferences import apply_color_scheme

        apply_color_scheme()

        for name, cb, accels in [
            ("quit", lambda *_: self.quit(), ["<primary>q"]),
            ("about", self._on_about, []),
            ("preferences", self._on_preferences, ["<primary>comma"]),
            ("welcome", lambda *_: self.window and self.window.show_welcome(), []),
            ("global-search", self._on_global_search, ["<primary>k"]),
            ("quick-switcher", self._on_quick_switcher, ["<primary><shift>k"]),
            ("system-check", self._on_system_check, []),
        ]:
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)
        self.set_accels_for_action("win.search", ["<primary>f"])
        from orbits.undo import subscribe
        self._undo_unsubscribe = subscribe(self._dispatch_undo)
        action = Gio.SimpleAction.new('undo-last', None)
        action.connect('activate', self._undo_last)
        self.add_action(action)

    def do_shutdown(self):
        from orbits.config import trim_cache

        from orbits import downloads
        if downloads._manager is not None:
            downloads._manager.close()
        from orbits.clamav import close as close_scanner
        close_scanner()
        from orbits.undo import clear
        self._undo_unsubscribe()
        clear()
        trim_cache()
        Adw.Application.do_shutdown(self)

    def do_activate(self):
        if self.window is None:
            from orbits.ui.window import OrbitsWindow

            self.window = OrbitsWindow(application=self)
        self.window.present()

    def do_command_line(self, command_line):
        import argparse
        parser = argparse.ArgumentParser(prog='orbits')
        parser.add_argument('--quick-switcher', action='store_true')
        parser.add_argument('--apply-look')
        parser.add_argument('--search')
        try:
            args = parser.parse_args(command_line.get_arguments()[1:])
        except SystemExit as error:
            return int(error.code)
        if args.quick_switcher:
            self._on_quick_switcher()
        elif args.apply_look:
            from orbits import looks
            from orbits.config import DATA_DIR
            from orbits.search import resolve_look
            import json
            held = False
            try:
                path = resolve_look(DATA_DIR, args.apply_look)
                look = json.loads(path.read_text())
                look['_dir'] = str(path.parent)
                import types
                target = self.window or types.SimpleNamespace(pages={}, toast=lambda text, **kw:self.notify_error(text))
                # Only local Looks are accepted; login changes always require the editor.
                self.hold()
                held = True
                def done(notes):
                    self.notify_error("Look applied" + (' · '+'; '.join(notes) if notes else ''))
                    self.release()
                looks.LookApplier(look, target, done=done).start()
            except Exception as error:
                if held:
                    self.release()
                self.notify_error(str(error))
                return 1
        elif args.search is not None:
            self.do_activate()
            from orbits.ui.global_search import SearchDialog
            SearchDialog(self.window, args.search).present(self.window)
        else:
            self.do_activate()
        return 0

    def _on_global_search(self, *_):
        self.do_activate()
        from orbits.ui.global_search import SearchDialog
        SearchDialog(self.window).present(self.window)

    def _on_quick_switcher(self, *_):
        if self.window is None:
            from orbits.ui.window import OrbitsWindow
            self.window = OrbitsWindow(application=self)
        from orbits.ui.global_search import QuickSwitcher
        if not getattr(self, 'switcher', None):
            self.switcher = QuickSwitcher(self, self.window)
            self.switcher.connect('close-request', lambda *_: setattr(self, 'switcher', None))
        self.switcher.present()

    def _dispatch_undo(self, record):
        import threading
        if threading.current_thread() is threading.main_thread():
            self._notify_undo(record)
        else:
            GLib.idle_add(lambda:(self._notify_undo(record), False)[1])

    def _notify_undo(self, record):
        if (self.window and self.window.get_visible()) or (getattr(self, 'switcher', None) and self.switcher.get_visible()):
            return
        self._last_undo = record
        notification = Gio.Notification.new(record.label)
        notification.add_button(tr("Undo"), 'app.undo-last')
        self.send_notification('undo', notification)
        # Keep the callback available while its notification is actionable.
        self.hold()
        def expire():
            if getattr(self, '_last_undo', None) is record:
                self.withdraw_notification('undo')
            self.release()
            return False
        GLib.timeout_add_seconds(30, expire)

    def _undo_last(self, *_args):
        try:
            record = getattr(self, '_last_undo', None)
            if record and record.undo() is not False:
                self.notify_error(tr("Change undone"))
        except Exception as error:
            self.notify_error(str(error))

    def notify_error(self, message):
        if self.window and self.window.get_visible():
            self.window.toast(message, timeout=6)
        else:
            notification = Gio.Notification.new("Orbits")
            notification.set_body(message)
            self.send_notification('operation-result', notification)

    def _on_about(self, *_):
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=VERSION,
            developer_name="niruxx",
            comments=tr("Discover wallpapers and fonts, and make your login screen your own."),
            website="https://github.com/niruxx/Orbits",
            issue_url="https://github.com/niruxx/Orbits/issues",
            license_type=Gtk.License.GPL_3_0,
        )
        about.add_credit_section("Wallpaper sources", ["KDE Store https://store.kde.org",
                                                       "Wallhaven https://wallhaven.cc",
                                                       "Bing https://www.bing.com"])
        about.add_credit_section("Font sources", ["Google Fonts https://fonts.google.com",
                                                  "Fontsource https://fontsource.org",
                                                  "Nerd Fonts https://www.nerdfonts.com"])
        about.present(self.window)

    def _on_system_check(self, *_):
        from orbits.ui.system_check import SystemCheckDialog

        SystemCheckDialog().present(self.window)

    def _on_preferences(self, *_):
        from orbits.ui.preferences import PreferencesDialog

        PreferencesDialog().present(self.window)


def main():
    THUMB_CACHE.mkdir(parents=True, exist_ok=True)
    return OrbitsApplication().run(sys.argv)
