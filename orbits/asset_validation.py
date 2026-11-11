"""Structural font validation and data-only theme policy before installation."""
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET

FONT_SUFFIXES = {'.ttf', '.otf', '.ttc', '.otc'}
TEXT_SUFFIXES = {'.css', '.rc', '.theme', '.desktop', '.conf', '.ini', '.json', '.xml', '.txt', '.md', '.rst', '.colors', '.colorscheme', '.svg', '.svgz', '.plymouth', '.script', '.cfg', '.map'}
BINARY_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.bmp', '.xpm', '.xcursor', '.cur', '.ogg', '.oga', '.wav', '.flac', '.pf2', '.gresource', '.ttf', '.otf'}
BLOCKED_SUFFIXES = {'.sh', '.bash', '.zsh', '.fish', '.py', '.pyc', '.js', '.qml', '.so', '.dll', '.exe', '.desktop.in', '.service', '.socket', '.timer', '.rules'}


def validate_font_bytes(data):
    """Bound SFNT tables without invoking a font renderer (also used by root helper)."""
    def sfnt(offset):
        if offset < 0 or offset + 12 > len(data) or data[offset:offset+4] not in (b'\x00\x01\x00\x00', b'OTTO', b'true'):
            raise ValueError('Invalid font signature')
        count = struct.unpack_from('>H', data, offset + 4)[0]
        if not 1 <= count <= 256 or offset + 12 + count * 16 > len(data):
            raise ValueError('Invalid font table directory')
        tags = set()
        for index in range(count):
            tag, _checksum, start, length = struct.unpack_from('>4sIII', data, offset + 12 + index * 16)
            if tag in tags or start > len(data) or length > len(data) - start:
                raise ValueError('Invalid font table bounds')
            tags.add(tag)
        if not {b'head', b'maxp', b'cmap', b'name'}.issubset(tags):
            raise ValueError('Required font tables are missing')
    if len(data) > 128 * 1024**2:
        raise ValueError('Font exceeds its size limit')
    if data[:4] == b'ttcf':
        if len(data) < 12:
            raise ValueError('Invalid font collection')
        count = struct.unpack_from('>I', data, 8)[0]
        if not 1 <= count <= 256 or 12 + 4 * count > len(data):
            raise ValueError('Invalid font collection directory')
        for offset in struct.unpack_from('>' + 'I' * count, data, 12):
            sfnt(offset)
    else:
        sfnt(0)


def validate_font(path):
    path = Path(path)
    if path.stat().st_size > 128 * 1024**2:
        raise ValueError('Font exceeds its size limit')
    validate_font_bytes(path.read_bytes())


def validate_theme_file(path, relative, kind):
    suffix = path.suffix.lower()
    name = path.name.lower()
    if suffix in BLOCKED_SUFFIXES or re.search(r'\.so(?:\.\d+)*$', name):
        raise ValueError(f'Executable or service file is not allowed in a theme: {relative}')
    with path.open('rb') as stream:
        header = stream.read(4096)
    if header.startswith((b'\x7fELF', b'MZ', b'#!', b'\xca\xfe\xba\xbe', b'\xcf\xfa\xed\xfe')):
        raise ValueError(f'Executable content is not allowed in a theme: {relative}')
    if suffix == '.script':
        raise ValueError('Downloaded scripted themes are blocked because the boot runtime cannot sandbox them')
    signatures = {'.png': (b'\x89PNG\r\n\x1a\n',), '.jpg': (b'\xff\xd8\xff',),
                  '.jpeg': (b'\xff\xd8\xff',), '.gif': (b'GIF87a', b'GIF89a'),
                  '.bmp': (b'BM',), '.wav': (b'RIFF',), '.ogg': (b'OggS',),
                  '.oga': (b'OggS',), '.flac': (b'fLaC',)}
    if suffix in signatures and not header.startswith(signatures[suffix]):
        raise ValueError(f'Theme asset signature does not match its type: {relative}')
    if suffix == '.webp' and not (header.startswith(b'RIFF') and header[8:12] == b'WEBP'):
        raise ValueError('Invalid WebP theme asset')
    if suffix in FONT_SUFFIXES:
        validate_font(path)
    text_name = name.startswith(('readme', 'license', 'copying', 'authors', 'ofl', 'changelog', 'copyright')) or name in {'gtkrc', 'metadata', 'theme.txt', 'theme.conf', '.orbits.json'}
    # Cursor files normally have no extension; identify their binary signature.
    if suffix not in TEXT_SUFFIXES | BINARY_SUFFIXES and not text_name and not header.startswith(b'Xcur'):
        raise ValueError(f'Unsupported theme asset type: {relative}')
    if suffix in TEXT_SUFFIXES or text_name:
        if path.stat().st_size > 8 * 1024**2:
            raise ValueError(f'Theme configuration is too large: {relative}')
        if suffix == '.svgz':
            raise ValueError('Compressed SVG theme assets are not supported')
        text = path.read_text(encoding='utf-8-sig')
        if '\x00' in text:
            raise ValueError(f'Binary content in theme configuration: {relative}')
        if suffix == '.plymouth':
            import configparser
            config = configparser.ConfigParser(interpolation=None)
            config.read_string(text)
            module = config.get('Plymouth Theme', 'ModuleName', fallback='')
            if not re.fullmatch(r'[A-Za-z0-9_-]+', module) or module.lower() == 'script':
                raise ValueError('Scripted or invalid Plymouth modules are not allowed')
        if suffix == '.desktop' and re.search(r'^\s*(Exec|TryExec|DBusActivatable)\s*=', text, re.M | re.I):
            raise ValueError('Theme metadata cannot launch applications')
        if suffix in ('.css', '.svg', '.xml') and re.search(r'%[0-9a-fA-F]{2}|\\(?:[0-9a-fA-F]{1,6}|[./:])', text):
            raise ValueError('Encoded resource references are not allowed in themes')
        if suffix in ('.svg', '.xml'):
            if re.search(r'<!\s*(DOCTYPE|ENTITY)', text, re.I):
                raise ValueError('External XML declarations are not allowed in themes')
            document = ET.fromstring(text)
            if suffix == '.svg':
                for node in document.iter():
                    if node.tag.rsplit('}', 1)[-1].lower() in ('script', 'foreignobject'):
                        raise ValueError('Active SVG content is not allowed in themes')
                    for key, value in node.attrib.items():
                        name_ = key.rsplit('}', 1)[-1].lower()
                        if name_.startswith('on') or (name_ in ('href', 'base') and (value.startswith(('/', '//')) or ':' in value or '..' in Path(value).parts)):
                            raise ValueError('Active or external SVG references are not allowed')
        if suffix in ('.css', '.svg', '.xml') and re.search(r'(?:https?:|file:|ftp:|//|\.\./)', text, re.I):
            # Namespace declarations are identifiers, not resource fetches.
            scrubbed = re.sub(r'xmlns(?::[\w-]+)?\s*=\s*[\"\'][^\"\']*[\"\']', '', text)
            scrubbed = re.sub(r'/\*.*?\*/', '', scrubbed, flags=re.S)
            if re.search(r'(?:https?:|file:|ftp:|url\(\s*[\"\']?//|\.\./)', scrubbed, re.I):
                raise ValueError('Theme assets cannot reference external resources or parent paths')


def validate_theme_tree(root, kind):
    import os
    root = Path(root)
    count, total = 0, 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory)/name
            if path.is_symlink():
                raise ValueError('Theme links are not supported')
            if path.is_dir():
                continue
            if not path.is_file():
                raise ValueError('Theme contains a special file')
            count += 1
            total += path.stat().st_size
            if count > 10000 or total > 1024**3 or path.stat().st_size > 512 * 1024**2:
                raise ValueError('Theme exceeds its size limits')
            validate_theme_file(path, path.relative_to(root), kind)
