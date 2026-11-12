"""Download persistence and recycled-card regressions without a desktop."""
import gc
import importlib.util
from pathlib import Path
import types
import runpy
import tempfile
import unittest
from unittest.mock import Mock, patch
import weakref

from orbits.download_choice import select_download

ROOT = Path(__file__).resolve().parents[1]
_base = runpy.run_path(str(ROOT / "orbits/sources/base.py"))
Download, Item = _base["Download"], _base["Item"]


class DownloadChoiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='orbits-test-download-choice-')
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        glib = types.SimpleNamespace(get_user_data_dir=lambda: str(directory/'data'),
                                     get_user_config_dir=lambda: str(directory/'config'),
                                     get_user_cache_dir=lambda: str(directory/'cache'))
        with patch.dict('sys.modules', {'gi.repository': types.SimpleNamespace(GLib=glib)}):
            config = runpy.run_path(str(ROOT/'orbits/config.py'))
        self.Store = config['JsonStore']
        self.settings = self.Store(directory/'choices.json')
        self.data = self.settings.data
        self.config = patch.dict('sys.modules', {'orbits.config': types.SimpleNamespace(settings=self.settings)})
        self.config.start()
        self.addCleanup(self.config.stop)
        self.item = Item('store', '123', 'Theme', downloads=[Download('light.zip', 'https://a/old'),
                                                          Download('dark.zip', 'https://b/old')])

    def test_explicit_choice_survives_new_item_and_rotated_url(self):
        self.assertIs(select_download(self.item, self.item.downloads[0]), self.item.downloads[0])
        self.settings = self.Store(self.settings.path)
        self.config.stop()
        self.config = patch.dict('sys.modules', {'orbits.config': types.SimpleNamespace(settings=self.settings)})
        self.config.start()
        self.addCleanup(self.config.stop)
        current = Item('store', '123', 'Theme', downloads=[Download('dark.zip', 'https://b/new'),
                                                        Download('light.zip', 'https://a/new')])
        self.assertIs(select_download(current, prefer_archive=True), current.downloads[1])
        self.assertEqual(self.data['download_choices'], {'store:123': 'light.zip'})

    def test_missing_choice_uses_normal_default(self):
        select_download(self.item, self.item.downloads[0])
        self.item.downloads = [self.item.downloads[1]]
        self.assertIs(select_download(self.item, prefer_archive=True), self.item.downloads[0])

    def test_choices_are_scoped_to_source_and_item(self):
        select_download(self.item, self.item.downloads[0])
        self.item.source = 'other-store'
        self.assertIs(select_download(self.item, prefer_archive=True), self.item.downloads[-1])

    def test_local_import_does_not_replace_store_choice(self):
        self.item.source = 'local'
        explicit = Download('local.zip', 'file:///local.zip')
        self.assertIs(select_download(self.item, explicit), explicit)
        self.assertEqual(self.data, {})

    def test_defaults_and_empty_catalogue(self):
        self.assertIs(select_download(self.item), self.item.downloads[0])
        self.assertIs(select_download(self.item, prefer_archive=True), self.item.downloads[-1])
        self.item.downloads = []
        self.assertIsNone(select_download(self.item))


class Widget:
    def __init__(self, child=None):
        self.child = child
        self._texture_request = object()
        self._font_generation = 1
        self.paintable = object()

    def get_first_child(self):
        return self.child

    def get_next_sibling(self):
        return None

    def set_paintable(self, value):
        self.paintable = value


class ListItem:
    def __init__(self, value):
        self.item = types.SimpleNamespace(value=value)
        self.child = None

    def get_item(self):
        return self.item

    def set_child(self, value):
        self.child = value


class GridLifecycleTests(unittest.TestCase):
    def setUp(self):
        repository = types.SimpleNamespace(Gio=Mock(), GObject=types.SimpleNamespace(Object=object),
                                           Gtk=types.SimpleNamespace(GridView=object))
        with patch.dict('sys.modules', {'gi.repository': repository}):
            spec = importlib.util.spec_from_file_location('test_item_grid', ROOT/'orbits/ui/item_grid.py')
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.grid = object.__new__(self.module.ItemGrid)
        self.grid.bound = {}
        self.created = []
        def create(value):
            self.created.append(value)
            return Widget(Widget())
        self.grid.create_card = create

    def test_bind_uses_current_data_and_unbind_invalidates_nested_previews(self):
        item = ListItem('first')
        self.grid._bind(None, item)
        card = item.child
        picture = card.child
        self.grid._unbind(None, item)
        self.assertIsNone(item.child)
        self.assertEqual(self.grid.bound, {})
        self.assertTrue(card._grid_released)
        self.assertIsNone(picture._texture_request)
        self.assertIsNone(picture.paintable)
        self.assertEqual(picture._font_generation, 2)
        item.item.value = 'second'
        self.grid._bind(None, item)
        self.assertEqual(self.created, ['first', 'second'])
        self.assertIsNot(item.child, card)

    def test_unbinding_cancels_queued_work_on_cards_and_nested_pictures(self):
        from concurrent.futures import Future
        item = ListItem('preview')
        self.grid._bind(None, item)
        card_job, picture_job = Future(), Future()
        item.child._preview_futures = [card_job]
        item.child.child._preview_futures = [picture_job]
        self.grid._unbind(None, item)
        self.assertTrue(card_job.cancelled())
        self.assertTrue(picture_job.cancelled())

    def test_unbound_cards_are_not_retained_after_many_rebindings(self):
        item = ListItem(None)
        references = []
        for value in range(1000):
            item.item.value = value
            self.grid._bind(None, item)
            references.append(weakref.ref(item.child))
            self.grid._unbind(None, item)
        gc.collect()
        self.assertTrue(all(reference() is None for reference in references))
        self.assertEqual(self.grid.bound, {})


class LiveGridTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version('Gtk', '4.0')
            from gi.repository import Gtk, GLib
        except (ImportError, ValueError, AttributeError) as error:
            raise unittest.SkipTest(f'GTK introspection unavailable: {error}')
        if not Gtk.init_check():
            raise unittest.SkipTest('No GTK display available')
        cls.Gtk, cls.GLib = Gtk, GLib
        spec = importlib.util.spec_from_file_location('live_item_grid', ROOT/'orbits/ui/item_grid.py')
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def settle(self):
        import time
        context = self.GLib.MainContext.default()
        for _ in range(30):
            while context.pending():
                context.iteration(False)
            time.sleep(.01)

    def test_large_model_filters_activates_and_recycles_with_a_real_viewport(self):
        Gtk = self.Gtk
        activated = []
        grid = self.module.ItemGrid(lambda value: Gtk.Label(label=str(value),
                                    width_request=180, height_request=100), activated.append)
        grid.set_items(range(10000))
        scroller = Gtk.ScrolledWindow(child=grid)
        window = Gtk.Window(default_width=800, default_height=600, child=scroller)
        self.addCleanup(window.destroy)
        window.present()
        self.settle()
        self.assertGreater(len(grid.bound), 0)
        self.assertLess(len(grid.bound), 1000)
        adjustment = scroller.get_vadjustment()
        for fraction in (.25, .5, .75, 1):
            adjustment.set_value(fraction * (adjustment.get_upper()-adjustment.get_page_size()))
            self.settle()
            self.assertLess(len(grid.bound), 1000)
        grid.set_filter_func(lambda value: value % 2 == 0)
        self.assertEqual(grid.filtered.get_n_items(), 5000)
        grid.emit('activate', 1)
        self.assertEqual(activated, [2])
        grid.remove_all()
        self.settle()
        self.assertEqual(grid.bound, {})


if __name__ == '__main__':
    unittest.main()
