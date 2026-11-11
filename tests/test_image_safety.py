"""Sandbox boundary, raw-pixel reconstruction and local snapshot regressions."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from orbits import image_safety, security


class ImageSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root/'input.png'
        self.source.write_bytes(b'untrusted')
        scanner = patch('orbits.security.configured_scan', return_value='Not scanned')
        self.scan = scanner.start()
        self.addCleanup(scanner.stop)

    def fake_worker(self, command, **kwargs):
        output = Path(command[command.index('--bind') + 1])
        (output/'frames.json').write_text(json.dumps([[2, 1]]))
        (output/'0.rgba').write_bytes(bytes([255, 0, 0, 255])*2)
        return types.SimpleNamespace(returncode=0)

    def test_sandbox_has_only_runtime_and_explicit_input_output(self):
        with patch('orbits.image_safety.shutil.which', return_value='/usr/bin/bwrap'):
            command = image_safety.sandbox_command(self.source, self.root/'out', 640, 1)
        self.assertIn('--unshare-all', command)
        self.assertIn('--clearenv', command)
        self.assertIn('--new-session', command)
        self.assertIn('--cap-drop', command)
        self.assertNotIn(str(Path.home()), command)
        self.assertNotIn('--ro-bind / /', ' '.join(command))
        self.assertEqual(command.count('--bind'), 1)
        self.assertIn('-I', command)

    def test_missing_or_broken_sandbox_never_falls_back(self):
        with patch('orbits.image_safety.shutil.which', return_value=None), patch('orbits.image_safety.subprocess.run') as run:
            with self.assertRaisesRegex(RuntimeError, 'bubblewrap'):
                image_safety.normalize_images(self.source, self.root/'out')
            run.assert_not_called()
        with patch('orbits.image_safety.shutil.which', return_value='bwrap'), patch('orbits.image_safety.subprocess.run', return_value=types.SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(RuntimeError, 'namespaces'):
                image_safety.normalize_images(self.source, self.root/'out')

    def test_raw_pixels_are_encoded_without_original_metadata(self):
        from PIL import Image
        with patch('orbits.image_safety.shutil.which', return_value='bwrap'), patch('orbits.image_safety.subprocess.run', side_effect=self.fake_worker):
            images = image_safety.normalize_images(self.source, self.root/'out')
        self.scan.assert_called_once()
        with Image.open(images[0]) as image:
            self.assertEqual(image.size, (2, 1))
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 255))
            self.assertEqual(image.info, {})

    def test_worker_output_is_checked_even_if_decoder_is_compromised(self):
        for attack in ('oversized', 'short', 'symlink', 'badmanifest'):
            def worker(command, **kwargs):
                self.fake_worker(command, **kwargs)
                output = Path(command[command.index('--bind') + 1])
                if attack == 'oversized':
                    (output/'frames.json').write_text('[[100000,100000]]')
                elif attack == 'short':
                    (output/'0.rgba').write_bytes(b'x')
                elif attack == 'symlink':
                    (output/'0.rgba').unlink()
                    (output/'0.rgba').symlink_to(self.source)
                else:
                    (output/'frames.json').write_text('{}')
                return types.SimpleNamespace(returncode=0)
            with self.subTest(attack=attack), patch('orbits.image_safety.shutil.which', return_value='bwrap'), patch('orbits.image_safety.subprocess.run', side_effect=worker):
                with self.assertRaises((RuntimeError, OSError)):
                    image_safety.normalize_images(self.source, self.root/attack)
                self.assertFalse(list((self.root/attack).glob('*.png')))

    def test_snapshot_consumes_scanned_bytes_and_rejects_symlink(self):
        def scan(path):
            self.source.write_bytes(b'changed after scan')
        with patch('orbits.security.configured_scan', side_effect=scan):
            with security.checked_local_file(self.source, archive=False) as captured:
                self.assertEqual(captured.read_bytes(), b'untrusted')
        link = self.root/'link.png'
        link.symlink_to(self.source)
        with self.assertRaises(OSError):
            with security.checked_local_file(link):
                self.fail('A symlink must not be consumed')

    def test_scanner_rejection_prevents_decoder_execution(self):
        with patch('orbits.security.configured_scan', side_effect=RuntimeError('flagged')), patch('orbits.image_safety.subprocess.run') as run:
            with self.assertRaisesRegex(RuntimeError, 'flagged'):
                image_safety.normalize_images(self.source, self.root/'out')
            run.assert_not_called()

    def test_real_worker_decodes_and_rejects_bad_content(self):
        from PIL import Image
        Image.new('RGB', (8, 6), 'red').save(self.source)
        output = self.root/'pixels'; output.mkdir()
        worker = Path(image_safety.__file__).with_name('image_worker.py')
        command = [sys.executable, '-I', str(worker), '4', '1', str(self.source), str(output)]
        result = subprocess.run(command, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((output/'frames.json').read_text()), [[4, 3]])
        self.assertEqual((output/'0.rgba').stat().st_size, 48)
        self.source.write_bytes(b'#!/bin/sh\nmalware')
        result = subprocess.run(command, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)

    def test_live_bubblewrap_isolation_when_available(self):
        from PIL import Image
        import shutil
        if not shutil.which('bwrap'):
            self.skipTest('bubblewrap is not installed')
        # Probe namespace availability separately: failure must remain fail-closed in production.
        probe = subprocess.run(['bwrap', '--unshare-all', '--ro-bind', '/', '/', '--', '/usr/bin/true'], capture_output=True)
        if probe.returncode:
            self.skipTest('host blocks bubblewrap namespaces')
        Image.new('RGB', (8, 6), 'blue').save(self.source)
        result = image_safety.normalize_images(self.source, self.root/'out')
        self.assertEqual(len(result), 1)


class PreviewAndImportTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import Mock
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.texture = Mock()
        repository = types.SimpleNamespace(Gdk=types.SimpleNamespace(Texture=self.texture), GLib=types.SimpleNamespace(Bytes=types.SimpleNamespace(new=lambda data: data)))
        self.settings = {'malware_scan': False}
        config = types.SimpleNamespace(THUMB_CACHE=self.root, DATA_DIR=self.root, settings=self.settings)
        self.modules = patch.dict('sys.modules', {'gi.repository': repository, 'orbits.config': config})
        self.modules.start(); self.addCleanup(self.modules.stop)
        spec = importlib.util.spec_from_file_location('orbits_safety_test_net', Path(image_safety.__file__).with_name('net.py'))
        self.net = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.net)

    def test_preview_caps_advertised_and_actual_bytes(self):
        from io import BytesIO
        for advertised in ('100', None):
            response = BytesIO(b'123456789')
            response.headers = {'Content-Length': advertised} if advertised else {}
            with self.subTest(advertised=advertised), patch.object(self.net, '_open', return_value=response):
                with self.assertRaisesRegex(RuntimeError, 'limit'):
                    self.net.get_bytes('https://example.org/image', limit=8)

    def test_cached_preview_cannot_bypass_sandbox(self):
        import hashlib
        url = 'https://example.org/image'
        (self.root/hashlib.sha1(url.encode()).hexdigest()).write_bytes(b'malicious cached image')
        with patch('orbits.security.configured_scan'), patch('orbits.image_safety.shutil.which', return_value=None), patch.object(self.net, 'get_bytes') as fetch:
            with self.assertRaisesRegex(RuntimeError, 'bubblewrap'):
                self.net.cached_texture(url)
            fetch.assert_not_called()
            self.texture.new_from_bytes.assert_not_called()

    def test_local_theme_download_scans_before_replacing_destination(self):
        source, destination = self.root/'theme.zip', self.root/'installed.zip'
        source.write_bytes(b'untrusted theme')
        destination.write_bytes(b'previous')
        with patch('orbits.security.configured_scan', side_effect=RuntimeError('scanner blocked')):
            with self.assertRaisesRegex(RuntimeError, 'scanner blocked'):
                self.net.download(source.as_uri(), destination, allow_local=True)
        self.assertEqual(destination.read_bytes(), b'previous')

    def test_font_import_scans_before_font_parser(self):
        from orbits.font_tools import prepare_local
        source = self.root/'font.ttf'
        source.write_bytes(b'untrusted font')
        with patch('orbits.security.configured_scan', side_effect=RuntimeError('scanner blocked')), patch('orbits.font_tools.inspect_font') as parser:
            with self.assertRaisesRegex(RuntimeError, 'scanner blocked'):
                prepare_local([source], self.root/'fonts')
            parser.assert_not_called()


# Unit suites execute trusted worker code in child processes; live isolation has
# a separate required integration profile and never changes production behavior.
def setUpModule():
    from tests.parser_fixtures import run_worker
    global _parser_fixture
    _parser_fixture = patch('orbits.parser_sandbox.run', side_effect=run_worker)
    _parser_fixture.start()


def tearDownModule():
    _parser_fixture.stop()
