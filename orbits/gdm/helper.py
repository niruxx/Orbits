#!/usr/bin/python3 -I
"""Orbits privileged helper — run through pkexec, so it must stay self-contained (stdlib + gi only).

usage: helper.py apply <spec.json>
       helper.py reset
       helper.py font-install <dir> <slug>
       helper.py font-remove <slug>
       helper.py theme-install <themes|icons|plymouth> <dir> <name>
       helper.py theme-remove <themes|icons|plymouth> <name>
       helper.py plymouth-set <name>

The spec is produced by orbits.gdm.model.build_spec().  Everything the helper writes is tagged so a
reset returns the system to exactly what it was before Orbits touched it.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

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
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
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
            return f.read()
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
    """True if path sits on a read-only mount (Silverblue, NixOS, SteamOS…)."""
    p = path
    while not os.path.exists(p):
        p = os.path.dirname(p)
    return bool(os.statvfs(p).f_flag & os.ST_RDONLY)


def require_writable(*paths):
    for p in paths:
        if read_only(p):
            raise PermissionError(f"{p} is on a read-only filesystem (immutable system); "
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
            if rel == "orbits.marker" or rel == BG_NAME:
                continue
            data = base.lookup_data(rpath, Gio.ResourceLookupFlags.NONE).get_data()
            if THEMED_CSS.match(os.path.basename(rel)) and css:
                data = data + b"\n\n/* ---- Orbits ---- */\n" + css.encode()
            out = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as f:
                f.write(data)
            names.append(rel)
        if background:
            with open(os.path.join(tmp, BG_NAME), "wb") as f:
                f.write(read_user_file(background))
            names.append(BG_NAME)
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


# ---------------------------------------------------------------- fonts
SYSTEM_FONTS = "/usr/share/fonts/orbits"
FONT_FILE_RE = re.compile(r"^[^/]+\.(ttf|otf|ttc|otc)$|^(OFL|LICENSE)[^/]*$", re.I)
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")


def cmd_font_install(src_dir, slug):
    if not SLUG_RE.match(slug):
        raise ValueError(f"bad font name {slug!r}")
    names = [n for n in os.listdir(src_dir) if FONT_FILE_RE.match(n)]
    if not any(not n.upper().startswith(("OFL", "LICENSE")) for n in names):
        raise ValueError("no font files to install")
    dest = os.path.join(SYSTEM_FONTS, slug)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest, mode=0o755)
    for n in names:
        atomic_write(os.path.join(dest, n), read_user_file(os.path.join(src_dir, n), 256 << 20))
    os.chmod(SYSTEM_FONTS, 0o755)
    relabel(dest)
    subprocess.run(["fc-cache", "-f", SYSTEM_FONTS], check=False)
    log(f"installed {len(names)} files to {dest}")


def cmd_font_remove(slug):
    if not SLUG_RE.match(slug):
        raise ValueError(f"bad font name {slug!r}")
    dest = os.path.join(SYSTEM_FONTS, slug)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
        subprocess.run(["fc-cache", "-f"], check=False)
        log(f"removed {dest}")


# ---------------------------------------------------------------- themes
THEME_BASES = {"themes": "/usr/share/themes", "icons": "/usr/share/icons", "plymouth": "/usr/share/plymouth/themes"}
THEME_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+ ()-]{0,100}$")
THEME_MARKER = ".orbits.json"
MAX_THEME_BYTES = 2 << 30


def _theme_dest(base_key, name):
    if base_key not in THEME_BASES:
        raise ValueError(f"unknown theme location {base_key!r}")
    if not THEME_NAME_RE.match(name):
        raise ValueError(f"bad theme name {name!r}")
    return os.path.join(THEME_BASES[base_key], name)


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
    _check_tree(src)
    parent = os.path.dirname(dest)
    os.makedirs(parent, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".orbits-", dir=parent)
    try:
        staged = os.path.join(tmp, "theme")
        shutil.copytree(src, staged, symlinks=True)
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
        if os.path.exists(dest):
            shutil.rmtree(dest)
        os.rename(staged, dest)
        relabel(dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if base_key == "icons" and shutil.which("gtk-update-icon-cache") and os.path.exists(
            os.path.join(dest, "index.theme")):
        subprocess.run(["gtk-update-icon-cache", "-q", "-f", "-t", dest], check=False)
    log(f"installed {dest}")


def plymouth_default():
    try:
        out = subprocess.run(["plymouth-set-default-theme"], capture_output=True, text=True).stdout.strip()
        return out
    except OSError:
        return ""


def cmd_theme_remove(base_key, name):
    dest = _theme_dest(base_key, name)
    if not os.path.isdir(dest):
        return
    if not os.path.exists(os.path.join(dest, THEME_MARKER)):
        raise PermissionError(f"{name} wasn't installed by Orbits; remove it with your package manager")
    if base_key == "plymouth" and plymouth_default() == name:
        raise RuntimeError(f"{name} is the active boot splash; choose another one first")
    shutil.rmtree(dest)
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
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
        raise RuntimeError("initramfs rebuild failed: " + " / ".join(tail))


def cmd_plymouth_set(name):
    dest = _theme_dest("plymouth", name)
    if not os.path.isdir(dest) or not any(f.endswith(".plymouth") for f in os.listdir(dest)):
        raise FileNotFoundError(f"boot splash {name!r} is not installed")
    subprocess.run(["plymouth-set-default-theme", name], check=True)
    rebuild_initramfs()
    log(f"boot splash set to {name}")


# ---------------------------------------------------------------- entry points
def cmd_apply(spec_path):
    with open(spec_path) as f:
        spec = json.load(f)
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


def cmd_reset():
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
    edit_custom_conf({"AutomaticLoginEnable": None, "AutomaticLogin": None})


def main(argv):
    if os.geteuid() != 0:
        print("This helper must run as root (via pkexec).", file=sys.stderr)
        return 2
    try:
        if len(argv) >= 2 and argv[1] in ("apply", "reset"):
            require_writable(SHELL_DIR, "/etc")
        elif len(argv) >= 2 and argv[1].startswith("font-"):
            require_writable(SYSTEM_FONTS)
        elif len(argv) >= 3 and argv[1].startswith("theme-"):
            require_writable(THEME_BASES.get(argv[2], "/usr/share"))
        elif len(argv) >= 2 and argv[1] == "plymouth-set":
            require_writable(THEME_BASES["plymouth"], "/boot")
        if len(argv) == 3 and argv[1] == "apply":
            cmd_apply(argv[2])
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
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except Exception as e:  # report cleanly to the UI
        print(f"error: {e}", file=sys.stderr)
        return 1
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
