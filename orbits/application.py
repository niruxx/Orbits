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
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
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
        ]:
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)
        self.set_accels_for_action("win.search", ["<primary>f"])

    def do_shutdown(self):
        from orbits.config import trim_cache

        trim_cache()
        Adw.Application.do_shutdown(self)

    def do_activate(self):
        if self.window is None:
            from orbits.ui.window import OrbitsWindow

            self.window = OrbitsWindow(application=self)
        self.window.present()

    def _on_about(self, *_):
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=VERSION,
            developer_name="niruxx",
            comments="Discover wallpapers and fonts, and make your login screen your own.",
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

    def _on_preferences(self, *_):
        from orbits.ui.preferences import PreferencesDialog

        PreferencesDialog().present(self.window)


def main():
    THUMB_CACHE.mkdir(parents=True, exist_ok=True)
    return OrbitsApplication().run(sys.argv)
