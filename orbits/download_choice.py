"""Remember a store item's file name, never a possibly stale signed download URL."""
import re

_ARCHIVE = re.compile(r'\.(zip|tar(\.\w+)?|tgz|txz|tbz2?)$', re.I)


def select_download(item, explicit=None, *, prefer_archive=False):
    from orbits.config import settings

    choices = dict(settings.get('download_choices', {}))
    if explicit is not None:
        if item.source != 'local' and any(d.name == explicit.name for d in item.downloads):
            choices[item.key] = explicit.name
            settings.set('download_choices', choices)
        return explicit
    remembered = choices.get(item.key)
    match = next((d for d in item.downloads if d.name == remembered), None)
    if match is not None:
        return match
    if prefer_archive:
        archives = [d for d in item.downloads if _ARCHIVE.search(d.name)]
        if archives:
            return archives[-1]
    return next(iter(item.downloads), None)
