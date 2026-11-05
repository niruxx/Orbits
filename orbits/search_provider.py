"""GNOME Shell SearchProvider2 service exposing locally saved Looks."""
import os
from pathlib import Path
import shutil
import subprocess

from orbits.search import build_index, search, resolve_look

BUS_NAME = 'io.github.niruxx.Orbits.SearchProvider'
OBJECT_PATH = '/io/github/niruxx/Orbits/SearchProvider'
XML = '''<node><interface name="org.gnome.Shell.SearchProvider2">
<method name="GetInitialResultSet"><arg type="as" direction="in"/><arg type="as" direction="out"/></method>
<method name="GetSubsearchResultSet"><arg type="as" direction="in"/><arg type="as" direction="in"/><arg type="as" direction="out"/></method>
<method name="GetResultMetas"><arg type="as" direction="in"/><arg type="aa{sv}" direction="out"/></method>
<method name="ActivateResult"><arg type="s" direction="in"/><arg type="as" direction="in"/><arg type="u" direction="in"/></method>
<method name="LaunchSearch"><arg type="as" direction="in"/><arg type="u" direction="in"/></method>
</interface></node>'''


def launcher():
    checkout = Path(__file__).resolve().parent.parent/'orbits-run'
    return str(checkout) if checkout.is_file() else (shutil.which('orbits') or '/usr/bin/orbits')


class SearchProvider:
    def __init__(self, directory, launch=None):
        self.directory = Path(directory)
        self.launch = launch or (lambda args:subprocess.Popen([launcher(), *args], start_new_session=True))

    def results(self, terms):
        records = [item for item in build_index(self.directory) if item['kind']=='look']
        return search(records, ' '.join(terms), 20)

    def activate(self, identifier):
        resolve_look(self.directory, identifier)
        self.launch(['--apply-look',identifier])

    def dispatch(self, method, args):
        if method in ('GetInitialResultSet','GetSubsearchResultSet'):
            return [item['id'] for item in self.results(args[-1])]
        if method == 'GetResultMetas':
            records = {r['id']:r for r in build_index(self.directory) if r['kind']=='look'}
            return [records[key] for key in args[0] if key in records]
        if method == 'ActivateResult':
            self.activate(args[0])
        elif method == 'LaunchSearch':
            self.launch(['--search',' '.join(args[0])])
        else:
            raise ValueError('Unknown search provider method')


def main():
    from gi.repository import Gio, GLib
    directory = Path(GLib.get_user_data_dir())/'orbits'
    provider = SearchProvider(directory)
    loop = GLib.MainLoop()
    timeout = [None]
    def reset_timeout():
        if timeout[0]:
            GLib.source_remove(timeout[0])
        timeout[0] = GLib.timeout_add_seconds(60, lambda:(loop.quit(), False)[1])
    def method_call(connection, sender, path, interface, method, parameters, invocation):
        reset_timeout()
        try:
            result = provider.dispatch(method, parameters.unpack())
            if method in ('GetInitialResultSet','GetSubsearchResultSet'):
                value = GLib.Variant('(as)', (result,))
            elif method == 'GetResultMetas':
                metadata = [{'id':GLib.Variant('s', item['id']), 'name':GLib.Variant('s',item['title']),
                             'description':GLib.Variant('s','Apply this saved Look'),
                             'gicon':GLib.Variant('s','io.github.niruxx.Orbits')} for item in result]
                value = GLib.Variant('(aa{sv})', (metadata,))
            else:
                value = None
            invocation.return_value(value)
        except Exception as error:
            invocation.return_dbus_error(BUS_NAME+'.Error',str(error))
    def acquired(connection, name):
        interface = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
        connection.register_object(OBJECT_PATH, interface, method_call, None, None)
        reset_timeout()
    owner = Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                            acquired, None, lambda *_:loop.quit())
    try:
        loop.run()
    finally:
        Gio.bus_unown_name(owner)
    return 0
