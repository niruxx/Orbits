"""Wallpaper rotation through a systemd user timer; runnable without the app window."""
import json
from pathlib import Path
import random
import subprocess
import sys


def validate_config(config):
    result = dict(config)
    if result.get("mode", "library") not in ("library", "bing", "search"):
        raise ValueError("Unknown rotation mode")
    minutes = int(result.get("minutes", 30))
    if not 1 <= minutes <= 10080:
        raise ValueError("Choose an interval from 1 minute to 7 days")
    result["minutes"] = minutes
    result.setdefault("mode", "library")
    return result


def unit_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('\n', '\\n') + '"'


def unit_contents(config, python=None, root=None):
    config = validate_config(config)
    root = root or Path(__file__).resolve().parent.parent
    service = ("[Unit]\nDescription=Orbits wallpaper rotation\n\n[Service]\nType=oneshot\n"
               f"WorkingDirectory={unit_quote(root)}\n"
               f"ExecStart={unit_quote(python or sys.executable)} -m orbits.rotation\n")
    schedule = "OnCalendar=daily\nPersistent=true" if config.get("daily") else \
        f"OnStartupSec=1min\nOnUnitActiveSec={config['minutes']}min"
    timer = f"[Unit]\nDescription=Rotate Orbits wallpaper\n\n[Timer]\n{schedule}\n\n[Install]\nWantedBy=timers.target\n"
    return service, timer


def configure(config):
    from orbits.config import CONFIG_DIR
    from gi.repository import GLib
    config = validate_config(config)
    path = CONFIG_DIR / "rotation.json"
    units = Path(GLib.get_user_config_dir()) / "systemd/user"
    previous = path.read_bytes() if path.exists() else None
    units.mkdir(parents=True, exist_ok=True)
    unit_paths = [units / f"orbits-wallpaper.{suffix}" for suffix in ("service", "timer")]
    old_units = {p: p.read_bytes() if p.exists() else None for p in unit_paths}
    try:
        for target, text in zip(unit_paths, unit_contents(config)):
            target.write_text(text)
        path.write_text(json.dumps(config, indent=2))
        systemctl("daemon-reload")
        if config.get("enabled"):
            systemctl("enable", "--now", "orbits-wallpaper.timer")
            systemctl("restart", "orbits-wallpaper.timer")
        else:
            systemctl("disable", "--now", "orbits-wallpaper.timer")
    except Exception:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(previous)
        for target, content in old_units.items():
            if content is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(content)
        # Reload restored files; preserve the original, useful error if systemd is unavailable.
        try:
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise


def systemctl(*args):
    try:
        result = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Wallpaper rotation requires a working systemd user session") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Could not update wallpaper timer")


def load_config():
    from orbits.config import CONFIG_DIR
    try:
        return validate_config(json.loads((CONFIG_DIR / "rotation.json").read_text()))
    except (OSError, ValueError, TypeError):
        return {"enabled": False, "mode": "library", "minutes": 30, "daily": False, "collection": ""}


def candidates(items, config):
    out = []
    for entry in items:
        if config.get("collection") and config["collection"] not in entry.get("collections", []):
            continue
        if config.get("favorites") and not entry.get("favorite"):
            continue
        out.extend(rec for rec in entry["files"] if Path(rec["light"]).is_file())
    return out


def rotate_once(config=None):
    from orbits.config import CONFIG_DIR
    from orbits.library import get_library
    config = validate_config(config or load_config())
    library = get_library()
    if config["mode"] == "library":
        files = candidates(library.items.values(), config)
        if not files:
            raise RuntimeError("No wallpapers match the selected collection")
        current = library.current_uri()
        files = [f for f in files if Path(f["light"]).as_uri() != current] or files
        record = random.choice(files)
    else:
        from orbits.sources import BY_ID
        source = BY_ID.get("bing" if config["mode"] == "bing" else config.get("source", "wallhaven"))
        if source is None:
            raise ValueError("Choose a wallpaper source")
        items, _ = source.fetch(config.get("query", ""), config.get("sort", ""), config.get("category", ""), 1)
        items = [item for item in items if item.downloads]
        if not items:
            raise RuntimeError("The wallpaper source returned no images")
        item = items[0] if config["mode"] == "bing" else random.choice(items)
        entry = library.items.get(item.key)
        if entry is None:
            entry = library._install_blocking(item, item.downloads[0], lambda _fraction: None)
            library.items[item.key] = entry
            library._save()
        record = entry["files"][0]
    library.apply(record, lockscreen=bool(config.get("lockscreen")))
    (CONFIG_DIR / "rotation-last.json").write_text(json.dumps({"path": record["light"]}))
    return record


def main():
    import gi
    gi.require_version("Gdk", "4.0")
    # Avoid concurrent timer/manual runs writing the library at the same time.
    import fcntl
    from orbits.config import CONFIG_DIR
    with (CONFIG_DIR / "rotation.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        if not load_config().get("enabled"):
            return 0
        try:
            rotate_once()
        except Exception as exc:
            print(f"Wallpaper rotation failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
