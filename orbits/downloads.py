"""Queued, resumable transfers. No GTK or desktop dependencies."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
import uuid


class DownloadCancelled(RuntimeError):
    pass


@dataclass
class Transfer:
    url: str
    destination: Path
    checksum: str = ""
    algorithm: str = "md5"
    label: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: str = "queued"
    received: int = 0
    total: int = 0
    error: str = ""
    progress: object = None
    result: object = None
    run_lock: object = field(default_factory=threading.Lock, repr=False)
    condition: object = field(default_factory=threading.Condition, repr=False)
    interrupt: object = field(default_factory=threading.Event, repr=False)


class DownloadManager:
    def __init__(self, directory, opener=None, workers=2, changed=None, offline=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.opener = opener or urllib.request.urlopen
        self.offline = offline or (lambda:False)
        self.changed = changed or (lambda: None)
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='orbits-download')
        self.jobs = {}
        self.lock = threading.Lock()
        self.cache_locks = {}
        self.closed = False

    def submit(self, url, destination, progress=None, checksum='', algorithm='md5', label=''):
        if self.closed:
            raise DownloadCancelled('The download manager is shutting down')
        if algorithm not in ('md5', 'sha256'):
            raise ValueError('Unsupported checksum algorithm')
        if checksum and not re.fullmatch('[0-9a-fA-F]{'+str(32 if algorithm == 'md5' else 64)+'}', checksum):
            raise ValueError('Invalid download checksum')
        job = Transfer(url, Path(destination), checksum.lower(), algorithm, label or Path(destination).name,
                       progress=progress)
        with self.lock:
            self.jobs[job.id] = job
        self.pool.submit(self._run, job)
        self.changed()
        return job

    def wait(self, job):
        with job.condition:
            job.condition.wait_for(lambda: job.state in ('completed', 'failed', 'cancelled'))
        if job.state == 'cancelled':
            raise DownloadCancelled('Download cancelled; partial data is kept for a later retry')
        if job.state == 'failed':
            raise RuntimeError(job.error)
        return job.result

    def pause(self, job):
        with job.condition:
            if job.state in ('queued', 'downloading'):
                job.state = 'paused'
                job.interrupt.set()
        self.changed()

    def resume(self, job):
        with job.condition:
            if job.state != 'paused':
                return
            job.state = 'queued'
            self.pool.submit(self._run, job)
        self.changed()

    def cancel(self, job):
        with job.condition:
            if job.state in ('completed', 'failed', 'cancelled'):
                return
            job.state = 'cancelled'
            job.interrupt.set()
            job.condition.notify_all()
        self.changed()

    def clear_finished(self):
        with self.lock:
            if any(job.state in ('queued','downloading','paused') for job in self.jobs.values()):
                raise RuntimeError('Finish or cancel active transfers before clearing the cache')
            for path in self.directory.glob('*.complete'):
                path.unlink()
            for path in self.directory.glob('*.json'):
                if not path.with_suffix('.part').exists():
                    path.unlink()
            self.jobs.clear()
        self.changed()

    def close(self):
        self.closed = True
        for job in list(self.jobs.values()):
            self.cancel(job)
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _run(self, job):
        # A URL has one partial file. Serializing cache access also prevents duplicate writers.
        with self.lock:
            cache_lock = self.cache_locks.setdefault(job.url, threading.Lock())
        with job.run_lock, cache_lock:
            with job.condition:
                if job.state != 'queued':
                    return
                job.interrupt.clear()
                job.state = 'downloading'
            self.changed()
            try:
                result = self._transfer(job)
                with job.condition:
                    if not job.interrupt.is_set():
                        job.result = result
                        job.state = 'completed'
            except Exception as error:
                with job.condition:
                    if job.state not in ('paused', 'cancelled'):
                        job.error = str(error)
                        job.state = 'failed'
            finally:
                with job.condition:
                    job.condition.notify_all()
                self.changed()

    def _transfer(self, job):
        key = hashlib.sha256((job.url+'\0'+job.algorithm+'\0'+job.checksum).encode()).hexdigest()
        partial = self.directory / (key+'.part')
        metadata = self.directory / (key+'.json')
        complete = self.directory / (key+'.complete')
        try:
            info = json.loads(metadata.read_text())
        except (OSError, ValueError):
            info = {}
        cached_complete = complete.is_file() and info.get('complete')
        if cached_complete:
            self._verify(complete, job)
            if job.checksum or self.offline():
                return self._deliver(complete, job, info)
        offset = partial.stat().st_size if partial.exists() else 0
        validator = info.get('etag') or info.get('modified')
        if offset and not (validator or job.checksum):
            offset = 0
        headers = {'User-Agent':'Orbits download manager', 'Accept-Encoding':'identity'}
        if cached_complete and not offset:
            if info.get('etag'):
                headers['If-None-Match'] = info['etag']
            elif info.get('modified'):
                headers['If-Modified-Since'] = info['modified']
        if offset:
            headers['Range'] = f'bytes={offset}-'
            if validator:
                headers['If-Range'] = validator
        request = urllib.request.Request(job.url, headers=headers)
        try:
            response = self.opener(request, timeout=30)
        except urllib.error.HTTPError as error:
            if error.code == 304 and cached_complete:
                error.close()
                return self._deliver(complete, job, info)
            if error.code != 416:
                raise
            error.close()
            offset = 0
            response = self.opener(urllib.request.Request(job.url, headers={
                'User-Agent':'Orbits download manager', 'Accept-Encoding':'identity'}), timeout=30)
        with response:
            status = getattr(response, 'status', 200)
            if status == 206:
                current_validator = response.headers.get('ETag') or response.headers.get('Last-Modified')
                if offset and validator and current_validator != validator:
                    partial.unlink(missing_ok=True)
                    raise RuntimeError('The remote file changed; retry to download the new version')
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                if not match or int(match[1]) != offset or int(match[2]) >= int(match[3]):
                    raise RuntimeError('The server returned an invalid resume range')
                total = int(match[3])
            elif status == 200:
                offset = 0
                total = int(response.headers.get('Content-Length') or 0)
            else:
                raise RuntimeError(f'Download server returned HTTP {status}')
            info = {'etag':response.headers.get('ETag'), 'modified':response.headers.get('Last-Modified'),
                    'total':total, 'type':response.headers.get_content_type(), 'url':response.geturl(),
                    'complete':False, 'updated':time.time()}
            self._save_metadata(metadata, info)
            job.received, job.total = offset, total
            with partial.open('ab' if offset else 'wb') as stream:
                while not job.interrupt.is_set():
                    chunk = response.read(1 << 16)
                    if not chunk:
                        break
                    stream.write(chunk)
                    job.received += len(chunk)
                    if job.progress and total:
                        job.progress(min(1, job.received/total))
                    self.changed()
            if job.interrupt.is_set():
                return None
            if total and job.received != total:
                raise RuntimeError('Download interrupted before all bytes arrived; retry to resume')
        self._verify(partial, job)
        os.replace(partial, complete)
        info['complete'] = True
        self._save_metadata(metadata, info)
        return self._deliver(complete, job, info)

    @staticmethod
    def _verify(path, job):
        if job.checksum:
            digest = hashlib.new(job.algorithm)
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1 << 20), b''):
                    digest.update(chunk)
            if digest.hexdigest() != job.checksum:
                path.unlink(missing_ok=True)
                raise RuntimeError('Download checksum mismatch; the file was not installed')

    @staticmethod
    def _save_metadata(path, info):
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(info))
        temporary.chmod(0o600)
        os.replace(temporary, path)

    @staticmethod
    def _deliver(source, job, info):
        if job.interrupt.is_set():
            return None
        job.destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = job.destination.with_name(job.destination.name+'.orbits-part')
        shutil.copyfile(source, temporary)
        os.replace(temporary, job.destination)
        job.received = source.stat().st_size
        return info.get('type', 'application/octet-stream'), info.get('url', job.url)


_manager = None
_listeners = []


def subscribe(callback):
    _listeners.append(callback)
    return lambda: _listeners.remove(callback) if callback in _listeners else None


def get_manager():
    global _manager
    if _manager is None:
        from orbits.config import CACHE_DIR
        from gi.repository import GLib
        def changed():
            GLib.idle_add(lambda: ([callback() for callback in list(_listeners)], False)[1])
        def opener(request, timeout):
            from orbits.offline import enabled, OfflineError
            if enabled():
                raise OfflineError('You are offline. This download is not cached yet.')
            return urllib.request.urlopen(request, timeout=timeout)
        from orbits.offline import enabled
        _manager = DownloadManager(CACHE_DIR/'downloads', changed=changed, opener=opener, offline=enabled)
    return _manager
