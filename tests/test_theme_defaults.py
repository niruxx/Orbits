"""Desktop theme defaults without changing user settings or deleting installed themes."""
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock,patch

from orbits import themes,undo


class Settings:
    def __init__(self,defaults,values):
        self.defaults=defaults
        self.values=dict(values)
        self.changed=[]
        self.writable=True
    def is_writable(self,key):return self.writable
    def reset(self,key):
        self.changed.append(key)
        self.values.pop(key,None)
    def get_string(self,key):return self.values.get(key,self.defaults[key])
    def get_value(self,key):return self.get_string(key)
    def set_value(self,key,value):self.values[key]=value


class ThemeDefaultTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='orbits-test-theme-default-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.settings=Settings({'gtk-theme':'VendorDefault','icon-theme':'VendorIcons','cursor-theme':'VendorCursor'},
                               {'gtk-theme':'CustomGtk','icon-theme':'CustomIcons','cursor-theme':'CustomCursor'})
        self.shell=Settings({'name':''},{'name':'CustomShell'})
        for name,value in (('orbits.themes._iface',Mock(return_value=self.settings)),
                           ('orbits.themes._user_theme_settings',Mock(return_value=self.shell)),
                           ('orbits.desktop.detect',Mock(return_value='gnome')),
                           ('orbits.themes.GTK4_CONFIG',self.root/'gtk-4.0'),
                           ('orbits.themes.LIBADWAITA_MARKER',self.root/'gtk-4.0/.orbits-libadwaita'),
                           ('orbits.undo._listeners',[])):
            context=patch(name,value);context.start();self.addCleanup(context.stop)

    def test_each_page_restores_only_its_schema_default(self):
        for kind,key,expected in [('gtk','gtk-theme','VendorDefault'),('icons','icon-theme','VendorIcons'),
                                  ('cursors','cursor-theme','VendorCursor')]:
            self.settings.changed.clear()
            self.assertEqual(themes.restore_default(kind),expected)
            self.assertEqual(self.settings.changed,[key])
        self.assertEqual(themes.restore_default('shell'),'')
        self.assertEqual(self.shell.changed,['name'])

    def test_gtk_reset_removes_orbits_links_and_restores_preexisting_configuration(self):
        config=themes.GTK4_CONFIG
        config.mkdir()
        original=config/'orbits-backup'
        original.mkdir()
        (original/'gtk.css').write_text('/* user configuration before Orbits theme */')
        installed=self.root/'InstalledTheme/gtk-4.0'
        installed.mkdir(parents=True)
        stylesheet=installed/'gtk.css'
        stylesheet.write_text('/* installed theme */')
        (config/'gtk.css').symlink_to(stylesheet)
        themes.LIBADWAITA_MARKER.write_text('InstalledTheme\n')
        themes.restore_default('gtk')
        self.assertFalse((config/'gtk.css').is_symlink())
        self.assertEqual((config/'gtk.css').read_text(),'/* user configuration before Orbits theme */')
        self.assertFalse(themes.LIBADWAITA_MARKER.exists())
        self.assertTrue(stylesheet.exists())

    def test_reset_preserves_unowned_gtk_configuration(self):
        config=themes.GTK4_CONFIG
        config.mkdir()
        (config/'gtk.css').write_text('/* independent user customization */')
        themes.restore_default('gtk')
        self.assertEqual((config/'gtk.css').read_text(),'/* independent user customization */')

    def test_locked_setting_is_reported_without_changing_it(self):
        self.settings.writable=False
        with self.assertRaisesRegex(RuntimeError,'locked'):
            themes.restore_default('gtk')
        self.assertEqual(self.settings.changed,[])
        self.assertEqual(self.settings.get_string('gtk-theme'),'CustomGtk')

    def test_shell_without_user_themes_extension_is_already_default(self):
        with patch('orbits.themes._user_theme_settings',return_value=None):
            self.assertEqual(themes.restore_default('shell'),'')

    def test_shell_default_restore_has_undo_even_for_extension_local_schema(self):
        with patch.object(undo,'_listeners',[Mock()]),patch.object(undo,'_records',[]), \
             patch.object(undo,'DesktopState',return_value=Mock()):
            themes.restore_default('shell')
            self.assertEqual(self.shell.get_string('name'),'')
            undo._records[-1].undo()
            self.assertEqual(self.shell.get_string('name'),'CustomShell')
            undo.clear()

    def test_unrelated_theme_types_cannot_be_reset_by_this_action(self):
        for kind in ('plymouth','terminal','sounds'):
            with self.subTest(kind=kind),self.assertRaises(ValueError):themes.restore_default(kind)
        self.assertEqual(self.settings.changed,[])


if __name__=='__main__':unittest.main()
