"""Font and Looks regressions without GTK, network access or real desktop changes."""
import copy
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET
import zipfile

from orbits import font_tools, look_assets, look_gallery, look_schedule
from orbits.wallpaper_tools import slideshow_xml

ROOT=Path(__file__).resolve().parents[1]
try:
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
except ImportError:
    FontBuilder=None


def load_module(name,path):
    import sys
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    module=importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules,{name:module}):spec.loader.exec_module(module)
    return module


def make_font(path,variable=False):
    builder=FontBuilder(1000,isTTF=True)
    order=['.notdef','A','B']
    builder.setupGlyphOrder(order);builder.setupCharacterMap({65:'A',66:'B'})
    glyphs={}
    for name in order:
        pen=TTGlyphPen(None);pen.moveTo((50,0));pen.lineTo((250,700));pen.lineTo((450,0));pen.closePath()
        glyphs[name]=pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name:(600,0) for name in order})
    builder.setupHorizontalHeader(ascent=800,descent=-200)
    builder.setupNameTable({'familyName':'Orbits Fixture','styleName':'Regular','uniqueFontIdentifier':'Orbits Fixture Regular',
                            'fullName':'Orbits Fixture Regular','psName':'OrbitsFixture-Regular','version':'Version 1.0'})
    builder.setupOS2(sTypoAscender=800,sTypoDescender=-200,usWinAscent=800,usWinDescent=200)
    builder.setupPost();builder.setupMaxp()
    builder.font['name'].setName('Fixture test license',13,3,1,0x409)
    from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
    addOpenTypeFeaturesFromString(builder.font,'feature liga { sub A B by A; } liga;')
    if variable:
        from fontTools.ttLib import newTable
        from fontTools.ttLib.tables._f_v_a_r import Axis
        table=newTable('fvar');axis=Axis()
        axis.axisTag='wght';axis.minValue=100;axis.defaultValue=400;axis.maxValue=900;axis.flags=0;axis.axisNameID=256
        table.axes=[axis];table.instances=[];builder.font['fvar']=table
        builder.font['name'].setName('Weight',256,3,1,0x409)
    builder.save(path)
    return path


@unittest.skipIf(FontBuilder is None,'python-fonttools is required')
class FontToolsTests(unittest.TestCase):
    def setUp(self):
        scanner = patch('orbits.security.configured_scan', return_value='Not scanned')
        scanner.start(); self.addCleanup(scanner.stop)
        self.tmp=tempfile.TemporaryDirectory(prefix='orbits-test-fonttools-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.font=make_font(self.root/'fixture.ttf')

    def test_actual_tables_supply_glyphs_features_and_license(self):
        metadata=font_tools.inspect_font(self.font)
        self.assertEqual(metadata['family'],'Orbits Fixture')
        self.assertEqual(metadata['codepoints'],[65,66])
        self.assertEqual(metadata['glyph_count'],3)
        self.assertIn('liga',metadata['features'])
        self.assertEqual(metadata['license'],'Fixture test license')

    def test_collection_second_face_supplies_its_own_metadata_and_preview(self):
        from fontTools.ttLib import TTCollection, TTFont
        from orbits.font_safety import render_font
        second_path=make_font(self.root/'second.ttf')
        collection_path=self.root/'two-faces.ttc'
        with TTFont(self.font) as first, TTFont(second_path) as second:
            for name_id in (1, 4, 16):
                second['name'].setName('Second Collection Family', name_id, 3, 1, 0x409)
            for subtable in second['cmap'].tables:
                if subtable.isUnicode():subtable.cmap={66:'B'}
            collection=TTCollection()
            collection.fonts=[first, second]
            collection.save(collection_path)
        first=font_tools.inspect_font(collection_path)
        second=font_tools.inspect_font(collection_path, 1)
        self.assertEqual(first['face_count'], 2)
        self.assertEqual(first['family'], 'Orbits Fixture')
        self.assertEqual(second['family'], 'Second Collection Family')
        self.assertEqual(second['codepoints'], [66])
        self.assertEqual(second['face'], 1)
        self.assertTrue(render_font(collection_path, 'B', 24, face=1).startswith(b'\x89PNG'))
        with self.assertRaises(ValueError):font_tools.inspect_font(collection_path, 2)

    def test_variable_axes_preserve_real_limits_and_default(self):
        metadata=font_tools.inspect_font(make_font(self.root/'variable.ttf',True))
        self.assertEqual(metadata['axes'],[{'tag':'wght','name':'Weight','min':100,'default':400,'max':900}])

    def test_local_zip_is_flattened_and_install_scripts_ignored(self):
        archive=self.root/'fonts.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.write(self.font,'nested/first.ttf');z.write(self.font,'nested/fixture.ttf')
            z.writestr('install.sh','touch /tmp/do-not-execute')
            z.writestr('LICENSE.txt','Local license')
        dest=self.root/'prepared'
        result=font_tools.prepare_local([archive],dest)
        self.assertEqual(result['families'],['Orbits Fixture'])
        self.assertEqual(len(result['files']),2)
        self.assertTrue((dest/'LICENSE.txt').exists())
        self.assertFalse((dest/'install.sh').exists())
        self.assertFalse((self.root/'outside.ttf').exists())

    def test_bad_font_is_rejected_before_installation(self):
        bad=self.root/'broken.ttf';bad.write_bytes(b'not a font')
        with self.assertRaises(ValueError):font_tools.prepare_local([bad],self.root/'out')

    def test_content_identity_is_stable(self):
        first=font_tools.prepare_local([self.font],self.root/'one')
        second=font_tools.prepare_local([self.font],self.root/'two')
        self.assertEqual(first['digest'],second['digest'])

    def test_glyph_search_and_pagination(self):
        records,total=font_tools.glyph_page([0,65,66,67],'LATIN CAPITAL',0,2)
        self.assertEqual(total,3);self.assertEqual([r['character'] for r in records],['A','B'])
        records,total=font_tools.glyph_page([65,66],'U+0042')
        self.assertEqual(records[0]['character'],'B')

    @unittest.skipUnless(shutil.which('fc-list'),'fontconfig is required')
    def test_disabled_file_pattern_is_honored_by_real_fontconfig(self):
        fonts=self.root/'fonts';fonts.mkdir()
        target=fonts/'Fixture[wght].ttf';shutil.copyfile(self.font,target)
        config=self.root/'fonts.conf'
        def write(disabled):
            root=ET.fromstring(font_tools.fontconfig_xml({},disabled).split('<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">')[-1])
            ET.SubElement(root,'dir').text=str(fonts)
            ET.SubElement(root,'cachedir').text=str(self.root/'cache')
            config.write_text(ET.tostring(root,encoding='unicode'))
        environment={**os.environ,'FONTCONFIG_FILE':str(config),'XDG_CACHE_HOME':str(self.root/'xdg-cache')}
        write([])
        before=subprocess.run(['fc-list','--format','%{file}\n'],env=environment,capture_output=True,text=True,check=True).stdout
        self.assertIn(str(target),before)
        write([target])
        after=subprocess.run(['fc-list','--format','%{file}\n'],env=environment,capture_output=True,text=True,check=True).stdout
        self.assertNotIn(str(target),after)
        self.assertTrue(target.exists())


class FontConfigTests(unittest.TestCase):
    def test_xml_escapes_families_and_supports_all_preferences(self):
        text=font_tools.fontconfig_xml({'emoji':'Emoji & Color','substitutions':{'Old <Font>':'New Font'},'hinting':{'New Font':'slight'}},['/tmp/A[wght].ttf'])
        root=ET.fromstring(text.split('<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">')[-1])
        self.assertEqual(root.find('selectfont/rejectfont/pattern/patelt/string').text,'/tmp/A[wght].ttf')
        self.assertIn('Emoji &amp; Color',text)
        self.assertIn('hintslight',text)

    def test_invalid_hinting_and_control_characters_are_rejected(self):
        with self.assertRaises(ValueError):font_tools.fontconfig_xml({'hinting':{'Font':'random'}})
        with self.assertRaises(ValueError):font_tools.fontconfig_xml({'emoji':'Bad\nFamily'})

    def test_writes_preserve_existing_preferences_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'99-orbits.conf';path.write_text('My original file')
            font_tools.write_fontconfig(path,{'emoji':'Noto Color Emoji'})
            font_tools.write_fontconfig(path,{'emoji':'Another Font'})
            self.assertEqual(path.with_name(path.name+'.orbits-backup').read_text(),'My original file')

    def test_pairings_report_whether_both_fonts_are_installed(self):
        pairs=font_tools.suggest_pairings(['Lora','Inter'])
        self.assertTrue(next(pair['installed'] for pair in pairs if pair['heading']=='Lora'))


class LookScheduleTests(unittest.TestCase):
    def test_daily_times_and_midnight_wrap(self):
        config={'day':'light','night':'dark','day_start':'07:00','night_start':'19:00'}
        self.assertEqual(look_schedule.choose(config,datetime(2026,1,1,6,59)),'dark')
        self.assertEqual(look_schedule.choose(config,datetime(2026,1,1,7,0)),'light')
        self.assertEqual(look_schedule.choose(config,datetime(2026,1,1,19,0)),'dark')
        config.update(day_start='20:00',night_start='06:00')
        self.assertEqual(look_schedule.choose(config,datetime(2026,1,1,23,0)),'light')
        self.assertEqual(look_schedule.choose(config,datetime(2026,1,1,12,0)),'dark')

    def test_weekday_and_no_change_days(self):
        config={'mode':'weekday','weekdays':{'0':'monday'}}
        self.assertEqual(look_schedule.choose(config,datetime(2026,11,2,12)),'monday')
        self.assertIsNone(look_schedule.choose(config,datetime(2026,11,3,12)))

    def test_invalid_times_and_traversal_ids_rejected(self):
        for config in ({'day_start':'25:00'},{'day_start':'07:00','night_start':'07:00'},{'day':'../evil'},{'day':'..'},{'weekdays':{'7':'bad'}}):
            with self.assertRaises(ValueError):look_schedule.validate(config)

    def test_period_keys_cross_midnight_and_reapply_on_a_new_day(self):
        config={'day':'same','night':'same','day_start':'07:00','night_start':'19:00'}
        evening=look_schedule.selection_key(config,datetime(2026,1,1,21))
        after_midnight=look_schedule.selection_key(config,datetime(2026,1,2,1))
        self.assertEqual(evening,after_midnight)
        self.assertNotEqual(look_schedule.selection_key(config,datetime(2026,1,1,9)),
                            look_schedule.selection_key(config,datetime(2026,1,2,9)))
        weekdays={'mode':'weekday','weekdays':{'0':'same','2':'same'}}
        self.assertNotEqual(look_schedule.selection_key(weekdays,datetime(2026,11,2)),
                            look_schedule.selection_key(weekdays,datetime(2026,11,4)))

    def test_units_use_headless_module_and_persistent_minute_timer(self):
        service,timer=look_schedule.unit_contents('/tmp/Python Space','/tmp/100% Repo')
        self.assertIn('-m orbits.look_schedule',service)
        self.assertIn('100%% Repo',service)
        self.assertIn('OnCalendar=*-*-* *:*:00',timer)
        self.assertIn('Persistent=true',timer)


class GalleryTests(unittest.TestCase):
    def record(self):return {'id':'test','name':'A Look','download':'test.orbits','screenshot':'test.png','sha256':'a'*64}

    def test_relative_remote_assets_are_resolved_and_unsafe_schemes_rejected(self):
        result=look_gallery.parse_catalog({'format':1,'looks':[self.record()]},'https://example.test/gallery/index.json')
        self.assertEqual(result[0]['download'],'https://example.test/gallery/test.orbits')
        bad=self.record();bad['download']='file:///etc/passwd'
        with self.assertRaises(ValueError):look_gallery.parse_catalog({'format':1,'looks':[bad]},'https://example.test/index.json')

    def test_local_catalogue_assets_cannot_escape_folder(self):
        bad=self.record();bad['download']='../secret.orbits'
        with self.assertRaises(ValueError):look_gallery.parse_catalog({'format':1,'looks':[bad]},'/tmp/gallery/index.json')

    def test_duplicate_ids_and_missing_checksums_are_rejected(self):
        with self.assertRaises(ValueError):look_gallery.parse_catalog({'format':1,'looks':[self.record(),self.record()]},'/tmp/index.json')
        bad=self.record();bad.pop('sha256')
        with self.assertRaises(ValueError):look_gallery.parse_catalog({'format':1,'looks':[bad]},'/tmp/index.json')

    def test_download_checksums_are_verified_and_failed_files_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.orbits';source.write_bytes(b'archive data')
            destination=Path(tmp)/'download.orbits'
            record={'download':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
            # Local catalogue downloads do not invoke HTTP.
            with patch.dict('sys.modules',{'orbits.net':types.SimpleNamespace()}):
                look_gallery.fetch_look(record,destination)
                self.assertEqual(destination.read_bytes(),source.read_bytes())
                record['sha256']='0'*64
                with self.assertRaises(ValueError):look_gallery.fetch_look(record,destination)
                self.assertFalse(destination.exists())


class LooksStorageTests(unittest.TestCase):
    def setUp(self):
        def copy_image(source, destination, **kwargs):
            Path(destination).mkdir(parents=True, exist_ok=True)
            out = Path(destination)/'frame-000.png'
            shutil.copyfile(source, out)
            return [out]
        decoder = patch('orbits.image_safety.normalize_images', side_effect=copy_image)
        decoder.start(); self.addCleanup(decoder.stop)
        scanner = patch('orbits.security.configured_scan', return_value='Not scanned')
        scanner.start(); self.addCleanup(scanner.stop)
        self.tmp=tempfile.TemporaryDirectory(prefix='orbits-test-look-new-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        class Object:
            def emit(self,*args):pass
        class Variant:
            def __init__(self,type_,value):self.value=value
            def get_type_string(self):return 's'
        class Settings:
            def __init__(self):self.values={}
            def set_string(self,key,value):self.values[key]=value
            def set_value(self,key,value):self.values[key]=value.value
            def get_value(self,key):return Variant('s',self.values.get(key,''))
        self.settings={}
        def settings(schema):return self.settings.setdefault(schema,Settings())
        repository=types.SimpleNamespace(GObject=types.SimpleNamespace(Object=Object,SignalFlags=types.SimpleNamespace(RUN_FIRST=0)),
            Gio=types.SimpleNamespace(Settings=types.SimpleNamespace(new=settings,sync=lambda:None)),
            GLib=types.SimpleNamespace(Variant=Variant,Error=RuntimeError,filename_to_uri=lambda path:Path(path).as_uri()))
        self.colors=types.SimpleNamespace(ROLES={'accent':None,'window_bg':None},is_applied=Mock(return_value=True),remove=Mock(),apply=Mock(),load_state=lambda:{})
        self.themes=types.SimpleNamespace(installed=Mock(return_value=[]),apply=Mock(),libadwaita_state=Mock(return_value=(None,False)),remove_libadwaita=Mock())
        self.fonts=types.SimpleNamespace(system_families=Mock(return_value=['Inter']),get_font_library=lambda:types.SimpleNamespace(items={}))
        self.patch=patch.dict('sys.modules',{'gi':types.ModuleType('gi'),'gi.repository':repository,
            'orbits.config':types.SimpleNamespace(DATA_DIR=self.root),'orbits.colors':self.colors,'orbits.themes':self.themes,'orbits.fonts':self.fonts})
        self.patch.start();self.addCleanup(self.patch.stop)
        # from-orbits imports resolve package attributes first, so patch both boundaries.
        import orbits
        self.package=patch.multiple(orbits,colors=self.colors,themes=self.themes,create=True)
        self.package.start();self.addCleanup(self.package.stop)
        self.looks=load_module('orbits_test_new_looks','orbits/looks.py')
        self.store=self.looks.LookStore()

    def look(self):return {'format':1,'name':'Test','created':1,'interface':{},'wallpaper':{'options':'zoom'},'assets':{'themes':{},'fonts':{}}}

    def test_screenshot_and_static_wallpaper_round_trip(self):
        look=self.look();image=self.root/'image.png';image.write_bytes(b'fake image')
        look['wallpaper']['light']=str(image);look['screenshot']=str(image)
        folder=self.store.save(look)
        self.assertEqual(look['wallpaper']['light'],str(image),'save must not mutate captured metadata')
        stored=self.store.looks()[0]
        self.assertEqual(Path(stored['screenshot']).parent,folder)
        archive=self.root/'look.orbits';self.store.export(stored,archive)
        imported=self.store.import_file(archive)
        self.assertEqual(Path(imported['screenshot']).read_bytes(),b'fake image')
        with zipfile.ZipFile(archive) as z:self.assertNotIn(str(self.root),z.read('look.json').decode())

    def test_login_light_dark_backgrounds_are_portable_but_update_policy_stays_local(self):
        look=self.look()
        image=self.root/'login.png';image.write_bytes(b'login wallpaper')
        look['login']={key:str(image) for key in ('bg_image','bg_image_light','bg_image_dark')}
        look['login'].update(auto_reapply=True,bg_monitors=[{'light':str(image)}],bg_mode='image')
        archive=self.root/'login.orbits'
        self.store.export(look,archive)
        defaults={key:'' for key in ('bg_image','bg_image_light','bg_image_dark','bg_mode','auto_reapply','bg_monitors')}
        model=types.SimpleNamespace(THEME_DEFAULTS=defaults,GdmModel=lambda:types.SimpleNamespace(keys=[]))
        with patch.dict('sys.modules',{'orbits.gdm.model':model}):
            imported=self.store.import_file(archive)
        for key in ('bg_image','bg_image_light','bg_image_dark'):
            self.assertEqual(Path(imported['login'][key]).read_bytes(),b'login wallpaper')
        self.assertNotIn('auto_reapply',imported['login'])
        self.assertNotIn('bg_monitors',imported['login'])
        with zipfile.ZipFile(archive) as z:self.assertNotIn(str(self.root),z.read('look.json').decode())

    def test_dynamic_frames_survive_original_library_removal_and_export(self):
        source=self.root/'original';source.mkdir()
        first=source/'day.png';first.write_bytes(b'day')
        second=source/'night.png';second.write_bytes(b'night')
        xml=source/'background.xml';xml.write_text(slideshow_xml([(0,first),(43200,second)]))
        look=self.look();look['wallpaper']['light']=str(xml)
        self.store.save(look);shutil.rmtree(source)
        stored=self.store.looks()[0];archive=self.root/'dynamic.orbits';self.store.export(stored,archive)
        imported=self.store.import_file(archive)
        root=ET.parse(imported['wallpaper']['light']).getroot()
        references=[n.text for n in root.iter() if n.tag in ('file','from','to')]
        self.assertTrue(all(Path(path).is_file() for path in references))
        self.assertTrue(all(Path(path).parent==Path(imported['_dir']) for path in references))
        self.assertTrue(self.looks.preview_path(imported))

    def test_malicious_slideshow_references_are_rejected_and_cleaned_up(self):
        archive=self.root/'bad.orbits';look=self.look();look['wallpaper']['light']='background.xml'
        xml=slideshow_xml([(0,'/etc/passwd'),(43200,'/etc/shadow')])
        with zipfile.ZipFile(archive,'w') as z:z.writestr('look.json',json.dumps(look));z.writestr('background.xml',xml)
        with self.assertRaises(ValueError):self.store.import_file(archive)
        self.assertEqual(self.store.looks(),[])

    def test_gallery_export_has_importable_archive_and_valid_checksum(self):
        look=self.look();image=self.root/'preview.png';image.write_bytes(b'preview');look['screenshot']=str(image)
        manifest=look_gallery.export_bundle(self.store,look,self.root/'gallery','Artist')
        records=look_gallery.parse_catalog(json.loads(manifest.read_text()),manifest)
        self.assertEqual(records[0]['author'],'Artist')
        self.assertTrue(Path(records[0]['screenshot']).exists())
        digest=hashlib.sha256(Path(records[0]['download']).read_bytes()).hexdigest()
        self.assertEqual(digest,records[0]['sha256'])

    def test_fonts_only_does_not_touch_themes_colors_or_wallpaper(self):
        look=self.look();look['interface']={'font-name':'Inter 12 @wght=600','gtk-theme':'Other','accent-color':'purple'}
        look['shell_theme']='Other';look['wallpaper']['light']=str(self.root/'wall.png')
        self.looks._iface_keys=lambda:list(look['interface'])
        notes=[];window=types.SimpleNamespace(pages={})
        applier=self.looks.LookApplier(look,window,done=lambda value:notes.extend(value),components={'fonts'})
        applier._apply_settings()
        self.assertEqual(self.settings['org.gnome.desktop.interface'].values,{'font-name':'Inter 12 @wght=600'})
        self.themes.apply.assert_not_called();self.colors.remove.assert_not_called();self.colors.apply.assert_not_called()
        self.assertEqual(getattr(self.settings.get('org.gnome.desktop.background'),'values',{}),{})
        self.assertEqual(notes,[])

    def test_missing_assets_are_filtered_to_selected_components(self):
        look=self.look();look['assets']['themes']={'gtk:Missing':{'key':'theme-gtk:1'}}
        look['assets']['fonts']={'Absent':{'key':'google-fonts:absent'}}
        applier=self.looks.LookApplier(look,types.SimpleNamespace(),components={'wallpaper'})
        self.assertEqual(list(applier._missing()),[])
        self.fonts.system_families.assert_not_called()

    def test_empty_selection_creates_no_snapshot_or_changes(self):
        self.looks.get_store=Mock()
        done=Mock();applier=self.looks.LookApplier(self.look(),types.SimpleNamespace(),done=done,components=set())
        applier.start();self.looks.get_store.assert_not_called();done.assert_called_once()


@unittest.skipIf(FontBuilder is None,'python-fonttools is required')
class FontLibraryTests(unittest.TestCase):
    def setUp(self):
        scanner = patch('orbits.security.configured_scan', return_value='Not scanned')
        scanner.start(); self.addCleanup(scanner.stop)
        self.tmp=tempfile.TemporaryDirectory(prefix='orbits-test-font-library-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        for folder in ('data','cache','config'):(self.root/folder).mkdir()
        class Object:
            def emit(self,*args):pass
        self.interface=types.SimpleNamespace(get_string=lambda _key:'Sans 11',set_string=Mock())
        repository=types.SimpleNamespace(GObject=types.SimpleNamespace(Object=Object,SignalFlags=types.SimpleNamespace(RUN_FIRST=0)),
            Gio=types.SimpleNamespace(Settings=types.SimpleNamespace(new=lambda _schema:self.interface,sync=lambda:None)),
            GLib=types.SimpleNamespace(get_user_data_dir=lambda:str(self.root/'data'),get_user_config_dir=lambda:str(self.root/'config')))
        def run_async(work,done):
            try:result=work()
            except Exception as error:done(None,error)
            else:done(result,None)
        self.net=types.SimpleNamespace(run_async=run_async)
        config=types.SimpleNamespace(DATA_DIR=self.root/'data',CACHE_DIR=self.root/'cache',CONFIG_DIR=self.root/'config')
        self.patch=patch.dict('sys.modules',{'gi':types.ModuleType('gi'),'gi.repository':repository,'orbits.net':self.net,'orbits.config':config})
        self.patch.start();self.addCleanup(self.patch.stop)
        import orbits
        self.package=patch.multiple(orbits,net=self.net,create=True);self.package.start();self.addCleanup(self.package.stop)
        self.fonts=load_module('orbits_test_font_library','orbits/fonts.py')
        self.fonts.SYSTEM_FONT_DIR=self.root/'system'
        self.run=patch.object(self.fonts.subprocess,'run',return_value=types.SimpleNamespace(returncode=0,stdout='',stderr=''))
        self.run.start();self.addCleanup(self.run.stop)
        self.popen=patch.object(self.fonts.subprocess,'Popen');self.popen.start();self.addCleanup(self.popen.stop)
        self.lib=self.fonts.FontLibrary();self.source=make_font(self.root/'fixture.ttf')

    def install(self):
        result=[]
        self.lib.import_files([str(self.source)],lambda entry,error:result.append((entry,error)))
        self.assertIsNone(result[0][1]);return result[0][0]

    def test_rejected_font_metadata_is_checked_before_install_publication(self):
        from orbits.rejections import scanner_alert,ScanRejected
        item=self.fonts.FontItem('fixture','font','Rejected Fixture')
        def download(_item,destination,_report):shutil.copyfile(self.source,Path(destination)/'fixture.ttf')
        source=types.SimpleNamespace(download_files=download)
        result=[]
        with patch.dict(self.fonts.FONT_SOURCES_BY_ID,{'fixture':source}), \
             patch.object(self.fonts,'families_in',side_effect=scanner_alert('Fixture.BeforePublish')):
            self.lib.install(item,lambda entry,error:result.append((entry,error)))
        self.assertIsNone(result[0][0])
        self.assertIsInstance(result[0][1],ScanRejected)
        self.assertFalse((self.fonts.USER_FONT_DIR/'rejected-fixture').exists())
        self.assertNotIn(item.key,self.lib.items)

    def test_local_import_reimport_is_idempotent_and_preserves_metadata(self):
        entry=self.install();self.lib.set_collections(entry['key'],['Work'])
        self.lib.set_enabled(entry['key'],False)
        second=self.install()
        self.assertEqual(second['key'],entry['key']);self.assertEqual(len(self.lib.items),1)
        self.assertEqual(second['collections'],['Work']);self.assertFalse(second['enabled'])

    def test_disable_and_reenable_keep_font_files(self):
        entry=self.install();path=Path(entry['files'][0]);original=path.read_bytes()
        self.lib.set_enabled(entry['key'],False)
        self.assertIn(str(path),self.fonts.FONTCONFIG.read_text())
        self.assertEqual(path.read_bytes(),original)
        self.lib.set_enabled(entry['key'],True)
        self.assertNotIn('<rejectfont>',self.fonts.FONTCONFIG.read_text())
        self.assertEqual(path.read_bytes(),original)

    def test_concurrent_collection_and_activation_changes_are_merged(self):
        entry=self.install();other=self.fonts.FontLibrary()
        self.lib.set_enabled(entry['key'],False);other.set_collections(entry['key'],['Design'])
        data=json.loads(self.fonts.FONT_LIBRARY.read_text())[entry['key']]
        self.assertEqual(data['collections'],['Design']);self.assertFalse(data['enabled'])

    def test_apply_reenables_font_and_preserves_variable_axes(self):
        entry=self.install();self.lib.set_enabled(entry['key'],False)
        self.lib.apply(entry['family'],'interface',variations='wght=650')
        self.assertTrue(self.lib.items[entry['key']]['enabled'])
        self.interface.set_string.assert_called_with('font-name','Orbits Fixture 11 @wght=650')

    def test_cache_failure_removes_unregistered_local_font_files(self):
        with patch.object(self.fonts.subprocess,'run',side_effect=OSError('fc-cache unavailable')):
            result=[]
            self.lib.import_files([str(self.source)],lambda entry,error:result.append((entry,error)))
        self.assertIsNone(result[0][0]);self.assertEqual(self.lib.items,{})
        self.assertEqual(list(self.fonts.USER_FONT_DIR.iterdir()),[])

    def test_bad_import_does_not_register_partial_entry(self):
        bad=self.root/'bad.ttf';bad.write_bytes(b'bad font')
        result=[];self.lib.import_files([str(bad)],lambda entry,error:result.append((entry,error)))
        self.assertIsNone(result[0][0]);self.assertIsInstance(result[0][1],ValueError)
        self.assertEqual(self.lib.items,{})


class ScreenshotTests(unittest.TestCase):
    def test_portal_response_is_delivered_once_and_subscription_is_cleaned_up(self):
        from orbits import screenshot
        signals={};pending=[];result=[]
        connection=types.SimpleNamespace(get_unique_name=lambda:':1.42',signal_unsubscribe=Mock())
        def subscribe(*args):signals['callback']=args[-2];return 7
        connection.signal_subscribe=subscribe
        connection.call=lambda *args:pending.append(args)
        connection.call_finish=lambda _result:types.SimpleNamespace(unpack=lambda:('/org/freedesktop/portal/desktop/request/1_42/test',))
        glib=types.SimpleNamespace(Variant=lambda *_args:object(),VariantType=lambda _type:object(),Error=RuntimeError,
                                  source_remove=Mock(),timeout_add_seconds=lambda *_args:11)
        gio=types.SimpleNamespace(bus_get_sync=lambda *_args:connection,BusType=types.SimpleNamespace(SESSION=0),
            DBusSignalFlags=types.SimpleNamespace(NONE=0),DBusCallFlags=types.SimpleNamespace(NONE=0),
            File=types.SimpleNamespace(new_for_uri=lambda _uri:types.SimpleNamespace(get_path=lambda:'/tmp/screen.png')))
        with patch.dict('sys.modules',{'gi':types.ModuleType('gi'),'gi.repository':types.SimpleNamespace(Gio=gio,GLib=glib)}):
            screenshot.capture(lambda path,error:result.append((path,error)))
            pending[0][-1](connection,None)
            parameters=types.SimpleNamespace(unpack=lambda:(0,{'uri':'file:///tmp/screen.png'}))
            args=(connection,None,'/org/freedesktop/portal/desktop/request/1_42/test',None,None,parameters,None)
            signals['callback'](*args);signals['callback'](*args)
        self.assertEqual(result,[('/tmp/screen.png',None)])
        connection.signal_unsubscribe.assert_called_once_with(7);glib.source_remove.assert_called_once_with(11)


if __name__=='__main__':unittest.main()


# Unit suites execute trusted worker code in child processes; live isolation has
# a separate required integration profile and never changes production behavior.
def setUpModule():
    from tests.parser_fixtures import run_worker
    global _parser_fixture, _publisher_fixture
    _parser_fixture = patch('orbits.parser_sandbox.run', side_effect=run_worker)
    _parser_fixture.start()
    _publisher_fixture = patch('orbits.publishers.verify_local', return_value='Publisher unverified')
    _publisher_fixture.start()


def tearDownModule():
    _parser_fixture.stop()
    _publisher_fixture.stop()
