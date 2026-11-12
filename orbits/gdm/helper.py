#!/usr/bin/python3 -I
"""Orbits privileged helper — run through pkexec, so it must stay self-contained (stdlib + gi only).

usage: helper.py apply <spec.json>
       helper.py reset
       helper.py reapply             # root package hook / systemd only
       helper.py updates-disable
       helper.py undo <identifier>
       helper.py font-install <dir> <slug>
       helper.py font-remove <slug>
       helper.py theme-install <themes|icons|plymouth> <dir> <name>
       helper.py theme-remove <themes|icons|plymouth> <name>
       helper.py plymouth-set <name>
       helper.py plymouth-preview <name> <local-display> <xauthority-file>
       helper.py boot-list
       helper.py boot-theme-install <grub|refind> <dir> <name>
       helper.py boot-theme-apply <grub|refind> <name>
       helper.py boot-theme-remove <grub|refind> <name>
       helper.py boot-theme-reset <grub|refind>
       helper.py boot-theme-default <grub|refind>

The spec is produced by orbits.gdm.model.build_spec().  Everything the helper writes is tagged so a
reset returns the system to exactly what it was before Orbits touched it.
"""
import json
import hashlib
import glob
import os
import re
import shutil
import shlex
import stat
import subprocess
import sys
import tempfile
import struct
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET

from gi.repository import Gio, GLib

SHELL_DIR = "/usr/share/gnome-shell"
GRESOURCE = os.path.join(SHELL_DIR, "gnome-shell-theme.gresource")
BACKUP = GRESOURCE + ".orbits-backup"
PREFIX = "/org/gnome/shell/theme"
MARKER = PREFIX + "/orbits.marker"
BG_NAME = "orbits-background.jpg"
THEMED_CSS = re.compile(r"^(gnome-shell|gdm3?)(-[a-z-]+)?\.css$")

# Debian/Ubuntu let distributions pick GDM's theme with update-alternatives instead of replacing
# gnome-shell-theme.gresource (Ubuntu ships Yaru this way). There we register our own alternative
# rather than overwriting a file owned by a package.
ALTERNATIVES = [
    (os.path.join(SHELL_DIR, "gdm-theme.gresource"), "gdm-theme.gresource"),     # Ubuntu 20.04+
    (os.path.join(SHELL_DIR, "gdm3-theme.gresource"), "gdm3-theme.gresource"),   # Ubuntu 18.04–19.10
]
ALT_CMD = "update-alternatives"
OUR_DIR = os.path.join(SHELL_DIR, "orbits")
OUR_GRES = os.path.join(OUR_DIR, "gdm-theme.gresource")
ALT_BASE_FILE = os.path.join(OUR_DIR, "base")

DCONF_PROFILE = "/etc/dconf/profile/gdm"
DCONF_DB_DIR = "/etc/dconf/db/gdm.d"
DCONF_FILE = os.path.join(DCONF_DB_DIR, "99-orbits")
PROFILE_DEFAULT = "user-db:user\nsystem-db:gdm\nfile-db:/usr/share/gdm/greeter-dconf-defaults\n"

DATA_DIR = "/usr/share/orbits"
GDM_CONF_CANDIDATES = ["/etc/gdm/custom.conf", "/etc/gdm3/custom.conf", "/etc/gdm3/daemon.conf"]
CUSTOM_KEYS = {"AutomaticLoginEnable", "AutomaticLogin", "WaylandEnable", "TimedLoginEnable",
               "TimedLogin", "TimedLoginDelay"}
ORBITS_TAG = "# managed by Orbits"


def log(msg):
    print(msg, flush=True)


def invoking_uid():
    try:
        return int(os.environ["PKEXEC_UID"])
    except (KeyError, ValueError):
        return os.getuid()


def read_user_file(path, max_bytes=64 << 20):
    """Read a file the invoking user owns. Prevents leaking root-only files into world-readable places."""
    if not os.path.isabs(path):
        raise ValueError(f"not an absolute path: {path}")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(f"not a regular file: {path}")
        if st.st_uid != invoking_uid():
            raise PermissionError(f"{path} must be owned by you")
        if st.st_size > max_bytes:
            raise ValueError(f"{path} is too large")
        with os.fdopen(fd, "rb") as f:
            fd = None
            data = f.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError("File grew beyond its size limit")
            return data
    finally:
        if fd is not None:
            os.close(fd)


def selinux_enabled():
    return os.path.isdir("/sys/fs/selinux") and bool(shutil.which("restorecon"))


def relabel(path):
    """Give files we write the SELinux label their location expects (Fedora/RHEL), so gdm may read them."""
    if selinux_enabled() and os.path.lexists(path):
        subprocess.run(["restorecon", "-RF", path], check=False, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)


def read_only(path):
    """True if path sits on a read-only mount, including protected boot/EFI mounts."""
    p = path
    while not os.path.exists(p):
        p = os.path.dirname(p)
    return bool(os.statvfs(p).f_flag & os.ST_RDONLY)


def require_writable(*paths):
    for p in paths:
        if read_only(p):
            raise PermissionError(f"{p} is on a read-only filesystem; "
                                  "Orbits can't make system-wide changes here")


def atomic_write(path, data: bytes, mode=0o644):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".orbits-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        relabel(path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ---------------------------------------------------------------- shell theme
def is_ours(path):
    try:
        res = Gio.Resource.load(path)
        res.lookup_data(MARKER, Gio.ResourceLookupFlags.NONE)
        return True
    except GLib.Error:
        return False


def walk(res, path):
    for child in res.enumerate_children(path, Gio.ResourceLookupFlags.NONE):
        if child.endswith("/"):
            yield from walk(res, path + child)
        else:
            yield path + child


def theme_location():
    """("alternatives", link, name) where GDM's theme is an update-alternatives link, else ("file", path, None)."""
    if shutil.which(ALT_CMD):
        for link, name in ALTERNATIVES:
            if os.path.lexists(link):
                return "alternatives", link, name
    return "file", GRESOURCE, None


def active_theme_file():
    """The gresource GDM will actually load."""
    _mode, path, _name = theme_location()
    return os.path.realpath(path)


def build_gresource(base_path, css, background, out_path):
    """Copy every resource from base_path, append css to the shell/GDM stylesheets, add our files."""
    base = Gio.Resource.load(base_path)
    with tempfile.TemporaryDirectory(prefix="orbits-theme-") as tmp:
        names = []
        for rpath in walk(base, PREFIX + "/"):
            rel = rpath[len(PREFIX) + 1:]
            if rel == "orbits.marker" or rel in (BG_NAME, "orbits-background-light.jpg", "orbits-background-dark.jpg"):
                continue
            data = base.lookup_data(rpath, Gio.ResourceLookupFlags.NONE).get_data()
            if THEMED_CSS.match(os.path.basename(rel)) and css:
                variant = "light" if "-light" in os.path.basename(rel) else "dark"
                extra = css.get(variant, "") if isinstance(css, dict) else css
                data = data + b"\n\n/* ---- Orbits ---- */\n" + extra.encode()
            out = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as f:
                f.write(data)
            names.append(rel)
        if isinstance(css, dict):
            resource_names = set(names)
            for rel in list(names):
                if os.path.basename(rel) not in ("gnome-shell.css", "gdm.css", "gdm3.css"):
                    continue
                original = base.lookup_data(PREFIX + "/" + rel, Gio.ResourceLookupFlags.NONE).get_data()
                for variant in ("light", "dark"):
                    variant_name = rel[:-4] + f"-{variant}.css"
                    if variant_name not in resource_names:
                        with open(os.path.join(tmp, variant_name), "wb") as f:
                            f.write(original + b"\n\n/* ---- Orbits ---- */\n" + css.get(variant, "").encode())
                        names.append(variant_name)
        if background:
            images = background if isinstance(background, dict) else {"default": background}
            for variant, source in images.items():
                if variant not in ("default", "light", "dark"):
                    raise ValueError("Invalid background variant")
                filename = BG_NAME if variant == "default" else f"orbits-background-{variant}.jpg"
                with open(os.path.join(tmp, filename), "wb") as f:
                    f.write(read_user_file(source))
                names.append(filename)
        with open(os.path.join(tmp, "orbits.marker"), "w") as f:
            f.write(f"Modified by Orbits. Built from {base_path}\n")
        names.append("orbits.marker")

        xml = ['<?xml version="1.0" encoding="UTF-8"?>', "<gresources>", f'  <gresource prefix="{PREFIX}">']
        xml += [f"    <file>{GLib.markup_escape_text(n)}</file>" for n in names]
        xml += ["  </gresource>", "</gresources>"]
        xml_path = os.path.join(tmp, "orbits.gresource.xml")
        with open(xml_path, "w") as f:
            f.write("\n".join(xml))
        target = os.path.join(tmp, "out.gresource")
        subprocess.run(["glib-compile-resources", f"--sourcedir={tmp}", f"--target={target}", xml_path],
                       check=True)
        with open(target, "rb") as f:
            atomic_write(out_path, f.read())


def apply_theme(css, background):
    mode, path, name = theme_location()
    if mode == "alternatives":
        _apply_alternative(path, name, css, background)
        return
    if not os.path.exists(GRESOURCE):
        raise FileNotFoundError(f"{GRESOURCE} not found — is GNOME Shell installed?")
    if not is_ours(GRESOURCE):
        # Current file is pristine (or came from a package update / another tool): it becomes the base.
        shutil.copy2(GRESOURCE, BACKUP)
        log("backed up original shell theme")
    elif not os.path.exists(BACKUP):
        raise RuntimeError("Theme was modified by Orbits but the backup is missing; reinstall gnome-shell")
    build_gresource(BACKUP, css, background, GRESOURCE)
    log("installed custom shell theme")


def _alt(*args):
    subprocess.run([ALT_CMD, *args], check=True, stdout=subprocess.DEVNULL)


def _alternative_base(link, name):
    """The distribution theme our alternative is built on (whatever was selected before Orbits)."""
    current = os.path.realpath(link)
    if current != os.path.realpath(OUR_GRES) and os.path.exists(current):
        os.makedirs(OUR_DIR, exist_ok=True)
        atomic_write(ALT_BASE_FILE, current.encode())
        return current
    try:
        with open(ALT_BASE_FILE) as f:
            base = f.read().strip()
        if os.path.exists(base):
            return base
    except FileNotFoundError:
        pass
    out = subprocess.run([ALT_CMD, "--list", name], capture_output=True, text=True).stdout.split()
    for cand in out:
        if os.path.realpath(cand) != os.path.realpath(OUR_GRES) and os.path.exists(cand):
            atomic_write(ALT_BASE_FILE, cand.encode())
            return cand
    raise RuntimeError(f"no original {name} alternative found to build on")


def _apply_alternative(link, name, css, background):
    base = _alternative_base(link, name)
    os.makedirs(OUR_DIR, exist_ok=True)
    build_gresource(base, css, background, OUR_GRES)
    # Priority 0 so "auto" mode never picks us on its own; --set selects us explicitly.
    _alt("--install", link, name, OUR_GRES, "0")
    _alt("--set", name, OUR_GRES)
    log(f"selected Orbits theme as the {name} alternative (based on {base})")


def restore_theme():
    mode, path, name = theme_location()
    if mode == "alternatives":
        if os.path.exists(OUR_GRES):
            base = None
            try:
                with open(ALT_BASE_FILE) as f:
                    base = f.read().strip()
            except FileNotFoundError:
                pass
            if os.path.realpath(path) == os.path.realpath(OUR_GRES):
                if base and os.path.exists(base):
                    _alt("--set", name, base)
                else:
                    _alt("--auto", name)
            _alt("--remove", name, OUR_GRES)
            log(f"restored the original {name} alternative")
        shutil.rmtree(OUR_DIR, ignore_errors=True)
        return
    if is_ours(GRESOURCE) and os.path.exists(BACKUP):
        with open(BACKUP, "rb") as f:
            atomic_write(GRESOURCE, f.read())
        log("restored original shell theme")


# ---------------------------------------------------------------- dconf
GROUP_RE = re.compile(r"^[a-z0-9][a-z0-9./-]*$")
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def ensure_profile():
    try:
        with open(DCONF_PROFILE) as f:
            text = f.read()
    except FileNotFoundError:
        atomic_write(DCONF_PROFILE, PROFILE_DEFAULT.encode())
        log("created dconf profile for gdm")
        return
    lines = text.splitlines()
    if not any(line.strip() == "system-db:gdm" for line in lines):
        idx = next((i + 1 for i, line in enumerate(lines) if line.startswith("user-db:")), 0)
        lines.insert(idx, "system-db:gdm")
        atomic_write(DCONF_PROFILE, ("\n".join(lines) + "\n").encode())
        log("added system-db:gdm to dconf profile")


def write_dconf(groups: dict):
    kf = GLib.KeyFile()
    for group, keys in groups.items():
        if not GROUP_RE.match(group):
            raise ValueError(f"bad dconf group {group!r}")
        for key, text in keys.items():
            if not KEY_RE.match(key):
                raise ValueError(f"bad dconf key {key!r}")
            value = GLib.Variant.parse(None, text, None, None)
            kf.set_value(group, key, value.print_(True))
    data = "# Generated by Orbits — remove this file to undo.\n" + kf.to_data()[0]
    os.makedirs(DCONF_DB_DIR, exist_ok=True)
    atomic_write(DCONF_FILE, data.encode())
    ensure_profile()
    dconf_update()


def dconf_update():
    subprocess.run(["dconf", "update"], check=True)
    log("updated dconf databases")


def install_logo(path):
    data = read_user_file(path, 16 << 20)
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".png", ".svg", ".jpg", ".jpeg", ".webp"):
        raise ValueError("logo must be png, svg, jpg or webp")
    for old in os.listdir(DATA_DIR) if os.path.isdir(DATA_DIR) else []:
        if old.startswith("gdm-logo"):
            os.unlink(os.path.join(DATA_DIR, old))
    dest = os.path.join(DATA_DIR, "gdm-logo" + ext)
    atomic_write(dest, data)
    return dest


# ---------------------------------------------------------------- custom.conf
def gdm_conf_path():
    for p in GDM_CONF_CANDIDATES:
        if os.path.exists(p):
            return p
    return GDM_CONF_CANDIDATES[0]


def edit_custom_conf(values: dict):
    """Set (or with value None, remove) keys in [daemon], keeping comments and other sections intact."""
    for k, v in values.items():
        if k not in CUSTOM_KEYS:
            raise ValueError(f"refusing to set {k}")
        if v is not None and not re.match(r"^[A-Za-z0-9_.$-]{0,64}$", str(v)):
            raise ValueError(f"bad value for {k}")
    path = gdm_conf_path()
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = ["# GDM configuration storage", "", "[daemon]"]
    out, in_daemon, seen_daemon, pending = [], False, False, dict(values)

    def flush():
        # insert before any trailing blank lines so the next [section] keeps its spacing
        trailing = []
        while out and not out[-1].strip():
            trailing.append(out.pop())
        for k, v in list(pending.items()):
            if v is not None:
                out.append(f"{k}={v}")
            pending.pop(k)
        out.extend(trailing)

    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if in_daemon:
                flush()
            in_daemon = s == "[daemon]"
            seen_daemon |= in_daemon
            out.append(line)
            continue
        if in_daemon and "=" in s and not s.startswith("#"):
            k = s.split("=", 1)[0].strip()
            if k in values:
                if k in pending and pending[k] is not None:
                    out.append(f"{k}={pending[k]}")
                pending.pop(k, None)
                continue
        out.append(line)
    if in_daemon:
        flush()
    if pending and any(v is not None for v in pending.values()):
        if not seen_daemon:
            out += ["", "[daemon]"]
        flush()
    atomic_write(path, ("\n".join(out) + "\n").encode())
    log(f"updated {path}")


INTEGRITY_MANIFEST = '.orbits-integrity.json'


def _hash_file(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 512 * 1024**2:
            raise ValueError('Invalid asset file for integrity checking')
        digest, count = hashlib.sha256(), 0
        while chunk := stream.read(65536):
            count += len(chunk)
            if count > 512 * 1024**2:
                raise ValueError('Asset grew beyond its size limit')
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('Asset changed during integrity checking')
        return digest.hexdigest(), count


def _tree_hashes(root):
    root = Path(root)
    if root.is_symlink():
        raise ValueError('Asset directory must not be a link')
    result, total = {}, 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            if (Path(directory)/name).is_symlink():
                raise ValueError('Asset directory contains a link')
        for name in files:
            path = Path(directory)/name
            relative = str(path.relative_to(root))
            if relative in (INTEGRITY_MANIFEST, '.uuid'):
                continue
            value, size = _hash_file(path)
            total += size
            if len(result) >= 10000 or total > 1024**3:
                raise ValueError('Integrity check exceeds its size limits')
            result[relative] = value
    return result


def record_tree(root, *, plain=False):
    root = Path(root)
    values = _tree_hashes(root)
    with tempfile.NamedTemporaryFile(mode='w', dir=root, prefix='.integrity-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump({'version': 1, 'sha256': values}, stream, sort_keys=True)
            stream.flush()
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    try:
        if not plain:
            os.chmod(temporary, 0o644)
        os.replace(temporary, root/INTEGRITY_MANIFEST)
    finally:
        temporary.unlink(missing_ok=True)


def verify_tree(root):
    root = Path(root)
    manifest = root/INTEGRITY_MANIFEST
    if not manifest.exists() and not manifest.is_symlink():
        return None  # Legacy assets have no trusted baseline; do not silently create one.
    descriptor = os.open(manifest, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'r') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Invalid integrity manifest')
        text = stream.read(2 * 1024**2 + 1)
        if len(text) > 2 * 1024**2:
            raise ValueError('Integrity manifest exceeds its limit')
        data = json.loads(text)
    expected = data.get('sha256') if isinstance(data, dict) and data.get('version') == 1 else None
    if not isinstance(expected, dict) or len(expected) > 10000:
        raise ValueError('Invalid integrity manifest')
    for name, digest in expected.items():
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or not path.parts or '\\' in name or not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Invalid integrity manifest entry')
    actual = _tree_hashes(root)
    changed = sorted(name for name in set(actual) | set(expected) if actual.get(name) != expected.get(name))
    if changed:
        raise ValueError('Installed asset changed since validation: ' + ', '.join(changed[:5]))
    return True



# Shared data-only validation policy (kept self-contained for isolated helper execution).
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


def _snapshot_user_tree(source, destination, *, plain=False):
    """Anchor every read to open directory FDs, then validate the root-owned copy."""
    uid, total, entries = invoking_uid(), 0, 0
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    def walk(descriptor, output, depth):
        nonlocal total, entries
        current = os.fstat(descriptor)
        if current.st_uid != uid or not stat.S_ISDIR(current.st_mode) or current.st_mode & 0o6022:
            raise PermissionError('Asset directories must be owned by you without unsafe permissions')
        if depth > 20:
            raise ValueError('Asset directory is too deep')
        os.mkdir(output, 0o755)
        for name in os.listdir(descriptor):
            entries += 1
            if entries > 10000 or len(name) > 255 or name in ('.', '..'):
                raise ValueError('Asset tree exceeds its entry limit')
            child = os.open(name, flags, dir_fd=descriptor)
            try:
                before = os.fstat(child)
                if before.st_uid != uid or before.st_mode & 0o6022:
                    raise PermissionError('Asset has unsafe ownership or permissions')
                target = os.path.join(output, name)
                if stat.S_ISDIR(before.st_mode):
                    walk(child, target, depth + 1)
                elif stat.S_ISREG(before.st_mode) and before.st_nlink == 1:
                    if before.st_size > 512 * 1024**2:
                        raise ValueError('Asset file exceeds its size limit')
                    with open(target, 'xb') as stream:
                        count = 0
                        while chunk := os.read(child, 65536):
                            total += len(chunk)
                            count += len(chunk)
                            if total > 1024**3 or count > 512 * 1024**2:
                                raise ValueError('Asset tree exceeds its byte limit')
                            stream.write(chunk)
                    after = os.fstat(child)
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or count != before.st_size:
                        raise ValueError('Asset changed while it was being copied')
                    if not plain:
                        os.chmod(target, 0o644)
                else:
                    raise PermissionError('Asset links and special files are not supported')
            finally:
                os.close(child)
    descriptor = os.open(source, flags | os.O_DIRECTORY)
    try:
        walk(descriptor, destination, 0)
    finally:
        os.close(descriptor)


def _publish_asset_tree(staged, destination):
    if os.path.islink(destination) or os.path.islink(os.path.dirname(destination)):
        raise PermissionError('Asset destination must not be a symbolic link')
    backup = os.path.join(os.path.dirname(staged), 'previous')
    previous = os.path.exists(destination)
    if previous:
        os.rename(destination, backup)
    try:
        os.rename(staged, destination)
    except Exception:
        if previous:
            os.rename(backup, destination)
        raise


# ---------------------------------------------------------------- fonts
SYSTEM_FONTS = "/usr/share/fonts/orbits"
FONT_FILE_RE = re.compile(r"^[^/]+\.(ttf|otf|ttc|otc)$|^(OFL|LICENSE)[^/]*$", re.I)
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")


def cmd_font_install(src_dir, slug):
    if not SLUG_RE.match(slug):
        raise ValueError(f"bad font name {slug!r}")
    if os.path.islink(SYSTEM_FONTS):
        raise PermissionError('System font destination cannot be a link')
    os.makedirs(SYSTEM_FONTS, mode=0o755, exist_ok=True)
    dest = os.path.join(SYSTEM_FONTS, slug)
    with tempfile.TemporaryDirectory(prefix='.orbits-font-', dir=SYSTEM_FONTS) as temporary:
        staged = os.path.join(temporary, 'font')
        _snapshot_user_tree(src_dir, staged)
        names = os.listdir(staged)
        fonts = []
        for name in names:
            path = Path(staged)/name
            if not path.is_file():
                raise ValueError('Font packages must contain regular files only')
            if path.suffix.lower() in FONT_SUFFIXES:
                validate_font(path)
                fonts.append(name)
            elif not name.upper().startswith(('OFL', 'LICENSE', 'COPYING')) and name != '.orbits-integrity.json':
                raise ValueError('Unexpected file in font package')
        if not fonts:
            raise ValueError('No font files to install')
        record_tree(staged)
        _publish_asset_tree(staged, dest)
    relabel(dest)
    # Do not parse newly installed fonts as root. Desktop font caches refresh
    # in the invoking user's session or lazily when Fontconfig sees directory changes.
    log(f'installed {len(fonts)} fonts to {dest}')


def cmd_font_remove(slug):
    if not SLUG_RE.match(slug):
        raise ValueError(f"bad font name {slug!r}")
    dest = os.path.join(SYSTEM_FONTS, slug)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
        log(f"removed {dest}")


# ---------------------------------------------------------------- themes
THEME_BASES = {"themes": "/usr/share/themes", "icons": "/usr/share/icons", "plymouth": "/usr/share/plymouth/themes",
               "sounds": "/usr/share/sounds", "terminal": "/usr/share/orbits/terminal-themes"}
THEME_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+ ()-]{0,100}$")
THEME_MARKER = ".orbits.json"
MAX_THEME_BYTES = 2 << 30


def _theme_dest(base_key, name):
    if base_key not in THEME_BASES:
        raise ValueError(f"unknown theme location {base_key!r}")
    if not THEME_NAME_RE.fullmatch(name):
        raise ValueError(f"bad theme name {name!r}")
    dest = os.path.join(THEME_BASES[base_key], name)
    if os.path.islink(THEME_BASES[base_key]) or os.path.islink(dest):
        raise PermissionError('Theme destination must not be a symbolic link')
    return dest


def _check_tree(src):
    """Every file must belong to the invoking user; symlinks must stay inside the tree."""
    uid, root, total = invoking_uid(), os.path.realpath(src), 0
    st = os.lstat(src)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid:
        raise PermissionError(f"{src} must be a folder you own")
    for dirpath, dirnames, filenames in os.walk(src):
        for n in dirnames + filenames:
            p = os.path.join(dirpath, n)
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                target = os.path.realpath(p)
                if not (target == root or target.startswith(root + os.sep)):
                    raise PermissionError(f"symlink {p} points outside the theme")
            elif stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode):
                if st.st_uid != uid:
                    raise PermissionError(f"{p} must be owned by you")
                total += st.st_size
                if total > MAX_THEME_BYTES:
                    raise ValueError("theme is too large")
            else:
                raise PermissionError(f"{p} is not a regular file")


def cmd_theme_install(base_key, src, name):
    dest = _theme_dest(base_key, name)
    if os.path.exists(dest) and not os.path.exists(os.path.join(dest, THEME_MARKER)):
        raise FileExistsError(f"{dest} exists and wasn't installed by Orbits")
    parent = os.path.dirname(dest)
    os.makedirs(parent, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".orbits-", dir=parent)
    try:
        staged = os.path.join(tmp, "theme")
        _snapshot_user_tree(src, staged)
        validate_theme_tree(staged, base_key)
        for dirpath, dirnames, filenames in os.walk(staged):
            os.chown(dirpath, 0, 0)
            os.chmod(dirpath, 0o755)
            for n in filenames:
                p = os.path.join(dirpath, n)
                if not os.path.islink(p):
                    os.chown(p, 0, 0)
                    os.chmod(p, 0o644)
            for n in dirnames:
                p = os.path.join(dirpath, n)
                if os.path.islink(p):
                    os.lchown(p, 0, 0)
        marker = os.path.join(staged, THEME_MARKER)
        if not os.path.exists(marker):
            with open(marker, "w") as f:
                json.dump({"installed_by": "orbits"}, f)
        record_tree(staged)
        _publish_asset_tree(staged, dest)
        relabel(dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # Leave icon-cache parsing to the unprivileged desktop rather than processing
    # third-party icon resources with root privileges.
    record_tree(dest)
    log(f"installed {dest}")


def plymouth_default():
    try:
        out = subprocess.run(["plymouth-set-default-theme"], capture_output=True, text=True).stdout.strip()
        return out
    except OSError:
        return ""


def _purge_theme_undo(destination):
    """Invalidate retained Undo records that contain the permanently removed theme."""
    root = Path(UNDO_DIR)
    if not root.is_dir() or root.is_symlink():
        return
    target = Path(destination)
    for record in root.iterdir():
        if record.is_symlink() or not record.is_dir() or not re.fullmatch('[0-9a-f]{32}', record.name):
            continue
        manifest = record/'manifest.json'
        if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 2 * 1024**2:
            continue
        try:
            data = json.loads(manifest.read_text())
        except (OSError, ValueError):
            continue
        paths = data.get('paths', []) if isinstance(data, dict) else []
        if any(isinstance(entry, dict) and isinstance(entry.get('path'), str)
               and Path(entry['path']).is_relative_to(target) for entry in paths):
            shutil.rmtree(record)


def cmd_theme_remove(base_key, name):
    dest = _theme_dest(base_key, name)
    if not os.path.isdir(dest):
        _purge_theme_undo(dest)
        return
    if not os.path.exists(os.path.join(dest, THEME_MARKER)):
        raise PermissionError(f"{name} wasn't installed by Orbits; remove it with your package manager")
    if base_key == "plymouth" and plymouth_default() == name:
        raise RuntimeError(f"{name} is the active boot splash; choose another one first")
    shutil.rmtree(dest)
    _purge_theme_undo(dest)
    log(f"removed {dest}")


def rebuild_initramfs():
    if shutil.which("mkinitcpio"):
        cmd = ["mkinitcpio", "-P"]
    elif shutil.which("dracut"):
        cmd = ["dracut", "--force", "--regenerate-all"]
    elif shutil.which("update-initramfs"):
        cmd = ["update-initramfs", "-u", "-k", "all"]
    else:
        raise RuntimeError("no initramfs tool found (mkinitcpio, dracut or update-initramfs)")
    log("rebuilding initramfs: " + " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
        raise RuntimeError("initramfs rebuild failed: " + " / ".join(tail))


def cmd_plymouth_set(name):
    if THEME_NAME_RE.fullmatch(name):
        installed = Path(THEME_BASES['plymouth'])/name
        verify_tree(installed)
        if (installed/THEME_MARKER).is_file():
            validate_theme_tree(installed, 'plymouth')
    dest = _theme_dest("plymouth", name)
    if not os.path.isdir(dest) or not any(f.endswith(".plymouth") for f in os.listdir(dest)):
        raise FileNotFoundError(f"boot splash {name!r} is not installed")
    subprocess.run(["plymouth-set-default-theme", name], check=True)
    rebuild_initramfs()
    log(f"boot splash set to {name}")



# Preview runner is constant code, executed only inside the private namespaces below.
PLYMOUTH_PREVIEW_SCRIPT = r"""
import subprocess
import sys
import time

with open('/run/preview.log', 'w+') as output:
    daemon = subprocess.Popen([sys.argv[1], '--no-daemon', '--no-boot-log', '--debug',
        '--pid-file=/run/plymouth/preview.pid',
        '--kernel-command-line=splash plymouth.ignore-serial-consoles plymouth.ignore-udev'],
        stdout=output, stderr=output)
    try:
        for _ in range(50):
            if daemon.poll() is not None:
                raise RuntimeError('The preview daemon exited before opening its X11 window')
            probe = subprocess.run([sys.argv[2], '--ping'], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=1)
            if probe.returncode == 0:
                break
            time.sleep(.1)
        else:
            raise RuntimeError('The preview daemon did not start')
        subprocess.run([sys.argv[2], 'show-splash'], check=True, timeout=3)
        time.sleep(.3)
        output.flush()
        output.seek(0)
        renderer_log = output.read()
        if not any('opened renderer plugin' in line and 'x11.so' in line for line in renderer_log.splitlines()):
            raise RuntimeError('Plymouth could not open its X11 renderer')
        try:
            daemon.wait(timeout=10)
            raise RuntimeError('The preview daemon exited early')
        except subprocess.TimeoutExpired:
            pass
    except Exception as error:
        output.flush()
        output.seek(0)
        print(str(error) + ': ' + output.read()[-2500:], file=sys.stderr)
        sys.exit(1)
    finally:
        try:
            subprocess.run([sys.argv[2], 'quit'], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if daemon.poll() is None:
            daemon.terminate()
            try:
                daemon.wait(timeout=2)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.wait()
"""


def plymouth_preview_command(name, display, authority_copy, conf_dir):
    # Both the filesystem socket and abstract socket namespace are isolated from the boot daemon.
    if not re.fullmatch(r":[0-9]+(?:\.[0-9]+)?", display):
        raise ValueError("Preview requires a local X11/XWayland display")
    bwrap = shutil.which("bwrap")
    daemon = shutil.which("plymouthd")
    client = shutil.which("plymouth")
    if not all((bwrap, daemon, client)):
        raise RuntimeError("Preview requires bubblewrap, plymouthd and plymouth")
    renderers = [path for pattern in ("/usr/lib/plymouth/renderers/x11.so", "/usr/lib64/plymouth/renderers/x11.so",
                 "/usr/lib/*/plymouth/renderers/x11.so") for path in glob.glob(pattern)]
    if not renderers:
        raise RuntimeError("Plymouth's X11 renderer is missing; install plymouth-x11 (or your distribution's equivalent)")
    number = display[1:].split(".")[0]
    socket = "/tmp/.X11-unix/X" + number
    if not os.path.exists(socket):
        raise RuntimeError("The local X11 display socket was not found")
    return [bwrap, "--die-with-parent", "--unshare-pid", "--unshare-net", "--unshare-ipc", "--unshare-uts",
            "--cap-drop", "ALL", "--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev",
            "--tmpfs", "/sys", "--tmpfs", "/run", "--dir", "/run/plymouth", "--tmpfs", "/tmp",
            "--dir", "/tmp/.X11-unix", "--ro-bind", socket, socket,
            "--ro-bind", conf_dir, "/etc/plymouth", "--ro-bind", authority_copy, "/run/preview.auth",
            "--clearenv", "--setenv", "PATH", "/usr/sbin:/usr/bin:/sbin:/bin",
            "--setenv", "DISPLAY", display, "--setenv", "XAUTHORITY", "/run/preview.auth",
            "--setenv", "GDK_BACKEND", "x11", "--setenv", "HOME", "/run",
            sys.executable, "-I", "-c", PLYMOUTH_PREVIEW_SCRIPT, daemon, client]


def cmd_plymouth_preview(name, display, authority):
    dest = _theme_dest("plymouth", name)
    if not os.path.isfile(os.path.join(dest, name + ".plymouth")):
        raise FileNotFoundError(f"The installed theme needs {name}.plymouth for Plymouth to load it")
    auth = read_user_file(authority, max_bytes=1 << 20)
    with tempfile.TemporaryDirectory(prefix="orbits-plymouth-preview-") as folder:
        conf = os.path.join(folder, "config")
        os.mkdir(conf)
        with open(os.path.join(conf, "plymouthd.conf"), "w") as stream:
            stream.write(f"[Daemon]\nTheme={name}\nShowDelay=0\nDeviceTimeout=0\n")
        authority_copy = os.path.join(folder, "auth")
        with open(authority_copy, "wb") as stream:
            stream.write(auth)
        os.chmod(authority_copy, 0o600)
        command = plymouth_preview_command(name, display, authority_copy, conf)
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("The isolated Plymouth preview timed out") from error
        if result.returncode:
            raise RuntimeError("Plymouth preview failed: " + (result.stderr.strip()[-3000:] or "X11 renderer could not start"))
    log("Plymouth preview finished without changing boot settings")


# ---------------------------------------------------------------- bootloader themes
GRUB_DIRS = ["/boot/grub", "/boot/grub2"]
GRUB_DEFAULT = "/etc/default/grub"
ESP_CANDIDATES = ["/boot", "/efi", "/boot/efi"]
REFIND_SUBDIRS = ["EFI/refind", "EFI/BOOT", "EFI/Boot"]
REFIND_BEGIN = "# >>> Orbits theme (managed by Orbits — change it in the app)"
REFIND_END = "# <<< Orbits theme"
BOOT_MARKER = ".orbits.json"


def grub_dir():
    for d in GRUB_DIRS:
        if os.path.isdir(d) and (os.path.exists(os.path.join(d, "grub.cfg")) or os.path.isdir(os.path.join(d, "themes"))
                                 or os.path.isdir(os.path.join(d, "fonts"))):
            return d
    return None


def refind_dir():
    for esp in ESP_CANDIDATES:
        for sub in REFIND_SUBDIRS:
            d = os.path.join(esp, sub)
            if os.path.isfile(os.path.join(d, "refind.conf")):
                return d
    return None


def _themes_root(loader):
    if loader == "grub":
        d = grub_dir()
        if not d:
            raise FileNotFoundError("GRUB doesn't appear to be installed (no /boot/grub or /boot/grub2)")
        return os.path.join(d, "themes")
    if loader == "refind":
        d = refind_dir()
        if not d:
            raise FileNotFoundError("rEFInd's refind.conf wasn't found on the EFI system partition")
        return os.path.join(d, "themes")
    raise ValueError(f"unsupported bootloader {loader!r}")


def _theme_file(loader):
    return "theme.txt" if loader == "grub" else "theme.conf"


def _grub_default_lines():
    try:
        with open(GRUB_DEFAULT) as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []


def grub_current_theme():
    current = ""
    for line in _grub_default_lines():
        m = re.match(r'^\s*(?:export\s+)?GRUB_THEME\s*=\s*(.*)$', line)
        if m:
            try:
                values = shlex.split(m.group(1), comments=True)
                current = values[0] if values else ""
            except ValueError:
                continue
    return current


def _refind_text(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('rEFInd configuration is not a regular file')
        raw = stream.read(2 * 1024**2 + 1)
    if len(raw) > 2 * 1024**2:
        raise ValueError('rEFInd configuration is too large')
    codec = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8'
    # Preserve UTF-16 byte order and BOM when saving.
    if raw.startswith(b'\xfe\xff'):
        return raw[2:].decode('utf-16-be'), 'utf-16-be-bom'
    return raw.decode(codec), codec


def _refind_tokens(line):
    lexer = shlex.shlex(line, posix=True)
    lexer.whitespace_split = True
    lexer.whitespace += '=,'  # rEFInd also separates unquoted tokens with equals/commas.
    lexer.escape = ''  # EFI paths use backslashes, unlike a Unix shell.
    return list(lexer)


def refind_current_theme():
    directory = refind_dir()
    if not directory:
        return ''
    text, _codec = _refind_text(os.path.join(directory, 'refind.conf'))
    current = ''
    for line in text.splitlines():
        tokens = _refind_tokens(line)
        if len(tokens) == 2 and tokens[0].lower() == 'include':
            path = tokens[1].replace('\\', '/')
            parts = path.split('/')
            if len(parts) == 3 and parts[0].lower() == 'themes' and parts[2].lower() == 'theme.conf':
                current = parts[1]
    return current


def _refind_efi_path(value):
    """Quoted rEFInd tokens preserve slashes; emit native EFI separators explicitly."""
    if any(character in value for character in ('"', '\n', '\r', '\0')):
        raise ValueError('Invalid character in rEFInd resource path')
    return value.replace('/', '\\')


def _prepare_refind_theme(root, name):
    root = Path(root)
    resources = {'icons_dir', 'banner', 'selection_big', 'selection_small', 'font'}
    path = root/'theme.conf'
    text, codec = _refind_text(path)
    result = []
    for line in text.splitlines():
        tokens = _refind_tokens(line)
        if not tokens:
            result.append(line)
            continue
        keyword = tokens[0].lower()
        if keyword == 'include':
            raise ValueError('rEFInd does not support nested theme includes; flatten theme.conf before importing')
        if keyword in resources:
            if len(tokens) != 2:
                raise ValueError('Invalid rEFInd resource directive: ' + keyword)
            value = tokens[1].replace('\\', '/')
            parts = value.strip('/').split('/')
            if 'themes' in parts:
                index = parts.index('themes')
                parts = parts[index + 2:]
            if not parts or any(part in ('', '.', '..') for part in parts) or ':' in value:
                raise ValueError('Invalid rEFInd resource path')
            relative = '/'.join(parts)
            target = root.joinpath(*parts)
            if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
                raise ValueError('rEFInd resource escapes its theme')
            valid = target.is_dir() if keyword == 'icons_dir' else target.is_file()
            if not valid:
                raise ValueError('Missing rEFInd resource: ' + relative)
            efi_path = _refind_efi_path(f'themes/{name}/{relative}')
            result.append(f'{keyword} "{efi_path}"')
        else:
            result.append(line)
    normalized = '\n'.join(result) + '\n'
    if normalized != text:
        _write_refind_text(path, normalized, codec)
    return normalized != text


def _write_refind_text(path, text, codec):
    data = b'\xfe\xff' + text.encode('utf-16-be') if codec == 'utf-16-be-bom' else text.encode(codec)
    path = Path(path)
    if path.is_symlink():
        raise PermissionError('rEFInd configuration cannot be a symbolic link')
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.orbits-refind-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    try:
        # A same-directory rename works on an ESP too; FAT cannot store Unix modes.
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def cmd_boot_list():
    out = {}
    for loader in ("grub", "refind"):
        try:
            root = _themes_root(loader)
        except (FileNotFoundError, ValueError):
            continue
        themes = []
        if os.path.isdir(root):
            for name in sorted(os.listdir(root)):
                d = os.path.join(root, name)
                if os.path.isfile(os.path.join(d, _theme_file(loader))):
                    marker = os.path.join(d, BOOT_MARKER)
                    metadata = {}
                    try:
                        descriptor = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW)
                        with os.fdopen(descriptor) as stream:
                            record = json.loads(stream.read(65536))
                        if isinstance(record, dict):
                            metadata = {key: record[key] for key in ("key", "page", "title")
                                        if isinstance(record.get(key), str)}
                    except (OSError, ValueError):
                        pass
                    themes.append({"name": name, "managed": os.path.exists(marker), "metadata": metadata})
        if loader == "grub":
            cur = grub_current_theme()
            current = os.path.basename(os.path.dirname(cur)) if cur else ""
        else:
            current = refind_current_theme()
        out[loader] = {"themes_dir": root, "themes": themes, "current": current}
    print(json.dumps(out), flush=True)


def _copy_plain(src, dest):
    """Copy a theme tree without symlinks or ownership (the ESP is usually FAT32)."""
    shutil.copytree(src, dest, symlinks=False, copy_function=shutil.copyfile)


def cmd_boot_theme_install(loader, src, name):
    root = _themes_root(loader)
    dest = _theme_dest_in(root, name)
    if not os.path.isfile(os.path.join(src, _theme_file(loader))):
        raise ValueError(f"{src} has no {_theme_file(loader)}")
    if os.path.exists(dest) and not os.path.exists(os.path.join(dest, BOOT_MARKER)):
        raise FileExistsError(f"{dest} exists and wasn't installed by Orbits")
    os.makedirs(root, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".orbits-", dir=root)
    tmp = os.path.join(staging, "theme")
    try:
        _snapshot_user_tree(src, tmp, plain=loader == "refind")
        if not os.path.isfile(os.path.join(tmp, _theme_file(loader))):
            raise ValueError('Boot theme configuration is missing from staged copy')
        validate_theme_tree(tmp, loader)
        if loader == "refind":
            _prepare_refind_theme(tmp, name)
        if not os.path.exists(os.path.join(tmp, BOOT_MARKER)):
            with open(os.path.join(tmp, BOOT_MARKER), "w") as f:
                json.dump({"installed_by": "orbits"}, f)
        record_tree(tmp, plain=loader == "refind")
        _publish_asset_tree(tmp, dest)
        relabel(dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    log(f"installed {dest}")


def _theme_dest_in(root, name):
    if not THEME_NAME_RE.fullmatch(name):
        raise ValueError(f"bad theme name {name!r}")
    dest = os.path.join(root, name)
    if os.path.islink(root) or os.path.islink(dest):
        raise PermissionError("boot theme destinations must not be symbolic links")
    return dest


def _backup_once(path):
    backup = path + ".orbits-backup"
    if os.path.exists(path) and not os.path.exists(backup):
        shutil.copy2(path, backup)
        log(f"backed up {path}")


def _set_grub_theme(value):
    """Set (or with None, undo) GRUB_THEME in /etc/default/grub. Only active lines are touched."""
    lines = _grub_default_lines()
    if not lines and not os.path.exists(GRUB_DEFAULT):
        raise FileNotFoundError(f"{GRUB_DEFAULT} not found")
    _backup_once(GRUB_DEFAULT)
    disabled = "# Orbits: disabled so the graphical theme can show — "
    active_theme = re.compile(r"^\s*(?:export\s+)?GRUB_THEME\s*=")
    out, placed = [], False
    for line in lines:
        if active_theme.match(line):
            if value is not None and not placed:
                out.append(f'GRUB_THEME="{value}"')
                placed = True
            continue  # drop other / previous active theme lines
        if value is not None and re.match(r'^\s*GRUB_TERMINAL_OUTPUT\s*=\s*"?console"?', line):
            out.append(disabled + line.strip())
            continue
        if value is None and line.startswith(disabled):
            out.append(line[len(disabled):])
            continue
        out.append(line)
    if value is not None and not placed:
        out.append(f'GRUB_THEME="{value}"')
    if value is None:
        try:  # bring back the theme the user had before Orbits, if any
            with open(GRUB_DEFAULT + ".orbits-backup") as f:
                out += [ln for ln in f.read().splitlines() if active_theme.match(ln)]
        except FileNotFoundError:
            pass
    atomic_write(GRUB_DEFAULT, ("\n".join(out) + "\n").encode())


def _change_grub_theme(value):
    # Keep the settings and generated menu consistent when generation fails.
    with open(GRUB_DEFAULT, "rb") as f:
        previous = f.read()
    cfg = os.path.join(grub_dir(), "grub.cfg")
    old_cfg = None
    if os.path.isfile(cfg):
        with open(cfg, "rb") as f:
            old_cfg = f.read()
    try:
        _set_grub_theme(value)
        grub_mkconfig()
    except Exception:
        atomic_write(GRUB_DEFAULT, previous)
        if old_cfg is not None:
            atomic_write(cfg, old_cfg)
        elif os.path.exists(cfg):
            os.unlink(cfg)
        raise


def grub_mkconfig():
    d = grub_dir()
    if shutil.which("update-grub"):
        cmd = ["update-grub"]
    elif shutil.which("grub-mkconfig"):
        cmd = ["grub-mkconfig", "-o", os.path.join(d, "grub.cfg")]
    elif shutil.which("grub2-mkconfig"):
        cmd = ["grub2-mkconfig", "-o", os.path.join(d, "grub.cfg")]
    else:
        raise RuntimeError("no grub-mkconfig / grub2-mkconfig / update-grub found")
    log("regenerating GRUB config: " + " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
        raise RuntimeError("GRUB config regeneration failed: " + " / ".join(tail))


REFIND_APPEARANCE = frozenset({
    'icons_dir', 'banner', 'banner_scale', 'selection_big', 'selection_small',
    'font', 'hideui', 'big_icon_size', 'small_icon_size',
})


def _refind_builtin_config(directory, text):
    """Disable custom appearance while leaving boot entries and unrelated includes intact."""
    root = Path(directory).resolve()
    output = []
    for line in text.splitlines(keepends=True):
        tokens = _refind_tokens(line)
        if not tokens:
            output.append(line)
            continue
        keyword = tokens[0].lower()
        disable = keyword in REFIND_APPEARANCE
        if keyword == 'include' and len(tokens) == 2:
            relative = tokens[1].replace('\\', '/')
            parts = relative.strip('/').split('/')
            if any(part in ('', '.', '..') for part in parts) or ':' in relative or relative.startswith('/'):
                raise ValueError('Review external rEFInd includes before restoring the built-in theme')
            target = root.joinpath(*parts)
            if target.is_symlink() or not target.resolve().is_relative_to(root):
                raise ValueError('Review linked or external rEFInd includes before restoring the built-in theme')
            directives = []
            if target.is_file():
                contents, _codec = _refind_text(target)
                directives = [parsed[0].lower() for included in contents.splitlines()
                              if (parsed := _refind_tokens(included))]
            disable = parts[0].lower() == 'themes' or bool(REFIND_APPEARANCE.intersection(directives))
            if disable and (set(directives) - REFIND_APPEARANCE - {'use_graphics_for', 'showtools'}):
                raise ValueError('A theme include also contains boot entries or other settings; separate its appearance settings before restoring defaults: '+relative)
        output.append('# Orbits default: '+line if disable else line)
    return ''.join(output)


def _set_refind_include(name, *, builtin=False):
    directory = refind_dir()
    if not directory:
        raise FileNotFoundError('rEFInd configuration was not found')
    conf = Path(directory)/'refind.conf'
    text, codec = _refind_text(conf)
    lines = text.splitlines(keepends=True)
    out, inside, blocks = [], False, 0
    for line in lines:
        if line.strip() == REFIND_BEGIN:
            if inside or blocks:
                raise ValueError('rEFInd configuration has multiple or nested Orbits blocks')
            inside, blocks = True, blocks + 1
            if out and not out[-1].strip():
                out.pop()  # Separator written by older Orbits versions.
        elif line.strip() == REFIND_END:
            if not inside:
                raise ValueError('rEFInd configuration has an unmatched Orbits block')
            inside = False
        elif not inside:
            out.append(line)
    if inside:
        raise ValueError('rEFInd configuration has an unterminated Orbits block')
    backup = Path(str(conf) + '.orbits-backup')
    if backup.is_symlink():
        raise PermissionError('rEFInd backup cannot be a symbolic link')
    if not backup.exists():
        with backup.open('xb') as stream:
            stream.write(conf.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
    result = ''.join(out)
    newline = '\r\n' if '\r\n' in text else '\n'
    if name is not None:
        if not THEME_NAME_RE.fullmatch(name):
            raise ValueError('Invalid rEFInd theme name')
        if result and not result.endswith(('\n', '\r')):
            result += newline
        efi_path = _refind_efi_path(f'themes/{name}/theme.conf')
        result += REFIND_BEGIN + newline + f'include "{efi_path}"' + newline + REFIND_END + newline
    else:
        original, _ = _refind_text(backup)
        if result == original + newline:
            result = original
    if builtin:
        result = _refind_builtin_config(directory, result)
    _write_refind_text(conf, result, codec)
    log(f'updated {conf}')


def cmd_boot_theme_apply(loader, name):
    root = _themes_root(loader)
    dest = _theme_dest_in(root, name)
    if not os.path.isfile(os.path.join(dest, _theme_file(loader))):
        raise FileNotFoundError(f"{name} isn't installed for {loader}")
    verified = verify_tree(dest)
    if loader == "grub":
        _change_grub_theme(os.path.join(dest, "theme.txt"))
    else:
        theme_config = Path(dest)/'theme.conf'
        manifest = Path(dest)/INTEGRITY_MANIFEST
        repair = verified is True and (Path(dest)/BOOT_MARKER).is_file()
        original = _refind_text(theme_config) if repair else None
        original_manifest = manifest.read_bytes() if repair else None
        try:
            if repair:
                validate_theme_tree(dest, 'refind')
                if _prepare_refind_theme(dest, name):
                    record_tree(dest, plain=True)
            _set_refind_include(name)
        except Exception:
            if repair:
                _write_refind_text(theme_config, *original)
                manifest.write_bytes(original_manifest)
            raise
    log(f"{loader} theme set to {name}")


def cmd_boot_theme_default(loader):
    if loader == 'refind':
        _set_refind_include(None, builtin=True)
    elif loader == 'grub':
        cmd_boot_theme_reset(loader)
    else:
        raise ValueError(loader)
    log(f'{loader} default theme restored')


def cmd_boot_theme_reset(loader):
    if loader == "grub":
        _change_grub_theme(None)
    elif loader == "refind":
        _set_refind_include(None)
    else:
        raise ValueError(loader)
    log(f"{loader} theme reset")


def cmd_boot_theme_remove(loader, name):
    root = _themes_root(loader)
    dest = _theme_dest_in(root, name)
    if not os.path.isdir(dest):
        _purge_theme_undo(dest)
        return
    if not os.path.exists(os.path.join(dest, BOOT_MARKER)):
        raise PermissionError(f"{name} wasn't installed by Orbits")
    current = refind_current_theme() if loader == "refind" else os.path.basename(
        os.path.dirname(grub_current_theme() or "/x/"))
    if current == name:
        cmd_boot_theme_reset(loader)
    shutil.rmtree(dest)
    _purge_theme_undo(dest)
    log(f"removed {dest}")


# ---------------------------------------------------------------- update persistence
UPDATE_STATE = "/var/lib/orbits/gdm"
UPDATE_RUNNER = "/usr/local/libexec/orbits-gdm-update"
PACMAN_HOOK = "/etc/pacman.d/hooks/95-orbits-gdm.hook"
APT_HOOK = "/etc/apt/apt.conf.d/99orbits-gdm"
UPDATE_SERVICE = "/etc/systemd/system/orbits-gdm-reapply.service"
UPDATE_PATH = "/etc/systemd/system/orbits-gdm-reapply.path"


def disable_updates():
    if os.path.exists(UPDATE_PATH) and shutil.which("systemctl"):
        subprocess.run(["systemctl", "disable", "--now", "orbits-gdm-reapply.path"], check=True)
    for path in (PACMAN_HOOK, APT_HOOK, UPDATE_SERVICE, UPDATE_PATH, UPDATE_RUNNER):
        if os.path.exists(path):
            os.unlink(path)
    shutil.rmtree(UPDATE_STATE, ignore_errors=True)
    if shutil.which("systemctl") and os.path.isdir("/run/systemd/system"):
        subprocess.run(["systemctl", "daemon-reload"], check=True)


def save_update_theme(theme, enabled):
    """Persist only theme data in root-owned storage; hooks never read user configuration."""
    if not enabled or not theme:
        disable_updates()
        return
    pacman = bool(shutil.which("pacman"))
    apt = os.path.isdir("/etc/apt/apt.conf.d")
    systemd = os.path.isdir("/run/systemd/system") and bool(shutil.which("systemctl"))
    if not (pacman or apt or systemd):
        raise RuntimeError("Automatic reapplication requires pacman, apt or systemd")
    os.makedirs(UPDATE_STATE, mode=0o700, exist_ok=True)
    saved = {"css": theme.get("css", ""), "background": None}
    images = theme.get("background")
    if images:
        images = images if isinstance(images, dict) else {"default": images}
        saved["background"] = {}
        for variant, source in images.items():
            if variant not in ("default", "light", "dark"):
                raise ValueError("Invalid background variant")
            target = os.path.join(UPDATE_STATE, f"background-{variant}.jpg")
            atomic_write(target, read_user_file(source), mode=0o600)
            saved["background"][variant] = target
    if os.path.exists(ALT_BASE_FILE):
        with open(ALT_BASE_FILE) as source:
            base = source.read().strip()
        if os.path.isfile(base):
            with open(base, "rb") as source:
                saved["base_digest"] = hashlib.sha256(source.read()).hexdigest()
    atomic_write(os.path.join(UPDATE_STATE, "theme.json"), json.dumps(saved).encode(), mode=0o600)
    # Copy the self-contained helper. A checkout or user-writable launcher must never run on updates.
    os.makedirs(os.path.dirname(UPDATE_RUNNER), exist_ok=True)
    with open(__file__, "rb") as source:
        atomic_write(UPDATE_RUNNER, source.read(), mode=0o755)
    command = f"/usr/bin/python3 -I {UPDATE_RUNNER} reapply"
    if pacman:
        os.makedirs(os.path.dirname(PACMAN_HOOK), exist_ok=True)
        atomic_write(PACMAN_HOOK, ("[Trigger]\nOperation = Install\nOperation = Upgrade\n"
            "Type = Package\nTarget = gnome-shell\nTarget = gdm\nTarget = yaru-gnome-shell-theme\n"
            "\n[Action]\nDescription = Reapplying Orbits login theme\nWhen = PostTransaction\n"
            f"Exec = {command}\n").encode())
    if apt:
        atomic_write(APT_HOOK, f'DPkg::Post-Invoke {{ "{command} || true"; }};\n'.encode())
    if systemd:
        watched = {GRESOURCE, active_theme_file()}
        # In alternatives mode the recorded distribution resource may be updated in place.
        if os.path.exists(ALT_BASE_FILE):
            with open(ALT_BASE_FILE) as base:
                watched.add(base.read().strip())
        atomic_write(UPDATE_SERVICE, ("[Unit]\nDescription=Reapply Orbits GDM theme after updates\n"
            "StartLimitIntervalSec=120\nStartLimitBurst=3\n[Service]\nType=oneshot\nRestart=on-failure\nRestartSec=10\n" + f"ExecStart={command}\n").encode())
        atomic_write(UPDATE_PATH, ("[Unit]\nDescription=Watch GDM theme updates (including DNF/RPM)\n"
            "[Path]\n" + "".join(f"PathChanged={path}\n" for path in sorted(watched) if path) +
            "Unit=orbits-gdm-reapply.service\n[Install]\nWantedBy=multi-user.target\n").encode())
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", "--now", "orbits-gdm-reapply.path"], check=True)


def cmd_reapply():
    """Refresh on the latest distribution resource, without replaying login/auth settings."""
    if invoking_uid() != 0:
        raise PermissionError("Update reapplication only runs from a root package hook or service")
    manifest = os.path.join(UPDATE_STATE, "theme.json")
    if not os.path.isfile(manifest):
        return
    with open(manifest) as source:
        theme = json.load(source)
    active = active_theme_file()
    if is_ours(active):
        # An alternatives link can still select Orbits while its distribution base was upgraded.
        if os.path.realpath(active) != os.path.realpath(OUR_GRES) or not os.path.exists(ALT_BASE_FILE):
            return
        with open(ALT_BASE_FILE) as source:
            base = source.read().strip()
        if not os.path.isfile(base):
            raise FileNotFoundError("The original distribution theme is missing")
        with open(base, "rb") as source:
            digest = hashlib.sha256(source.read()).hexdigest()
        if digest == theme.get("base_digest"):
            return
        theme["base_digest"] = digest
    apply_theme(theme.get("css", ""), theme.get("background"))
    atomic_write(manifest, json.dumps(theme).encode(), mode=0o600)


# ---------------------------------------------------------------- authenticated undo
UNDO_DIR = "/var/lib/orbits/undo"


def undo_targets(argv):
    command = argv[1] if len(argv) > 1 else ""
    if command in ('theme-remove', 'boot-theme-remove'):
        return [], ''  # A clean theme uninstall must not retain a full Undo copy.
    paths, refresh = [], ""
    if command in ("apply", "reset"):
        paths = [GRESOURCE, BACKUP, OUR_DIR, DCONF_FILE, DCONF_PROFILE, gdm_conf_path(), DATA_DIR,
                 UPDATE_STATE, UPDATE_RUNNER, PACMAN_HOOK, APT_HOOK, UPDATE_SERVICE, UPDATE_PATH]
        for link, name in ALTERNATIVES:
            paths.extend([link, "/etc/alternatives/"+name, "/var/lib/dpkg/alternatives/"+name,
                          "/var/lib/alternatives/"+name])
        refresh = "gdm"
    elif command == "font-remove" and len(argv) == 3:
        if not SLUG_RE.fullmatch(argv[2]):
            raise ValueError("Invalid font name")
        paths = [os.path.join(SYSTEM_FONTS, argv[2])]
        refresh = "fonts"
    elif command == "plymouth-set" and len(argv) == 3:
        paths = ["/etc/plymouth/plymouthd.conf", "/usr/share/plymouth/themes/default.plymouth",
                 "/etc/alternatives/default.plymouth", "/var/lib/dpkg/alternatives/default.plymouth"]
        refresh = "plymouth"
    elif command in ("boot-theme-apply", "boot-theme-reset", "boot-theme-default") and len(argv) >= 3:
        loader = argv[2]
        if loader == "grub":
            paths = [GRUB_DEFAULT, os.path.join(grub_dir(), "grub.cfg")]
        elif loader == "refind":
            paths = [os.path.join(refind_dir(), "refind.conf")]
        else:
            raise ValueError("Unknown bootloader")
        if command == "boot-theme-apply" and loader == "refind" and len(argv) == 4:
            theme = _theme_dest_in(_themes_root(loader), argv[3])
            paths.extend([os.path.join(theme, 'theme.conf'), os.path.join(theme, INTEGRITY_MANIFEST)])
    return list(dict.fromkeys(paths)), refresh


def _undo_copy(source, destination):
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    if os.path.islink(source):
        os.symlink(os.readlink(source), destination)
    elif os.path.isdir(source):
        shutil.copytree(source, destination, symlinks=True)
    else:
        shutil.copy2(source, destination)


def capture_undo(argv):
    paths, refresh = undo_targets(argv)
    if not paths:
        return None
    import uuid
    os.makedirs(UNDO_DIR, mode=0o700, exist_ok=True)
    token = uuid.uuid4().hex
    directory = os.path.join(UNDO_DIR, token)
    os.mkdir(directory, mode=0o700)
    entries = []
    try:
        for index, path in enumerate(paths):
            exists = os.path.lexists(path)
            if exists:
                _undo_copy(path, os.path.join(directory, str(index)))
            entries.append({"path":path, "exists":exists})
        manifest = {"uid":invoking_uid(), "paths":entries, "refresh":refresh}
        atomic_write(os.path.join(directory, "manifest.json"), json.dumps(manifest).encode(), mode=0o600)
        return token
    except Exception:
        shutil.rmtree(directory)
        raise


def cmd_undo(token):
    if not re.fullmatch(r"[a-f0-9]{32}", token):
        raise ValueError("Invalid undo identifier")
    directory = os.path.join(UNDO_DIR, token)
    with open(os.path.join(directory, "manifest.json")) as source:
        manifest = json.load(source)
    if manifest["uid"] != invoking_uid():
        raise PermissionError("Only the user who made this change may undo it")
    for index, entry in enumerate(manifest["paths"]):
        path = entry["path"]
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        elif os.path.lexists(path):
            os.unlink(path)
        if entry["exists"]:
            _undo_copy(os.path.join(directory, str(index)), path)
            relabel(path)
    refresh = manifest.get("refresh")
    if refresh == "gdm":
        dconf_update()
        if os.path.isdir("/run/systemd/system") and shutil.which("systemctl"):
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "enable" if os.path.exists(UPDATE_PATH) else "disable",
                            "--now", "orbits-gdm-reapply.path"], check=False)
    elif refresh == "fonts":
        pass  # User-session Fontconfig handles cache refresh after undo.
    elif refresh == "plymouth":
        rebuild_initramfs()
    shutil.rmtree(directory)
    log("change undone")


# ---------------------------------------------------------------- entry points
def cmd_apply(spec_path):
    spec = json.loads(read_user_file(spec_path))
    theme = spec.get("theme")
    if theme:
        apply_theme(theme.get("css", ""), theme.get("background"))
    else:
        restore_theme()

    dconf = spec.get("dconf") or {}
    logo = spec.get("logo_source")
    if logo:
        dest = install_logo(logo)
        dconf.setdefault("org/gnome/login-screen", {})["logo"] = GLib.Variant("s", dest).print_(True)
    if dconf:
        write_dconf(dconf)
    elif os.path.exists(DCONF_FILE):
        os.unlink(DCONF_FILE)
        dconf_update()

    if spec.get("custom_conf") is not None:
        edit_custom_conf(spec["custom_conf"])
    save_update_theme(theme, bool(spec.get("auto_reapply")))


def cmd_reset():
    disable_updates()
    restore_theme()
    if os.path.exists(DCONF_FILE):
        os.unlink(DCONF_FILE)
        dconf_update()
    if os.path.isdir(DATA_DIR):
        for name in os.listdir(DATA_DIR):
            if name.startswith("gdm-logo"):
                os.unlink(os.path.join(DATA_DIR, name))
        if not os.listdir(DATA_DIR):
            os.rmdir(DATA_DIR)
    if os.path.exists(gdm_conf_path()):
        edit_custom_conf({"AutomaticLoginEnable": None, "AutomaticLogin": None})


def main(argv):
    if os.geteuid() != 0:
        print("This helper must run as root (via pkexec).", file=sys.stderr)
        return 2
    lock = None
    undo_token = None
    success = False
    try:
        if len(argv) >= 2 and argv[1] not in ("boot-list", "plymouth-preview"):
            import fcntl
            lock = open("/run/lock/orbits-gdm.lock", "a")
            fcntl.flock(lock, fcntl.LOCK_EX)
        if len(argv) >= 2 and argv[1] in ("apply", "reset", "reapply", "updates-disable", "undo"):
            require_writable(SHELL_DIR, "/etc")
        elif len(argv) >= 2 and argv[1].startswith("font-"):
            require_writable(SYSTEM_FONTS)
        elif len(argv) >= 3 and argv[1].startswith("theme-"):
            require_writable(THEME_BASES.get(argv[2], "/usr/share"))
        elif len(argv) >= 2 and argv[1] == "plymouth-set":
            require_writable(THEME_BASES["plymouth"], "/boot")
        elif len(argv) >= 3 and argv[1].startswith("boot-theme"):
            require_writable(_themes_root(argv[2]))
            if argv[2] == "grub":
                require_writable(os.path.dirname(GRUB_DEFAULT))
        undo_token = capture_undo(argv)
        if len(argv) == 3 and argv[1] == "undo":
            cmd_undo(argv[2])
        elif len(argv) == 3 and argv[1] == "apply":
            cmd_apply(argv[2])
        elif len(argv) == 2 and argv[1] == "reapply":
            cmd_reapply()
        elif len(argv) == 2 and argv[1] == "updates-disable":
            disable_updates()
        elif len(argv) == 2 and argv[1] == "reset":
            cmd_reset()
        elif len(argv) == 4 and argv[1] == "font-install":
            cmd_font_install(argv[2], argv[3])
        elif len(argv) == 3 and argv[1] == "font-remove":
            cmd_font_remove(argv[2])
        elif len(argv) == 5 and argv[1] == "theme-install":
            cmd_theme_install(argv[2], argv[3], argv[4])
        elif len(argv) == 4 and argv[1] == "theme-remove":
            cmd_theme_remove(argv[2], argv[3])
        elif len(argv) == 3 and argv[1] == "plymouth-set":
            cmd_plymouth_set(argv[2])
        elif len(argv) == 5 and argv[1] == "plymouth-preview":
            cmd_plymouth_preview(argv[2], argv[3], argv[4])
        elif len(argv) == 2 and argv[1] == "boot-list":
            cmd_boot_list()
            return 0
        elif len(argv) == 5 and argv[1] == "boot-theme-install":
            cmd_boot_theme_install(argv[2], argv[3], argv[4])
        elif len(argv) == 4 and argv[1] == "boot-theme-apply":
            cmd_boot_theme_apply(argv[2], argv[3])
        elif len(argv) == 4 and argv[1] == "boot-theme-remove":
            cmd_boot_theme_remove(argv[2], argv[3])
        elif len(argv) == 3 and argv[1] == "boot-theme-default":
            cmd_boot_theme_default(argv[2])
        elif len(argv) == 3 and argv[1] == "boot-theme-reset":
            cmd_boot_theme_reset(argv[2])
        else:
            print(__doc__, file=sys.stderr)
            return 2
        success = True
        if undo_token:
            log("ORBITS_UNDO=" + undo_token)
            # Keep only a small, root-owned history.
            entries = sorted((os.path.join(UNDO_DIR, name) for name in os.listdir(UNDO_DIR)), key=os.path.getmtime)
            for old in entries[:-10]:
                if os.path.isdir(old):
                    shutil.rmtree(old)
    except Exception as e:  # report cleanly to the UI
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        if undo_token and not success:
            shutil.rmtree(os.path.join(UNDO_DIR, undo_token), ignore_errors=True)
        if lock is not None:
            lock.close()
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
