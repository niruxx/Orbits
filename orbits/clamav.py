"""Private unprivileged ClamAV process; stream each asset over a private Unix socket."""
import atexit
import os
from pathlib import Path
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time

MAX_BYTES = 512 * 1024**2


class DaemonUnavailable(RuntimeError):
    """The regular clamscan path must be used instead."""


def database_identity(directory):
    directory = Path(directory).resolve()
    records = []
    for path in sorted(directory.iterdir()):
        info = path.lstat()
        if stat.S_ISREG(info.st_mode):
            records.append((path.name, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
        if len(records) > 10000:
            raise DaemonUnavailable('Virus database contains too many files')
    if not any(name.endswith(('.cvd', '.cld', '.cud', '.ndb', '.ldb', '.hdb', '.hsb')) for name, *_ in records):
        raise DaemonUnavailable('No readable virus database found')
    return str(directory), tuple(records)


class Scanner:
    def __init__(self, executable, database):
        if any(character in str(database) for character in ('\n', '\r', '\0')):
            raise DaemonUnavailable('Invalid virus database directory')
        self.directory = tempfile.TemporaryDirectory(prefix='orbits-clamd-')
        root = Path(self.directory.name)
        self.socket = root/'scanner.sock'
        configuration = root/'clamd.conf'
        # No TCP listener, system service, log file, or privilege change is configured.
        configuration.write_text('\n'.join([
            f'DatabaseDirectory {database}', f'LocalSocket {self.socket}', 'LocalSocketMode 600',
            'Foreground yes', 'MaxThreads 2', 'MaxQueue 4', 'StreamMaxLength 512M',
            'MaxFileSize 512M', 'MaxScanSize 1024M', 'MaxFiles 10000', 'MaxRecursion 32',
            'AlertEncrypted yes', 'AlertExceedsMax yes', 'ReadTimeout 180',
            'CommandReadTimeout 180', 'SendBufTimeout 180', 'SelfCheck 0',
        ])+'\n')
        configuration.chmod(0o600)
        self.process = None
        try:
            launcher = Path(__file__).with_name('clamav_worker.py')
            self.process = subprocess.Popen([sys.executable, '-I', str(launcher), str(os.getpid()),
                                             executable, '--foreground', '--config-file='+str(configuration)],
                                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, start_new_session=True)
            deadline = time.monotonic()+30
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise DaemonUnavailable('Private ClamAV process could not start')
                try:
                    with self.connect(timeout=.2) as connection:
                        connection.sendall(b'zPING\0')
                        if self.response(connection) == b'PONG':
                            return
                except OSError:
                    pass
                time.sleep(.05)
            raise DaemonUnavailable('Private ClamAV process startup timed out')
        except Exception:
            self.close()
            raise

    def connect(self, timeout=180):
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(timeout)
        try:
            connection.connect(str(self.socket))
            return connection
        except Exception:
            connection.close()
            raise

    @staticmethod
    def response(connection):
        output = bytearray()
        while len(output) <= 4096:
            chunk = connection.recv(4097-len(output))
            if not chunk:
                break
            output.extend(chunk)
            if b'\0' in output:
                return bytes(output).split(b'\0', 1)[0]
        raise RuntimeError('Malware scanner returned an incomplete or oversized response; installation blocked')

    def scan(self, path):
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
                raise RuntimeError('Malware scan requires a bounded regular file')
            try:
                with self.connect() as connection:
                    connection.sendall(b'zINSTREAM\0')
                    total = 0
                    while chunk := source.read(65536):
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise RuntimeError('Asset exceeds the malware scan size limit')
                        connection.sendall(struct.pack('!I', len(chunk))+chunk)
                    connection.sendall(bytes(4))
                    result = self.response(connection)
            except OSError as error:
                raise RuntimeError('Malware scan failed or timed out; installation blocked') from error
            after = os.fstat(source.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise RuntimeError('Asset changed during malware scanning; installation blocked')
            if result.endswith(b' FOUND'):
                raise RuntimeError('Malware scanner flagged this download (malware, encrypted content or scan limits); installation blocked')
            if result != b'stream: OK':
                raise RuntimeError('Malware scanner rejected this asset or returned an error; installation blocked')

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.directory.cleanup()


_lock = threading.Lock()
_scanner = None
_identity = None
_failed_identity = None


def scan(path, database):
    """Keep definitions warm; restart before scanning when database files change."""
    global _scanner, _identity, _failed_identity
    executable = shutil.which('clamd')
    if not executable:
        raise DaemonUnavailable('clamd is unavailable')
    with _lock:
        try:
            identity = database_identity(database)
        except OSError as error:
            raise DaemonUnavailable('Virus definitions are unavailable') from error
        if identity == _failed_identity:
            raise DaemonUnavailable('Private scanner is unavailable for these definitions')
        if identity != _identity or _scanner is None or _scanner.process.poll() is not None:
            close()
            try:
                _scanner = Scanner(executable, identity[0])
                if database_identity(database) != identity:
                    close()
                    raise DaemonUnavailable('Virus definitions changed during scanner startup')
            except (DaemonUnavailable, OSError) as error:
                _failed_identity = identity
                raise DaemonUnavailable(str(error)) from error
            _identity = identity
        _scanner.scan(path)


def close():
    global _scanner, _identity
    if _scanner is not None:
        _scanner.close()
    _scanner, _identity = None, None


atexit.register(close)
