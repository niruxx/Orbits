"""Storage and source contract tests with desktop/network boundaries replaced.

The real library/source code runs against temporary files, with no desktop writes or HTTP calls.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch, Mock

from orbits import rotation

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="orbits-test-state-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wallpapers = self.root/"wallpapers"
        self.wallpapers.mkdir()
        self.settings = types.SimpleNamespace(get=lambda key: False)
        self.config = types.SimpleNamespace(DATA_DIR=self.root, CONFIG_DIR=self.root,
            BG_PROPERTIES_DIR=self.root/"backgrounds", download_dir=lambda:self.wallpapers, settings=self.settings)
        class Settings:
            def __init__(self):
                self.values={}
            def get_string(self,key):
                return self.values.get(key,"")
            def set_string(self,key,value):
                self.values[key]=value
            def connect(self,*args):
                pass
        class Object:
            def emit(self,*args):
                pass
        self.repository=types.SimpleNamespace(
            GLib=types.SimpleNamespace(filename_to_uri=lambda value:Path(value).as_uri(), get_user_config_dir=lambda:str(self.root)),
            Gio=types.SimpleNamespace(Settings=types.SimpleNamespace(new=lambda _schema:Settings(),sync=lambda:None)),
            GObject=types.SimpleNamespace(Object=Object,SignalFlags=types.SimpleNamespace(RUN_FIRST=0)))
        self.net=types.SimpleNamespace(get_json=Mock())
        self.patch=patch.dict('sys.modules',{
            'gi':types.ModuleType('gi'), 'gi.repository':self.repository,
            'orbits.config':self.config,'orbits.net':self.net,
            'orbits.sources.base':types.SimpleNamespace(Download=type('Download',(),{}), Item=type('Item',(),{}))})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.library=load_module('orbits_test_library','orbits/library.py')

    def new_entry(self, library, name):
        folder=self.wallpapers/name
        folder.mkdir(exist_ok=True)
        image=folder/"image.png"
        image.write_bytes(name.encode())
        library.items[name]={"key":name,"title":name,"source":"local","dir":str(folder),
                             "files":[{"light":str(image),"dark":None}]}
        library._save()

    def test_timer_install_and_gui_metadata_changes_are_merged(self):
        first=self.library.Library()
        self.new_entry(first,"one")
        second=self.library.Library()
        self.new_entry(second,"two")
        first.set_favorite("one",True)
        third=self.library.Library()
        self.assertEqual(set(third.items),{"one","two"})
        self.assertTrue(third.items["one"]["favorite"])
        second.set_collections("two",["Nature","Nature"," Work "])
        third.reload_index()
        self.assertTrue(third.items["one"]["favorite"])
        self.assertEqual(third.items["two"]["collections"],["Nature","Work"])

    def test_concurrent_metadata_edits_on_one_item_merge(self):
        first=self.library.Library()
        self.new_entry(first,"one")
        second=self.library.Library()
        first.set_favorite("one",True)
        second.set_collections("one",["Nature"])
        first.reload_index()
        self.assertTrue(first.items["one"]["favorite"])
        self.assertEqual(first.items["one"]["collections"],["Nature"])

    def test_deleted_entries_are_not_resurrected_by_unrelated_save(self):
        first=self.library.Library()
        self.new_entry(first,"one")
        self.new_entry(first,"two")
        second=self.library.Library()
        first.uninstall("one")
        second.set_favorite("two",True)
        self.assertEqual(set(json.loads(self.library.LIBRARY_FILE.read_text())),{"two"})

    def test_generated_spanned_wallpaper_applies_correct_mode(self):
        library=self.library.Library()
        path=self.root/"composite.png"
        path.write_bytes(b"image")
        entry=library.add_generated(path,"Monitors",options="spanned")
        library.apply(entry["files"][0])
        self.assertEqual(library._bg.get_string("picture-options"),"spanned")
        self.assertNotEqual(entry["files"][0]["light"],str(path))

    def test_failed_timer_configuration_restores_previous_files(self):
        path=self.root/"rotation.json"
        path.write_text('{"enabled":false,"minutes":10}')
        unit=self.root/"systemd/user/orbits-wallpaper.timer"
        unit.parent.mkdir(parents=True)
        unit.write_text("previous timer")
        with patch.object(rotation,'systemctl',side_effect=RuntimeError("No session bus")), patch.object(rotation.subprocess,'run'):
            with self.assertRaisesRegex(RuntimeError,"No session bus"):
                rotation.configure({"enabled":True,"minutes":15})
        self.assertEqual(json.loads(path.read_text())["minutes"],10)
        self.assertEqual(unit.read_text(),"previous timer")
        self.assertFalse((unit.parent/"orbits-wallpaper.service").exists())

    def test_daily_slideshow_owns_frames_and_relocates_embedded_paths(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("python-pillow required")
        library=self.library.Library()
        image=self.root/"source.png"
        Image.new("RGB",(20,20),"red").save(image)
        entry=library.create_slideshow([(0,image),(43200,image)])
        original=entry["dir"]
        xml=Path(entry["files"][0]["light"])
        self.assertIn(original,xml.read_text())
        library.relocate(self.root/"moved")
        entry=library.items[entry["key"]]
        xml=Path(entry["files"][0]["light"])
        self.assertNotIn(original,xml.read_text())
        self.assertIn(entry["dir"],xml.read_text())
        self.assertTrue(Path(entry["files"][0]["preview"]).exists())


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.net=types.SimpleNamespace(get_json=Mock())
        self.values={}
        self.config=types.SimpleNamespace(settings=types.SimpleNamespace(get=lambda key:self.values.get(key)))
        # Load actual Item/Download definitions without importing the provider registry.
        import runpy
        definitions=runpy.run_path(str(ROOT/'orbits/sources/base.py'))
        base=types.SimpleNamespace(**{key:definitions[key] for key in ('Source','Download','Item')})
        with patch.dict('sys.modules',{'orbits.net':self.net,'orbits.config':self.config,'orbits.sources.base':base}):
            self.sources=load_module('orbits_test_extra','orbits/sources/extra.py')

    def test_nasa_skips_video_days_and_prefers_hd(self):
        self.net.get_json.return_value=[{"date":"2026-01-01","media_type":"video","url":"video"},
            {"date":"2026-01-02","media_type":"image","url":"https://nasa.test/small.jpg","hdurl":"https://nasa.test/large.jpg","copyright":"Creator"}]
        items,_=self.sources.NasaSource().fetch('', '', '', 1)
        self.assertEqual(len(items),1)
        self.assertEqual(items[0].downloads[0].url,'https://nasa.test/large.jpg')
        self.assertEqual(items[0].author,'Creator')

    def test_openverse_keeps_license_and_resolution(self):
        self.net.get_json.return_value={"page_count":2,"results":[{"id":"1","url":"https://images.test/a.jpg","title":"A &amp; B",
            "width":3840,"height":2160,"license":"by","license_version":"4.0","license_url":"https://license.test/","creator":"Artist"}]}
        items,more=self.sources.OpenverseSource().fetch('nature','','',1)
        self.assertTrue(more)
        self.assertEqual(items[0].title,'A & B')
        self.assertEqual(items[0].resolution,'3840x2160')
        self.assertIn('by 4.0',items[0].description)

    def test_reddit_requires_token_and_excludes_nsfw_or_external_links(self):
        source=self.sources.RedditSource()
        with self.assertRaisesRegex(RuntimeError,'OAuth'):
            source.fetch('','','',1)
        self.values['reddit_token']='test-token'
        self.net.get_json.return_value={"data":{"after":"next","children":[{"data":{"id":"ok","url":"https://i.redd.it/a.png","permalink":"/r/wallpapers/ok"}},
            {"data":{"id":"nsfw","over_18":True,"url":"https://i.redd.it/b.png"}},
            {"data":{"id":"external","url":"https://example.com/a.png"}}]}}
        items,more=source.fetch('','','',1)
        self.assertEqual([item.id for item in items],['ok'])
        self.assertTrue(more)
        self.assertEqual(self.net.get_json.call_args.kwargs['headers']['Authorization'],'Bearer test-token')
        source.fetch('','','',2)
        self.assertEqual(self.net.get_json.call_args.args[1]['after'],'next')


if __name__ == '__main__':
    unittest.main()
