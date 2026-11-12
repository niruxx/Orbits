"""Collection directories, selected-face metadata and asynchronous inspector races."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import orbits
from orbits import font_worker, font_safety

ROOT = Path(__file__).resolve().parents[1]


def metadata(face=0, count=2):
    return dict(family=f'Family {face}', style='Regular', full_name=f'Face {face}',
                version='', designer='', license='', license_url='', glyph_count=1,
                codepoints=[65+face], features=[], axes=[], face=face, face_count=count)


class CollectionDirectoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='orbits-test-collection-')
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)/'collection.ttc'

    def collection(self, count, version=0x10000):
        self.path.write_bytes(b'ttcf'+struct.pack('>II', version, count)+b'\0\0\0\0'*min(count, 256))
        return self.path

    def test_collection_count_is_bounded_and_directory_must_be_complete(self):
        for count in (1, 2, 256):
            self.assertEqual(font_worker.face_count(self.collection(count)), count)
        for count in (0, 257, 0xffffffff):
            with self.subTest(count=count), self.assertRaises(ValueError):
                font_worker.face_count(self.collection(count))
        self.collection(2)
        self.path.write_bytes(self.path.read_bytes()[:-1])
        with self.assertRaises(ValueError):font_worker.face_count(self.path)

    def test_truncated_and_unknown_version_headers_are_rejected(self):
        for content in (b'ttcf', b'ttcf'+struct.pack('>II', 3, 1)+bytes(4)):
            self.path.write_bytes(content)
            with self.assertRaises(ValueError):font_worker.face_count(self.path)
        self.assertEqual(font_worker.face_count(self.collection(2, 0x20000)), 2)

    def test_worker_passes_selected_face_to_fonttools(self):
        path = self.collection(2)
        font = Mock()
        font.get.return_value = None
        font.getBestCmap.return_value = {66: 'B'}
        font.getGlyphOrder.return_value = ['.notdef', 'B']
        font.__contains__ = Mock(return_value=False)
        context = Mock(__enter__=Mock(return_value=font), __exit__=Mock(return_value=False))
        factory = Mock(return_value=context)
        with patch.object(font_worker, 'font_class', return_value=factory):
            result = font_worker.inspect_font(path, 1)
        self.assertEqual(factory.call_args.kwargs['fontNumber'], 1)
        self.assertEqual((result['face'], result['face_count'], result['codepoints']), (1, 2, [66]))
        with self.assertRaises(ValueError):font_worker.inspect_font(path, 2)
        self.path.write_bytes(b'\0\1\0\0'+bytes(8))
        self.assertEqual(font_worker.face_count(path), 1)
        with self.assertRaises(ValueError):font_worker.inspect_font(path, 1)

    def test_parent_rejects_invalid_worker_counts(self):
        for count in (0, 257, True, '2', None):
            with self.subTest(count=count), patch.object(font_safety, '_process', return_value=metadata(count=count)):
                with self.assertRaises(ValueError):font_safety.inspect_font(self.path)
        with patch.object(font_safety, '_process', return_value=metadata(count=1)):
            with self.assertRaises(ValueError):font_safety.inspect_font(self.path, 1)
        with patch.object(font_safety, '_process', return_value=metadata(face=1)):
            self.assertEqual(font_safety.inspect_font(self.path, 1)['face_count'], 2)


class InspectorFaceTests(unittest.TestCase):
    def setUp(self):
        self.jobs = []
        self.inspect = Mock()
        self.net = types.SimpleNamespace(run_async=lambda work, done: self.jobs.append((work, done)))
        self.library = Mock()
        self.render = Mock()
        repository = types.SimpleNamespace(Adw=types.SimpleNamespace(Dialog=object),
            Gtk=types.SimpleNamespace(StringList=types.SimpleNamespace(new=lambda values: values)),
            Gdk=Mock(), Pango=Mock(), PangoCairo=Mock())
        stubs = {'gi.repository': repository, 'orbits.net': self.net,
                 'orbits.fonts': types.SimpleNamespace(get_font_library=lambda:self.library, system_families=Mock()),
                 'orbits.ui.font_preview': types.SimpleNamespace(load=self.render),
                 'orbits.ui.font_actions': types.SimpleNamespace(font_attrs=Mock())}
        with patch.dict('sys.modules', stubs), patch.object(orbits, 'net', self.net, create=True):
            spec = importlib.util.spec_from_file_location('collection_inspector', ROOT/'orbits/ui/font_tools.py')
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.module.font_tools = types.SimpleNamespace(inspect_font=self.inspect)
        self.dialog = object.__new__(self.module.FontInspector)
        d = self.dialog
        d.files = ['collection.ttc', 'single.ttf']
        d.file_choice = Mock(get_selected=Mock(return_value=0))
        d.face_choice = Mock()
        d._syncing_faces = False
        d.closed = False
        d.generation = 0
        d.metadata = None
        d.sample = types.SimpleNamespace(_font_generation=0, set_paintable=Mock())
        d.sample_text = Mock(get_text=Mock(return_value='AB'))
        d.info, d.license, d.axis_group, d.feature_group, d.window = [Mock() for _ in range(5)]
        d.axis_rows, d.feature_rows, d.axes, d.features = [], [], {}, {}
        d.entry = {}
        d.glyph_page = Mock()

    def test_selected_face_controls_metadata_preview_and_applied_family(self):
        d = self.dialog
        d.load(1)
        self.jobs[0][0]()
        self.inspect.assert_called_once_with('collection.ttc', 1)
        self.jobs[0][1](metadata(1), None)
        self.assertEqual(d.metadata['codepoints'], [66])
        d.face_choice.set_selected.assert_called_with(1)
        d.face_choice.set_visible.assert_called_with(True)
        self.assertEqual(self.render.call_args.kwargs['face'], 1)
        d.apply('document')
        self.library.apply.assert_called_once_with('Family 1', 'document', variations='')

    def test_switching_files_resets_face_and_discards_old_result(self):
        d = self.dialog
        d.load(1)
        old_generation = d.sample._font_generation
        d.file_choice.get_selected.return_value = 1
        d._file_changed()
        self.assertGreater(d.sample._font_generation, old_generation)
        self.assertIsNone(d.metadata)
        self.jobs[0][1](metadata(1), None)
        self.assertIsNone(d.metadata)
        self.jobs[1][0]()
        self.inspect.assert_called_with('single.ttf', 0)
        self.jobs[1][1](metadata(0, 1), None)
        d.face_choice.set_visible.assert_called_with(False)
        self.assertEqual(d.preview_path, 'single.ttf')

    def test_error_clears_old_metadata_and_prevents_stale_apply(self):
        d = self.dialog
        d.metadata = metadata()
        d.load(1)
        self.jobs[0][1](None, ValueError('Bad font face'))
        d.apply('interface')
        self.library.apply.assert_not_called()
        self.assertIsNone(d.metadata)
        d.info.set_label.assert_called_with('Bad font face')

    def test_programmatic_selector_sync_does_not_start_new_request(self):
        d = self.dialog
        d.face_choice.set_selected.side_effect = lambda _value:d._face_changed(d.face_choice, None)
        d._set_faces(2, 1)
        self.assertEqual(self.jobs, [])


class FontPreviewFaceTests(unittest.TestCase):
    def test_raster_preview_receives_face_and_invalidates_previous_request(self):
        jobs = []
        render = Mock(return_value=b'png')
        repository = types.SimpleNamespace(Gdk=Mock(), GLib=types.SimpleNamespace(
            timeout_add=lambda _delay, callback:callback(), Bytes=Mock()))
        net = types.SimpleNamespace(run_async=lambda work, done:jobs.append((work, done)))
        with patch.dict('sys.modules', {'gi.repository':repository, 'orbits.net':net}), patch.object(orbits,'net',net,create=True):
            spec = importlib.util.spec_from_file_location('collection_preview',ROOT/'orbits/ui/font_preview.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        module.render_font = render
        picture = types.SimpleNamespace(_font_generation=0, set_paintable=Mock(), set_tooltip_text=Mock())
        module.load(picture, 'fonts.ttc', 'AB', 24, face=1)
        jobs[0][0]()
        render.assert_called_once_with('fonts.ttc', 'AB', 24, face=1, variations=None, features=None)
        module.load(picture, 'fonts.ttc', 'AB', 24, face=0)
        jobs[0][1](b'old face', None)
        picture.set_paintable.assert_not_called()


if __name__ == '__main__':
    unittest.main()
