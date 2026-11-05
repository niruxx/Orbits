"""Desktop settings adapters; session detection never executes shell command text."""
import os
from pathlib import Path
import subprocess


def detect():
    names = (os.environ.get('XDG_CURRENT_DESKTOP','')+':'+os.environ.get('DESKTOP_SESSION','')).lower()
    if 'cinnamon' in names:
        return 'cinnamon'
    if 'xfce' in names:
        return 'xfce'
    if 'budgie' in names:
        return 'budgie'
    return 'gnome'


def xfconf(channel, prop, value=None, type_='string'):
    command = ['xfconf-query','-c',channel,'-p',prop]
    if value is not None:
        existing = subprocess.run(command, capture_output=True, text=True)
        if existing.returncode:
            command += ['--create','--type',type_]
        command += ['--set',str(value)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Could not update XFCE settings')
    return result.stdout.strip()


class XfceInterface:
    keys = {'gtk-theme':('/Net/ThemeName','s'), 'icon-theme':('/Net/IconThemeName','s'),
            'cursor-theme':('/Gtk/CursorThemeName','s'), 'cursor-size':('/Gtk/CursorThemeSize','i'),
            'font-name':('/Gtk/FontName','s'), 'document-font-name':('/Gtk/FontName','s'),
            'monospace-font-name':('/Gtk/MonospaceFontName','s'), 'text-scaling-factor':('/Xft/DPI','i')}

    def get_string(self, key):
        return xfconf('xsettings',self.keys[key][0])

    def set_string(self, key, value):
        xfconf('xsettings',self.keys[key][0],value)
        return True

    def get_value(self, key):
        from gi.repository import GLib
        value = self.get_string(key)
        if key == 'text-scaling-factor':
            dpi = int(value)
            return GLib.Variant('d',dpi/96 if dpi>0 else 1.0)
        return GLib.Variant(self.keys[key][1], int(value) if self.keys[key][1]=='i' else value)

    def set_value(self, key, value):
        value = value.unpack()
        if key == 'text-scaling-factor':
            value = round(value*96)
        xfconf('xsettings',self.keys[key][0],value,'int' if self.keys[key][1]=='i' else 'string')
        return True

    def connect(self, *args):
        # xfconf changes are picked up on the next refresh; this adapter owns no daemon.
        return 0

    def bind(self, key, widget, prop, flags):
        from gi.repository import GLib
        widget.set_property(prop, self.get_value(key).unpack())
        return widget.connect('notify::'+prop, lambda obj, _param:self.set_value(
            key, GLib.Variant('d' if key=='text-scaling-factor' else self.keys[key][1], obj.get_property(prop))))

    def get_property(self, name):
        if name == 'settings-schema':
            return self
        raise ValueError(name)

    def has_key(self, key):
        return key in self.keys


class SettingsUnion:
    """Use a desktop's native keys and fall back only for keys it does not define."""
    def __init__(self, primary, fallback):
        self.primary, self.fallback = primary, fallback

    def target(self, key):
        for settings in (self.primary, self.fallback):
            if settings.get_property('settings-schema').has_key(key):
                return settings
        raise ValueError(f'This desktop does not support {key}')

    def has_key(self, key):
        return any(settings.get_property('settings-schema').has_key(key) for settings in (self.primary,self.fallback))

    def get_property(self, name):
        return self if name=='settings-schema' else self.primary.get_property(name)

    def get_key(self, key):
        return self.target(key).get_property('settings-schema').get_key(key)

    def get_string(self, key):return self.target(key).get_string(key)
    def set_string(self, key, value):return self.target(key).set_string(key,value)
    def get_value(self, key):return self.target(key).get_value(key)
    def set_value(self, key, value):return self.target(key).set_value(key,value)
    def get_double(self, key):return self.get_value(key).unpack()
    def set_double(self, key, value):
        from gi.repository import GLib
        return self.set_value(key, GLib.Variant('d',value))
    def connect(self, signal, callback):
        return self.primary.connect(signal,callback)
    def bind(self, key, widget, prop, flags):
        return self.target(key).bind(key,widget,prop,flags)


class CinnamonBackground:
    def __init__(self, settings):self.settings=settings
    def get_string(self,key):
        if key=='picture-uri-dark' and not self.settings.get_property('settings-schema').has_key(key):
            key='picture-uri'
        return self.settings.get_string(key)
    def set_string(self,key,value):
        if key=='picture-uri-dark' and not self.settings.get_property('settings-schema').has_key(key):
            return True
        return self.settings.set_string(key,value)
    def __getattr__(self,key):return getattr(self.settings,key)


class XfceBackground:
    def get_string(self,key):
        from urllib.parse import urlparse
        if key=='picture-options':return 'zoom'
        result=subprocess.run(['xfconf-query','-c','xfce4-desktop','-l'],capture_output=True,text=True,check=True)
        prop=next((p for p in result.stdout.splitlines() if p.endswith('/last-image')),None)
        path=xfconf('xfce4-desktop',prop) if prop else ''
        return Path(path).as_uri() if path and Path(path).is_absolute() else ''
    def get_property(self,name):return self
    def has_key(self,key):return key in ('picture-uri','picture-options')
    def get_value(self,key):
        from gi.repository import GLib
        return GLib.Variant('s',self.get_string(key))
    def set_value(self,key,value):
        from urllib.parse import unquote,urlparse
        if key=='picture-uri' and value.unpack():xfce_wallpaper(unquote(urlparse(value.unpack()).path))
    def connect(self,*args):return 0


def interface_settings():
    from gi.repository import Gio
    desktop = detect()
    if desktop == 'xfce':return XfceInterface()
    base=Gio.Settings.new('org.gnome.desktop.interface')
    if desktop=='cinnamon':return SettingsUnion(Gio.Settings.new('org.cinnamon.desktop.interface'),base)
    return base


def background_settings():
    from gi.repository import Gio
    if detect()=='xfce':return XfceBackground()
    if detect()=='cinnamon':return CinnamonBackground(Gio.Settings.new('org.cinnamon.desktop.background'))
    return Gio.Settings.new('org.gnome.desktop.background')


def xfce_wallpaper(path, options='zoom'):
    result = subprocess.run(['xfconf-query','-c','xfce4-desktop','-l'], capture_output=True, text=True, check=True)
    props = [p for p in result.stdout.splitlines() if p.startswith('/backdrop/') and p.endswith('/last-image')]
    if not props:
        raise RuntimeError('XFCE has no configured monitor backgrounds; open Desktop Settings first')
    previous = {p:xfconf('xfce4-desktop',p) for p in props}
    styles = {p.rsplit('/',1)[0]+'/image-style':None for p in props}
    for prop in styles:
        styles[prop] = xfconf('xfce4-desktop',prop)
    try:
        for prop in props:
            xfconf('xfce4-desktop',prop,str(Path(path).resolve()))
        for prop in styles:
            xfconf('xfce4-desktop',prop,{'none':0,'centered':1,'wallpaper':2,'stretched':3,'scaled':4,'zoom':5,'spanned':6}.get(options,5),'int')
    except Exception:
        for prop, value in previous.items():
            xfconf('xfce4-desktop',prop,value)
        for prop, value in styles.items():
            xfconf('xfce4-desktop',prop,value,'int')
        raise
    return previous, styles
