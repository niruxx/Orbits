"""Tests for App Colors, Looks, Updates and theme detection. Run with XDG_* pointing at a temp dir
(tests/run.sh does this) so nothing touches the real desktop."""
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

assert "orbits-test" in os.environ.get("XDG_CONFIG_HOME", ""), "run via tests/run.sh"

from orbits import colors, looks, themes, updates  # noqa: E402
from orbits.sources.base import Download, Item  # noqa: E402


class ColorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="orbits-test-colors-"))
        self.saved = (colors.GTK4, colors.GTK3, colors.STATE)
        colors.GTK4, colors.GTK3, colors.STATE = self.tmp / "gtk-4.0", self.tmp / "gtk-3.0", self.tmp / "s.json"

    def tearDown(self):
        colors.GTK4, colors.GTK3, colors.STATE = self.saved

    def test_css_has_both_schemes_and_readable_accent_text(self):
        css = colors.gtk4_css(colors.PRESETS["Nord"])
        self.assertTrue(css.startswith(colors.MARKER))
        self.assertIn("--window-bg-color: #eceff4;", css)
        self.assertIn("@media (prefers-color-scheme: dark)", css)
        self.assertIn("--window-bg-color: #2e3440;", css)
        self.assertEqual(colors.contrast_fg("#000000"), "#ffffff")
        self.assertNotEqual(colors.contrast_fg("#ffffff"), "#ffffff")

    def test_dark_only_preset_leaves_light_alone(self):
        css = colors.gtk4_css(colors.PRESETS["Dracula"])
        self.assertNotIn(":root {\n  --", css.split("@media")[0])

    def test_apply_backs_up_users_file_and_remove_restores_it(self):
        colors.GTK4.mkdir(parents=True)
        mine = colors.GTK4 / "gtk.css"
        mine.write_text("/* my tweaks */")
        state = {"preset": "Nord", "palette": colors.PRESETS["Nord"], "gtk3": False, "flatpak": False}
        colors.apply(state)
        self.assertTrue(colors.is_applied())
        self.assertEqual((colors.GTK4 / "orbits-backup" / "gtk.css").read_text(), "/* my tweaks */")
        colors.remove()
        self.assertEqual(mine.read_text(), "/* my tweaks */")
        self.assertFalse(colors.is_applied())

    def test_symlinked_theme_is_replaced_not_followed(self):
        colors.GTK4.mkdir(parents=True)
        target = self.tmp / "theme.css"
        target.write_text("/* theme */")
        (colors.GTK4 / "gtk.css").symlink_to(target)
        self.assertEqual(colors.gtk4_conflict()[0], "link")
        colors._write(colors.GTK4 / "gtk.css", colors.gtk4_css(colors.PRESETS["Nord"]))
        self.assertEqual(target.read_text(), "/* theme */", "must not write through the symlink")
        self.assertFalse((colors.GTK4 / "gtk.css").is_symlink())


class LookSanitizeTests(unittest.TestCase):
    def base(self, **kw):
        raw = {"format": 1, "name": "Test", "interface": {}, "wallpaper": {}, "assets": {}}
        raw.update(kw)
        return raw

    def test_rejects_wrong_format(self):
        with self.assertRaises(ValueError):
            looks.sanitize({"format": 99})

    def test_css_injection_in_palette_is_dropped(self):
        raw = self.base(app_colors={"preset": "x", "palette": {
            "dark": {"accent": "#112233", "window_bg": "red; } * { background: url(http://evil)",
                     "nope": "#000000"}, "light": None}})
        pal = looks.sanitize(raw)["app_colors"]["palette"]["dark"]
        self.assertEqual(pal, {"accent": "#112233"})

    def test_interface_values_are_validated(self):
        raw = self.base(interface={"gtk-theme": "../../etc", "icon-theme": "Papirus", "cursor-size": 9999,
                                   "font-name": "Inter 11", "monospace-font-name": "x}\n* {", "color-scheme": "prefer-dark"})
        iface = looks.sanitize(raw)["interface"]
        self.assertEqual(iface, {"icon-theme": "Papirus", "font-name": "Inter 11", "color-scheme": "prefer-dark"})

    def test_login_never_imports_autologin_or_logo(self):
        raw = self.base(login={"autologin_enable": True, "autologin_user": "root", "logo": "/etc/shadow",
                               "bg_mode": "color", "bg_color": "#123456", "text_color": "red;}",
                               "banner_text": "Hello"})
        login = looks.sanitize(raw)["login"]
        self.assertNotIn("autologin_enable", login)
        self.assertNotIn("autologin_user", login)
        self.assertNotIn("logo", login)
        self.assertNotIn("text_color", login)
        self.assertEqual(login["bg_color"], "#123456")


class LookRoundTripTests(unittest.TestCase):
    def test_export_then_import_keeps_wallpaper_and_settings(self):
        tmp = Path(tempfile.mkdtemp(prefix="orbits-test-look-"))
        wp = tmp / "wall.png"
        wp.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        look = {"format": 1, "name": "Round Trip", "created": 1, "interface": {"icon-theme": "Adwaita"},
                "wallpaper": {"light": str(wp), "dark": None, "options": "zoom"}, "lockscreen": None,
                "shell_theme": "", "libadwaita_theme": None,
                "app_colors": {"preset": "Nord", "palette": colors.PRESETS["Nord"]}, "assets": {}}
        out = tmp / "x.orbits"
        store = looks.get_store()
        store.export(look, out)
        with zipfile.ZipFile(out) as z:
            self.assertIn("wallpaper-light.png", z.namelist())
            self.assertNotIn(str(tmp), z.read("look.json").decode(), "no local paths in exported files")
        imported = store.import_file(out)
        self.assertEqual(imported["name"], "Round Trip")
        self.assertTrue(Path(imported["wallpaper"]["light"]).is_file())
        self.assertEqual(imported["app_colors"]["palette"]["dark"]["accent"], "#88c0d0")
        store.delete(imported)

    def test_import_ignores_paths_outside_the_archive(self):
        tmp = Path(tempfile.mkdtemp(prefix="orbits-test-look-"))
        out = tmp / "evil.orbits"
        with zipfile.ZipFile(out, "w") as z:
            z.writestr("look.json", json.dumps({"format": 1, "name": "Evil",
                                                "wallpaper": {"light": "../../../etc/passwd"}}))
        imported = looks.get_store().import_file(out)
        self.assertIsNone(imported["wallpaper"]["light"])
        looks.get_store().delete(imported)


class UpdateTests(unittest.TestCase):
    def test_is_newer_prefers_recorded_remote_date(self):
        self.assertTrue(updates.is_newer("2025-12-27T03:13:28+00:00", {"remote_changed": "2025-01-01T00:00:00+00:00"}))
        self.assertFalse(updates.is_newer("2025-12-27T03:13:28+00:00", {"remote_changed": "2025-12-27T03:13:28+00:00"}))
        # older installs only know when they were installed
        self.assertTrue(updates.is_newer("2025-12-27", {"installed_at": 1_700_000_000}))
        self.assertFalse(updates.is_newer("2020-01-01", {"installed_at": 1_700_000_000}))
        self.assertFalse(updates.is_newer("", {"installed_at": 0}))

    def test_pick_download_matches_previous_file(self):
        item = Item(source="x", id="1", title="t", downloads=[Download("A-Dark.tar.xz", "u1"),
                                                              Download("A-Light.tar.xz", "u2")])
        self.assertEqual(updates._pick_download(item, {"download": "A-Light.tar.xz"}).url, "u2")
        self.assertIsNone(updates._pick_download(item, {"download": "Gone.zip"}))


class ThemeDetectionTests(unittest.TestCase):
    def test_plymouth_name_comes_from_imagedir(self):
        root = Path(tempfile.mkdtemp(prefix="orbits-test-ply-"))
        d = root / "pack" / "src"
        d.mkdir(parents=True)
        (d / "x.plymouth").write_text("[Plymouth Theme]\nName=X\n[script]\n"
                                      "ImageDir=/usr/share/plymouth/themes/zorin-mac/images\n")
        found = themes.find_themes(root, themes.KINDS["plymouth"])
        self.assertEqual([n for _, n in found], ["zorin-mac"])

    def test_nested_variants_are_all_found_once(self):
        root = Path(tempfile.mkdtemp(prefix="orbits-test-gtk-"))
        for name in ("Pack-Dark", "Pack-Light"):
            (root / "Pack" / name / "gtk-3.0").mkdir(parents=True)
            (root / "Pack" / name / "gtk-3.0" / "gtk.css").write_text("")
            (root / "Pack" / name / "gtk-4.0" / "x").mkdir(parents=True)
        found = sorted(n for _, n in themes.find_themes(root, themes.KINDS["gtk"]))
        self.assertEqual(found, ["Pack-Dark", "Pack-Light"])


if __name__ == "__main__":
    unittest.main()
