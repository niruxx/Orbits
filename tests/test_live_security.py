"""Required live profile: ORBITS_REQUIRE_LIVE_SECURITY=1 disables dependency skips."""
import base64
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
import zipfile

REQUIRED = os.environ.get('ORBITS_REQUIRE_LIVE_SECURITY') == '1'


def unavailable(test, reason):
    if REQUIRED:
        test.fail(reason)
    test.skipTest(reason)


class LiveParserTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        if not shutil.which('bwrap'):
            unavailable(self, 'bubblewrap is unavailable')
        result = subprocess.run(['bwrap', '--unshare-all', '--ro-bind', '/', '/', '--', '/usr/bin/true'], capture_output=True)
        if result.returncode:
            unavailable(self, 'host does not permit bubblewrap namespaces')

    def test_archive_and_font_parsers_use_real_sandboxes(self):
        from orbits.safe_archive import extract, is_archive
        from orbits.font_safety import inspect_font, render_font
        from tests.test_fonts_looks import make_font
        archive = self.root/'assets.zip'
        with zipfile.ZipFile(archive, 'w') as stream:
            stream.writestr('nested/file', b'content')
        self.assertTrue(is_archive(archive))
        extract(archive, self.root/'extracted')
        self.assertEqual((self.root/'extracted/nested/file').read_bytes(), b'content')
        font = self.root/'font.ttf'; make_font(font)
        with patch('orbits.security.configured_scan', return_value='Not scanned'):
            self.assertEqual(inspect_font(font)['family'], 'Orbits Fixture')
            self.assertTrue(render_font(font, 'AB', 24).startswith(b'\x89PNG'))

    def test_worker_cannot_read_private_files_write_input_or_connect(self):
        from orbits.parser_sandbox import run
        source = self.root/'input'; source.write_text('input')
        secret = self.root/'secret'; secret.write_text('private')
        worker = self.root/'probe.py'
        worker.write_text('''import json, socket
from pathlib import Path
results = []
try:
    Path(''' + repr(str(secret)) + ''').read_text()
    results.append(False)
except OSError:
    results.append(True)
try:
    Path('/input').write_text('changed')
    results.append(False)
except OSError:
    results.append(True)
try:
    connection = socket.create_connection(('1.1.1.1', 443), timeout=.2)
    connection.close()
    results.append(False)
except OSError:
    results.append(True)
Path('/output/probe.json').write_text(json.dumps(results))
''')
        output = self.root/'output'; output.mkdir()
        run(source, output, worker, [])
        self.assertEqual(json.loads((output/'probe.json').read_text()), [True, True, True])
        self.assertEqual(source.read_text(), 'input')


class LiveSignatureTests(unittest.TestCase):
    def test_minisign_accepts_correct_signature_and_rejects_tampering(self):
        from orbits.publishers import verify_signature
        executable = shutil.which('minisign')
        if not executable:
            unavailable(self, 'minisign is unavailable')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public, private, payload = root/'publisher.pub', root/'publisher.key', root/'payload'
            subprocess.run([executable, '-G', '-W', '-p', str(public), '-s', str(private)], capture_output=True, check=True)
            payload.write_bytes(b'signed asset')
            subprocess.run([executable, '-S', '-m', str(payload), '-s', str(private)], capture_output=True, check=True)
            key = public.read_text().splitlines()[-1]
            signature = Path(str(payload)+'.minisig').read_bytes()
            self.assertIn('verified', verify_signature(payload, signature, [{'key': key, 'name': 'Fixture'}]))
            payload.write_bytes(b'tampered asset')
            with self.assertRaises(ValueError):
                verify_signature(payload, signature, [{'key': key, 'name': 'Fixture'}])


class LiveMalwareTests(unittest.TestCase):
    def test_clamav_detects_eicar(self):
        from orbits.security import scan_download
        scanner = shutil.which('clamscan')
        if not scanner:
            unavailable(self, 'ClamAV is unavailable')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'fixture'
            path.write_text('clean')
            try:
                scan_download(path)
            except RuntimeError as error:
                unavailable(self, 'ClamAV definitions are unavailable: ' + str(error))
            path.write_bytes(b'X5O!P%@AP[4' + bytes([92]) + b'PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*')
            with self.assertRaisesRegex(RuntimeError, 'flagged'):
                scan_download(path)


class RootStagingTests(unittest.TestCase):
    def test_actual_root_permissions_in_temporary_installation(self):
        if os.geteuid() != 0:
            unavailable(self, 'root staging profile requires a disposable privileged runner')
        from tests.test_fonts_looks import make_font
        repository = types.SimpleNamespace(Gio=Mock(), GLib=Mock())
        spec = importlib.util.spec_from_file_location('live_root_helper', Path(__file__).resolve().parents[1]/'orbits/gdm/helper.py')
        helper = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'gi.repository': repository}):
            spec.loader.exec_module(helper)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root/'source'; source.mkdir()
            font = source/'fixture.ttf'; make_font(font)
            uid = int(os.environ.get('SUDO_UID', '0'))
            source.chmod(0o755); os.chown(source, uid, -1); os.chown(font, uid, -1)
            helper.SYSTEM_FONTS = str(root/'system-fonts')
            fontconfig = root/'fonts.conf'
            fontconfig.write_text(f'<fontconfig><dir>{root}/system-fonts</dir><cachedir>{root}/font-cache</cachedir></fontconfig>')
            with patch.dict(os.environ, {'PKEXEC_UID': str(uid), 'FONTCONFIG_FILE': str(fontconfig), 'XDG_CACHE_HOME': str(root/'cache')}):
                helper.cmd_font_install(str(source), 'fixture')
            installed = root/'system-fonts/fixture/fixture.ttf'
            self.assertEqual(installed.stat().st_uid, 0)
            self.assertEqual(installed.stat().st_mode & 0o777, 0o644)
            self.assertTrue(helper.verify_tree(installed.parent))
