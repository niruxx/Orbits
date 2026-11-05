"""A local, network-free index shared by global search and the Shell provider."""
import json
from pathlib import Path
import unicodedata


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(text)).casefold()
                   if not unicodedata.combining(c))


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def build_index(directory):
    root = Path(directory)
    records = []
    for filename, kind, page, title in [('library.json','wallpaper','library','title'),
                                      ('fonts.json','font','my-fonts','family')]:
        data = read_json(root/filename, {})
        if not isinstance(data, dict):
            continue
        for key, entry in data.items():
            if isinstance(entry, dict):
                records.append(dict(id=f'{kind}:{key}', kind=kind, title=entry.get(title, key), page=page,
                                    detail=' · '.join(entry.get('collections', [])), key=key))
    themes = read_json(root/'themes.json', {})
    if not isinstance(themes, dict):
        themes = {}
    for kind, entries in themes.items():
        if not isinstance(entries, dict):
            continue
        for key, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            records.append(dict(id=f'theme:{kind}:{key}', kind='theme', title=entry.get('title', key),
                                page=f'theme-{kind}', detail=' · '.join(entry.get('names', [])), key=key))
    for path in sorted((root/'looks').glob('*/look.json')):
        entry = read_json(path, {})
        if entry.get('name'):
            records.append(dict(id='look:'+path.parent.name, kind='look', title=entry['name'],
                                page='looks', detail='Look', key=path.parent.name))
    return records


def search(records, query, limit=60):
    terms = normalize(query).split()
    if not terms:
        return records[:limit]
    result = []
    for record in records:
        title = normalize(record['title'])
        text = title+' '+normalize(record.get('detail', ''))+' '+record['kind']
        if all(term in text for term in terms):
            result.append((0 if title == normalize(query) else 1 if title.startswith(terms[0]) else 2,
                           title, record))
    return [r[2] for r in sorted(result, key=lambda item:item[:2])[:limit]]


def resolve_look(directory, identifier):
    # Provider IDs are opaque, validated before resolving local data.
    if not identifier.startswith('look:'):
        raise ValueError('Unknown Look')
    name = identifier[5:]
    if not name or name in ('.','..') or '/' in name or '\\' in name:
        raise ValueError('Invalid Look identifier')
    root = (Path(directory)/'looks').resolve()
    path = root/name/'look.json'
    if path.resolve().parent.parent != root or not path.is_file():
        raise ValueError('Look no longer exists')
    return path
