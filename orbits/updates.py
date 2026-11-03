"""Update checks for everything installed from a store: KDE Store wallpapers, gnome-look themes, fonts."""
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from gi.repository import GLib, GObject

from orbits import net
from orbits.config import settings

CHECK_INTERVAL = 24 * 3600


def _ts(iso):
    """ISO-8601 (or YYYY-MM-DD) → epoch seconds; 0 when unknown."""
    if not iso:
        return 0.0
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def is_newer(remote_iso, entry):
    """True when the store changed the item after what we have installed.

    Prefer the remote date recorded at install time; older installs only have installed_at.
    """
    remote = _ts(remote_iso)
    if not remote:
        return False
    recorded = _ts(entry.get("remote_changed", ""))
    if recorded:
        return remote > recorded + 1
    return remote > float(entry.get("installed_at") or 0) + 60


@dataclass
class Update:
    kind: str           # "Wallpaper", "GTK theme", "Font", …
    key: str
    title: str
    remote_changed: str
    installed_at: float
    icon: str
    preview: str = ""
    apply: object = None   # callable(callback(ok, msg)) run on the main loop
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------- per-collection checkers (worker thread)
def _check_wallpapers():
    from orbits.library import get_library
    from orbits.sources import BY_ID

    lib, out = get_library(), []
    src = BY_ID.get("kde")
    for entry in list(lib.items.values()):
        if entry.get("source") != "kde":
            continue
        try:
            item = src.get_item(entry["id"])
        except Exception:  # noqa: BLE001 — a single failing item shouldn't stop the check
            continue
        if is_newer(item.updated, entry):
            out.append(Update("Wallpaper", entry["key"], entry["title"], item.updated, entry.get("installed_at", 0),
                              "preferences-desktop-wallpaper-symbolic", item.thumb_url,
                              extra={"item": item, "entry": entry, "type": "wallpaper"}))
    return out


def _check_themes():
    from orbits import themes

    out = []
    for kind in themes.KINDS.values():
        mgr = themes.get_manager(kind.id)
        src = themes.ThemeStoreSource(kind)
        for key, entry in list(mgr.items.items()):
            item_id = key.split(":", 1)[-1]
            if not key.startswith(src.id + ":"):
                continue
            try:
                item = src.get_item(item_id)
            except Exception:  # noqa: BLE001
                continue
            if is_newer(item.updated, entry):
                out.append(Update(kind.noun.capitalize(), key, entry.get("title", item.title), item.updated,
                                  entry.get("installed_at", 0), kind.icon, item.thumb_url,
                                  extra={"item": item, "entry": entry, "type": "theme", "kind": kind.id}))
    return out


def _check_fonts():
    from orbits.fonts import FONT_SOURCES_BY_ID, get_font_library

    lib, out = get_font_library(), []
    catalogs = {}
    for entry in list(lib.items.values()):
        src = FONT_SOURCES_BY_ID.get(entry.get("source"))
        if not src:
            continue
        if src.id not in catalogs:
            try:
                catalogs[src.id] = {i.id: i for i in src.catalog()}
            except Exception:  # noqa: BLE001
                catalogs[src.id] = {}
        item = catalogs[src.id].get(entry["id"])
        if item and is_newer(item.extra.get("modified", ""), entry):
            out.append(Update("Font", entry["key"], entry["family"], item.extra.get("modified", ""),
                              entry.get("installed_at", 0), "preferences-desktop-font-symbolic",
                              extra={"item": item, "entry": entry, "type": "font"}))
    return out


def check_all():
    """Run every checker (worker thread). Returns (updates, errors)."""
    updates, errors = [], []
    for fn in (_check_wallpapers, _check_themes, _check_fonts):
        try:
            updates += fn()
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
    return updates, errors


# ---------------------------------------------------------------- applying an update (main loop)
def _pick_download(item, entry):
    name = entry.get("download")
    if name:
        exact = next((d for d in item.downloads if d.name == name), None)
        if exact:
            return exact
        stem = name.split(".")[0].split("-v")[0]
        similar = next((d for d in item.downloads if d.name.startswith(stem)), None)
        if similar:
            return similar
    return None  # let the installer choose


def run_update(update: Update, callback):
    """Re-install the item in place. callback(ok, message)."""
    kind = update.extra["type"]
    item, entry = update.extra["item"], update.extra["entry"]
    if kind == "wallpaper":
        from orbits.library import get_library

        lib = get_library()
        was_current = lib.is_current(entry)

        def done(new, err):
            if new and was_current:
                lib.apply(new["files"][0], desktop=True, lockscreen=settings.get("apply_lockscreen_too"))
            callback(err is None, str(err) if err else "")

        lib.install(item, _pick_download(item, entry), done)
    elif kind == "theme":
        from orbits import themes

        mgr = themes.get_manager(update.extra["kind"])
        mgr.install(item, _pick_download(item, entry), lambda new, err: callback(err is None, str(err) if err else ""))
    elif kind == "font":
        from orbits.fonts import get_font_library

        lib = get_font_library()
        scope = entry.get("scope", "user")

        def done(new, err):
            if err:
                callback(False, str(err))
                return
            if scope in ("system", "both"):
                from orbits.privileged import run_helper

                def sys_done(ok, msg):
                    if ok:
                        lib.mark_system(new["key"], True)
                    callback(ok, msg)

                run_helper(["font-install", new["dir"], new["slug"]], sys_done)
            else:
                callback(True, "")

        lib.install(item, done)
    else:
        callback(False, f"Don't know how to update {kind}")


class UpdateChecker(GObject.Object):
    """Holds the latest results; emits 'changed' when a check finishes or an update is applied."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__()
        self.updates: list[Update] = []
        self.errors: list[str] = []
        self.checking = False
        self.last_checked = float(settings.get("updates_last_checked") or 0)

    def check(self, callback=None):
        if self.checking:
            return
        self.checking = True
        self.emit("changed")

        def done(res, err):
            self.checking = False
            if res:
                self.updates, self.errors = res
            elif err:
                self.errors = [str(err)]
            self.last_checked = time.time()
            settings.set("updates_last_checked", self.last_checked)
            self.emit("changed")
            if callback:
                callback(self.updates)

        net.run_async(check_all, done)

    def check_if_due(self, callback=None):
        if settings.get("auto_check_updates", True) and time.time() - self.last_checked > CHECK_INTERVAL:
            GLib.timeout_add_seconds(8, lambda: (self.check(callback), False)[1])

    def apply(self, update, callback=None):
        def done(ok, msg):
            if ok:
                self.updates = [u for u in self.updates if u.key != update.key]
            self.emit("changed")
            if callback:
                callback(ok, msg)

        run_update(update, done)


_checker = None


def get_checker() -> UpdateChecker:
    global _checker
    if _checker is None:
        _checker = UpdateChecker()
    return _checker
