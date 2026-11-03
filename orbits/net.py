"""HTTP helpers and a background worker pool that reports back on the GTK main loop."""
import hashlib
import json
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from gi.repository import Gdk, GLib

from orbits import VERSION
from orbits.config import THUMB_CACHE

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


def _open(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


def get_json(url, params=None):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    with _open(url) as r:
        return json.loads(r.read().decode("utf-8"))


def get_bytes(url, timeout=60):
    with _open(url, timeout) as r:
        return r.read()


def download(url, dest, progress=None, chunk=1 << 16):
    """Stream url into dest path. progress(fraction) is called from the worker thread."""
    with _open(url, 120) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            buf = r.read(chunk)
            if not buf:
                break
            f.write(buf)
            got += len(buf)
            if progress and total:
                progress(got / total)
        return r.headers.get_content_type(), r.geturl()


def cached_texture(url):
    """Fetch an image (with disk cache) and decode it into a Gdk.Texture. Worker-thread safe."""
    path = THUMB_CACHE / hashlib.sha1(url.encode()).hexdigest()
    if path.exists():
        data = path.read_bytes()
    else:
        data = get_bytes(url)
        path.write_bytes(data)
    return Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
