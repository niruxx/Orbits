"""Image, palette and schedule regressions. Run without GTK: python -m unittest tests.test_wallpaper_tools."""
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
import xml.etree.ElementTree as ET

from orbits import palettes, rotation, wallpaper_tools as tools

try:
    from PIL import Image
except ImportError:
    Image = None


@unittest.skipIf(Image is None, "python-pillow is required for image processing tests")
class ImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="orbits-test-images-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.red = self.root / "red.png"
        self.blue = self.root / "blue.png"
        Image.new("RGB", (120, 80), "red").save(self.red)
        Image.new("RGB", (80, 120), "blue").save(self.blue)

    def test_edits_create_copy_and_keep_original(self):
        before = self.red.read_bytes()
        target = tools.edit_image(self.red, self.root / "edited.png", crop=(0.25,0,0.75,1),
                                  size=(40,40), dim=0.5, blur=3, tint="#0000ff", tint_amount=0.5)
        with Image.open(target) as result:
            self.assertEqual(result.size, (40,40))
            r,g,b = result.getpixel((20,20))
            self.assertTrue(60 <= r <= 65 and g == 0 and 125 <= b <= 130)
        self.assertEqual(self.red.read_bytes(), before)

    def test_invalid_crop_and_size_are_rejected(self):
        for crop in ((1,0,0,1), (-1,0,1,1), (0,0,1,2)):
            with self.assertRaises(ValueError):
                tools.edit_image(self.red, self.root / "out.png", crop=crop)
        with self.assertRaises(ValueError):
            tools.edit_image(self.red, self.root / "out.png", size=(99999,99999))

    def test_negative_monitor_positions_and_gap(self):
        path = tools.compose_monitors([
            {"path":self.red,"x":-100,"y":0,"width":100,"height":100},
            {"path":self.blue,"x":50,"y":0,"width":100,"height":100}], self.root/"layout.png")
        with Image.open(path) as image:
            self.assertEqual(image.size, (250,100))
            self.assertEqual(image.getpixel((25,50)), (255,0,0))
            self.assertEqual(image.getpixel((125,50)), (0,0,0))
            self.assertEqual(image.getpixel((225,50)), (0,0,255))

    def test_duplicate_scan_reports_exact_files_without_deleting(self):
        copy = self.root / "copy.png"
        copy.write_bytes(self.red.read_bytes())
        entries = [{"key":"one","files":[{"light":str(self.red),"dark":None}]},
                   {"key":"two","files":[{"light":str(copy),"dark":str(self.blue)}]}]
        result = tools.duplicate_groups(entries)
        self.assertEqual(len(result),1)
        self.assertEqual({key for key,_path in result[0]}, {"one","two"})
        self.assertTrue(copy.exists())

    def test_palette_is_deterministic_and_styles_are_readable(self):
        palette = palettes.from_wallpaper(self.blue)
        self.assertEqual(palette, palettes.from_wallpaper(self.blue))
        self.assertEqual(palette, palettes.validate(palette))
        self.assertEqual(palettes.nearest_accent(palette["light"]["accent"]), "blue")
        for tone in ("light","dark"):
            bg, fg = palette[tone]["window_bg"], palette[tone]["window_fg"]
            def luminance(color):
                channels = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in palettes.rgb(color)]
                return sum(a*b for a,b in zip(channels,(.2126,.7152,.0722)))
            a,b=sorted((luminance(bg),luminance(fg)))
            self.assertGreater((b+.05)/(a+.05), 7)

    def test_multiframe_heic_extracts_all_frames(self):
        try:
            import pillow_heif
        except ImportError:
            self.skipTest("pillow-heif is optional")
        pillow_heif.register_heif_opener()
        source=self.root/"dynamic.heic"
        Image.new("RGB",(40,40),"red").save(source, save_all=True, append_images=[Image.new("RGB",(40,40),"blue")])
        frames=tools.heic_frames(source,self.root/"dynamic-frames")
        self.assertEqual(len(frames),2)
        with Image.open(frames[0]) as first, Image.open(frames[1]) as second:
            self.assertGreater(first.getpixel((20,20))[0],200)
            self.assertGreater(second.getpixel((20,20))[2],200)

    def test_single_heic_import_extracts_png(self):
        try:
            import pillow_heif
        except ImportError:
            self.skipTest("pillow-heif is optional")
        source = self.root / "single.heic"
        pillow_heif.from_pillow(Image.new("RGB", (40,40), "red")).save(source)
        frames = tools.heic_frames(source, self.root/"frames")
        self.assertEqual(len(frames),1)
        self.assertTrue(frames[0].exists())


class ScheduleTests(unittest.TestCase):
    def test_xml_escapes_paths_and_spans_exactly_one_day(self):
        xml = tools.slideshow_xml([(0,"/tmp/a & b.png"),(21600,"/tmp/noon.png"),(64800,"/tmp/night.png")], transition=15)
        root = ET.fromstring(xml)
        self.assertEqual(root.find("static/file").text,"/tmp/a & b.png")
        self.assertEqual(sum(float(e.text) for e in root.findall(".//duration")),86400)
        self.assertEqual(root.findall("transition")[-1].find("to").text,"/tmp/a & b.png")

    def test_xml_rejects_duplicate_and_out_of_range_times(self):
        for frames in ([(0,"a")],[(0,"a"),(0,"b")],[(1,"a"),(10,"b")],[(0,"a"),(86400,"b")]):
            with self.assertRaises(ValueError):
                tools.slideshow_xml(frames)

    def test_timer_is_headless_and_handles_spaces_and_specifiers(self):
        service,timer = rotation.unit_contents({"minutes":15}, python="/tmp/a b/python", root="/tmp/100% Orbits")
        self.assertIn('ExecStart="/tmp/a b/python" -m orbits.rotation',service)
        self.assertIn('WorkingDirectory="/tmp/100%% Orbits"',service)
        self.assertIn('OnUnitActiveSec=15min',timer)
        self.assertNotIn('OnCalendar',timer)
        _,timer = rotation.unit_contents({"daily":True})
        self.assertIn('OnCalendar=daily',timer)
        self.assertIn('Persistent=true',timer)

    def test_rotation_config_rejects_invalid_intervals(self):
        for minutes in (0,-1,10081):
            with self.assertRaises(ValueError):
                rotation.validate_config({"minutes":minutes})

    def test_collection_and_favorite_selection_excludes_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"image.png"
            path.write_bytes(b"file")
            records=[{"key":"one","favorite":True,"collections":["Nature"],"files":[{"light":str(path)}]},
                     {"key":"two","favorite":False,"collections":["Nature"],"files":[{"light":"/missing"}]}]
            self.assertEqual(len(rotation.candidates(records,{"collection":"Nature","favorites":True})),1)
            self.assertEqual(rotation.candidates(records,{"collection":"Work"}),[])

    def test_fits_screen_requires_resolution_and_aspect_ratio(self):
        self.assertTrue(tools.fits_resolution("3840x2160",1920,1080))
        self.assertFalse(tools.fits_resolution("1920x1200",1920,1080))
        self.assertFalse(tools.fits_resolution("unknown",1920,1080))
        self.assertFalse(tools.fits_resolution("1280x720",1920,1080))


@unittest.skipIf(Image is None, "python-pillow is required to generate the fixture palette")
class PaletteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="orbits-test-palettes-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        path = self.root/"image.png"
        Image.new("RGB",(16,16),"green").save(path)
        self.palette=palettes.from_wallpaper(path)

    def test_exports_parse_and_only_selected_targets_are_written(self):
        files=palettes.export_files(self.palette,"dark")
        self.assertIn("colors",tomllib.loads(files["alacritty"][1]))
        self.assertEqual(json.loads(files["vscode"][1])["colors"]["editor.foreground"],self.palette["dark"]["window_fg"])
        paths=palettes.export_selected(self.palette,"light",["kitty","ptyxis"],self.root/"exports")
        self.assertEqual(len(paths),2)
        self.assertFalse((self.root/"exports"/"firefox").exists())
        self.assertIn("Color15=",paths[1].read_text())

    def test_orbits_palette_round_trip(self):
        path=self.root/"custom.json"
        path.write_text(json.dumps({"name":"Community","palette":self.palette}))
        name,palette=palettes.import_palette(path,self.palette)
        self.assertEqual(name,"Community")
        self.assertEqual(palette,self.palette)

    def test_gradience_import_resolves_references(self):
        path=self.root/"gradience.json"
        path.write_text(json.dumps({"name":"Community","variables":{"window_bg_color":"#111111","accent_bg_color":"@accent_color","accent_color":"#123456"}}))
        _,palette=palettes.import_palette(path,self.palette)
        self.assertIsNone(palette["light"])
        self.assertEqual(palette["dark"]["accent"],"#123456")

    def test_gradience_translucent_cards_are_composited(self):
        path=self.root/"gradience-alpha.json"
        path.write_text(json.dumps({"variables":{"window_bg_color":"#000000","card_bg_color":"rgba(255, 255, 255, 0.08)","window_fg_color":"rgb(255, 255, 255)"}}))
        _,palette=palettes.import_palette(path,self.palette)
        self.assertEqual(palette["dark"]["card_bg"],"#141414")
        self.assertEqual(palette["dark"]["window_fg"],"#ffffff")

    def test_invalid_and_cyclic_palette_values_are_rejected(self):
        path=self.root/"bad.json"
        path.write_text(json.dumps({"variables":{"window_bg_color":"@a","a":"@a"}}))
        with self.assertRaises(ValueError):
            palettes.import_palette(path,self.palette)
        bad=json.loads(json.dumps(self.palette))
        bad["dark"]["accent"]='red; } * { color: blue'
        with self.assertRaises(ValueError):
            palettes.validate(bad)
        with self.assertRaises(ValueError):
            palettes.validate({"light":None,"dark":None})


if __name__ == "__main__":
    unittest.main()
