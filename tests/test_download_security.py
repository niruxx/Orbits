"""Security checks must block delivery rather than silently bypass errors."""
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

from orbits.downloads import DownloadManager, Transfer
from orbits.security import scan_download, validate_wallpaper


class MalwareTests(unittest.TestCase):
    def test_missing_scanner_blocks(self):
        with patch('orbits.security.shutil.which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                scan_download('unused')

    def test_scanner_outcomes(self):
        for code in (0, 1, 2):
            with self.subTest(code=code), patch('orbits.security.shutil.which', return_value='/usr/bin/clamscan'), patch('orbits.security.subprocess.run', return_value=types.SimpleNamespace(returncode=code)) as run:
                if code == 0:
                    self.assertEqual(scan_download('sample'), 'No known malware detected')
                else:
                    with self.assertRaises(RuntimeError):
                        scan_download('sample')
                self.assertIn('--alert-exceeds-max=yes', run.call_args.args[0])
                self.assertFalse(run.call_args.kwargs.get('shell', False))

    def test_timeout_blocks(self):
        with patch('orbits.security.shutil.which', return_value='clamscan'), patch('orbits.security.subprocess.run', side_effect=subprocess.TimeoutExpired('clamscan', 180)):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                scan_download('sample')

    def test_scan_failure_preserves_destination_and_removes_cached_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, destination = root/'payload.complete', root/'installed'
            source.write_bytes(b'payload')
            destination.write_bytes(b'previous')
            def reject(path):
                raise RuntimeError('blocked')
            manager = DownloadManager(root/'cache', scanner=reject)
            try:
                job = Transfer('https://example.org/file', destination)
                with self.assertRaisesRegex(RuntimeError, 'blocked'):
                    manager._deliver(source, job, {})
                self.assertEqual(destination.read_bytes(), b'previous')
                self.assertFalse(source.exists())
                self.assertEqual(job.scan_result, 'Scan blocked installation')
            finally:
                manager.close()

    def test_cached_delivery_is_rescanned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'cached.complete'
            source.write_bytes(b'payload')
            calls = []
            def scan(path):
                calls.append(path)
                return 'No known malware detected'
            manager = DownloadManager(root/'cache', scanner=scan)
            try:
                for _ in range(2):
                    manager._deliver(source, Transfer('https://example.org/file', root/'out'), {})
                self.assertEqual(len(calls), 2)
            finally:
                manager.close()


class WallpaperTests(unittest.TestCase):
    def test_fake_image_rejected_and_valid_image_accepted(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'image.png'
            path.write_bytes(b'#!/bin/sh\necho unexpected')
            with self.assertRaisesRegex(RuntimeError, 'validation failed'):
                validate_wallpaper(path)
            Image.new('RGB', (10, 10)).save(path)
            validate_wallpaper(path)
            path.write_bytes(path.read_bytes()[:40])
            with self.assertRaises(RuntimeError):
                validate_wallpaper(path)

    def test_svg_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'SVG'):
            validate_wallpaper('image.svg')


class ArchiveTests(unittest.TestCase):
    def test_reject_links_and_excessive_expansion(self):
        import tarfile
        import zipfile
        from orbits.security import inspect_archive
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'archive'
            with tarfile.open(path, 'w') as archive:
                entry = tarfile.TarInfo('link')
                entry.type = tarfile.SYMTYPE
                entry.linkname = '/etc/passwd'
                archive.addfile(entry)
            with self.assertRaisesRegex(ValueError, 'link'):
                inspect_archive(path)
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('image.png', b'data')
            inspect_archive(path)
            with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('large', b'0' * 65536)
            with patch('orbits.safe_archive.MAX_BYTES', 32768):
                with self.assertRaises(ValueError):
                    inspect_archive(path)

    def test_transfer_rejects_unknown_length_oversize_before_delivery(self):
        from email.message import Message
        from io import BytesIO
        class Response(BytesIO):
            status = 200
            headers = Message()
            def geturl(self):
                return 'https://example.org/file'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = DownloadManager(root/'cache', opener=lambda *a, **kw: Response(b'123456789'))
            try:
                with patch('orbits.downloads.MAX_DOWNLOAD', 8):
                    job = manager.submit('https://example.org/file', root/'out')
                    with self.assertRaisesRegex(RuntimeError, 'limit'):
                        manager.wait(job)
                self.assertFalse((root/'out').exists())
            finally:
                manager.close()


class DefinitionTests(unittest.TestCase):
    def test_scanning_disabled_never_updates_or_scans(self):
        from orbits.security import scan_with_settings
        with patch('orbits.security.update_definitions') as update, patch('orbits.security.scan_download') as scan:
            self.assertEqual(scan_with_settings('file', {'malware_scan': False, 'malware_auto_update': True}, '/unused'), 'Not scanned')
            update.assert_not_called()
            scan.assert_not_called()

    def test_update_toggle_and_offline_behavior(self):
        from orbits.security import scan_with_settings
        for automatic, offline in ((False, False), (True, False), (True, True)):
            with self.subTest(automatic=automatic, offline=offline), patch('orbits.security.update_definitions') as update, patch('orbits.security.scan_download', return_value='scanned') as scan, patch('orbits.security.has_definitions', return_value=True):
                self.assertEqual(scan_with_settings('file', {'malware_scan': True, 'malware_auto_update': automatic}, '/private', offline=offline), 'scanned')
                self.assertEqual(update.called, automatic and not offline)
                scan.assert_called_once_with('file', database='/private')

    def test_update_offline_missing_tool_and_failure(self):
        from orbits.security import update_definitions
        with tempfile.TemporaryDirectory() as directory:
            with patch('orbits.security.subprocess.run') as run:
                with self.assertRaisesRegex(RuntimeError, 'Offline'):
                    update_definitions(directory, offline=True)
                run.assert_not_called()
            with patch('orbits.security.shutil.which', return_value=None):
                with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                    update_definitions(directory)
            with patch('orbits.security.shutil.which', return_value='freshclam'), patch('orbits.security.subprocess.run', return_value=types.SimpleNamespace(returncode=1)):
                with self.assertRaisesRegex(RuntimeError, 'failed'):
                    update_definitions(directory)
                self.assertFalse((Path(directory)/'last-update').exists())

    def test_success_daily_throttle_and_manual_override(self):
        from orbits.security import update_definitions
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def update(*args, **kwargs):
                (root/'main.cvd').touch()
                (root/'daily.cvd').touch()
                return types.SimpleNamespace(returncode=0)
            with patch('orbits.security.shutil.which', return_value='freshclam'), patch('orbits.security.subprocess.run', side_effect=update) as run:
                update_definitions(root, force=False)
                update_definitions(root, force=False)
                self.assertEqual(run.call_count, 1)
                update_definitions(root, force=True)
                self.assertEqual(run.call_count, 2)
                self.assertIn('--datadir='+str(root), run.call_args.args[0])
                (root/'daily.cvd').unlink()
                update_definitions(root, force=False)
                self.assertEqual(run.call_count, 3)

    def test_failed_automatic_update_blocks_scan(self):
        from orbits.security import scan_with_settings
        with patch('orbits.security.update_definitions', side_effect=RuntimeError('update failed')), patch('orbits.security.scan_download') as scan:
            with self.assertRaisesRegex(RuntimeError, 'update failed'):
                scan_with_settings('file', {'malware_scan': True, 'malware_auto_update': True}, '/private')
            scan.assert_not_called()


# Unit suites execute trusted worker code in child processes; live isolation has
# a separate required integration profile and never changes production behavior.
def setUpModule():
    from tests.parser_fixtures import run_worker
    global _parser_fixture
    _parser_fixture = patch('orbits.parser_sandbox.run', side_effect=run_worker)
    _parser_fixture.start()


def tearDownModule():
    _parser_fixture.stop()
