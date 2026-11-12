"""Cache ownership for managed themes; never infer arbitrary filesystem paths from metadata."""
import hashlib
import json
from pathlib import Path
import re


def download_key(url, checksum='', algorithm='md5'):
    return hashlib.sha256((url+'\0'+algorithm+'\0'+checksum.lower()).encode()).hexdigest()


def cache_identity(item, download, previous=None):
    previews = {hashlib.sha1(url.encode()).hexdigest() for url in (item.thumb_url,item.preview_url) if url}
    archives={download_key(download.url,getattr(download,'md5',''))} if download.url.startswith('https://') else set()
    if previous:
        older_archives,older_previews=_keys(previous)
        archives.update(older_archives)
        previews.update(older_previews)
    return {'downloads':sorted(archives),'previews':sorted(previews)}


def _keys(entry):
    cache = entry.get('cache') or {}
    if not isinstance(cache,dict) or any(not isinstance(cache.get(field,[]),list) for field in ('downloads','previews')):
        raise RuntimeError('Theme cache ownership is invalid; cache files were not removed')
    downloads = {value for value in cache.get('downloads', [])
                 if isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value)}
    previews = {value for value in cache.get('previews', [])
                if isinstance(value,str) and re.fullmatch('[0-9a-f]{40}',value)}
    for field in ('preview','preview_url','thumb_url'):
        url=entry.get(field)
        if isinstance(url,str) and url.startswith('https://'):
            previews.add(hashlib.sha1(url.encode()).hexdigest())
    # Before explicit ownership was recorded, boot GitHub downloads always used this URL.
    page=entry.get('page','')
    if not cache and str(entry.get('key','')).startswith('boot-refind:') and isinstance(page,str):
        match=re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?',page)
        if match:
            downloads.add(download_key('https://codeload.github.com/'+match[1]+'/'+match[2]+'/zip/HEAD'))
    return downloads,previews


def cleanup(entry):
    """Delete only owned cache files with no remaining installed references."""
    from orbits.config import CACHE_DIR,DATA_DIR,THUMB_CACHE
    from orbits import downloads as transfers
    wanted_downloads,wanted_previews=_keys(entry)
    referenced_downloads,referenced_previews=set(),set()
    def references(value):
        if isinstance(value,dict):
            a,b=_keys(value)
            referenced_downloads.update(a)
            referenced_previews.update(b)
            for child in value.values():references(child)
        elif isinstance(value,list):
            for child in value:references(child)
    for filename in ('themes.json','boot-themes.json','library.json','fonts.json'):
        path=DATA_DIR/filename
        if path.exists():
            try:
                references(json.loads(path.read_text()))
            except (OSError,ValueError,TypeError,AttributeError,RecursionError) as error:
                raise RuntimeError('Theme removed, but shared-cache ownership could not be checked: '+filename) from error
    manager=transfers._manager
    if manager is not None:
        for job in list(manager.jobs.values()):
            if job.state in ('queued','downloading','paused'):
                referenced_downloads.add(download_key(job.url,job.checksum,job.algorithm))
    if (CACHE_DIR/'downloads').is_symlink() or THUMB_CACHE.is_symlink():
        raise RuntimeError('Theme removed, but cache cleanup refuses a linked cache directory')
    removed_downloads=wanted_downloads-referenced_downloads
    if manager is not None and removed_downloads:
        with manager.lock:
            for identifier,job in list(manager.jobs.items()):
                if job.state in ('completed','failed','cancelled') and download_key(job.url,job.checksum,job.algorithm) in removed_downloads:
                    del manager.jobs[identifier]
        manager.changed()
    for key in removed_downloads:
        for suffix in ('.complete','.part','.json','.tmp'):
            (CACHE_DIR/'downloads'/(key+suffix)).unlink(missing_ok=True)
    for key in wanted_previews-referenced_previews:
        (THUMB_CACHE/key).unlink(missing_ok=True)

    # Failed staging folders carry the same managed key; never remove unmarked folders.
    staging=DATA_DIR/'staging'
    if staging.is_dir() and not staging.is_symlink() and entry.get('key'):
        import shutil
        for directory in staging.iterdir():
            marker=directory/'.orbits.json'
            if directory.is_symlink() or not directory.is_dir() or marker.is_symlink() or not marker.is_file():
                continue
            try:
                if marker.stat().st_size > 65536:continue
                data=json.loads(marker.read_text())
            except (OSError,ValueError):continue
            if isinstance(data,dict) and data.get('key')==entry['key']:
                shutil.rmtree(directory)
