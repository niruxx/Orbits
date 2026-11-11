"""Network, undo, search, packaging and desktop regressions without a real desktop."""
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch
import urllib.error

from orbits import desktop, offline, search, undo
from orbits.downloads import DownloadManager, DownloadCancelled
from orbits.search_provider import SearchProvider

ROOT = Path(__file__).resolve().parents[1]


class DownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = bytes(range(256))*2048
        cls.requests = []
        data, requests = cls.data, cls.requests
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                offset = int(self.headers.get('Range','bytes=0-').split('=')[1].split('-')[0])
                requests.append((self.path,dict(self.headers)))
                if self.path == '/ignore-range':
                    offset = 0
                if self.path == '/not-modified' and self.headers.get('If-None-Match'):
                    self.send_response(304)
                    self.end_headers()
                    return
                self.send_response(206 if offset else 200)
                self.send_header('Content-Length',str(len(data)-offset))
                self.send_header('Content-Type','application/octet-stream')
                self.send_header('ETag','"stable"')
                if offset:
                    self.send_header('Content-Range',f'bytes {offset}-{len(data)-1}/{len(data)}')
                self.end_headers()
                try:
                    self.wfile.write(data[offset:])
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self,*args):
                pass
        cls.server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = DownloadManager(self.root/'cache', opener=__import__('urllib.request', fromlist=['urlopen']).urlopen)
        self.addCleanup(self.manager.close)

    def seed_partial(self, url, amount=100000):
        key = hashlib.sha256((url+'\0md5\0').encode()).hexdigest()
        (self.root/'cache'/f'{key}.part').write_bytes(self.data[:amount])
        (self.root/'cache'/f'{key}.json').write_text(json.dumps({'etag':'"stable"'}))

    def test_transfer_verifies_checksum_and_reuses_complete_cache(self):
        url = self.base+'/image'
        checksum = hashlib.md5(self.data).hexdigest()
        job = self.manager.submit(url,self.root/'first',checksum=checksum)
        self.assertEqual(self.manager.wait(job)[0],'application/octet-stream')
        before = len(self.requests)
        second = self.manager.submit(url,self.root/'second',checksum=checksum)
        self.manager.wait(second)
        self.assertEqual((self.root/'second').read_bytes(),self.data)
        self.assertEqual(len(self.requests),before)

    def test_online_cache_is_revalidated_and_304_reuses_verified_bytes(self):
        url=self.base+'/not-modified'
        self.manager.wait(self.manager.submit(url,self.root/'first'))
        before=len(self.requests)
        self.manager.wait(self.manager.submit(url,self.root/'second'))
        self.assertEqual(len(self.requests),before+1)
        self.assertEqual(self.requests[-1][1]['If-None-Match'],'"stable"')
        self.assertEqual((self.root/'second').read_bytes(),self.data)

    def test_changed_validator_cannot_mix_old_and_new_download_bytes(self):
        url=self.base+'/changed'
        self.seed_partial(url)
        key=hashlib.sha256((url+'\0md5\0').encode()).hexdigest()
        (self.root/'cache'/f'{key}.json').write_text(json.dumps({'etag':'"old"'}))
        job=self.manager.submit(url,self.root/'result')
        with self.assertRaisesRegex(RuntimeError,'remote file changed'):
            self.manager.wait(job)
        self.assertFalse((self.root/'result').exists())
        self.assertFalse((self.root/'cache'/f'{key}.part').exists())

    def test_partial_download_resumes_with_validator(self):
        url = self.base+'/resume'
        self.seed_partial(url)
        self.manager.wait(self.manager.submit(url,self.root/'result'))
        headers = next(headers for path,headers in reversed(self.requests) if path=='/resume')
        self.assertEqual(headers['Range'],'bytes=100000-')
        self.assertEqual(headers['If-Range'],'"stable"')
        self.assertEqual((self.root/'result').read_bytes(),self.data)

    def test_server_ignoring_range_restarts_without_duplicate_bytes(self):
        url = self.base+'/ignore-range'
        self.seed_partial(url)
        self.manager.wait(self.manager.submit(url,self.root/'result'))
        self.assertEqual((self.root/'result').read_bytes(),self.data)

    def test_checksum_mismatch_is_never_delivered(self):
        job = self.manager.submit(self.base+'/bad',self.root/'result',checksum='0'*32)
        with self.assertRaisesRegex(RuntimeError,'checksum mismatch'):
            self.manager.wait(job)
        self.assertFalse((self.root/'result').exists())
        self.assertEqual(list((self.root/'cache').glob('*.complete')),[])

    def test_pause_releases_worker_and_resume_finishes_original_request(self):
        holder = []
        ready = threading.Event()
        paused = threading.Event()
        def progress(_fraction):
            ready.wait(3)
            if not paused.is_set():
                self.manager.pause(holder[0])
                paused.set()
        job = self.manager.submit(self.base+'/pause',self.root/'result',progress=progress)
        holder.append(job)
        ready.set()
        self.assertTrue(paused.wait(3))
        self.assertFalse((self.root/'result').exists())
        self.manager.resume(job)
        self.manager.wait(job)
        self.assertEqual((self.root/'result').read_bytes(),self.data)

    def test_cancel_queued_transfer_unblocks_installer(self):
        with self.manager.lock:
            cache_lock = self.manager.cache_locks.setdefault(self.base+'/cancel', threading.Lock())
        cache_lock.acquire()
        try:
            job = self.manager.submit(self.base+'/cancel',self.root/'result')
            self.manager.cancel(job)
            with self.assertRaises(DownloadCancelled):
                self.manager.wait(job)
            self.assertFalse((self.root/'result').exists())
        finally:
            cache_lock.release()

    def test_invalid_checksum_is_rejected_before_network_access(self):
        with self.assertRaises(ValueError):
            self.manager.submit(self.base+'/invalid',self.root/'result',checksum='not-a-checksum')

    def test_clearing_finished_cache_keeps_partial_resume_data(self):
        job=self.manager.submit(self.base+'/clear',self.root/'result',checksum=hashlib.md5(self.data).hexdigest())
        self.manager.wait(job)
        self.seed_partial(self.base+'/unfinished')
        self.manager.clear_finished()
        self.assertEqual(list((self.root/'cache').glob('*.complete')),[])
        self.assertEqual(len(list((self.root/'cache').glob('*.part'))),1)
        self.assertEqual(len(list((self.root/'cache').glob('*.json'))),1)
        self.assertEqual((self.root/'result').read_bytes(),self.data)


class OfflineTests(unittest.TestCase):
    def test_offline_cached_catalogue_and_missing_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            offline.cached_json(tmp,'query',lambda:{'items':[1]},ttl=0)
            fetch = Mock(side_effect=AssertionError('no network'))
            result, cached = offline.with_status(lambda:offline.cached_json(tmp,'query',fetch,offline=True))
            self.assertEqual(result,{'items':[1]})
            self.assertTrue(cached)
            fetch.assert_not_called()
            with self.assertRaises(offline.OfflineError):
                offline.cached_json(tmp,'other query',fetch,offline=True)

    def test_connection_failure_uses_stale_data_but_authentication_errors_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            offline.cached_json(tmp,'key',lambda:[1],ttl=0)
            result, cached = offline.with_status(lambda:offline.cached_json(tmp,'key',
                Mock(side_effect=urllib.error.URLError('offline')),ttl=0))
            self.assertEqual(result,[1]);self.assertTrue(cached)
            with self.assertRaises(urllib.error.HTTPError):
                offline.cached_json(tmp,'key',Mock(side_effect=urllib.error.HTTPError('url',403,'denied',{},None)),ttl=0)

    def test_corrupt_cache_is_replaced_after_successful_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/(hashlib.sha256(b'key').hexdigest()+'.json')
            path.write_text('broken')
            self.assertEqual(offline.cached_json(tmp,'key',lambda:[2]),[2])
            self.assertEqual(json.loads(path.read_text())['data'],[2])


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'library.json').write_text(json.dumps({'test:1':{'title':'Café Sky','collections':['Night']}}))
        (self.root/'fonts.json').write_text(json.dumps({'font:1':{'family':'Inter'}}))
        (self.root/'themes.json').write_text(json.dumps({'gtk':{'theme:1':{'title':'Nord','names':['Nord Dark']}}}))
        for i in range(30):
            folder=self.root/'looks'/f'look{i}';folder.mkdir(parents=True)
            (folder/'look.json').write_text(json.dumps({'name':f'Look {i}'}))

    def test_index_searches_all_four_kinds_and_normalizes_accents(self):
        records=search.build_index(self.root)
        self.assertEqual({r['kind'] for r in records},{'wallpaper','theme','font','look'})
        self.assertEqual(search.search(records,'CAFE night')[0]['title'],'Café Sky')
        self.assertEqual(search.search(records,'Nord dark')[0]['kind'],'theme')

    def test_provider_can_return_metadata_for_results_outside_first_page(self):
        launch=Mock();provider=SearchProvider(self.root,launch)
        ids=provider.dispatch('GetInitialResultSet',(['Look 29'],))
        self.assertEqual(ids,['look:look29'])
        self.assertEqual(provider.dispatch('GetResultMetas',(ids,))[0]['title'],'Look 29')
        provider.dispatch('ActivateResult',(ids[0],[],0))
        launch.assert_called_once_with(['--apply-look','look:look29'])

    def test_provider_rejects_traversal_and_symlinked_look(self):
        for value in ('look:../secret','look:..','wallpaper:x'):
            with self.assertRaises(ValueError):search.resolve_look(self.root,value)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'look.json').write_text('{}')
            (self.root/'looks'/'outside').symlink_to(tmp)
            with self.assertRaises(ValueError):search.resolve_look(self.root,'look:outside')


class UndoTests(unittest.TestCase):
    def tearDown(self):
        undo.clear()

    def test_deleted_files_and_symlinks_restore_without_touching_external_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);external=root/'external';external.write_text('outside')
            directory=root/'asset';directory.mkdir();(directory/'font').write_text('font')
            (directory/'link').symlink_to(external)
            snapshot=undo.FilesState([directory])
            self.addCleanup(snapshot.cleanup)
            import shutil
            shutil.rmtree(directory)
            snapshot.restore()
            self.assertEqual((directory/'font').read_text(),'font')
            self.assertTrue((directory/'link').is_symlink())
            self.assertEqual(external.read_text(),'outside')

    def test_one_shot_undo_restores_entry_without_losing_new_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'asset';path.write_text('asset')
            store=types.SimpleNamespace(items={'old':{'title':'Old'}},_save=Mock(),emit=Mock())
            snapshot=undo.EntryState(store,['old'],[path])
            del store.items['old'];store.items['new']={'title':'New'};path.unlink()
            record=undo.publish('Removed',snapshot.restore,snapshot.cleanup)
            record.undo()
            self.assertEqual(set(store.items),{'old','new'})
            self.assertEqual(path.read_text(),'asset')
            with self.assertRaises(RuntimeError):record.undo()

    def test_nested_mutations_offer_one_undo_and_failures_offer_none(self):
        observer=Mock();unsubscribe=undo.subscribe(observer);self.addCleanup(unsubscribe)
        state=Mock()
        @undo.reversible('inner')
        def inner():return 4
        @undo.reversible('outer')
        def outer():return inner()
        with patch.object(undo,'DesktopState',return_value=state):
            self.assertEqual(outer(),4)
        observer.assert_called_once()
        observer.call_args.args[0].undo()
        state.restore.assert_called_once()
        observer.reset_mock()
        @undo.reversible('failure')
        def failure():raise ValueError('failed')
        with patch.object(undo,'DesktopState',return_value=Mock()),self.assertRaises(ValueError):failure()
        observer.assert_not_called()


class DesktopTests(unittest.TestCase):
    def test_session_detection(self):
        for value, expected in [('X-Cinnamon','cinnamon'),('Budgie:GNOME','budgie'),('XFCE','xfce'),('GNOME','gnome')]:
            with patch.dict(os.environ,{'XDG_CURRENT_DESKTOP':value,'DESKTOP_SESSION':''}):
                self.assertEqual(desktop.detect(),expected)

    def test_xfce_automatic_dpi_has_normal_text_scaling(self):
        repository=types.SimpleNamespace(GLib=types.SimpleNamespace(Variant=lambda kind,value:value))
        with patch.dict('sys.modules',{'gi.repository':repository}), patch.object(desktop,'xfconf',return_value='-1'):
            self.assertEqual(desktop.XfceInterface().get_value('text-scaling-factor'),1.0)

    def test_xfce_theme_values_are_argv_data(self):
        with patch.object(desktop.subprocess,'run',return_value=types.SimpleNamespace(returncode=0,stdout='',stderr='')) as run:
            desktop.XfceInterface().set_string('gtk-theme','A; $(do not execute)')
        self.assertEqual(run.call_args.args[0][-2:],['--set','A; $(do not execute)'])
        self.assertNotIn('shell',run.call_args.kwargs)

    def test_xfce_wallpaper_updates_only_actual_monitor_properties(self):
        prop='/backdrop/screen0/monitorHDMI-1/workspace0/last-image'
        with (patch.object(desktop.subprocess,'run',return_value=types.SimpleNamespace(stdout=prop+'\n/unrelated\n')),
              patch.object(desktop,'xfconf',return_value='old') as query):
            desktop.xfce_wallpaper('/tmp/new.png')
        writes=[call.args for call in query.call_args_list if len(call.args)>=3]
        self.assertEqual(writes[0],('xfce4-desktop',prop,'/tmp/new.png'))
        self.assertEqual(writes[1][-2:],(5,'int'))


class TranslationTests(unittest.TestCase):
    def test_configuration_keys_are_never_gettext_calls(self):
        import ast
        tree=ast.parse((ROOT/'orbits/ui/preferences.py').read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='_switch':
                self.assertIsInstance(node.args[2],ast.Constant)

    @unittest.skipUnless(__import__('shutil').which('msgfmt'),'gettext tools required')
    def test_spanish_catalogue_compiles_and_translates(self):
        import gettext
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'es/LC_MESSAGES/orbits.mo';output.parent.mkdir(parents=True)
            subprocess.run(['msgfmt','--check','-o',str(output),str(ROOT/'po/es.po')],check=True)
            catalogue=gettext.translation('orbits',localedir=tmp,languages=['es'])
            self.assertEqual(catalogue.gettext('Cancel'),'Cancelar')
            self.assertEqual(catalogue.gettext('not translated'),'not translated')


class PackagingTests(unittest.TestCase):
    def test_release_is_reproducible_and_aur_checksum_matches_archive(self):
        build=runpy.run_path(str(ROOT/'packaging/build-release.py'))['build']
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for part in ('orbits','packaging/aur','packaging/arch','.git','__pycache__'):(root/part).mkdir(parents=True)
            (root/'orbits/__init__.py').write_text('VERSION="1.2.3"')
            (root/'packaging/aur/PKGBUILD.in').write_text('pkgver=@VERSION@\nsha256sums=("@SHA256@")')
            (root/'packaging/arch/orbits.install').write_text('# package hooks')
            (root/'.git/secret').write_text('never bundle')
            first=build(root);content=first.read_bytes();second=build(root)
            self.assertEqual(content,second.read_bytes())
            self.assertIn(hashlib.sha256(content).hexdigest(),(root/'dist/aur/PKGBUILD').read_text())
            import tarfile
            with tarfile.open(first) as archive:self.assertFalse(any('/.git/' in name for name in archive.getnames()))


if __name__=='__main__':
    unittest.main()


# Unit suites execute trusted worker code in child processes; live isolation has
# a separate required integration profile and never changes production behavior.
def setUpModule():
    from tests.parser_fixtures import run_worker
    global _parser_fixture
    _parser_fixture = patch('orbits.parser_sandbox.run', side_effect=run_worker)
    _parser_fixture.start()


def tearDownModule():
    _parser_fixture.stop()
