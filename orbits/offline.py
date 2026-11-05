"""Atomic JSON response cache and explicit offline state."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import urllib.error

_status = threading.local()


class OfflineError(RuntimeError):
    pass


def enabled():
    from orbits.config import settings
    return bool(settings.get('offline', False))


def with_status(function, *args):
    _status.cached = False
    return function(*args), bool(getattr(_status, 'cached', False))


def cached_json(directory, key, fetch, offline=False, ttl=900):
    path = Path(directory) / (hashlib.sha256(key.encode()).hexdigest()+'.json')
    cached = None
    try:
        cached = json.loads(path.read_text())
        if offline or time.time()-cached['time'] < ttl:
            _status.cached = bool(offline) or bool(getattr(_status, "cached", False))
            return cached['data']
    except (OSError, ValueError, KeyError, TypeError):
        cached = None
    if offline:
        raise OfflineError('You are offline. This catalogue has not been cached yet.')
    try:
        data = fetch()
    except (OSError, urllib.error.URLError, TimeoutError) as error:
        if isinstance(error, urllib.error.HTTPError) and error.code < 500:
            raise
        if cached is None:
            raise OfflineError('You are offline. Connect to load this catalogue for the first time.') from error
        _status.cached = True
        return cached['data']
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'time':time.time(), 'data':data}, stream)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return data
