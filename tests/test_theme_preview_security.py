"""Theme preview discovery and GUI decode boundaries without GTK or a display."""
import importlib.util
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import orbits
from orbits.theme_preview import icon_samples

ROOT = Path(__file__).resolve().parents[1]


class IconDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='orbits-test-icon-preview-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.theme = self.root/'theme'
        self.theme.mkdir()

    def test_only_regular_raster_samples_are_selected(self):
        folder = self.theme/'48x48/places'
        folder.mkdir(parents=True)
        (folder/'folder.png').write_bytes(b'png input remains untrusted until decoding')
        (folder/'home.svg').write_text('<svg><script/></svg>')
        external = self.root/'external.png'
        external.write_bytes(b'external')
        (folder/'linked.png').symlink_to(external)
        (self.theme/'linked-dir').symlink_to(self.root, target_is_directory=True)
        self.assertEqual(icon_samples(self.theme, [['folder'], ['home'], ['linked']]),
                         [folder/'folder.png', None, None])

    def test_discovery_rejects_symlink_roots_and_excessive_depth(self):
        link = self.root/'link'
        link.symlink_to(self.theme, target_is_directory=True)
        with self.assertRaises(ValueError):icon_samples(link, [['folder']])
        parent = self.theme
        for _ in range(21):
            parent = parent/'nested'
            parent.mkdir()
        with self.assertRaisesRegex(ValueError, 'depth limit'):icon_samples(self.theme, [['folder']])

    def test_discovery_rejects_excessive_entries(self):
        class Entries:
            def __enter__(self):return iter([types.SimpleNamespace(stat=lambda **kwargs:types.SimpleNamespace(st_mode=0))]*10001)
            def __exit__(self, *_):pass
        with patch('orbits.theme_preview.os.scandir', return_value=Entries()):
            with self.assertRaisesRegex(ValueError, 'entry limit'):icon_samples(self.theme, [['folder']])


class ThemeCardBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.load = Mock()
        self.themes = types.SimpleNamespace(preview_image=Mock(return_value=Path('/theme/thumbnail.png')))
        self.jobs = []
        net = types.SimpleNamespace(run_preview=lambda work, done:self.jobs.append((work, done)))
        gtk = types.SimpleNamespace(Box=Mock(), Picture=Mock(), Label=Mock(),
                                    ContentFit=Mock(), Align=Mock())
        # Only a Box base class is needed to load the module; widget constructors are spies.
        class Box:
            pass
        gtk.Box = Box
        repository = types.SimpleNamespace(Gtk=gtk, Adw=Mock(), Gdk=Mock(), Gio=Mock(), GLib=Mock())
        with patch.dict('sys.modules', {'gi.repository':repository, 'orbits.net':net,
             'orbits.themes':self.themes, 'orbits.ui.widgets':types.SimpleNamespace(CoverPicture=Mock(), load_texture_into=self.load)}), \
             patch.object(orbits,'net',net,create=True), patch.object(orbits,'themes',self.themes,create=True):
            spec=importlib.util.spec_from_file_location('security_theme_card',ROOT/'orbits/ui/theme_card.py')
            self.module=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.gtk=gtk
        self.card=object.__new__(self.module.ThemeCard)
        self.card.theme=types.SimpleNamespace(kind='gtk',path=Path('/theme'),meta={})
        self.card.preview=Mock()

    def test_small_local_thumbnail_is_always_sent_to_protected_loader(self):
        self.card._build_preview()
        self.load.assert_called_once_with(self.gtk.Picture.return_value, path='/theme/thumbnail.png', size=600)
        self.gtk.Picture.return_value.set_filename.assert_not_called()
        self.card.preview.set_child.assert_called_once_with(self.gtk.Picture.return_value)

    def test_remote_thumbnail_uses_same_protected_loader(self):
        self.themes.preview_image.return_value=None
        self.card.theme.meta={'preview':'https://example.test/preview.png'}
        self.card._build_preview()
        self.assertEqual(self.load.call_args.kwargs, {'url':'https://example.test/preview.png'})

    def test_icon_samples_use_protected_loader_and_released_cards_ignore_results(self):
        self.card.theme.kind='icons'
        self.gtk.Box=Mock()
        self.card._icon_preview()
        self.jobs[0][1]([Path('/theme/folder.png'), None], None)
        self.load.assert_called_once_with(self.gtk.Picture.return_value, path='/theme/folder.png', size=128)
        self.load.reset_mock()
        self.card._grid_released=True
        self.jobs[0][1]([Path('/theme/folder.png')], None)
        self.load.assert_not_called()


class GdmBackgroundBoundaryTests(unittest.TestCase):
    def test_native_loader_receives_only_the_protected_decoder_output(self):
        jobs = []
        net = types.SimpleNamespace(run_preview=lambda work, done:jobs.append((work, done)))
        repository = types.SimpleNamespace(Gtk=types.SimpleNamespace(Widget=object, Overlay=object),
            Gdk=Mock(), GdkPixbuf=Mock(), Gio=Mock(), GLib=Mock(), Graphene=Mock(), Gsk=Mock(), Pango=Mock())
        stubs = {'gi.repository':repository, 'orbits.net':net,
            'orbits.gdm.backgrounds':types.SimpleNamespace(background_path=lambda values, variant:values['bg_image']),
            'orbits.gdm.model':types.SimpleNamespace(ACCENT_HEX={}, login_users=Mock()),
            'orbits.wallpaper_tools':types.SimpleNamespace(compose_monitors=Mock()),
            'orbits.ui.widgets':types.SimpleNamespace(load_optional_texture_into=Mock())}
        with patch.dict('sys.modules',stubs), patch.object(orbits,'net',net,create=True):
            spec=importlib.util.spec_from_file_location('security_gdm_preview',ROOT/'orbits/ui/gdm_preview.py')
            module=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        preview=object.__new__(module.GdmPreview)
        values={'bg_image':'/untrusted/background.png'}
        preview.model=types.SimpleNamespace(values=values,get=lambda key,default=None:values.get(key,default))
        preview.bg_texture_path=None
        preview.refresh=Mock()
        preview._ensure_bg_texture()
        normalized=[]
        def normalize(source, destination, **kwargs):
            self.assertEqual(source, '/untrusted/background.png')
            self.assertEqual(kwargs, {'size':2560})
            normalized.append(Path(destination)/'sanitized.png')
            return [normalized[-1]]
        with patch('orbits.image_safety.normalize_images',side_effect=normalize):
            jobs[0][0]()
        repository.GdkPixbuf.Pixbuf.new_from_file_at_scale.assert_called_once_with(
            str(normalized[0]), 2560, 2560, True)


if __name__ == '__main__':
    unittest.main()
