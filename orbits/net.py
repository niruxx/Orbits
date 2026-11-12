"""HTTP helpers and a background worker pool that reports back on the GTK main loop."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from gi.repository import Gdk, GLib

from orbits import VERSION
from orbits.config import THUMB_CACHE
from orbits.offline import OfflineError, enabled as offline_enabled, with_status

USER_AGENT = f"Orbits/{VERSION} (GTK wallpaper manager; +https://github.com/niruxx/Orbits)"
_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="orbits")
# Slow thumbnails must never occupy the workers that start installs/catalogue requests.
_preview_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="orbits-preview")


def run_async(func, callback=None, *args):
    """Run foreground work without queuing it behind thumbnail decoding."""
    return _submit(_pool, func, callback, args)


def run_preview(func, callback=None, *args):
    """Run lower-priority preview work in its own bounded worker pool."""
    return _submit(_preview_pool, func, callback, args)


def _submit(pool, func, callback, args):
    def done(fut):
        if fut.cancelled():
            return
        err = fut.exception()
        from orbits.rejections import ScanRejected
        if callback is None and not isinstance(err,ScanRejected):return
        res = None if err else fut.result()
        def deliver():
            from orbits.rejections import notify
            notify(err)
            if callback is not None:callback(res,err)
            return False
        GLib.idle_add(deliver)

    from orbits.rejections import guarded
    fut = pool.submit(guarded(func), *args)
    fut.add_done_callback(done)
    return fut


def _open(url, timeout=30, headers=None):
    if offline_enabled():
        raise OfflineError("You are offline. This file is not cached yet.")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    from orbits.safe_network import open_remote
    return open_remote(req, timeout=timeout)


def get_json(url, params=None, headers=None):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    from orbits.offline import cached_json
    from orbits.config import CACHE_DIR
    def fetch():
        with _open(url, headers=headers) as response:
            data = response.read(16 * 1024**2 + 1)
            if len(data) > 16 * 1024**2:
                raise RuntimeError('Catalogue exceeds the 16 MiB limit')
            return json.loads(data.decode('utf-8'))
    key = json.dumps([url, headers or {}], sort_keys=True)
    return cached_json(CACHE_DIR / "catalogues", key, fetch, offline=offline_enabled())


def get_bytes(url, timeout=60, limit=32 * 1024**2):
    with _open(url, timeout) as r:
        length = r.headers.get('Content-Length')
        if length and int(length) > limit:
            raise RuntimeError('Preview exceeds the 32 MiB download limit')
        data = r.read(limit + 1)
        if len(data) > limit:
            raise RuntimeError('Preview exceeds the 32 MiB download limit')
        return data


def download(url, dest, progress=None, chunk=1 << 16, checksum="", algorithm="md5", *, allow_local=False):
    """Queue a transfer and wait in the caller's worker; retained partials resume on retry."""
    from orbits.rejections import register_download,register_created_file
    new_destination=not Path(dest).exists() and not Path(dest).is_symlink()
    register_download(url,dest,checksum,algorithm)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == 'file':
        if not allow_local:
            raise RuntimeError('Remote content cannot request local files; use an explicit local import')
        if parsed.netloc not in ('', 'localhost'):
            raise RuntimeError('Remote file URLs are not supported')
        from orbits.security import checked_local_file
        source = Path(urllib.request.url2pathname(parsed.path))
        destination = Path(dest)
        with checked_local_file(source) as captured:
            from orbits.publishers import verify_local
            verify_local(captured, source)
            if checksum:
                if algorithm not in ('md5', 'sha256'):
                    raise ValueError('Unsupported checksum algorithm')
                digest = hashlib.new(algorithm)
                with captured.open('rb') as stream:
                    for block in iter(lambda: stream.read(65536), b''):
                        digest.update(block)
                if digest.hexdigest().lower() != checksum.lower():
                    raise ValueError('Local import checksum mismatch')
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
                temporary = Path(output.name)
                try:
                    with captured.open('rb') as incoming:
                        shutil.copyfileobj(incoming, output)
                except Exception:
                    temporary.unlink(missing_ok=True)
                    raise
            try:
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        if new_destination:register_created_file(dest)
        return 'application/octet-stream', url
    from orbits.downloads import get_manager
    manager = get_manager()
    result=manager.wait(manager.submit(url, dest, progress, checksum, algorithm))
    if new_destination:register_created_file(dest)
    return result


def cached_texture(url, size=640):
    """Bound preview downloads; scan and decode even cached originals in isolation."""
    from orbits.image_safety import normalize_images
    if type(size) is not int or not 1 <= size <= 4096:
        raise ValueError("Invalid preview size")
    if urllib.parse.urlparse(url).scheme != 'https':
        raise RuntimeError('Remote previews require an HTTPS URL')
    path = THUMB_CACHE / hashlib.sha1(url.encode()).hexdigest()
    if not path.exists():
        data = get_bytes(url)
        with tempfile.NamedTemporaryFile(dir=THUMB_CACHE, delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    if path.stat().st_size > 32 * 1024**2:
        raise RuntimeError('Cached preview exceeds the 32 MiB limit')
    from orbits.rejections import reject_cleanup
    with reject_cleanup([path],source=url,label=Path(urllib.parse.urlsplit(url).path).name or 'Preview'), tempfile.TemporaryDirectory(prefix='orbits-preview-') as temporary:
        image = normalize_images(path, temporary, size=size)[0]
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(image.read_bytes()))
