"""HTTP helpers and a background worker pool that reports back on the GTK main loop."""
import hashlib
import json
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from gi.repository import Gdk, GLib

from orbits import VERSION
from orbits.config import THUMB_CACHE
from orbits.offline import OfflineError, enabled as offline_enabled, with_status

USER_AGENT = f"Orbits/{VERSION} (GTK wallpaper manager; +https://github.com/niruxx/Orbits)"
_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="orbits")


def run_async(func, callback=None, *args):
    """Run func(*args) in a worker; call callback(result, error) on the main loop."""

    def done(fut):
        if callback is None:
            return
        err = fut.exception()
        res = None if err else fut.result()
        GLib.idle_add(lambda: (callback(res, err), False)[1])

    fut = _pool.submit(func, *args)
    fut.add_done_callback(done)
    return fut


def _open(url, timeout=30, headers=None):
    if offline_enabled():
        raise OfflineError("You are offline. This file is not cached yet.")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


def get_json(url, params=None, headers=None):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    from orbits.offline import cached_json
    from orbits.config import CACHE_DIR
    def fetch():
        with _open(url, headers=headers) as response:
            return json.loads(response.read().decode("utf-8"))
    key = json.dumps([url, headers or {}], sort_keys=True)
    return cached_json(CACHE_DIR / "catalogues", key, fetch, offline=offline_enabled())


def get_bytes(url, timeout=60):
    with _open(url, timeout) as r:
        return r.read()


def download(url, dest, progress=None, chunk=1 << 16, checksum="", algorithm="md5"):
    """Queue a transfer and wait in the caller's worker; retained partials resume on retry."""
    from orbits.downloads import get_manager
    manager = get_manager()
    return manager.wait(manager.submit(url, dest, progress, checksum, algorithm))


def cached_texture(url):
    """Fetch an image (with disk cache) and decode it into a Gdk.Texture. Worker-thread safe."""
    path = THUMB_CACHE / hashlib.sha1(url.encode()).hexdigest()
    if path.exists():
        data = path.read_bytes()
    else:
        data = get_bytes(url)
        path.write_bytes(data)
    return Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
