"""Explicitly pinned Minisign publisher keys; downloaded keys are never trusted."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit, urlunsplit


def keys(directory):
    path = Path(directory)/'trusted-publishers.json'
    if not path.exists():
        return []
    if path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError('Invalid publisher trust store')
    records = json.loads(path.read_text())
    if not isinstance(records, list) or len(records) > 100:
        raise ValueError('Invalid publisher trust store')
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('Invalid publisher key')
        validate_key(record.get('host'), record.get('key'), record.get('name'))
    return records


def validate_key(host, key, name):
    if not isinstance(host, str) or not host or any(c in host for c in '/:@#?%') or urlsplit('https://' + host).hostname != host.lower():
        raise ValueError('Enter a publisher hostname without a URL, path or port')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
        raise ValueError('Enter a publisher name (maximum 100 characters)')
    try:
        raw = base64.b64decode(key, validate=True)
    except Exception as error:
        raise ValueError('Invalid Minisign public key') from error
    if len(raw) != 42 or raw[:2] != b'Ed':
        raise ValueError('Invalid Minisign public key')


def save_keys(directory, records):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    for record in records:
        validate_key(record['host'], record['key'], record['name'])
    if len(records) > 100:
        raise ValueError('Too many publisher keys')
    with tempfile.NamedTemporaryFile(mode='w', dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(records, stream)
    try:
        temporary.chmod(0o600)
        os.replace(temporary, directory/'trusted-publishers.json')
    finally:
        temporary.unlink(missing_ok=True)


def verify_signature(payload, signature, records):
    executable = shutil.which('minisign')
    if not executable:
        raise RuntimeError('Publisher verification requires minisign')
    if not 0 < len(signature) <= 4096:
        raise ValueError('Invalid publisher signature size')
    with tempfile.TemporaryDirectory(prefix='orbits-signature-') as temporary:
        path = Path(temporary)/'signature.minisig'; path.write_bytes(signature)
        for record in records:
            try:
                result = subprocess.run([executable, '-V', '-H', '-q', '-m', str(payload), '-x', str(path), '-P', record['key']],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise RuntimeError('Publisher verification failed or timed out') from error
            if result.returncode == 0:
                return 'Publisher verified: ' + record['name']
    raise ValueError('Publisher signature does not match a trusted key')


def verify_download(payload, url, directory, cache, *, required=False, offline=False):
    from orbits.safe_network import open_remote
    parsed = urlsplit(url)
    records = keys(directory)
    selected = [record for record in records if record['host'].lower() == (parsed.hostname or '').lower()]
    if not selected:
        if required:
            raise ValueError('No trusted publisher key is configured for this source')
        return 'Publisher unverified'
    if parsed.scheme != 'https':
        raise ValueError('Publisher downloads require HTTPS')
    with Path(payload).open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    cache = Path(cache); cache.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.sha256((url + '\0' + digest).encode()).hexdigest()
    stored = cache/(cache_key + '.minisig')
    if offline:
        if not stored.is_file() or stored.is_symlink() or stored.stat().st_size > 4096:
            raise ValueError('Publisher signature is not cached for offline verification')
        signature = stored.read_bytes()
    else:
        signature_url = urlunsplit(parsed._replace(path=parsed.path + '.minisig', fragment=''))
        with open_remote(signature_url, timeout=20) as response:
            signature = response.read(4097)
    status = verify_signature(payload, signature, selected)
    if not offline:
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as stream:
            temporary = Path(stream.name); stream.write(signature)
        try:
            os.replace(temporary, stored)
        finally:
            temporary.unlink(missing_ok=True)
    return status


def configured_verify(payload, url):
    from orbits.config import CONFIG_DIR, CACHE_DIR, settings
    from orbits.offline import enabled
    return verify_download(payload, url, CONFIG_DIR, CACHE_DIR/'publisher-signatures',
                           required=bool(settings.get('require_publisher')), offline=enabled())


def verify_local(payload, original):
    from orbits.config import settings
    if not settings.get('require_publisher'):
        return 'Publisher unverified'
    from orbits.config import CONFIG_DIR
    records = keys(CONFIG_DIR)
    if not records:
        raise ValueError('No trusted publisher keys are configured')
    sidecar = Path(str(original) + '.minisig')
    descriptor = os.open(sidecar, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    import stat
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Invalid local publisher signature')
        signature = stream.read(4097)
    return verify_signature(payload, signature, records)
