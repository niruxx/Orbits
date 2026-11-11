"""Local checks for untrusted downloads. No remote uploads or shell execution."""
from pathlib import Path
import shutil
import subprocess
import warnings
import threading
import time

MAX_DOWNLOAD = 512 * 1024 * 1024


_definitions_lock = threading.Lock()


def update_definitions(directory, *, offline=False, force=True):
    """Update a private database as the current user, never the system service."""
    if offline:
        raise RuntimeError("Virus definitions cannot be updated in Offline Mode")
    directory = Path(directory)
    with _definitions_lock:
        stamp = directory / "last-update"
        try:
            recent = 0 <= time.time() - float(stamp.read_text()) < 86400
        except (OSError, ValueError):
            recent = False
        if not force and recent and has_definitions(directory):
            return "Virus definitions checked within the last day"
        updater = shutil.which("freshclam")
        if not updater:
            raise RuntimeError("Definition updater unavailable: install freshclam (ClamAV)")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        config = directory / "freshclam.conf"
        config.write_text("DatabaseMirror database.clamav.net\n")
        config.chmod(0o600)
        try:
            result = subprocess.run([
                updater, "--config-file=" + str(config.resolve()),
                "--datadir=" + str(directory.resolve()), "--stdout"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=300, check=False)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("Virus definition update timed out; try again later") from error
        except OSError as error:
            raise RuntimeError("Virus definition updater could not start") from error
        if result.returncode != 0 or not has_definitions(directory):
            raise RuntimeError("Virus definition update failed; check your connection and freshclam installation, or retry later if rate limited")
        stamp.write_text(str(time.time()))
        return "Virus definitions updated"


def has_definitions(directory):
    directory = Path(directory)
    return any((directory / ("main" + suffix)).is_file() for suffix in (".cvd", ".cld")) and any(
        (directory / ("daily" + suffix)).is_file() for suffix in (".cvd", ".cld"))


def scan_with_settings(path, settings, directory, *, offline=False):
    if not settings.get("malware_scan"):
        return "Not scanned"
    if settings.get("malware_auto_update") and not offline:
        update_definitions(directory, force=False)
    with _definitions_lock:
        database = directory if has_definitions(directory) else None
        return scan_download(path, database=database)


def scan_download(path, database=None, *, recursive=False):
    """Fail closed when scanning was requested; never interpret an error as clean."""
    scanner = shutil.which("clamscan")
    if not scanner:
        raise RuntimeError("Malware scan unavailable: install ClamAV and update its signatures with freshclam")
    try:
        result = subprocess.run([
            scanner, *(["--recursive", "--follow-dir-symlinks=0", "--follow-file-symlinks=0"] if recursive else []), *(["--database=" + str(Path(database).resolve())] if database else []), "--no-summary", "--infected", "--alert-encrypted=yes",
            "--alert-exceeds-max=yes", "--max-filesize=512M", "--max-scansize=1024M",
            "--max-files=10000", "--max-recursion=32", "--", str(Path(path).resolve())],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180,
            check=False)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Malware scan timed out; download was not installed") from error
    except OSError as error:
        raise RuntimeError("Malware scanner could not start; download was not installed") from error
    if result.returncode == 1:
        raise RuntimeError("Malware scanner flagged this download (malware, encrypted content or scan limits); installation blocked")
    if result.returncode != 0:
        raise RuntimeError("Malware scan failed; check ClamAV signatures and configuration. Download was not installed")
    return "No known malware detected"


def validate_wallpaper(path):
    """Fully decode raster content before it can be installed as a wallpaper."""
    path = Path(path)
    if path.suffix.lower() == ".svg":
        raise RuntimeError("Downloaded SVG wallpapers are not supported by image validation; use a raster image")
    try:
        from PIL import Image
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.format not in {"JPEG", "PNG", "WEBP", "AVIF", "JPEGXL", "HEIF", "BMP", "TIFF"}:
                    raise ValueError("Unsupported image format")
                if image.width * image.height > 40_000_000:
                    raise ValueError("Image exceeds 40 megapixels")
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Downloaded wallpapers must be single-frame images")
                image.verify()
            with Image.open(path) as image:
                image.load()
    except Exception as error:
        raise RuntimeError("Wallpaper image validation failed: " + str(error)) from error


def inspect_archive(path):
    from orbits.safe_archive import is_archive, extract
    import tempfile
    if is_archive(path):
        with tempfile.TemporaryDirectory(prefix='orbits-archive-check-') as temporary:
            extract(path, temporary)


from contextlib import contextmanager
import os
import stat
import tempfile


@contextmanager
def checked_local_file(source, *, scan=True, archive=True, publisher=False):
    """Check a bounded private snapshot, then consume exactly the bytes checked."""
    source = Path(source)
    with tempfile.TemporaryDirectory(prefix='orbits-checked-') as temporary:
        target = Path(temporary)/source.name
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as incoming, target.open('wb') as outgoing:
            if not stat.S_ISREG(os.fstat(incoming.fileno()).st_mode):
                raise RuntimeError('Select a regular asset file')
            total = 0
            while chunk := incoming.read(65536):
                total += len(chunk)
                if total > MAX_DOWNLOAD:
                    raise RuntimeError('Asset exceeds the 512 MiB limit')
                outgoing.write(chunk)
        if publisher:
            from orbits.publishers import verify_local
            verify_local(target, source)
        if scan:
            configured_scan(target)
        if archive:
            inspect_archive(target)
        yield target


def configured_scan(path):
    from orbits.config import settings, DATA_DIR
    from orbits.offline import enabled
    return scan_with_settings(path, settings, DATA_DIR/'clamav', offline=enabled())


def rescan_assets(roots):
    """Explicit read-only scan of existing user appearance directories."""
    from orbits.config import settings, DATA_DIR
    from orbits.offline import enabled
    directory = DATA_DIR/'clamav'
    if settings.get('malware_auto_update') and not enabled():
        update_definitions(directory, force=False)
    checked = set()
    with _definitions_lock:
        database = directory if has_definitions(directory) else None
        for value in roots:
            root = Path(value)
            if root.is_symlink() or not root.exists():
                continue
            root = root.resolve()
            if any(root.is_relative_to(parent) for parent in checked):
                continue
            scan_download(root, database=database, recursive=True)
            checked.add(root)
    return 'No known malware detected in user appearance assets' if checked else 'No user appearance assets found'


def definition_status(directory, settings):
    """Describe check time separately from database age; never call an unknown DB fresh."""
    from datetime import datetime
    directory = Path(directory)
    parts = ['Scanning enabled' if settings.get('malware_scan') else 'Scanning disabled']
    parts.append('clamscan available' if shutil.which('clamscan') else 'clamscan missing')
    parts.append('freshclam available' if shutil.which('freshclam') else 'freshclam missing')
    try:
        checked = float((directory/'last-update').read_text())
        parts.append('Last successful definition check: ' + datetime.fromtimestamp(checked).astimezone().strftime('%Y-%m-%d %H:%M %Z'))
    except (OSError, ValueError, OverflowError):
        parts.append('No successful Orbits definition update recorded')
    if has_definitions(directory):
        daily = next(path for path in (directory/'daily.cvd', directory/'daily.cld') if path.is_file())
        age = max(0, int((time.time() - daily.stat().st_mtime) // 86400))
        parts.append(f'Orbits daily database file last changed {age} days ago' + (' (update recommended)' if age >= 7 else ''))
    else:
        parts.append('Using system-managed definitions; database age unknown')
        scanner = shutil.which('clamscan')
        if scanner:
            try:
                result = subprocess.run([scanner, '--version'], capture_output=True, text=True, timeout=5, check=False)
                if result.returncode == 0:
                    parts.append('System scanner/database: ' + result.stdout.strip()[:300])
            except (OSError, subprocess.TimeoutExpired):
                parts.append('System database status unavailable')
    return ' · '.join(parts)
