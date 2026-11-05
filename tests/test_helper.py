"""Tests for the privileged helper, run against temporary directories (no root needed)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio  # noqa: E402

from orbits.gdm import helper as H  # noqa: E402

HERE = Path(__file__).resolve().parent
PREFIX = "/org/gnome/shell/theme"


def make_gresource(path: Path, files: dict):
    src = Path(tempfile.mkdtemp())
    for name, data in files.items():
        (src / name).write_text(data)
    xml = src / "x.xml"
    xml.write_text('<?xml version="1.0"?><gresources><gresource prefix="%s">%s</gresource></gresources>'
                   % (PREFIX, "".join(f"<file>{n}</file>" for n in files)))
    subprocess.run(["glib-compile-resources", f"--sourcedir={src}", f"--target={path}", str(xml)], check=True)
    shutil.rmtree(src)


def css_of(path, name="gnome-shell.css"):
    return Gio.Resource.load(str(path)).lookup_data(f"{PREFIX}/{name}", 0).get_data().decode()


class HelperTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="orbits-test-"))
        self._saved = {k: getattr(H, k) for k in ("SHELL_DIR", "GRESOURCE", "BACKUP", "ALTERNATIVES", "ALT_CMD",
                                                    "OUR_DIR", "OUR_GRES", "ALT_BASE_FILE", "GDM_CONF_CANDIDATES",
                                                    "THEME_BASES")}
        self._saved["THEME_BASES"] = dict(H.THEME_BASES)
        shell = self.tmp / "gnome-shell"
        shell.mkdir()
        H.SHELL_DIR = str(shell)
        H.GRESOURCE = str(shell / "gnome-shell-theme.gresource")
        H.BACKUP = H.GRESOURCE + ".orbits-backup"
        H.OUR_DIR = str(shell / "orbits")
        H.OUR_GRES = os.path.join(H.OUR_DIR, "gdm-theme.gresource")
        H.ALT_BASE_FILE = os.path.join(H.OUR_DIR, "base")
        H.ALTERNATIVES = [(str(shell / "gdm-theme.gresource"), "gdm-theme.gresource")]
        H.ALT_CMD = str(self.tmp / "no-update-alternatives")  # absent unless a test installs it
        self.theme = {"gnome-shell.css": "#lockDialogGroup { background-color: #222226; }\n",
                      "gdm.css": "/* gdm */\n", "icon.svg": "<svg/>"}

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(H, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def use_fake_alternatives(self):
        state = self.tmp / "alt-state"
        state.mkdir()
        os.environ["FAKE_ALT_DIR"] = str(state)
        wrapper = self.tmp / "update-alternatives"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {HERE / 'fake_update_alternatives.py'} \"$@\"\n")
        wrapper.chmod(0o755)
        H.ALT_CMD = str(wrapper)


class FileModeTests(HelperTestCase):
    def test_apply_backup_reapply_restore(self):
        make_gresource(Path(H.GRESOURCE), self.theme)
        original = Path(H.GRESOURCE).read_bytes()
        self.assertEqual(H.theme_location()[0], "file")

        H.apply_theme("#lockDialogGroup { background: red; }", None)
        self.assertTrue(H.is_ours(H.GRESOURCE))
        self.assertFalse(H.is_ours(H.BACKUP))
        self.assertIn("background: red", css_of(H.GRESOURCE))
        self.assertIn("background: red", css_of(H.GRESOURCE, "gdm.css"))

        H.apply_theme("#lockDialogGroup { background: blue; }", None)
        css = css_of(H.GRESOURCE)
        self.assertEqual(css.count("---- Orbits ----"), 1, "re-applying must not stack CSS")
        self.assertIn("blue", css)
        self.assertNotIn("red", css)

        H.restore_theme()
        self.assertEqual(Path(H.GRESOURCE).read_bytes(), original)

    def test_package_update_becomes_new_base(self):
        make_gresource(Path(H.GRESOURCE), self.theme)
        H.apply_theme("a { color: red; }", None)
        # a gnome-shell upgrade overwrites our file with a fresh one
        make_gresource(Path(H.GRESOURCE), {**self.theme, "gnome-shell.css": "/* v2 */\n"})
        H.apply_theme("a { color: red; }", None)
        self.assertIn("/* v2 */", css_of(H.GRESOURCE))
        self.assertFalse(H.is_ours(H.BACKUP))


class AlternativesModeTests(HelperTestCase):
    def setUp(self):
        super().setUp()
        self.use_fake_alternatives()
        self.yaru = self.tmp / "Yaru" / "gnome-shell-theme.gresource"
        self.yaru.parent.mkdir()
        make_gresource(self.yaru, {**self.theme, "gnome-shell.css": "/* yaru */\n"})
        link, name = H.ALTERNATIVES[0]
        subprocess.run([H.ALT_CMD, "--install", link, name, str(self.yaru), "10"], check=True)

    def test_apply_registers_alternative_and_restore_switches_back(self):
        mode, link, _name = H.theme_location()
        self.assertEqual(mode, "alternatives")
        self.assertEqual(os.path.realpath(link), str(self.yaru))
        yaru_bytes = self.yaru.read_bytes()

        H.apply_theme("#lockDialogGroup { background: red; }", None)
        self.assertEqual(os.path.realpath(link), os.path.realpath(H.OUR_GRES))
        self.assertTrue(H.is_ours(H.active_theme_file()))
        css = css_of(H.OUR_GRES)
        self.assertIn("/* yaru */", css, "must be built on the distribution theme")
        self.assertIn("background: red", css)
        self.assertEqual(self.yaru.read_bytes(), yaru_bytes, "distribution file must not be touched")
        self.assertFalse(os.path.exists(H.GRESOURCE), "upstream path must not be created")

        H.apply_theme("#lockDialogGroup { background: blue; }", None)
        self.assertEqual(css_of(H.OUR_GRES).count("---- Orbits ----"), 1)

        H.restore_theme()
        self.assertEqual(os.path.realpath(link), str(self.yaru))
        self.assertFalse(os.path.exists(H.OUR_DIR))
        out = subprocess.run([H.ALT_CMD, "--list", "gdm-theme.gresource"], capture_output=True, text=True).stdout
        self.assertNotIn(H.OUR_GRES, out)

    def test_restore_returns_to_users_manual_choice(self):
        other = self.tmp / "Other" / "gnome-shell-theme.gresource"
        other.parent.mkdir()
        make_gresource(other, {**self.theme, "gnome-shell.css": "/* other */\n"})
        link, name = H.ALTERNATIVES[0]
        subprocess.run([H.ALT_CMD, "--install", link, name, str(other), "5"], check=True)
        subprocess.run([H.ALT_CMD, "--set", name, str(other)], check=True)  # user prefers the lower priority one

        H.apply_theme("a { color: red; }", None)
        self.assertIn("/* other */", css_of(H.OUR_GRES))
        H.restore_theme()
        self.assertEqual(os.path.realpath(link), str(other))


class ConfigTests(HelperTestCase):
    def test_custom_conf_keeps_comments_and_sections(self):
        conf = self.tmp / "custom.conf"
        conf.write_text("# GDM configuration storage\n\n[daemon]\n\n[security]\n\n[debug]\n#Enable=true\n")
        H.GDM_CONF_CANDIDATES = [str(conf)]
        H.edit_custom_conf({"AutomaticLoginEnable": "true", "AutomaticLogin": "alice", "WaylandEnable": None})
        text = conf.read_text()
        self.assertIn("[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=alice\n\n[security]", text)
        self.assertIn("#Enable=true", text)
        H.edit_custom_conf({"AutomaticLoginEnable": None, "AutomaticLogin": None})
        self.assertNotIn("AutomaticLogin", conf.read_text())

    def test_custom_conf_rejects_unknown_keys_and_bad_values(self):
        H.GDM_CONF_CANDIDATES = [str(self.tmp / "custom.conf")]
        with self.assertRaises(ValueError):
            H.edit_custom_conf({"Exec": "/bin/sh"})
        with self.assertRaises(ValueError):
            H.edit_custom_conf({"AutomaticLogin": "bob\n[evil]"})


class TreeCheckTests(HelperTestCase):
    def test_symlink_escaping_theme_is_rejected(self):
        d = self.tmp / "theme"
        d.mkdir()
        (d / "ok").write_text("x")
        os.symlink("ok", d / "inside")
        H._check_tree(str(d))  # relative link inside the tree is fine
        os.symlink("/etc/passwd", d / "escape")
        with self.assertRaises(PermissionError):
            H._check_tree(str(d))

    def test_theme_names_are_validated(self):
        for bad in ("../etc", ".hidden", "a/b", ""):
            with self.assertRaises(ValueError):
                H._theme_dest("icons", bad)
        with self.assertRaises(ValueError):
            H._theme_dest("bin", "x")

    def test_read_user_file_refuses_symlinks(self):
        target = self.tmp / "real"
        target.write_text("secret")
        os.symlink(target, self.tmp / "link")
        with self.assertRaises(OSError):
            H.read_user_file(str(self.tmp / "link"))



class BootloaderTests(HelperTestCase):
    def setUp(self):
        super().setUp()
        self._boot = (H.GRUB_DEFAULT, H.ESP_CANDIDATES, H.GRUB_DIRS, H.grub_mkconfig)
        esp = self.tmp / "esp"
        (esp / "EFI" / "refind").mkdir(parents=True)
        self.conf = esp / "EFI" / "refind" / "refind.conf"
        self.conf.write_text("timeout 5\ninclude themes/old/theme.conf\n")
        H.ESP_CANDIDATES = [str(esp)]
        (self.tmp / "grub" / "themes").mkdir(parents=True)
        H.GRUB_DIRS = [str(self.tmp / "grub")]
        H.GRUB_DEFAULT = str(self.tmp / "default-grub")
        Path(H.GRUB_DEFAULT).write_text('GRUB_TIMEOUT=5\nGRUB_TERMINAL_OUTPUT="console"\n#GRUB_THEME="/path/to/gfxtheme"\n')
        self.src = self.tmp / "src"
        self.src.mkdir()
        (self.src / "theme.conf").write_text("banner bg.png\n")
        (self.src / "theme.txt").write_text("title-text: \"\"\n")

    def tearDown(self):
        H.GRUB_DEFAULT, H.ESP_CANDIDATES, H.GRUB_DIRS, H.grub_mkconfig = self._boot
        super().tearDown()

    def test_refind_install_apply_reset(self):
        H.cmd_boot_theme_install("refind", str(self.src), "neat")
        self.assertTrue((self.conf.parent / "themes" / "neat" / ".orbits.json").exists())
        H.cmd_boot_theme_apply("refind", "neat")
        self.assertEqual(H.refind_current_theme(), "neat")
        H.cmd_boot_theme_apply("refind", "neat")  # re-apply keeps a single managed block
        self.assertEqual(self.conf.read_text().count(H.REFIND_BEGIN), 1)
        H.cmd_boot_theme_reset("refind")
        self.assertEqual(self.conf.read_text(), "timeout 5\ninclude themes/old/theme.conf\n")
        self.assertTrue(os.path.exists(str(self.conf) + ".orbits-backup"))

    def test_grub_apply_and_reset_restore_config(self):
        H.grub_mkconfig = lambda: None  # no real GRUB in tests
        original = Path(H.GRUB_DEFAULT).read_text()
        H.cmd_boot_theme_install("grub", str(self.src), "Vimix")
        H.cmd_boot_theme_apply("grub", "Vimix")
        text = Path(H.GRUB_DEFAULT).read_text()
        self.assertIn(f'GRUB_THEME="{self.tmp}/grub/themes/Vimix/theme.txt"', text)
        self.assertIn('# Orbits: disabled', text)
        self.assertEqual([ln for ln in text.splitlines() if ln.startswith("GRUB_THEME=")], [f'GRUB_THEME="{self.tmp}/grub/themes/Vimix/theme.txt"'])
        H.cmd_boot_theme_reset("grub")
        restored = Path(H.GRUB_DEFAULT).read_text()
        self.assertIn('GRUB_TERMINAL_OUTPUT="console"', restored)
        self.assertNotIn("Vimix", restored)
        self.assertEqual(sorted(restored.splitlines()), sorted(original.splitlines()))

    def test_refind_names_with_spaces_and_active_removal(self):
        H.cmd_boot_theme_install("refind", str(self.src), "My Theme")
        H.cmd_boot_theme_apply("refind", "My Theme")
        self.assertIn('include "themes/My Theme/theme.conf"', self.conf.read_text())
        self.assertEqual(H.refind_current_theme(), "My Theme")
        H.cmd_boot_theme_remove("refind", "My Theme")
        self.assertEqual(H.refind_current_theme(), "old")
        self.assertFalse((self.conf.parent / "themes" / "My Theme").exists())

    def test_grub_failure_restores_defaults_and_generated_menu(self):
        defaults = Path(H.GRUB_DEFAULT)
        cfg = Path(H.GRUB_DIRS[0]) / "grub.cfg"
        cfg.write_text("original menu")
        original = defaults.read_bytes()
        H.cmd_boot_theme_install("grub", str(self.src), "neat")

        def fail():
            cfg.write_text("incomplete menu")
            raise RuntimeError("generation failed")

        H.grub_mkconfig = fail
        with self.assertRaisesRegex(RuntimeError, "generation failed"):
            H.cmd_boot_theme_apply("grub", "neat")
        self.assertEqual(defaults.read_bytes(), original)
        self.assertEqual(cfg.read_text(), "original menu")

    def test_grub_reads_last_assignment_with_shell_quotes(self):
        Path(H.GRUB_DEFAULT).write_text("GRUB_THEME=old\nexport GRUB_THEME='/boot/My Theme/theme.txt' # mine\n")
        self.assertEqual(H.grub_current_theme(), "/boot/My Theme/theme.txt")

    def test_symlink_destination_is_rejected(self):
        root = self.conf.parent / "themes"
        root.mkdir()
        (root / "linked").symlink_to(self.src, target_is_directory=True)
        for action in (lambda: H.cmd_boot_theme_install("refind", str(self.src), "linked"),
                       lambda: H.cmd_boot_theme_apply("refind", "linked"),
                       lambda: H.cmd_boot_theme_remove("refind", "linked")):
            with self.assertRaises(PermissionError):
                action()
        self.assertTrue((self.src / "theme.conf").exists())

    def test_boot_remove_refuses_foreign_themes(self):
        (self.conf.parent / "themes" / "theirs").mkdir(parents=True)
        (self.conf.parent / "themes" / "theirs" / "theme.conf").write_text("")
        with self.assertRaises(PermissionError):
            H.cmd_boot_theme_remove("refind", "theirs")
        with self.assertRaises(ValueError):
            H.cmd_boot_theme_install("refind", str(self.src), "../../x")


if __name__ == "__main__":
    unittest.main()
