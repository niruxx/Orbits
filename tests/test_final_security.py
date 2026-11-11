"""Publisher pinning, fail-closed parser isolation and legacy baseline tests."""
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from orbits import publishers, parser_sandbox, legacy_safety, integrity

KEY = base64.b64encode(b'Ed' + bytes(40)).decode()


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.payload = self.root/'asset'; self.payload.write_bytes(b'asset')
        self.record = {'name': 'Fixture', 'host': 'example.org', 'key': KEY}

    def test_explicit_trust_round_trip_and_revocation(self):
        publishers.save_keys(self.root, [self.record])
        self.assertEqual(publishers.keys(self.root), [self.record])
        self.assertEqual((self.root/'trusted-publishers.json').stat().st_mode & 0o777, 0o600)
        publishers.save_keys(self.root, [])
        self.assertEqual(publishers.keys(self.root), [])

    def test_unknown_publishers_are_not_implicitly_trusted(self):
        self.assertEqual(publishers.verify_download(self.payload, 'https://example.org/a', self.root, self.root/'cache'), 'Publisher unverified')
        with self.assertRaisesRegex(ValueError, 'No trusted'):
            publishers.verify_download(self.payload, 'https://example.org/a', self.root, self.root/'cache', required=True)

    def test_bad_key_and_invalid_signature_never_pass(self):
        with self.assertRaises(ValueError): publishers.validate_key('example.org', 'bad', 'name')
        with patch('orbits.publishers.shutil.which', return_value='minisign'), patch('orbits.publishers.subprocess.run', return_value=types.SimpleNamespace(returncode=1)):
            with self.assertRaises(ValueError): publishers.verify_signature(self.payload, b'signature', [self.record])
        with patch('orbits.publishers.shutil.which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'minisign'): publishers.verify_signature(self.payload, b'signature', [self.record])

    def test_pinned_host_verifies_online_and_reverifies_cache_offline(self):
        publishers.save_keys(self.root, [self.record])
        response = io.BytesIO(b'signature')
        with patch('orbits.safe_network.open_remote', return_value=response) as request, patch('orbits.publishers.verify_signature', return_value='Publisher verified: Fixture') as verify:
            publishers.verify_download(self.payload, 'https://example.org/asset?q=1', self.root, self.root/'cache')
            request.assert_called_once_with('https://example.org/asset.minisig?q=1', timeout=20)
            publishers.verify_download(self.payload, 'https://example.org/asset?q=1', self.root, self.root/'cache', offline=True)
            self.assertEqual(verify.call_count, 2)
            self.payload.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'not cached'):
                publishers.verify_download(self.payload, 'https://example.org/asset?q=1', self.root, self.root/'cache', offline=True)


class ParserBoundaryTests(unittest.TestCase):
    def test_missing_sandbox_never_launches_a_parser(self):
        with patch('orbits.image_safety.shutil.which', return_value=None), patch('orbits.parser_sandbox.subprocess.run') as process:
            with self.assertRaises(RuntimeError): parser_sandbox.run('/input', '/output', '/worker.py', ['probe'])
            process.assert_not_called()

    def test_only_the_explicit_trusted_worker_replaces_the_image_worker(self):
        with patch('orbits.image_safety.shutil.which', return_value='bwrap'):
            command = parser_sandbox.command('/input-file', '/output-dir', '/trusted/archive_worker.py', ['probe'])
        self.assertIn('/trusted/archive_worker.py', command)
        self.assertNotIn(str(Path(__file__).resolve().parents[1]/'orbits/image_worker.py'), command)
        self.assertIn('--unshare-all', command)
        self.assertEqual(command[-2:], ['/worker.py', 'probe'])

    def test_compromised_font_worker_metadata_is_rejected(self):
        from orbits.font_safety import inspect_font, render_font
        with patch('orbits.font_safety._process', return_value={'family': []}):
            with self.assertRaises(ValueError): inspect_font('font.ttf')
        with self.assertRaises(ValueError): render_font('font.ttf', text='x'*2049)


class LegacyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root/'image.png').write_bytes(b'fixture')

    def test_first_baseline_only_after_successful_validation(self):
        with patch('orbits.security.configured_scan'), patch('orbits.image_safety.normalize_images') as decoder:
            result = legacy_safety.revalidate([('wallpaper', self.root)])
            self.assertIn('1 legacy', result)
            decoder.assert_called_once()
            self.assertTrue(integrity.verify_tree(self.root))

    def test_invalid_assets_do_not_receive_a_baseline(self):
        with patch('orbits.security.configured_scan'), patch('orbits.image_safety.normalize_images', side_effect=ValueError('invalid')):
            with self.assertRaises(ValueError): legacy_safety.revalidate([('wallpaper', self.root)])
        self.assertFalse((self.root/integrity.MANIFEST).exists())

    def test_changed_existing_baseline_is_never_replaced(self):
        integrity.record_tree(self.root)
        previous = (self.root/integrity.MANIFEST).read_bytes()
        (self.root/'image.png').write_bytes(b'changed')
        with self.assertRaises(ValueError): legacy_safety.revalidate([('wallpaper', self.root)])
        self.assertEqual((self.root/integrity.MANIFEST).read_bytes(), previous)


class FontWorkerTests(unittest.TestCase):
    def test_actual_font_worker_inspects_and_renders_valid_font(self):
        from tests.parser_fixtures import run_worker
        from tests.test_fonts_looks import make_font
        from orbits.font_safety import inspect_font, render_font
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'font.ttf'; make_font(path)
            with patch('orbits.parser_sandbox.run', side_effect=run_worker), patch('orbits.security.configured_scan'):
                self.assertEqual(inspect_font(path)['family'], 'Orbits Fixture')
                image = render_font(path, 'AB', 24)
                self.assertTrue(image.startswith(b'\x89PNG'))
                path.write_bytes(b'not a font')
                with self.assertRaises(ValueError): inspect_font(path)

    def test_downloaded_script_modules_are_rejected(self):
        from orbits.asset_validation import validate_theme_file
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'theme.plymouth'
            path.write_text('[Plymouth Theme]\nModuleName=script\n')
            with self.assertRaises(ValueError): validate_theme_file(path, path.name, 'plymouth')
