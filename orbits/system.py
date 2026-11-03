"""What this system supports: drives the System Check window and feature warnings."""
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path




@dataclass
class Check:
    area: str      # Login Screen, Boot Splash, Themes, Fonts, General
    title: str
    ok: bool
    detail: str = ""
    level: str = ""   # "" (ok/error by ok flag) or "info" for neutral facts


def os_release():
    info = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                info[k] = v.strip().strip('"')
    except OSError:
        pass
    return info


def _gnome_shell_version():
    try:
        out = subprocess.run(["gnome-shell", "--version"], capture_output=True, text=True, timeout=5).stdout
        return out.strip().replace("GNOME Shell ", "")
    except (OSError, subprocess.TimeoutExpired):
        return ""


def usr_read_only():
    try:
        return bool(os.statvfs("/usr").f_flag & os.ST_RDONLY)
    except OSError:
        return False


def _mkinitcpio_hooks():
    hooks = ""
    for path in [Path("/etc/mkinitcpio.conf"), *sorted(Path("/etc/mkinitcpio.conf.d").glob("*.conf"))]:
        try:
            for line in path.read_text().splitlines():
                if line.strip().startswith("HOOKS="):
                    hooks = line
        except OSError:
            pass
    return hooks


def run_checks() -> list[Check]:
    from orbits.gdm import helper
    from orbits.privileged import POLICY

    rel = os_release()
    checks = [Check("General", "System", True, f"{rel.get('PRETTY_NAME', 'Unknown Linux')}", "info")]
    shell = _gnome_shell_version()
    checks.append(Check("General", "GNOME Shell", bool(shell), shell or "Not found — most features need GNOME"))
    checks.append(Check("General", "Administrator access (pkexec)", bool(shutil.which("pkexec")),
                        "" if shutil.which("pkexec") else "Install polkit to make system-wide changes"))
    checks.append(Check("General", "Polkit policy", os.path.exists(POLICY),
                        "Installed — password prompts name Orbits" if os.path.exists(POLICY)
                        else "Not installed (running from source) — prompts mention python3", "info"))
    ro = usr_read_only()
    checks.append(Check("General", "Writable system folders", not ro,
                        "/usr is read-only (immutable system): login screen, boot splash and "
                        "all-users installs are unavailable" if ro else "/usr can be modified"))
    if helper.selinux_enabled():
        checks.append(Check("General", "SELinux", True, "Files Orbits writes are relabelled automatically", "info"))

    # login screen
    gdm = bool(shutil.which("gdm") or shutil.which("gdm3") or os.path.exists("/usr/sbin/gdm3"))
    checks.append(Check("Login Screen", "GDM", gdm, "" if gdm else "GDM isn't installed; the Login Screen page "
                                                                   "has no effect"))
    mode, path, name = helper.theme_location()
    if mode == "alternatives":
        detail = f"Managed with update-alternatives ({name}); Orbits adds its own choice and never edits " \
                 "distribution files"
    else:
        detail = f"{path}" + ("" if os.path.exists(path) else " (missing)")
    checks.append(Check("Login Screen", "Theme location", os.path.exists(os.path.realpath(path)), detail))
    tool = shutil.which("glib-compile-resources")
    checks.append(Check("Login Screen", "glib-compile-resources", bool(tool),
                        "" if tool else "Needed to build the login theme (glib2 / libglib2.0-bin)"))
    checks.append(Check("Login Screen", "dconf", bool(shutil.which("dconf")),
                        "" if shutil.which("dconf") else "Needed for GDM settings"))

    # boot splash
    ply = bool(shutil.which("plymouth-set-default-theme"))
    checks.append(Check("Boot Splash", "Plymouth", ply, "" if ply else "Install plymouth to use boot splashes"))
    if ply:
        initramfs = next((t for t in ("mkinitcpio", "dracut", "update-initramfs") if shutil.which(t)), None)
        checks.append(Check("Boot Splash", "Initramfs tool", bool(initramfs), initramfs or "None found"))
        if initramfs == "mkinitcpio":
            hooked = "plymouth" in _mkinitcpio_hooks()
            checks.append(Check("Boot Splash", "mkinitcpio plymouth hook", hooked,
                                "" if hooked else "Add “plymouth” to HOOKS in /etc/mkinitcpio.conf"))
        try:
            splash = "splash" in Path("/proc/cmdline").read_text().split()
        except OSError:
            splash = False
        checks.append(Check("Boot Splash", "“splash” kernel parameter", splash,
                            "" if splash else "Add “splash” (and “quiet”) to your bootloader's kernel command line"))

    # themes
    from orbits import themes

    ok, enabled, msg = themes.shell_status()
    checks.append(Check("Themes", "User Themes extension", ok and not msg,
                        msg or ("Enabled" if enabled else "Installed — enabled automatically when needed")))
    checks.append(Check("Themes", "Flatpak", bool(shutil.which("flatpak")),
                        "Can apply app colors to Flatpak apps" if shutil.which("flatpak") else "Not installed",
                        "info"))

    # fonts
    checks.append(Check("Fonts", "fontconfig", bool(shutil.which("fc-cache")),
                        "" if shutil.which("fc-cache") else "Needed to install fonts"))
    return checks


