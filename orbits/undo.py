"""One-shot undo records for desktop changes and managed-file removal."""
import copy
from functools import wraps
import os
from pathlib import Path
import shutil
import tempfile
import threading

_listeners = []
_local = threading.local()
_records = []


def subscribe(callback):
    _listeners.append(callback)
    return lambda: _listeners.remove(callback) if callback in _listeners else None


class UndoRecord:
    def __init__(self, label, restore, cleanup=lambda:None):
        self.label, self.restore, self.cleanup = label, restore, cleanup
        self.used = False

    def undo(self):
        if self.used:
            raise RuntimeError('This change has already been undone')
        previous = getattr(_local, 'depth', 0)
        _local.depth = previous+1
        try:
            result = self.restore()
            if result is False:  # An authenticated asynchronous undo is still pending.
                return False
            self.used = True
            self.cleanup()
        finally:
            _local.depth = previous


def publish(label, restore, cleanup=lambda:None):
    record = UndoRecord(label, restore, cleanup)
    _records.append(record)
    while len(_records) > 20:
        old = _records.pop(0)
        old.used = True
        old.cleanup()
    for callback in list(_listeners):
        callback(record)
    return record


class FilesState:
    def __init__(self, paths):
        self.directory = tempfile.TemporaryDirectory(prefix='orbits-undo-')
        self.paths = []
        for index, value in enumerate(dict.fromkeys(map(str, paths))):
            path = Path(value)
            backup = Path(self.directory.name)/str(index)
            exists = path.exists() or path.is_symlink()
            if exists:
                self.copy(path, backup)
            self.paths.append((path, backup, exists))

    @staticmethod
    def copy(source, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            destination.symlink_to(os.readlink(source))
        elif source.is_dir():
            shutil.copytree(source, destination, symlinks=True)
        else:
            shutil.copy2(source, destination)

    def restore(self):
        for path, backup, exists in self.paths:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
            if exists:
                self.copy(backup, path)

    def cleanup(self):
        self.directory.cleanup()


class DesktopState:
    def __init__(self):
        from gi.repository import Gio
        from orbits.desktop import interface_settings, background_settings, detect
        from orbits.config import CONFIG_DIR
        self.settings = []
        self.xfce = []
        if detect() == 'xfce':
            from orbits.desktop import xfconf
            import subprocess
            result = subprocess.run(['xfconf-query','-c','xfce4-desktop','-l'], capture_output=True, text=True, check=True)
            for prop in result.stdout.splitlines():
                if prop.startswith('/backdrop/') and prop.endswith(('/last-image','/image-style')):
                    self.xfce.append((prop, xfconf('xfce4-desktop',prop)))
        objects = [(interface_settings(), ['gtk-theme','icon-theme','cursor-theme','cursor-size','font-name',
                   'document-font-name','monospace-font-name','text-scaling-factor','accent-color','color-scheme']),
                   (background_settings(), ['picture-uri','picture-uri-dark','picture-options'])]
        for schema, keys in [('org.gnome.desktop.screensaver',['picture-uri','picture-options']),
                             ('org.gnome.desktop.sound',['theme-name','event-sounds']),
                             ('org.gnome.shell.extensions.user-theme',['name']),
                             ('org.gnome.shell',['enabled-extensions'])]:
            source = Gio.SettingsSchemaSource.get_default()
            if source and source.lookup(schema, True):
                objects.append((Gio.Settings.new(schema),keys))
        for settings, keys in objects:
            schema = settings.get_property('settings-schema')
            self.settings.extend((settings,key,settings.get_value(key)) for key in keys if schema.has_key(key))
        config = Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))
        self.files = FilesState([config/'gtk-4.0',config/'gtk-3.0',CONFIG_DIR/'app-colors.json',
                                 config/'fontconfig/conf.d/99-orbits.conf',
                                 Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))/'flatpak/overrides/global',Path.home()/'.gtkrc',Path.home()/'.gtkrc-2.0'])

    def restore(self):
        from gi.repository import Gio
        from orbits.desktop import xfconf
        self.files.restore()
        for settings, key, value in self.settings:
            settings.set_value(key, value)
        for prop, value in self.xfce:
            xfconf('xfce4-desktop',prop,value,'int' if prop.endswith('image-style') else 'string')
        Gio.Settings.sync()

    def cleanup(self):
        self.files.cleanup()


class EntryState:
    def __init__(self, store, keys, paths):
        self.store = store
        self.entries = {key:copy.deepcopy(store.items[key]) for key in keys if key in store.items}
        self.files = FilesState(paths)

    def restore(self):
        self.files.restore()
        self.store.items.update(copy.deepcopy(self.entries))
        self.store._save()
        self.store.emit('changed')
        if self.store.__class__.__name__ == 'FontLibrary':
            self.store._refresh_fonts()

    def cleanup(self):
        self.files.cleanup()


def reversible(label, extra=None):
    def decorate(function):
        @wraps(function)
        def run(*args, **kwargs):
            if not _listeners or getattr(_local,'depth',0):
                return function(*args, **kwargs)
            state = DesktopState()
            try:
                additional = extra(*args, **kwargs) if extra else None
            except Exception:
                state.cleanup()
                raise
            _local.depth = 1
            try:
                result = function(*args, **kwargs)
            except Exception:
                state.cleanup()
                if additional:
                    additional.cleanup()
                raise
            finally:
                _local.depth = 0
            def restore():
                if additional:
                    additional.restore()
                state.restore()
            def cleanup():
                state.cleanup()
                if additional:
                    additional.cleanup()
            publish(label, restore, cleanup)
            return result
        return run
    return decorate


def clear():
    for record in _records:
        record.cleanup()
    _records.clear()
