"""Permanent theme removal, cache ownership and retained-variant protection."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from orbits import boot,config,downloads,themes,theme_cleanup,undo
from orbits.sources.base import Item,Download


class ThemeCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='orbits-test-theme-cleanup-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.data=self.root/'data';self.data.mkdir()
        self.cache=self.root/'cache';(self.cache/'downloads').mkdir(parents=True)
        self.thumbs=self.cache/'thumbs';self.thumbs.mkdir()
        for target,value in ((('orbits.config.DATA_DIR'),self.data),(('orbits.config.CACHE_DIR'),self.cache),
                             (('orbits.config.THUMB_CACHE'),self.thumbs),(('orbits.downloads._manager'),None),
                             (('orbits.themes.INDEX_FILE'),self.data/'themes.json'),
                             (('orbits.boot.INDEX'),self.data/'boot-themes.json'),(('orbits.themes.HOME'),self.root)):
            context=patch(target,value);context.start();self.addCleanup(context.stop)
        self.item=Item('store','123','Fixture',thumb_url='https://example.test/thumb.png',preview_url='https://example.test/large.png')
        self.download=Download('fixture.zip','https://example.test/fixture.zip')
        self.entry={'key':self.item.key,'names':['Dark','Light'],'preview':self.item.thumb_url,
                    'cache':theme_cleanup.cache_identity(self.item,self.download)}
        self.archive=next(iter(self.entry['cache']['downloads']))
        self.artifacts=[]
        for suffix in ('.complete','.part','.json','.tmp'):
            path=self.cache/'downloads'/(self.archive+suffix);path.write_text('cached');self.artifacts.append(path)
        for key in self.entry['cache']['previews']:
            path=self.thumbs/key;path.write_text('preview');self.artifacts.append(path)
        self.kind=replace(themes.KINDS['gtk'],user_dir=self.root/'themes',search_dirs=(self.root/'themes',))
        context=patch.dict(themes.KINDS,{'gtk':self.kind});context.start();self.addCleanup(context.stop)
        for name in self.entry['names']:
            path=self.kind.user_dir/name/'gtk-3.0';path.mkdir(parents=True)
            (path/'gtk.css').write_text('/* data */')
        self.manager=themes.ThemeManager(self.kind)
        self.manager.items={self.item.key:self.entry}
        self.manager._save()
        for target,value in (('orbits.themes.current',Mock(return_value='Dark')),
                             ('orbits.themes.apply',Mock()),('orbits.themes.libadwaita_state',Mock(return_value=('Dark',False))),
                             ('orbits.themes.remove_libadwaita',Mock())):
            context=patch(target,value);context.start();self.addCleanup(context.stop)

    def test_cache_is_retained_until_last_variant_then_removed(self):
        record_count=len(undo._records)
        self.manager.uninstall_user('Dark')
        self.assertFalse((self.kind.user_dir/'Dark').exists())
        self.assertEqual(self.manager.items[self.item.key]['names'],['Light'])
        self.assertTrue(all(path.exists() for path in self.artifacts))
        themes.apply.assert_called_once_with('gtk',self.kind.default)
        themes.remove_libadwaita.assert_called_once()
        self.manager.uninstall_user('Light')
        self.assertFalse((self.kind.user_dir/'Light').exists())
        self.assertNotIn(self.item.key,self.manager.items)
        self.assertTrue(all(not path.exists() for path in self.artifacts))
        self.assertEqual(len(undo._records),record_count)

    def test_failed_removal_preserves_files_registry_and_cache_and_reports_failure(self):
        done=Mock()
        with patch('orbits.themes.shutil.rmtree',side_effect=PermissionError('fixture denied')):
            self.manager.uninstall(self.item.key,done)
        self.assertFalse(done.call_args.args[0])
        self.assertIn('fixture denied',done.call_args.args[1])
        self.assertEqual(self.manager.items[self.item.key]['names'],['Dark','Light'])
        self.assertTrue(all(path.exists() for path in self.artifacts))

    def test_shared_item_cache_is_preserved(self):
        shared={**self.entry,'key':'other:item','names':['Other']}
        self.manager.items[shared['key']]=shared
        other=self.kind.user_dir/'Other/gtk-3.0';other.mkdir(parents=True);(other/'gtk.css').write_text('')
        self.manager._save()
        self.manager.uninstall(self.item.key)
        self.assertTrue(all(path.exists() for path in self.artifacts))
        self.assertTrue(other.is_dir())

    def test_only_matching_marked_staging_data_is_removed(self):
        staging=self.data/'staging'
        for name,key in (('owned',self.item.key),('unrelated','other:item')):
            directory=staging/name;directory.mkdir(parents=True)
            (directory/'.orbits.json').write_text(json.dumps({'key':key}))
        self.manager.uninstall(self.item.key)
        self.assertFalse((staging/'owned').exists())
        self.assertTrue((staging/'unrelated').exists())

    def test_invalid_cache_ids_cannot_escape_cache_directories(self):
        outside=self.root/'outside';outside.write_text('keep')
        theme_cleanup.cleanup({'cache':{'downloads':['../../outside'],'previews':['../outside']}})
        self.assertTrue(outside.exists())

    def test_unreadable_registry_blocks_cache_deletion(self):
        (self.data/'themes.json').write_text('not JSON')
        with self.assertRaisesRegex(RuntimeError,'ownership'):
            theme_cleanup.cleanup(self.entry)
        self.assertTrue(all(path.exists() for path in self.artifacts))

    def test_legacy_refind_source_archive_is_removed_after_last_flavour(self):
        legacy={'key':'boot-refind:catppuccin~refind','page':'https://github.com/catppuccin/refind',
                'preview':'https://example.test/legacy-preview','names':['refind-mocha']}
        key=theme_cleanup.download_key('https://codeload.github.com/catppuccin/refind/zip/HEAD')
        archive=self.cache/'downloads'/(key+'.complete');archive.write_bytes(b'old archive')
        manager=boot.BootThemeManager('refind');manager.items={legacy['key']:legacy};manager._save()
        manager.forget('refind-mocha')
        self.assertFalse(archive.exists())
        self.assertEqual(manager.items,{})

    def test_updated_install_retains_ownership_of_older_downloads(self):
        new_download=Download('new.zip','https://example.test/new-version.zip')
        cache=theme_cleanup.cache_identity(self.item,new_download,self.entry)
        self.assertIn(self.archive,cache['downloads'])
        self.assertIn(theme_cleanup.download_key(new_download.url),cache['downloads'])

    def _system_manager(self):
        self.manager.items={};self.manager._save()
        kind=replace(themes.KINDS['plymouth'],search_dirs=(self.root/'system-splash',))
        context=patch.dict(themes.KINDS,{'plymouth':kind});context.start();self.addCleanup(context.stop)
        manager=themes.ThemeManager(kind)
        manager.items={self.item.key:{**self.entry,'names':['Splash']}}
        manager._save()
        return manager

    def test_system_only_theme_removal_waits_for_helper_before_cache_cleanup(self):
        manager=self._system_manager()
        done=Mock()
        with patch('orbits.privileged.run_helper') as helper:
            manager.uninstall(self.item.key,done)
            self.assertIn(self.item.key,manager.items)
            self.assertTrue(all(path.exists() for path in self.artifacts))
            helper.call_args.args[1](True,'removed')
        done.assert_called_once_with(True,'removed')
        self.assertEqual(manager.items,{})
        self.assertTrue(all(not path.exists() for path in self.artifacts))

    def test_failed_privileged_remove_preserves_cache_and_registry(self):
        manager=self._system_manager()
        done=Mock()
        with patch('orbits.privileged.run_helper') as helper:
            manager.uninstall(self.item.key,done)
            helper.call_args.args[1](False,'read-only filesystem')
        done.assert_called_once_with(False,'read-only filesystem')
        self.assertIn(self.item.key,manager.items)
        self.assertTrue(all(path.exists() for path in self.artifacts))

    def test_finished_transfer_history_is_removed_with_owned_archive(self):
        changed=Mock()
        manager=downloads.DownloadManager(self.cache/'downloads',changed=changed)
        self.addCleanup(manager.close)
        job=downloads.Transfer(self.download.url,self.root/'temporary-download',state='completed')
        manager.jobs[job.id]=job
        with patch('orbits.downloads._manager',manager):
            self.manager.uninstall(self.item.key)
        self.assertEqual(manager.jobs,{})
        changed.assert_called()

    def test_active_transfer_cache_is_not_deleted(self):
        job=Mock(state='downloading',url=self.download.url,checksum='',algorithm='md5')
        with patch('orbits.downloads._manager',Mock(jobs={'active':job})):
            self.manager.uninstall(self.item.key)
        self.assertTrue((self.cache/'downloads'/(self.archive+'.part')).exists())


if __name__=='__main__':unittest.main()
