"""Theme feature regressions with network, GTK and privileged boundaries replaced."""
import configparser
import importlib.util
import json
from pathlib import Path
import plistlib
import runpy
import shutil
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from orbits import ocs_details, terminal_schemes as terminal, theme_health

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    import sys
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules,{name:module}):
        spec.loader.exec_module(module)
    return module


def scheme():
    return {"name":"Example", "background":"#121212", "foreground":"#eeeeee", "colors":[f"#{i*10:02x}3040" for i in range(16)]}


class TerminalSchemeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='orbits-test-terminal-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_exported_formats_round_trip_all_ansi_colors(self):
        terminal.export_scheme(scheme(),self.root)
        for name in ('terminal.json','orbits.toml','orbits.conf','orbits.palette'):
            result=terminal.read_scheme(self.root/name)
            self.assertEqual(result['colors'],scheme()['colors'])
            self.assertEqual(result['background'],scheme()['background'])

    def test_konsole_rgb_palette_is_read_as_data(self):
        text='[General]\nDescription=Konsole\n[Background]\nColor=10,20,30\n[Foreground]\nColor=220,230,240\n'
        for suffix in ('','Intense'):
            for i in range(8):
                text+=f'[Color{i}{suffix}]\nColor={i},20,30\n'
        path=self.root/'konsole.colorscheme'
        path.write_text(text)
        result=terminal.read_scheme(path)
        self.assertEqual(result['background'],'#0a141e')
        self.assertEqual(len(result['colors']),16)

    def test_iterm_plist_channels_are_validated(self):
        rgb={'Red Component':.5,'Green Component':.25,'Blue Component':0}
        data={f'Ansi {i} Color':rgb for i in range(16)}
        data.update({'Background Color':rgb,'Foreground Color':rgb})
        path=self.root/'example.itermcolors'
        path.write_bytes(plistlib.dumps(data))
        self.assertEqual(terminal.read_scheme(path)['colors'][0],'#804000')

    def test_discovery_ignores_install_scripts_and_unrelated_json(self):
        (self.root/'install.sh').write_text('touch /tmp/never-execute-orbits-test')
        (self.root/'unrelated.json').write_text('{"name":"not a palette"}')
        terminal.export_scheme(scheme(),self.root/'valid')
        self.assertEqual(len(terminal.find_schemes(self.root)),4)

    def test_incomplete_or_css_injection_palettes_are_rejected(self):
        bad=scheme();bad['foreground']='red; arbitrary css'
        with self.assertRaises(ValueError):terminal.normalize(bad)
        bad=scheme();bad['colors']=bad['colors'][:15]
        with self.assertRaises(ValueError):terminal.normalize(bad)

    def test_ptyxis_dark_variant_is_selected(self):
        path=self.root/'adaptive.palette'
        text='[Palette]\nName=Adaptive\n[Dark]\nBackground=#111111\nForeground=#eeeeee\n'
        text+=''.join(f'Color{i}=#123456\n' for i in range(16))
        path.write_text(text)
        self.assertEqual(terminal.read_scheme(path)['background'],'#111111')


class OcsTests(unittest.TestCase):
    def test_rating_version_and_multiline_changelog(self):
        result=ocs_details.details({'status':'ok','data':[{'score':75,'version':'2.1','changelog':'<p>First</p><p>Second &amp; third</p>'}]})
        self.assertEqual(result['score'],75)
        self.assertEqual(result['changelog'],'First\nSecond & third')

    def test_nested_response_and_missing_rating(self):
        result=ocs_details.details({'ocs':{'meta':{'statuscode':100},'data':{'content':{'version':'1'}}}})
        self.assertIsNone(result['score'])
        with self.assertRaises(RuntimeError):ocs_details.details({'status':'failed','message':'Not found'})

    def test_comments_include_replies_and_preserve_plain_text(self):
        data={'status':'ok','data':{'comment':[{'id':1,'user':'A <b>Name</b>','text':'<p>Hello &amp; welcome</p><script>hide()</script>',
              'children':{'comment':{'id':2,'user':'Reply','text':'Text'}}}]}}
        result,more=ocs_details.comments(data)
        self.assertEqual(result[0]['author'],'A Name')
        self.assertEqual(result[0]['text'],'Hello & welcome')
        self.assertEqual(result[1]['depth'],1)
        self.assertFalse(more)

    def test_comment_pagination_and_empty_payload(self):
        records=[{'id':i,'text':'body'} for i in range(10)]
        result,more=ocs_details.comments({'status':'ok','data':{'comment':records}})
        self.assertEqual(len(result),10)
        self.assertTrue(more)
        self.assertEqual(ocs_details.comments({'status':'ok','data':None}),([],False))


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='orbits-test-health-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'gtk-4.0').mkdir()
        self.css=self.root/'gtk-4.0/gtk.css'

    def test_modern_css_is_not_flagged_by_name(self):
        self.css.write_text(':root { --window-bg-color: #111111; --accent-bg-color: #123456; }')
        self.assertEqual(theme_health.check(self.root,'1.8','4.20'),[])

    def test_removed_gtk3_property_reports_file_and_line(self):
        self.css.write_text('button {\n -GtkWidget-focus-line-width: 1;\n}\n')
        issues=theme_health.check(self.root,'1.8','4.20')
        self.assertEqual(issues[0]['code'],'gtk3-style-property')
        self.assertEqual(issues[0]['line'],2)
        self.assertEqual(issues[0]['severity'],'error')

    def test_missing_import_and_legacy_color_heuristic(self):
        self.css.write_text('@import "missing.css";\n@define-color window_bg_color #111111;')
        issues=theme_health.check(self.root,'1.8','4.20')
        self.assertEqual({i['code'] for i in issues},{'missing-import','legacy-colors'})
        self.assertIn('heuristic',next(i['message'] for i in issues if i['code']=='legacy-colors'))

    def test_author_version_range_is_compared_numerically(self):
        self.css.write_text(':root {}')
        (self.root/'index.theme').write_text('[Orbits]\nLibadwaitaMax=1.9\nGTKMin=4.18\n')
        issues=theme_health.check(self.root,'1.10','4.16')
        self.assertEqual([i['code'] for i in issues],['version-range','version-range'])

    def test_comments_are_ignored_and_external_import_is_not_read(self):
        self.css.write_text('/* -GtkWidget-fake: 1; */\n@import "../../../private.css";')
        result=theme_health.check(self.root,'1.8','4.20')
        self.assertEqual([i['code'] for i in result],['external-import'])


class ThemeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='orbits-test-theme-integration-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        class KeyFile:
            def __init__(self):self.parser=configparser.ConfigParser(interpolation=None)
            def load_from_file(self,path,_flags):
                with open(path) as stream:self.parser.read_file(stream)
            def has_group(self,group):return group in self.parser
        class Object:
            def emit(self,*args):pass
        settings=types.SimpleNamespace(get=lambda _key:None)
        repository=types.SimpleNamespace(Gio=types.SimpleNamespace(Settings=types.SimpleNamespace(sync=lambda:None)),
            GLib=types.SimpleNamespace(get_user_data_dir=lambda:str(self.root/'data'),get_user_config_dir=lambda:str(self.root/'config'),
                KeyFile=KeyFile,KeyFileFlags=types.SimpleNamespace(NONE=0),Error=(OSError,configparser.Error)),
            GObject=types.SimpleNamespace(Object=Object,SignalFlags=types.SimpleNamespace(RUN_FIRST=0)))
        defs=runpy.run_path(str(ROOT/'orbits/sources/base.py'))
        self.base=types.SimpleNamespace(**{name:defs[name] for name in ('Item','Download','Source')})
        self.net=types.SimpleNamespace(download=Mock())
        patches={'gi':types.ModuleType('gi'),'gi.repository':repository,'orbits.net':self.net,
                 'orbits.config':types.SimpleNamespace(DATA_DIR=self.root,settings=settings),
                 'orbits.sources.kde':types.SimpleNamespace(OcsSource=object)}
        self.patch=patch.dict('sys.modules',patches)
        self.patch.start();self.addCleanup(self.patch.stop)
        import orbits
        package=patch.object(orbits,'net',self.net,create=True)
        package.start();self.addCleanup(package.stop)
        with patch.object(Path,'home',return_value=self.root):
            self.themes=load_module('orbits_test_themes','orbits/themes.py')
        # Registry paths are temporary; no real user directories are inspected.
        from dataclasses import replace
        for key,kind in self.themes.KINDS.items():
            if kind.user_dir:
                self.themes.KINDS[key]=replace(kind,search_dirs=(kind.user_dir,))

    def test_legacy_and_sound_detection(self):
        root=self.root/'archive'
        (root/'old/gtk-2.0').mkdir(parents=True)
        (root/'old/gtk-2.0/gtkrc').write_text('')
        (root/'older/gtk').mkdir(parents=True)
        (root/'older/gtk/gtkrc').write_text('')
        (root/'sound').mkdir()
        (root/'sound/index.theme').write_text('[Sound Theme]\nName=Example\nInherits=freedesktop\n')
        self.assertEqual([n for _,n in self.themes.find_themes(root,self.themes.KINDS['gtk2'])],['old'])
        self.assertEqual([n for _,n in self.themes.find_themes(root,self.themes.KINDS['gtk1'])],['older'])
        self.assertEqual([n for _,n in self.themes.find_themes(root,self.themes.KINDS['sounds'])],['sound'])

    def test_gtk2_apply_and_reset_preserve_user_rc(self):
        kind=self.themes.KINDS['gtk2']
        (kind.user_dir/'Example/gtk-2.0').mkdir(parents=True)
        (kind.user_dir/'Example/gtk-2.0/gtkrc').write_text('')
        rc=self.root/'.gtkrc-2.0'
        original='gtk-theme-name = "My original"\n# user preferences\n'
        rc.write_text(original)
        self.themes.apply_legacy('gtk2','Example')
        self.themes.apply_legacy('gtk2','Example')
        self.assertEqual(rc.read_text().count(self.themes.LEGACY_BEGIN),1)
        self.assertEqual(self.themes.legacy_current('gtk2'),'Example')
        self.themes.apply_legacy('gtk2','Raleigh')
        self.assertEqual(rc.read_text(),original)
        self.assertEqual(rc.with_name(rc.name+'.orbits-backup').read_text(),original)

    def test_direct_terminal_scheme_install_normalizes_without_execution(self):
        source=self.root/'download.json'
        source.write_text(json.dumps(scheme()))
        self.net.download.side_effect=lambda _url,target,_report,**kwargs:shutil.copyfile(source,target)
        manager=self.themes.ThemeManager(self.themes.KINDS['terminal'])
        item=self.base.Item(source='local',id='terminal',title='Example')
        download=self.base.Download('Example.json','https://example.test/theme.json')
        result=manager._install_blocking(item,download,lambda _p:None)
        path=self.themes.USER_TERMINALS/result['names'][0]/'terminal.json'
        self.assertEqual(terminal.read_scheme(path)['colors'],scheme()['colors'])

    def test_new_source_kinds_use_full_catalog(self):
        source=self.themes.ThemeStoreSource(self.themes.KINDS['sounds'])
        self.assertEqual(source.default_category,'316')
        self.assertIn('opendesktop.org',source.api)


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='orbits-test-preview-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        repo=types.SimpleNamespace(Gio=object(),GLib=object())
        with patch.dict('sys.modules',{'gi':types.ModuleType('gi'),'gi.repository':repo}):
            self.helper=load_module('orbits_test_preview_helper','orbits/gdm/helper.py')

    def test_preview_command_isolates_both_runtime_socket_types(self):
        h=self.helper
        with patch.object(h.shutil,'which',side_effect=lambda name:'/usr/bin/'+name), patch.object(h.glob,'glob',return_value=['/usr/lib/plymouth/renderers/x11.so']), patch.object(h.os.path,'exists',return_value=True):
            command=h.plymouth_preview_command('Example',':0','/tmp/auth','/tmp/conf')
        self.assertIn('--unshare-net',command)
        self.assertIn('--unshare-pid',command)
        self.assertIn('--die-with-parent',command)
        self.assertIn('/run/preview.auth',command)
        self.assertIn('--no-boot-log',command[command.index('-c')+1])
        self.assertNotIn('plymouth-set-default-theme',' '.join(command))
        self.assertEqual(command[command.index('--ro-bind')+1:command.index('--ro-bind')+3],['/','/'])

    def test_preview_rejects_remote_displays_and_missing_dependencies(self):
        with self.assertRaises(ValueError):self.helper.plymouth_preview_command('Example','remote:0','auth','conf')
        with patch.object(self.helper.shutil,'which',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'bubblewrap'):
                self.helper.plymouth_preview_command('Example',':0','auth','conf')

    def test_preview_temporary_config_and_auth_are_removed(self):
        h=self.helper
        theme=self.root/'themes/Example';theme.mkdir(parents=True)
        (theme/'Example.plymouth').write_text('[Plymouth Theme]\nName=Example\n')
        h.THEME_BASES['plymouth']=str(theme.parent)
        auth=self.root/'auth';auth.write_bytes(b'cookie')
        captured=[]
        def command(name,display,authority,config):
            captured.extend([Path(authority),Path(config)])
            self.assertEqual(Path(authority).read_bytes(),b'cookie')
            self.assertEqual((Path(config)/'plymouthd.conf').read_text(),'[Daemon]\nTheme=Example\nShowDelay=0\nDeviceTimeout=0\n')
            return ['fake-preview']
        with patch.object(h,'plymouth_preview_command',side_effect=command), patch.object(h.subprocess,'run',return_value=types.SimpleNamespace(returncode=0,stderr='')):
            h.cmd_plymouth_preview('Example',':0',str(auth))
        self.assertTrue(all(not path.exists() for path in captured))
        self.assertTrue(auth.exists())


if __name__=='__main__':
    unittest.main()
