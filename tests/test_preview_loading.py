"""Preview ordering tests without a display or network access."""
import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

import orbits


class PreviewLoadingTests(unittest.TestCase):
    def setUp(self):
        self.jobs = []
        net = types.SimpleNamespace(run_async=lambda work, done: self.jobs.append((work, done)),
                                    cached_texture=lambda url: url)
        repository = types.SimpleNamespace(
            Gtk=types.SimpleNamespace(Widget=object, Overlay=object, Picture=object),
            Gdk=Mock(), GdkPixbuf=Mock(), Graphene=Mock(), Gsk=Mock())
        with patch.dict('sys.modules', {
            'gi.repository': repository, 'orbits.net': net,
            'orbits.config': types.SimpleNamespace(THUMB_CACHE=Path('/tmp')),
        }), patch.object(orbits, 'net', net, create=True):
            spec = importlib.util.spec_from_file_location(
                'preview_widgets', Path(__file__).resolve().parents[1] / 'orbits/ui/widgets.py')
            self.widgets = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.widgets)
        self.picture = Mock()
        self.spinner = Mock()

    def load(self):
        self.widgets.load_texture_into(self.picture, url='full', fallback_url='thumb',
                                       spinner=self.spinner)

    def finish(self, index, result):
        self.jobs[index][1](result, None if result else RuntimeError('unavailable'))

    def test_late_thumbnail_does_not_replace_full_preview(self):
        self.load()
        self.finish(1, 'sharp')
        self.finish(0, 'small')
        self.picture.set_paintable.assert_called_once_with('sharp')
        self.spinner.set_visible.assert_called_with(False)

    def test_thumbnail_upgrades_to_full_preview(self):
        self.load()
        self.finish(0, 'small')
        self.finish(1, 'sharp')
        self.assertEqual([c.args[0] for c in self.picture.set_paintable.call_args_list],
                         ['small', 'sharp'])

    def test_full_preview_failure_preserves_thumbnail_in_either_order(self):
        for order in ((0, 1), (1, 0)):
            with self.subTest(order=order):
                self.jobs.clear()
                self.picture.reset_mock()
                self.load()
                for index in order:
                    self.finish(index, 'small' if index == 0 else None)
                self.picture.set_paintable.assert_called_once_with('small')
                self.picture.add_css_class.assert_not_called()

    def test_previous_request_cannot_overwrite_new_image(self):
        self.load()
        self.widgets.load_texture_into(self.picture, url='new')
        self.finish(2, 'new image')
        self.finish(1, 'old image')
        self.finish(0, 'old thumbnail')
        self.picture.set_paintable.assert_called_once_with('new image')

    def test_both_failures_mark_image_failed(self):
        self.load()
        self.finish(0, None)
        self.finish(1, None)
        self.picture.set_paintable.assert_called_once_with(None)
        self.picture.add_css_class.assert_called_once_with('load-failed')


if __name__ == '__main__':
    unittest.main()
