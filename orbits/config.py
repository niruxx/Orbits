"""Paths and small JSON-backed settings stores."""
import json
import os
from pathlib import Path

from gi.repository import GLib

DATA_DIR = Path(GLib.get_user_data_dir()) / "orbits"
CONFIG_DIR = Path(GLib.get_user_config_dir()) / "orbits"
CACHE_DIR = Path(GLib.get_user_cache_dir()) / "orbits"
THUMB_CACHE = CACHE_DIR / "thumbs"
WALLPAPER_DIR = Path(GLib.get_user_data_dir()) / "backgrounds" / "orbits"
BG_PROPERTIES_DIR = Path(GLib.get_user_data_dir()) / "gnome-background-properties"
PACKAGE_DIR = Path(__file__).resolve().parent

for _d in (DATA_DIR, CONFIG_DIR, THUMB_CACHE, WALLPAPER_DIR):
    _d.mkdir(parents=True, exist_ok=True)


class JsonStore:
    """A dict persisted to a JSON file, written atomically."""

    def __init__(self, path: Path, defaults: dict | None = None):
        self.path = path
        self.data = dict(defaults or {})
        try:
            with open(path, encoding="utf-8") as f:
                self.data.update(json.load(f))
        except (OSError, ValueError):
            pass

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)


settings = JsonStore(
    CONFIG_DIR / "settings.json",
    {
        "welcome_done": False,
        "register_in_gnome": True,
        "default_source": "kde",
        "window_width": 1180,
        "window_height": 800,
        "download_dir": "",            # "" = ~/.local/share/backgrounds/orbits
        "apply_lockscreen_too": False,  # "Set as Wallpaper" also sets the lock screen
        "color_scheme": "default",      # default | light | dark
        "wallhaven_min_res": "1920x1080",
        "wallhaven_top_range": "1y",
        "wallhaven_api_key": "",
        "kde_page_size": 24,
        "thumb_cache_mb": 300,
    },
)


def download_dir() -> Path:
    custom = settings.get("download_dir")
    d = Path(custom).expanduser() if custom else WALLPAPER_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_size():
    return sum(f.stat().st_size for f in THUMB_CACHE.iterdir() if f.is_file())


def trim_cache(max_bytes=None):
    """Delete oldest thumbnails until the cache is under its size limit."""
    if max_bytes is None:
        max_bytes = int(settings.get("thumb_cache_mb", 300)) << 20
    files = sorted((f for f in THUMB_CACHE.iterdir() if f.is_file()), key=lambda f: f.stat().st_atime)
    total = sum(f.stat().st_size for f in files)
    for f in files:
        if total <= max_bytes:
            break
        total -= f.stat().st_size
        f.unlink(missing_ok=True)
