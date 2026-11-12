"""Real libadwaita Preferences construction and security-action regression tests."""
import os
import unittest
from unittest.mock import Mock, patch


class PreferencesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if 'orbits-test' not in os.environ.get('XDG_CONFIG_HOME', ''):
            raise unittest.SkipTest('Run via tests/run.sh for isolated settings')
        try:
            import gi
            gi.require_version('Gtk', '4.0')
            gi.require_version('Adw', '1')
            from gi.repository import Adw, Gtk
        except (ImportError, ValueError, AttributeError) as error:
            raise unittest.SkipTest(f'GTK/libadwaita unavailable: {error}')
        if not Gtk.init_check():
            raise unittest.SkipTest('No GTK display available')
        Adw.init()
        from orbits.ui.preferences import PreferencesDialog
        cls.Dialog, cls.Adw = PreferencesDialog, Adw

    def setUp(self):
        self.jobs = []
        asynchronous = patch('orbits.net.run_async', side_effect=lambda work, done:self.jobs.append((work, done)))
        asynchronous.start()
        self.addCleanup(asynchronous.stop)
        library = patch('orbits.ui.preferences.get_library', return_value=Mock(items={}))
        library.start()
        self.addCleanup(library.stop)
        self.dialog = self.Dialog()

    def test_preferences_constructs_with_descriptive_security_actions(self):
        for name in ('definitions_row', 'rescan_row', 'integrity_row', 'legacy_row'):
            with self.subTest(row=name):
                row = getattr(self.dialog, name)
                self.assertIsInstance(row, self.Adw.ActionRow)
                self.assertTrue(row.get_activatable())
                self.assertTrue(row.get_subtitle())
                self.assertFalse(row.get_use_markup())
                row.set_subtitle('Status <literal> & text')
                self.assertEqual(row.get_subtitle(), 'Status <literal> & text')

    def test_definition_action_updates_status_and_reenables_after_completion(self):
        self.jobs.clear()
        with patch('orbits.security.update_definitions', return_value='Fixture definitions updated') as update:
            row = self.dialog.definitions_row
            row.emit('activated')
            self.assertFalse(row.get_sensitive())
            self.assertEqual(len(self.jobs), 1)
            work, done = self.jobs[0]
            result = work()
            done(result, None)
            update.assert_called_once()
            self.assertTrue(row.get_sensitive())
            self.assertEqual(row.get_subtitle(), 'Fixture definitions updated')

    def test_definition_failure_is_displayed_and_allows_retry(self):
        self.jobs.clear()
        row = self.dialog.definitions_row
        row.emit('activated')
        self.jobs[0][1](None, RuntimeError('Fixture updater unavailable'))
        self.assertTrue(row.get_sensitive())
        self.assertEqual(row.get_subtitle(), 'Fixture updater unavailable')


if __name__ == '__main__':
    unittest.main()
