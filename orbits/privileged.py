"""Run the root helper through pkexec, asynchronously."""
import os
import sys

from gi.repository import Gio, GLib

from orbits.gdm.model import HELPER


def _python():
    return "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable


POLICY = "/usr/share/polkit-1/actions/io.github.niruxx.Orbits.policy"


def run_helper(args, callback, undo_callback=None):
    """callback(ok: bool, message: str) on the main loop."""
    if os.path.exists(POLICY) and os.access(HELPER, os.X_OK) and str(HELPER).startswith("/usr/"):
        # Packaged install: the polkit action names this exact path, giving a proper "Orbits" prompt.
        argv = ["pkexec", str(HELPER), *args]
    else:
        argv = ["pkexec", _python(), "-I", str(HELPER), *args]
    try:
        proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
    except GLib.Error as e:
        callback(False, e.message)
        return

    def finished(p, res):
        try:
            _ok, out, err = p.communicate_utf8_finish(res)
        except GLib.Error as e:
            callback(False, e.message)
            return
        status = p.get_exit_status()
        if status == 0:
            lines = (out or "").splitlines()
            token = next((line.removeprefix("ORBITS_UNDO=") for line in lines if line.startswith("ORBITS_UNDO=")), None)
            if token:
                from orbits.undo import publish
                def undo():
                    def restored(ok, message):
                        app = Gio.Application.get_default()
                        if ok:
                            record.used = True
                            if undo_callback:
                                undo_callback()
                        else:
                            publish("Undo system change", undo)
                        if app and hasattr(app, 'notify_error'):
                            app.notify_error("Change undone" if ok else message)
                    run_helper(["undo", token], restored)
                    return False
                record = publish("System change applied", undo)
            callback(True, "\n".join(line for line in lines if not line.startswith("ORBITS_UNDO=")))
        elif status in (126, 127):
            callback(False, "Authentication was cancelled")
        else:
            lines = [ln for ln in (err or "").splitlines() if ln.strip()]
            callback(False, (lines[-1] if lines else f"Helper failed ({status})").removeprefix("error: "))

    proc.communicate_utf8_async(None, None, finished)
