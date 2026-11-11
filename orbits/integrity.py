"""SHA-256 change detection for managed assets; not publisher authentication."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile

MANIFEST = '.orbits-integrity.json'


def _hash_file(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 512 * 1024**2:
            raise ValueError('Invalid asset file for integrity checking')
        digest, count = hashlib.sha256(), 0
        while chunk := stream.read(65536):
            count += len(chunk)
            if count > 512 * 1024**2:
                raise ValueError('Asset grew beyond its size limit')
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('Asset changed during integrity checking')
        return digest.hexdigest(), count


def _tree_hashes(root):
    root = Path(root)
    if root.is_symlink():
        raise ValueError('Asset directory must not be a link')
    result, total = {}, 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            if (Path(directory)/name).is_symlink():
                raise ValueError('Asset directory contains a link')
        for name in files:
            path = Path(directory)/name
            relative = str(path.relative_to(root))
            if relative in (MANIFEST, '.uuid'):
                continue
            value, size = _hash_file(path)
            total += size
            if len(result) >= 10000 or total > 1024**3:
                raise ValueError('Integrity check exceeds its size limits')
            result[relative] = value
    return result


def record_tree(root):
    root = Path(root)
    values = _tree_hashes(root)
    with tempfile.NamedTemporaryFile(mode='w', dir=root, prefix='.integrity-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump({'version': 1, 'sha256': values}, stream, sort_keys=True)
            stream.flush()
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.chmod(temporary, 0o644)
        os.replace(temporary, root/MANIFEST)
    finally:
        temporary.unlink(missing_ok=True)


def verify_tree(root):
    root = Path(root)
    manifest = root/MANIFEST
    if not manifest.exists() and not manifest.is_symlink():
        return None  # Legacy assets have no trusted baseline; do not silently create one.
    descriptor = os.open(manifest, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'r') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Invalid integrity manifest')
        text = stream.read(2 * 1024**2 + 1)
        if len(text) > 2 * 1024**2:
            raise ValueError('Integrity manifest exceeds its limit')
        data = json.loads(text)
    expected = data.get('sha256') if isinstance(data, dict) and data.get('version') == 1 else None
    if not isinstance(expected, dict) or len(expected) > 10000:
        raise ValueError('Invalid integrity manifest')
    for name, digest in expected.items():
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or not path.parts or '\\' in name or not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Invalid integrity manifest entry')
    actual = _tree_hashes(root)
    changed = sorted(name for name in set(actual) | set(expected) if actual.get(name) != expected.get(name))
    if changed:
        raise ValueError('Installed asset changed since validation: ' + ', '.join(changed[:5]))
    return True


def audit_roots(roots):
    checked, visited = 0, 0
    for root in sorted(set(map(Path, roots))):
        if root.is_symlink() or not root.exists():
            continue
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(directory)/name).is_symlink()]
            visited += 1
            if visited > 20000:
                raise ValueError('Integrity search exceeds its directory limit')
            if MANIFEST in files:
                verify_tree(directory)
                checked += 1
                dirs.clear()
    return f'Integrity matched for {checked} managed asset folders; older assets without a baseline were not verified'


def publish_tree(staged, destination):
    """Replace a managed directory with rollback if the final rename fails."""
    staged, destination = Path(staged), Path(destination)
    if destination.is_symlink():
        raise ValueError('Managed asset destination must not be a link')
    backup = staged.parent/'previous'
    previous = destination.exists()
    if previous:
        destination.rename(backup)
    try:
        staged.rename(destination)
    except Exception:
        if previous:
            backup.rename(destination)
        raise
