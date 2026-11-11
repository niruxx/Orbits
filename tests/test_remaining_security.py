"""Adversarial network, archive, asset policy and installed-integrity regressions."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch
import urllib.request
import zipfile

from orbits import asset_validation, integrity, safe_archive, safe_network, security


class NetworkTests(unittest.TestCase):
    def test_only_https_without_credentials(self):
        for url in ('http://example.org/x', 'file:///etc/passwd', 'ftp://example.org', 'https://user:password@example.org', 'https://localhost/', 'https://[fe80::1%25eth0]/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                safe_network.validate_url(url)
        self.assertEqual(safe_network.validate_url('https://example.org/file'), ('example.org', 443))

    def test_all_dns_answers_must_be_public(self):
        for address in ('127.0.0.1', '10.0.0.1', '169.254.169.254', '192.168.1.1', '100.64.0.1', '224.0.0.1', '::1', 'fc00::1', '::ffff:127.0.0.1'):
            family = socket.AF_INET6 if ':' in address else socket.AF_INET
            answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443)), (family, socket.SOCK_STREAM, 6, '', (address, 443))]
            with self.subTest(address=address), patch('orbits.safe_network.socket.getaddrinfo', return_value=answers), self.assertRaises(ValueError):
                safe_network.public_addresses('example.org', 443)

    def test_connection_pins_validated_numeric_address(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
        with patch('orbits.safe_network.socket.getaddrinfo', return_value=answers) as resolver, patch('orbits.safe_network.socket.socket') as factory:
            connection = safe_network._connect_public(('example.org', 443), timeout=5)
            factory.return_value.connect.assert_called_once_with(('8.8.8.8', 443))
            resolver.assert_called_once()
            self.assertIs(connection, factory.return_value)

    def test_redirect_rejects_downgrade_and_strips_cross_origin_credentials(self):
        handler = safe_network.CheckedRedirect()
        request = urllib.request.Request('https://example.org/file', headers={'Authorization': 'secret', 'Cookie': 'private'})
        for target in ('http://other.org/file', 'file:///etc/passwd'):
            with self.assertRaises(ValueError):
                handler.redirect_request(request, None, 302, 'Found', {}, target)
        redirected = handler.redirect_request(request, None, 302, 'Found', {}, 'https://other.org/file')
        self.assertFalse(redirected.has_header('Authorization'))
        self.assertFalse(redirected.has_header('Cookie'))
        self.assertEqual(handler.max_redirections, 5)

    def test_tls_keeps_hostname_verification(self):
        context = safe_network.ssl.create_default_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, safe_network.ssl.CERT_REQUIRED)
        connection = safe_network.PublicHTTPSConnection('example.org', context=context)
        self.assertIs(connection._create_connection, safe_network._connect_public)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive = self.root/'archive.zip'

    def test_traversal_duplicates_and_conflicts_rejected(self):
        for names in (['../escape'], ['/absolute'], ['a', 'a'], ['a', 'a/b'], ['a/b', 'a'], ['a/./b', 'a/b'], ['a\\b'], ['a/'*21+'file']):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                with zipfile.ZipFile(self.archive, 'w') as archive:
                    for name in names:
                        archive.writestr(name, b'x')
            with self.subTest(names=names), tempfile.TemporaryDirectory() as destination, self.assertRaises(ValueError):
                safe_archive.extract(self.archive, destination)
        self.assertFalse((self.root.parent/'escape').exists())

    def test_actual_byte_count_and_declared_size_enforced(self):
        with patch('orbits.safe_archive.MAX_BYTES', 4):
            with self.assertRaises(ValueError):
                safe_archive.Budget().copy(io.BytesIO(b'12345'), io.BytesIO())
        with self.assertRaises(ValueError):
            safe_archive.Budget().copy(io.BytesIO(b'12345'), io.BytesIO(), declared=4)
        with self.assertRaises(ValueError):
            safe_archive.Budget().copy(io.BytesIO(b'123'), io.BytesIO(), declared=4)

    def test_zip_symlinks_and_tar_hardlinks_rejected(self):
        member = zipfile.ZipInfo('link'); member.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(self.archive, 'w') as archive:
            archive.writestr(member, '/etc/passwd')
        with self.assertRaises(ValueError):
            safe_archive.extract(self.archive, self.root/'zip-output')
        with tarfile.open(self.archive, 'w') as archive:
            member = tarfile.TarInfo('link'); member.type = tarfile.LNKTYPE; member.linkname = '/etc/passwd'; archive.addfile(member)
        with self.assertRaises(ValueError):
            safe_archive.extract(self.archive, self.root/'tar-output')

    def test_existing_file_is_never_deleted_or_overwritten(self):
        destination = self.root/'out'; destination.mkdir()
        (destination/'file').write_bytes(b'previous')
        with zipfile.ZipFile(self.archive, 'w') as archive:
            archive.writestr('file', b'new')
        with self.assertRaises(FileExistsError):
            safe_archive.extract(self.archive, destination)
        self.assertEqual((destination/'file').read_bytes(), b'previous')

    def test_decompression_bomb_hits_expanded_limit(self):
        with zipfile.ZipFile(self.archive, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('bomb', b'0' * 65536)
        with patch('orbits.safe_archive.MAX_BYTES', 32768), self.assertRaises(ValueError):
            safe_archive.extract(self.archive, self.root/'out')


class IntegrityAndPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_changed_added_deleted_files_detected(self):
        for operation in ('change', 'add', 'delete'):
            root = self.root/operation; root.mkdir()
            path = root/'image.png'; path.write_bytes(b'validated')
            integrity.record_tree(root)
            self.assertTrue(integrity.verify_tree(root))
            if operation == 'change': path.write_bytes(b'changed')
            elif operation == 'delete': path.unlink()
            else: (root/'new').write_bytes(b'added')
            with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, 'changed'):
                integrity.verify_tree(root)

    def test_legacy_has_no_automatic_baseline_and_manifest_paths_are_untrusted(self):
        self.assertIsNone(integrity.verify_tree(self.root))
        (self.root/integrity.MANIFEST).write_text(json.dumps({'version': 1, 'sha256': {'../../outside': '0'*64}}))
        with self.assertRaisesRegex(ValueError, 'manifest'):
            integrity.verify_tree(self.root)

    def test_executables_and_active_metadata_blocked(self):
        for name, data in (('install.sh', b'echo malicious'), ('image.png', b'\x7fELFpayload'), ('metadata.desktop', b'[Desktop Entry]\nExec=evil\n'), ('theme.css', b'@import "https://example.org/payload.css";'), ('plugin.qml', b'import QtQuick')):
            path = self.root/name; path.write_bytes(data)
            with self.subTest(name=name), self.assertRaises(ValueError):
                asset_validation.validate_theme_file(path, name, 'gtk')

    def test_malformed_font_tables_rejected(self):
        for data in (b'not a font', b'\0\1\0\0' + b'\xff'*20, b'ttcf' + b'\0'*8):
            with self.assertRaises(ValueError):
                asset_validation.validate_font_bytes(data)

    def test_definition_status_reports_stale_and_missing_tools(self):
        (self.root/'main.cvd').touch(); daily = self.root/'daily.cvd'; daily.touch()
        os.utime(daily, (time.time()-10*86400,)*2)
        (self.root/'last-update').write_text(str(time.time()-86400))
        with patch('orbits.security.shutil.which', return_value=None):
            status = security.definition_status(self.root, {'malware_scan': False})
        self.assertIn('Scanning disabled', status)
        self.assertIn('clamscan missing', status)
        self.assertIn('update recommended', status)
        self.assertIn('Last successful', status)


class HelperStagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        repository = types.SimpleNamespace(Gio=Mock(), GLib=Mock())
        spec = importlib.util.spec_from_file_location('security_helper_test', Path(__file__).resolve().parents[1]/'orbits/gdm/helper.py')
        self.helper = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'gi.repository': repository}):
            spec.loader.exec_module(self.helper)
        self.source = self.root/'source'; self.source.mkdir()
        (self.source/'theme.css').write_text('body { color: red; }')
        self.owner = patch.object(self.helper, 'invoking_uid', return_value=os.getuid())
        self.owner.start(); self.addCleanup(self.owner.stop)

    def test_private_snapshot_rejects_links_and_unsafe_modes(self):
        self.helper._snapshot_user_tree(str(self.source), str(self.root/'valid'))
        self.assertEqual((self.root/'valid/theme.css').read_text(), 'body { color: red; }')
        (self.source/'link').symlink_to('/etc/passwd')
        with self.assertRaises((OSError, PermissionError)):
            self.helper._snapshot_user_tree(str(self.source), str(self.root/'linked'))
        (self.source/'link').unlink()
        (self.source/'theme.css').chmod(0o666)
        with self.assertRaises(PermissionError):
            self.helper._snapshot_user_tree(str(self.source), str(self.root/'writable'))

    def test_staged_theme_validation_precedes_replacement(self):
        destination = self.root/'themes'; destination.mkdir()
        existing = destination/'Example'; existing.mkdir()
        (existing/'.orbits.json').write_text('{}')
        (existing/'theme.css').write_text('previous')
        (self.source/'image.png').write_bytes(b'\x7fELFevil')
        with patch.dict(self.helper.THEME_BASES, {'themes': str(destination)}):
            with self.assertRaises(ValueError):
                self.helper.cmd_theme_install('themes', str(self.source), 'Example')
        self.assertEqual((existing/'theme.css').read_text(), 'previous')

    def test_root_validator_matches_unprivileged_policy(self):
        for name, data in (('theme.css', b'body { color: red; }'), ('theme.png', b'\x7fELFbad')):
            path = self.root/name; path.write_bytes(data)
            if name.endswith('.png'):
                with self.assertRaises(ValueError): self.helper.validate_theme_file(path, name, 'themes')
                with self.assertRaises(ValueError): asset_validation.validate_theme_file(path, name, 'gtk')
            else:
                self.helper.validate_theme_file(path, name, 'themes')
                asset_validation.validate_theme_file(path, name, 'gtk')


class LiveMalwareTests(unittest.TestCase):
    def test_eicar_when_clamav_and_signatures_are_available(self):
        scanner = shutil.which('clamscan')
        if not scanner:
            self.skipTest('ClamAV is not installed')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clean = root/'clean.txt'; clean.write_text('clean fixture')
            probe = subprocess.run([scanner, '--no-summary', str(clean)], capture_output=True, timeout=180)
            if probe.returncode:
                self.skipTest('ClamAV definitions are unavailable')
            # Standard harmless antivirus test fixture, constructed only inside this temporary directory.
            fixture = root/'eicar.txt'
            fixture.write_bytes(b'X5O!P%@AP[4' + bytes([92]) + b'PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*')
            with self.assertRaisesRegex(RuntimeError, 'flagged'):
                security.scan_download(fixture)


class AdditionalBoundaryTests(unittest.TestCase):
    def test_cached_payload_modification_is_detected(self):
        from orbits.downloads import DownloadManager
        from email.message import Message
        class Response(io.BytesIO):
            status = 200
            headers = Message()
            def geturl(self): return 'https://example.org/payload'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = DownloadManager(root/'cache', opener=lambda *args, **kwargs: Response(b'original'), offline=lambda: True)
            try:
                manager.wait(manager.submit('https://example.org/payload', root/'out'))
                next((root/'cache').glob('*.complete')).write_bytes(b'tampered')
                with self.assertRaisesRegex(RuntimeError, 'changed'):
                    manager.wait(manager.submit('https://example.org/payload', root/'out'))
                self.assertEqual((root/'out').read_bytes(), b'original')
            finally:
                manager.close()

    def test_font_validation_accepts_real_fixture_and_rejects_out_of_bounds_table(self):
        from tests.test_fonts_looks import make_font
        import struct
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'font.ttf'
            make_font(path)
            asset_validation.validate_font(path)
            data = bytearray(path.read_bytes())
            struct.pack_into('>I', data, 20, len(data)+100)
            with self.assertRaisesRegex(ValueError, 'bounds'):
                asset_validation.validate_font_bytes(data)

    def test_svg_active_and_encoded_references_are_rejected(self):
        for text in ('<svg xmlns="http://www.w3.org/2000/svg"><script>evil()</script></svg>', '<svg xmlns="http://www.w3.org/2000/svg"><image href="%2e%2e/secret"/></svg>', '<svg xmlns="http://www.w3.org/2000/svg" xml:base="file:///etc/"/>'):
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary)/'icon.svg'; path.write_text(text)
                with self.assertRaises(ValueError):
                    asset_validation.validate_theme_file(path, 'icon.svg', 'icons')

    def test_publish_failure_restores_old_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original, staged = root/'installed', root/'ready'
            original.mkdir(); staged.mkdir()
            (original/'file').write_bytes(b'previous')
            (staged/'file').write_bytes(b'new')
            rename = Path.rename
            def fail(source, destination):
                if source == staged: raise OSError('simulated failure')
                return rename(source, destination)
            with patch.object(Path, 'rename', fail), self.assertRaises(OSError):
                integrity.publish_tree(staged, original)
            self.assertEqual((original/'file').read_bytes(), b'previous')


class PrivilegedPolicyConsistencyTests(unittest.TestCase):
    setUp = HelperStagingTests.setUp

    def test_embedded_validators_match_application_source(self):
        import inspect
        for name in ('validate_font_bytes', 'validate_font', 'validate_theme_file', 'validate_theme_tree'):
            self.assertEqual(inspect.getsource(getattr(asset_validation, name)), inspect.getsource(getattr(self.helper, name)))

    def test_source_mutation_during_privileged_snapshot_is_rejected(self):
        original_read = os.read
        changed = False
        def changing_read(descriptor, size):
            nonlocal changed
            data = original_read(descriptor, size)
            if data and not changed:
                changed = True
                (self.source/'theme.css').write_text('changed during copy')
            return data
        with patch.object(self.helper.os, 'read', side_effect=changing_read), self.assertRaisesRegex(ValueError, 'changed'):
            self.helper._snapshot_user_tree(str(self.source), str(self.root/'snapshot'))

    def test_successful_privileged_theme_install_records_verified_baseline(self):
        destination = self.root/'themes'; destination.mkdir()
        with patch.dict(self.helper.THEME_BASES, {'themes': str(destination)}), patch.object(self.helper.os, 'chown'), patch.object(self.helper, 'relabel'):
            self.helper.cmd_theme_install('themes', str(self.source), 'Example')
        self.assertTrue(integrity.verify_tree(destination/'Example'))
        self.assertEqual((destination/'Example/theme.css').stat().st_mode & 0o777, 0o644)


# Unit suites execute trusted worker code in child processes; live isolation has
# a separate required integration profile and never changes production behavior.
def setUpModule():
    from tests.parser_fixtures import run_worker
    global _parser_fixture
    _parser_fixture = patch('orbits.parser_sandbox.run', side_effect=run_worker)
    _parser_fixture.start()


def tearDownModule():
    _parser_fixture.stop()
