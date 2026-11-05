"""Unprivileged, disposable GNOME Shell greeter previews."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading


def preview_command():
    shell = shutil.which("gnome-shell")
    bus = shutil.which("dbus-run-session")
    if not shell or not bus:
        raise RuntimeError("Install GNOME Shell and dbus-run-session to preview the greeter")
    help_text = subprocess.run([shell, "--help-all"], capture_output=True, text=True,
                               timeout=10, check=True).stdout
    if "--devkit" not in help_text:
        raise RuntimeError("This GNOME Shell does not support Devkit previews")
    if not os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
        raise RuntimeError("A graphical session is required to preview the greeter")
    return [bus, "--", shell, "--wayland", "--devkit", "--mode=gdm"]


def prepare_preview(spec, directory):
    """Compile a private theme and read-only dconf database; never use pkexec."""
    from orbits.gdm import helper

    root = Path(directory)
    data = root / "shell"
    data.mkdir()
    for source in Path(helper.SHELL_DIR).iterdir():
        if source.name not in ("gnome-shell-theme.gresource", "gdm-theme.gresource", "gdm3-theme.gresource"):
            (data / source.name).symlink_to(source)
    mode, active, _name = helper.theme_location()
    if mode == "file":
        base = helper.BACKUP if helper.is_ours(active) else active
    else:
        base = active
        if helper.is_ours(active):
            base_file = Path(helper.ALT_BASE_FILE)
            if not base_file.exists():
                raise RuntimeError("The original GDM theme is missing; reset or reinstall it first")
            base = base_file.read_text().strip()
    theme = spec.get("theme") or {}
    helper.build_gresource(str(base), theme.get("css", ""), theme.get("background"),
                           str(data / "gnome-shell-theme.gresource"))
    # Distribution session modes can name either GDM resource.
    for name in ("gdm-theme.gresource", "gdm3-theme.gresource"):
        (data / name).symlink_to(data / "gnome-shell-theme.gresource")
    keys = root / "keyfiles"
    keys.mkdir()
    groups = {group: dict(values) for group, values in spec.get("dconf", {}).items()}
    if spec.get("logo_source"):
        from gi.repository import GLib
        groups.setdefault("org/gnome/login-screen", {})["logo"] = GLib.Variant("s", spec["logo_source"]).print_(True)
    text = "\n".join(f"[{group}]\n" + "\n".join(f"{key}={value}" for key, value in values.items())
                     + "\n" for group, values in groups.items())
    (keys / "00-preview").write_text(text)
    database = root / "settings.db"
    subprocess.run(["dconf", "compile", str(database), str(keys)], check=True, capture_output=True)
    profile = root / "profile"
    profile.write_text(f"file-db:{database}\n")
    env = dict(os.environ)
    env.update(GNOME_SHELL_DATADIR=str(data), DCONF_PROFILE=str(profile), GSETTINGS_BACKEND="dconf",
               XDG_CONFIG_HOME=str(root / "config"), XDG_DATA_HOME=str(root / "data"),
               XDG_CACHE_HOME=str(root / "cache"))
    # dbus-run-session supplies its own bus; inherited overrides must not select the user's bus.
    runtime = root / "runtime"
    runtime.mkdir(mode=0o700)
    # Preserve access to the host compositor while keeping Shell runtime state disposable.
    wayland = env.get("WAYLAND_DISPLAY")
    if wayland and not os.path.isabs(wayland):
        host_runtime = env.get("XDG_RUNTIME_DIR")
        if not host_runtime:
            raise RuntimeError("The graphical session has no runtime directory")
        env["WAYLAND_DISPLAY"] = str(Path(host_runtime) / wayland)
    env["XDG_RUNTIME_DIR"] = str(runtime)
    env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    env.pop("DBUS_STARTER_ADDRESS", None)
    env.pop("DBUS_STARTER_BUS_TYPE", None)
    return env


class PreviewSession:
    def __init__(self, spec, directory):
        self.directory = directory
        self.process = None
        self.log = None
        self._stopped = False
        self._kill_timer = None
        try:
            command = preview_command()
            env = prepare_preview(spec, directory.name)
            self.log = open(Path(directory.name) / "greeter.log", "w+b")
            self.process = subprocess.Popen(command, env=env, stdout=self.log, stderr=self.log,
                                            start_new_session=True)
        except Exception:
            if self.log:
                self.log.close()
            self.directory.cleanup()
            raise

    def stop(self):
        self._stopped = True
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            def kill_if_running():
                if self.process.poll() is None:
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            self._kill_timer = threading.Timer(3, kill_if_running)
            self._kill_timer.daemon = True
            self._kill_timer.start()

    def wait(self):
        try:
            status = self.process.wait()
            self.log.seek(0, 2)
            self.log.seek(max(0, self.log.tell() - 4000))
            details = self.log.read().decode("utf-8", errors="replace")
            if not self._stopped and status not in (0, -signal.SIGTERM):
                raise RuntimeError("Greeter preview exited: " + (details.strip().splitlines()[-1] if details.strip() else str(status)))
        finally:
            if self._kill_timer:
                self._kill_timer.cancel()
            self.log.close()
            self.directory.cleanup()
