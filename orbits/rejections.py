"""Typed scanner alerts and deletion restricted to download-owned artifacts."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import hashlib
from pathlib import Path
import re


class ScanRejected(RuntimeError):
    def __init__(self,reason,*,source='',label=''):
        super().__init__(reason)
        self.reason=reason
        self.source=source
        self.label=label
        self.cleanup_attempted=False
        self.cleanup_errors=[]
        self.notified=False

    def __str__(self):
        text=self.reason
        if self.cleanup_errors:
            text+=' Some rejected files could not be removed: '+'; '.join(self.cleanup_errors)
        return text


def scanner_alert(finding):
    finding=''.join(character for character in str(finding) if character.isprintable())[:300]
    if 'encrypted' in finding.lower():
        reason='encrypted content could not be safely inspected'
    elif 'exceed' in finding.lower() or 'limits' in finding.lower():
        reason='scanner safety limits were exceeded'
    else:
        reason='suspected malware or unsafe content'
    return ScanRejected(f'Malware scanner flagged this asset: {reason}. ClamAV detection: {finding or "unspecified alert"}. Processing blocked.')


def discard_owned(paths,error):
    """Callers supply exact Orbits-owned file paths, never a user-selected original."""
    error.cleanup_attempted=True
    for path in set(map(Path,paths)):
        try:path.unlink(missing_ok=True)
        except OSError as failure:
            message=f'{path}: {failure}'
            if message not in error.cleanup_errors:error.cleanup_errors.append(message)


def discard_cached_source(source,error):
    from importlib import import_module
    config=import_module('orbits.config')
    source=Path(source)
    thumbnail=getattr(config,'THUMB_CACHE',None)
    cache=getattr(config,'CACHE_DIR',None)
    roots=[Path(thumbnail)] if thumbnail is not None else []
    if cache is not None:roots.append(Path(cache)/'font-previews')
    if any(source.parent==root and not root.is_symlink() for root in roots):
        discard_owned([source],error)
    elif cache is not None and source.parent==Path(cache)/'downloads' and not source.parent.is_symlink():
        if re.fullmatch('[0-9a-f]{64}',source.stem) and source.suffix in ('.part','.complete'):
            discard_owned([source.with_suffix(suffix) for suffix in ('.part','.complete','.json','.tmp')],error)


@contextmanager
def reject_cleanup(paths,*,source='',label=''):
    try:yield
    except ScanRejected as error:
        if source:error.source=source
        if label:error.label=label
        discard_owned(paths,error)
        raise


def notify(error):
    if not isinstance(error,ScanRejected) or error.notified:return
    from gi.repository import Gio
    app=Gio.Application.get_default()
    if app is not None and hasattr(app,'notify_scan_rejection'):
        app.notify_scan_rejection(error)
        error.notified=True


_owned_downloads=ContextVar('orbits_owned_downloads',default=None)


@contextmanager
def operation():
    if _owned_downloads.get() is not None:
        yield
        return
    state={'paths':set(),'source':''}
    token=_owned_downloads.set(state)
    try:
        yield
    except ScanRejected as error:
        if state['source'] and not error.source:error.source=state['source']
        if state['paths']:discard_owned(state['paths'],error)
        raise
    finally:
        _owned_downloads.reset(token)


def register_download(url,destination,checksum='',algorithm='md5'):
    state=_owned_downloads.get()
    if state is None:return
    if not state['source']:state['source']=url
    if url.startswith('https://'):
        from orbits.config import CACHE_DIR
        key=hashlib.sha256((url+'\0'+algorithm+'\0'+checksum.lower()).encode()).hexdigest()
        state['paths'].update(Path(CACHE_DIR)/'downloads'/(key+suffix)
                              for suffix in ('.part','.complete','.json','.tmp'))


def register_created_file(path):
    state=_owned_downloads.get()
    if state is not None:state['paths'].add(Path(path))


def guarded(function):
    @wraps(function)
    def work(*args,**kwargs):
        with operation():return function(*args,**kwargs)
    return work
