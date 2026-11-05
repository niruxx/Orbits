"""Login feature tests with real images/resources and isolated system paths."""
import copy
import html
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from orbits.gdm import backgrounds, preview

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {name: module}):
        spec.loader.exec_module(module)
    return module


class ResourceError(Exception):
    pass


class Resource:
    """Use GLib's CLI rather than requiring desktop Python bindings in these tests."""
    def __init__(self, path):
        self.path = path
        result = subprocess.run(['gresource', 'list', str(path)], capture_output=True, text=True)
        if result.returncode:
            raise ResourceError(result.stderr)
        self.paths = result.stdout.splitlines()

    def lookup_data(self, name, _flags):
        if name not in self.paths:
            raise ResourceError(name)
        result = subprocess.run(['gresource', 'extract', str(self.path), name], capture_output=True)
        if result.returncode:
            raise ResourceError(result.stderr)
        return types.SimpleNamespace(get_data=lambda: result.stdout)

    def enumerate_children(self, prefix, _flags):
        result = set()
        for name in self.paths:
            if name.startswith(prefix):
                suffix = name[len(prefix):]
                result.add(suffix.split('/')[0] + ('/' if '/' in suffix else ''))
        return sorted(result)


def make_resource(path, files):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for name, data in files.items():
            (root / name).write_text(data)
        xml = '<gresources><gresource prefix="/org/gnome/shell/theme">' + ''.join(
            f'<file>{name}</file>' for name in files) + '</gresource></gresources>'
        (root / 'theme.xml').write_text(xml)
        subprocess.run(['glib-compile-resources', f'--sourcedir={root}', f'--target={path}',
                        str(root / 'theme.xml')], check=True)


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, color in [('red', 'red'), ('blue', 'blue'), ('white', 'white')]:
            Image.new('RGB', (64, 40), color).save(self.root / f'{name}.png')

    def values(self):
        return dict(bg_mode='image', bg_image=str(self.root / 'red.png'),
                    bg_image_dark=str(self.root / 'blue.png'))

    def pixel(self, path, x=20, y=20):
        with Image.open(path) as image:
            return image.getpixel((x, y))

    def test_light_dark_render_and_fallback(self):
        result = backgrounds.render_backgrounds(self.values(), self.root / 'out')
        self.assertGreater(self.pixel(result['light'])[0], 240)
        self.assertGreater(self.pixel(result['dark'])[2], 240)

    def test_monitor_layout_handles_negative_positions_and_variant_fallback(self):
        values = self.values()
        values['bg_monitors'] = [dict(x=-64, y=0, width=64, height=40, light='', dark=''),
            dict(x=0, y=0, width=64, height=40, light=str(self.root/'white.png'), dark='')]
        result = backgrounds.render_backgrounds(values, self.root / 'out')
        with Image.open(result['light']) as image:
            self.assertEqual(image.size, (128, 40))
        self.assertGreater(min(self.pixel(result['light'], 90, 20)), 240)
        self.assertGreater(self.pixel(result['dark'], 90, 20)[2], 240)

    def test_dim_and_blur_are_applied_to_export(self):
        values = self.values()
        values.update(bg_image=str(self.root/'white.png'), bg_image_dark='', bg_dim=50, bg_blur=20)
        result = backgrounds.render_backgrounds(values, self.root/'out')
        self.assertTrue(all(120 <= channel <= 135 for channel in self.pixel(result['light'])))

    def test_missing_background_and_invalid_layout_fail_cleanly(self):
        with self.assertRaisesRegex(ValueError, 'Choose'):
            backgrounds.render_backgrounds({'bg_mode':'image'}, self.root/'out')
        values = self.values()
        values['bg_monitors'] = [dict(x=0, y=0, width=0, height=40)]
        with self.assertRaises(ValueError):
            backgrounds.render_backgrounds(values, self.root/'out')
        self.assertIsNone(backgrounds.render_backgrounds({'bg_mode':'default'}, self.root/'out'))


@unittest.skipUnless(shutil.which('gresource') and shutil.which('glib-compile-resources'), 'GLib resource tools required')
class ThemeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        repository = types.SimpleNamespace(
            Gio=types.SimpleNamespace(Resource=types.SimpleNamespace(load=Resource),
                                      ResourceLookupFlags=types.SimpleNamespace(NONE=0)),
            GLib=types.SimpleNamespace(Error=ResourceError, markup_escape_text=html.escape))
        with patch.dict(sys.modules, {'gi.repository':repository}):
            self.helper = load('orbits_test_gdm_helper', 'orbits/gdm/helper.py')
        h = self.helper
        for name, suffix in dict(SHELL_DIR='shell', GRESOURCE='shell/theme.gresource',
            BACKUP='shell/theme.backup', OUR_DIR='shell/orbits', OUR_GRES='shell/orbits/custom.gresource',
            ALT_BASE_FILE='shell/orbits/base', UPDATE_STATE='state', UPDATE_RUNNER='libexec/update',
            PACMAN_HOOK='pacman/hook', APT_HOOK='apt/hook', UPDATE_SERVICE='systemd/update.service',
            UPDATE_PATH='systemd/update.path').items():
            setattr(h, name, str(self.root/suffix))
        h.ALT_CMD = 'orbits-test-no-alternatives'
        self.root.joinpath('shell').mkdir()
        h.relabel = Mock()
        self.files = {'gnome-shell.css':'/* distro v1 */', 'gnome-shell-light.css':'/* light */'}
        make_resource(h.GRESOURCE, self.files)
        self.background = self.root/'image.jpg'
        self.background.write_bytes(b'image data')

    def css(self, path, name='gnome-shell.css'):
        return Resource(path).lookup_data('/org/gnome/shell/theme/'+name, 0).get_data().decode()

    def test_variants_are_compiled_and_missing_dark_stylesheet_is_created(self):
        h = self.helper
        h.apply_theme({'light':'a { color: white; }', 'dark':'a { color: black; }'},
                      {'light':str(self.background), 'dark':str(self.background)})
        self.assertIn('white', self.css(h.GRESOURCE, 'gnome-shell-light.css'))
        self.assertIn('black', self.css(h.GRESOURCE, 'gnome-shell-dark.css'))
        self.assertNotIn('white', self.css(h.GRESOURCE))
        self.assertEqual(Resource(h.GRESOURCE).lookup_data(
            '/org/gnome/shell/theme/orbits-background-light.jpg', 0).get_data(), b'image data')
        h.apply_theme({'light':'b {}', 'dark':'c {}'}, None)
        self.assertEqual(self.css(h.GRESOURCE).count('---- Orbits ----'), 1)
        h.restore_theme()
        self.assertEqual(self.css(h.GRESOURCE), '/* distro v1 */')

    def test_background_asset_names_cannot_escape_resource_tree(self):
        with self.assertRaises(ValueError):
            self.helper.build_gresource(self.helper.GRESOURCE, 'a {}',
                {'../../escape':str(self.background)}, str(self.root/'out.gresource'))

    def enable_updates(self):
        h = self.helper
        real_run = subprocess.run
        real_isdir = os.path.isdir
        self.commands = []
        def run(command, **kw):
            if command[0] == 'systemctl':
                self.commands.append(command)
                return types.SimpleNamespace(returncode=0)
            return real_run(command, **kw)
        patches = [patch.object(h.shutil, 'which', side_effect=lambda name:
                            '/usr/bin/'+name if name in ('pacman','systemctl') else None),
                   patch.object(h.os.path, 'isdir', side_effect=lambda path:
                            True if str(path) in ('/run/systemd/system','/etc/apt/apt.conf.d') else real_isdir(path)),
                   patch.object(h.subprocess, 'run', side_effect=run)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        theme = {'css':{'light':'a {}','dark':'b {}'}, 'background':{'dark':str(self.background)}}
        h.save_update_theme(theme, True)
        return theme

    def test_hooks_store_data_and_use_only_trusted_runner(self):
        self.enable_updates()
        h = self.helper
        manifest = json.loads(Path(h.UPDATE_STATE, 'theme.json').read_text())
        self.assertEqual(Path(manifest['background']['dark']).read_bytes(), b'image data')
        self.assertEqual(Path(h.UPDATE_STATE, 'theme.json').stat().st_mode & 0o777, 0o600)
        for name in (h.PACMAN_HOOK, h.APT_HOOK, h.UPDATE_SERVICE):
            self.assertIn(h.UPDATE_RUNNER, Path(name).read_text())
            self.assertNotIn(str(self.background), Path(name).read_text())
        self.assertIn('PostTransaction', Path(h.PACMAN_HOOK).read_text())
        self.assertIn('PathChanged=', Path(h.UPDATE_PATH).read_text())
        self.assertTrue(any(command[1:3] == ['enable', '--now'] for command in self.commands))
        h.disable_updates()
        for name in (h.UPDATE_RUNNER, h.UPDATE_STATE, h.PACMAN_HOOK, h.APT_HOOK, h.UPDATE_PATH):
            self.assertFalse(Path(name).exists())

    def test_package_update_rebuilds_from_new_base_without_touching_login_settings(self):
        h = self.helper
        self.enable_updates()
        h.apply_theme('/* first apply */', None)
        make_resource(h.GRESOURCE, {'gnome-shell.css':'/* distro v2 */'})
        # Files are owned by the test user; replace only the ownership boundary for the root hook.
        h.read_user_file = lambda path: Path(path).read_bytes()
        h.invoking_uid = lambda: 0
        h.write_dconf = Mock()
        h.edit_custom_conf = Mock()
        h.cmd_reapply()
        self.assertIn('distro v2', self.css(h.GRESOURCE))
        self.assertIn('b {}', self.css(h.GRESOURCE))
        h.write_dconf.assert_not_called()
        h.edit_custom_conf.assert_not_called()
        before = Path(h.GRESOURCE).read_bytes()
        h.cmd_reapply()
        self.assertEqual(Path(h.GRESOURCE).read_bytes(), before)
        h.restore_theme()
        self.assertEqual(self.css(h.GRESOURCE), '/* distro v2 */')

    def test_alternatives_updates_detect_changed_content_with_old_package_timestamp(self):
        h = self.helper
        self.enable_updates()
        base = self.root / "distribution.gresource"
        base.write_bytes(b"old theme")
        Path(h.OUR_DIR).mkdir(parents=True)
        Path(h.ALT_BASE_FILE).write_text(str(base))
        Path(h.OUR_GRES).write_bytes(b"orbits theme")
        h.active_theme_file = lambda: h.OUR_GRES
        h.is_ours = lambda path: path == h.OUR_GRES
        h.invoking_uid = lambda: 0
        h.apply_theme = Mock()
        h.cmd_reapply()
        h.apply_theme.assert_called_once()
        h.apply_theme.reset_mock()
        h.cmd_reapply()
        h.apply_theme.assert_not_called()
        base.write_bytes(b"new distribution theme")
        os.utime(base, (1, 1))
        h.cmd_reapply()
        h.apply_theme.assert_called_once()

    def test_preview_preparation_keeps_settings_and_theme_private(self):
        import orbits.gdm
        h = self.helper
        directory = self.root / 'preview'
        directory.mkdir()
        real_run = subprocess.run
        def run(command, **kw):
            if command[:2] == ['dconf', 'compile']:
                Path(command[2]).write_bytes(b'private settings')
                return types.SimpleNamespace(returncode=0)
            return real_run(command, **kw)
        spec = {'theme': {'css': {'light':'a {}', 'dark':'b {}'}, 'background':None},
                'dconf': {'org/gnome/desktop/interface': {'color-scheme': "'prefer-light'"}}}
        original = copy.deepcopy(spec)
        system_theme = Path(h.GRESOURCE).read_bytes()
        with patch.object(orbits.gdm, 'helper', h, create=True), \
             patch.object(preview.subprocess, 'run', side_effect=run), \
             patch.dict(os.environ, {'DBUS_SESSION_BUS_ADDRESS':'private-user-bus',
                                     'WAYLAND_DISPLAY':'wayland-test', 'XDG_RUNTIME_DIR':'/run/user/test'}):
            env = preview.prepare_preview(spec, directory)
        self.assertNotIn('DBUS_SESSION_BUS_ADDRESS', env)
        self.assertEqual(env['GSETTINGS_BACKEND'], 'dconf')
        self.assertEqual(env['WAYLAND_DISPLAY'], '/run/user/test/wayland-test')
        self.assertEqual(env['XDG_RUNTIME_DIR'], str(directory/'runtime'))
        self.assertEqual(env['XDG_CONFIG_HOME'], str(directory/'config'))
        self.assertEqual(Path(env['DCONF_PROFILE']).read_text(), f'file-db:{directory}/settings.db\n')
        self.assertIn('prefer-light', (directory/'keyfiles/00-preview').read_text())
        self.assertIn('b {}', self.css(directory/'shell/gnome-shell-theme.gresource'))
        self.assertEqual(spec, original)
        self.assertEqual(Path(h.GRESOURCE).read_bytes(), system_theme)

    def test_authenticated_undo_restores_files_and_rejects_another_user(self):
        h = self.helper
        h.UNDO_DIR = str(self.root/'undo')
        path = self.root/'setting'
        path.write_text('before')
        h.undo_targets = lambda args:([str(path)], '')
        h.invoking_uid = lambda:1000
        token = h.capture_undo(['helper','apply'])
        path.write_text('after')
        h.invoking_uid = lambda:1001
        with self.assertRaises(PermissionError):h.cmd_undo(token)
        self.assertEqual(path.read_text(),'after')
        h.invoking_uid = lambda:1000
        h.cmd_undo(token)
        self.assertEqual(path.read_text(),'before')
        self.assertFalse(Path(h.UNDO_DIR,token).exists())
        with self.assertRaises(ValueError):h.cmd_undo('../escape')

    def test_undo_restores_deleted_theme_without_following_external_symlinks(self):
        h = self.helper
        h.UNDO_DIR = str(self.root/'undo')
        directory=self.root/'installed-theme';directory.mkdir()
        external=self.root/'external';external.write_text('keep')
        (directory/'file').write_text('theme')
        (directory/'link').symlink_to(external)
        h.undo_targets=lambda args:([str(directory)],'')
        token=h.capture_undo(['helper','theme-remove'])
        shutil.rmtree(directory)
        h.cmd_undo(token)
        self.assertEqual((directory/'file').read_text(),'theme')
        self.assertTrue((directory/'link').is_symlink())
        self.assertEqual(external.read_text(),'keep')

    def test_reapply_refuses_user_invocation(self):
        self.helper.invoking_uid = lambda: 1000
        with self.assertRaises(PermissionError):
            self.helper.cmd_reapply()


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        config = types.SimpleNamespace(CACHE_DIR=Path(self.temp.name), CONFIG_DIR=Path(self.temp.name), JsonStore=Mock())
        with patch.dict(sys.modules, {'gi.repository':types.SimpleNamespace(Gio=Mock(), GLib=Mock()),
                                     'orbits.config':config}):
            module = load('orbits_test_gdm_model', 'orbits/gdm/model.py')
        self.model = module.GdmModel.__new__(module.GdmModel)
        self.model.values = copy.deepcopy(module.THEME_DEFAULTS)
        self.model.keys = []
        self.model.store = Mock(data={})

    def test_variant_css_and_monitor_layout(self):
        self.model.values.update(bg_mode='image', bg_monitors=[{'width':1920}])
        for variant in ('light', 'dark'):
            css = self.model.build_css(variant)
            self.assertIn(f'orbits-background-{variant}.jpg', css)
            self.assertIn('background-size: cover', css)
            self.assertNotIn('100%', css)
        self.model.values['bg_monitors'] = []
        self.assertIn('background-size: cover', self.model.build_css())

    def test_clock_font_is_escaped_and_panel_opacity_is_bounded(self):
        self.model.values.update(clock_font='Test"; }\n', panel_opacity=300)
        css = self.model.build_css()
        self.assertIn('font-family: "Test\\"; } "', css)
        self.assertIn('rgba(0, 0, 0, 1.00)', css)

    def test_spec_contains_variants_and_opt_in_without_authentication_side_effects(self):
        self.model.values.update(bg_mode='image', auto_reapply=True)
        spec = self.model.build_spec({'light':'light.jpg', 'dark':'dark.jpg'})
        self.assertEqual(set(spec['theme']['css']), {'light','dark'})
        self.assertTrue(spec['auto_reapply'])
        self.assertIsNone(spec['custom_conf']['AutomaticLoginEnable'])
        self.model.set('applied_theme', True)
        self.model.store.save.assert_called_once()


class PreviewTests(unittest.TestCase):
    def test_command_is_nested_and_uses_a_separate_bus(self):
        with patch.object(preview.shutil, 'which', side_effect=lambda name:'/usr/bin/'+name), \
             patch.object(preview.subprocess, 'run', return_value=types.SimpleNamespace(stdout='--devkit')), \
             patch.dict(os.environ, {'WAYLAND_DISPLAY':'wayland-test'}):
            command = preview.preview_command()
        self.assertEqual(command[:2], ['/usr/bin/dbus-run-session', '--'])
        self.assertIn('--devkit', command)
        self.assertIn('--mode=gdm', command)
        self.assertNotIn('--replace', command)

    def test_old_shell_is_reported_without_launching(self):
        with patch.object(preview.shutil, 'which', return_value='/usr/bin/tool'), \
             patch.object(preview.subprocess, 'run', return_value=types.SimpleNamespace(stdout='--nested')):
            with self.assertRaisesRegex(RuntimeError, 'does not support'):
                preview.preview_command()

    def test_failed_preview_launch_removes_temporary_files(self):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name)
        with patch.object(preview, 'preview_command', side_effect=RuntimeError('missing dependency')):
            with self.assertRaises(RuntimeError):
                preview.PreviewSession({}, directory)
        self.assertFalse(path.exists())

    def test_session_exit_and_user_stop_clean_up_without_errors(self):
        for stop in (False, True):
            with self.subTest(stop=stop):
                directory = tempfile.TemporaryDirectory()
                path = Path(directory.name)
                command = [sys.executable, '-c', 'import time; time.sleep(30)' if stop else 'print("preview")']
                with patch.object(preview, 'preview_command', return_value=command), \
                     patch.object(preview, 'prepare_preview', return_value=dict(os.environ)):
                    session = preview.PreviewSession({}, directory)
                if stop:
                    session.stop()
                session.wait()
                self.assertFalse(path.exists())

    def test_preview_failure_reports_log_and_removes_files(self):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name)
        command = [sys.executable, '-c', 'import sys; print("renderer unavailable"); sys.exit(1)']
        with patch.object(preview, 'preview_command', return_value=command), \
             patch.object(preview, 'prepare_preview', return_value=dict(os.environ)):
            session = preview.PreviewSession({}, directory)
        with self.assertRaisesRegex(RuntimeError, 'renderer unavailable'):
            session.wait()
        self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
