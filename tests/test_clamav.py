"""Private scanner framing, fail-closed replies, restarts and real engine fixture."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import struct
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from orbits import clamav, security


class Connection:
    def __init__(self, response=b'stream: OK\0'):
        self.reply = response
        self.sent = []
    def __enter__(self):return self
    def __exit__(self, *_):pass
    def sendall(self, data):self.sent.append(data)
    def recv(self, limit):
        chunk, self.reply = self.reply[:limit], self.reply[limit:]
        return chunk


class PrivateScannerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='orbits-test-clamd-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root/'input'
        self.source.write_bytes(b'asset bytes')
        self.scanner = object.__new__(clamav.Scanner)

    def test_scan_streams_bounded_bytes_and_requires_exact_success(self):
        connection = Connection()
        self.scanner.connect = Mock(return_value=connection)
        self.scanner.scan(self.source)
        self.assertEqual(connection.sent, [b'zINSTREAM\0',struct.pack('!I',11)+b'asset bytes',bytes(4)])
        for reply in (b'stream: Fixture FOUND\0', b'stream: ERROR\0', b'other: OK\0', b'stream: OK', b'x'*4097):
            self.scanner.connect.return_value = Connection(reply)
            with self.subTest(reply=reply[:30]), self.assertRaises(RuntimeError):
                self.scanner.scan(self.source)

    def test_links_and_oversized_assets_are_rejected_before_scanning(self):
        self.scanner.connect = Mock()
        link = self.root/'link'
        link.symlink_to(self.source)
        with self.assertRaises(OSError):self.scanner.scan(link)
        with patch.object(clamav,'MAX_BYTES',3):
            with self.assertRaises(RuntimeError):self.scanner.scan(self.source)
        self.scanner.connect.assert_not_called()

    def test_scanner_errors_never_fall_back_to_a_clean_verdict(self):
        with patch('orbits.clamav.scan',side_effect=RuntimeError('scanner flagged asset')), \
             patch('orbits.security.subprocess.run') as fallback:
            with self.assertRaisesRegex(RuntimeError,'flagged'):
                security.scan_download(self.source, database=self.root)
            fallback.assert_not_called()

    def test_startup_unavailability_uses_regular_fail_closed_scanner(self):
        with patch('orbits.clamav.scan',side_effect=clamav.DaemonUnavailable('unavailable')), \
             patch('orbits.security.shutil.which',return_value='clamscan'), \
             patch('orbits.security.subprocess.run',return_value=types.SimpleNamespace(returncode=1)) as fallback:
            with self.assertRaisesRegex(RuntimeError,'flagged'):
                security.scan_download(self.source,database=self.root)
            self.assertIn('--alert-exceeds-max=yes',fallback.call_args.args[0])

    def test_database_file_changes_invalidate_identity(self):
        database=self.root/'fixture.ndb'
        database.write_text('first')
        first=clamav.database_identity(self.root)
        database.write_text('second')
        self.assertNotEqual(clamav.database_identity(self.root),first)

    def test_startup_configuration_has_no_network_listener_and_retains_limits(self):
        captured=[]
        def launch(arguments,**kwargs):
            filename=arguments[-1].split('=',1)[1]
            captured.append(Path(filename).read_text())
            self.assertEqual(Path(filename).stat().st_mode & 0o777,0o600)
            return Mock(poll=Mock(return_value=1))
        with patch('orbits.clamav.subprocess.Popen',side_effect=launch):
            with self.assertRaises(clamav.DaemonUnavailable):clamav.Scanner('clamd',self.root)
        config=captured[0]
        self.assertNotIn('TCPSocket',config)
        self.assertIn('LocalSocketMode 600',config)
        self.assertIn('AlertEncrypted yes',config)
        self.assertIn('AlertExceedsMax yes',config)
        self.assertIn('MaxFileSize 512M',config)
        self.assertIn('MaxScanSize 1024M',config)


    @unittest.skipUnless(sys.platform=='linux','Linux parent-death signal required')
    def test_private_worker_exits_when_its_parent_exits(self):
        marker=self.root/'child.pid'
        worker=Path(clamav.__file__).with_name('clamav_worker.py')
        child_code='import os,time; from pathlib import Path; Path('+repr(str(marker))+').write_text(str(os.getpid())); time.sleep(30)'
        parent_code = (
            'import os,subprocess,sys,time; from pathlib import Path; '
            'subprocess.Popen([sys.executable,"-I",'+repr(str(worker))+',str(os.getpid()),sys.executable,"-I","-c",'+repr(child_code)+']); '
            'deadline=time.monotonic()+5; marker=Path('+repr(str(marker))+')\n'
            'while not marker.exists() and time.monotonic()<deadline: time.sleep(.01)\n'
            'assert marker.exists()\n')
        subprocess.run([sys.executable,'-I','-c',parent_code],check=True,timeout=8)
        child=int(marker.read_text())
        def alive():
            try:return '\nState:\tZ' not in Path(f'/proc/{child}/status').read_text()
            except FileNotFoundError:return False
        deadline=time.monotonic()+3
        while alive() and time.monotonic()<deadline:time.sleep(.01)
        if alive():
            os.kill(child,signal.SIGTERM)
            self.fail('Private scanner worker outlived its parent')


@unittest.skipUnless(shutil.which('clamd'),'clamd is required')
class LivePrivateScannerTests(unittest.TestCase):
    def test_real_engine_reuses_process_blocks_fixture_and_reloads_definitions(self):
        with tempfile.TemporaryDirectory(prefix='orbits-test-clamd-live-') as temporary:
            root=Path(temporary)
            marker=b'ORBITS-SCANNER-FIXTURE'
            database=root/'database'
            database.mkdir()
            definitions=database/'fixture.ndb'
            definitions.write_text('Orbits-Fixture:0:*:'+marker.hex()+'\n')
            source=root/'sample'
            source.write_bytes(b'clean sample')
            # Isolate the singleton from any scanner used elsewhere in the suite.
            with patch.object(clamav,'_scanner',None), patch.object(clamav,'_identity',None), \
                 patch.object(clamav,'_failed_identity',None):
                try:
                    clamav.scan(source,database)
                    first=clamav._scanner
                    clamav.scan(source,database)
                    self.assertIs(clamav._scanner,first)
                    source.write_bytes(marker)
                    with self.assertRaisesRegex(RuntimeError,'flagged'):clamav.scan(source,database)
                    definitions.write_text('Orbits-Other-Fixture:0:*:'+b'ANOTHER-FIXTURE-MARKER'.hex()+'\n')
                    source.write_bytes(b'clean again')
                    clamav.scan(source,database)
                    self.assertIsNot(clamav._scanner,first)
                    self.assertIsNotNone(first.process.poll())
                finally:clamav.close()


if __name__=='__main__':unittest.main()
