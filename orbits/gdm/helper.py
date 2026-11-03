#!/usr/bin/python3 -I
"""Orbits privileged helper — run through pkexec, so it must stay self-contained (stdlib + gi only).

usage: helper.py apply <spec.json>
       helper.py reset
       helper.py font-install <dir> <slug>
       helper.py font-remove <slug>

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
THEMED_CSS = re.compile(r"^gnome-shell(-[a-z-]+)?\.css$")

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


def atomic_write(path, data: bytes, mode=0o644):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".orbits-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
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


def apply_theme(css, background):
    if not os.path.exists(GRESOURCE):
        raise FileNotFoundError(f"{GRESOURCE} not found — is GNOME Shell installed?")
    if not is_ours(GRESOURCE):
        # Current file is pristine (or came from a package update / another tool): it becomes the base.
        shutil.copy2(GRESOURCE, BACKUP)
        log("backed up original shell theme")
    elif not os.path.exists(BACKUP):
        raise RuntimeError("Theme was modified by Orbits but the backup is missing; reinstall gnome-shell")

    base = Gio.Resource.load(BACKUP)
    with tempfile.TemporaryDirectory(prefix="orbits-theme-") as tmp:
        names = []
        for rpath in walk(base, PREFIX + "/"):
            rel = rpath[len(PREFIX) + 1:]
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
            f.write("Modified by Orbits. Original saved as gnome-shell-theme.gresource.orbits-backup\n")
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
            atomic_write(GRESOURCE, f.read())
    log("installed custom shell theme")


def restore_theme():
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
        if len(argv) == 3 and argv[1] == "apply":
            cmd_apply(argv[2])
        elif len(argv) == 2 and argv[1] == "reset":
            cmd_reset()
        elif len(argv) == 4 and argv[1] == "font-install":
            cmd_font_install(argv[2], argv[3])
        elif len(argv) == 3 and argv[1] == "font-remove":
            cmd_font_remove(argv[2])
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
