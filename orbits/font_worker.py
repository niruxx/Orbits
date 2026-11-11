"""Standalone, isolated font inspection and raster preview worker."""
from pathlib import Path
import math
import json
import sys
import resource
MAX_FONT = 128 << 20

def font_class():
    try:
        from fontTools.ttLib import TTFont
    except ImportError as error:
        raise RuntimeError("Install python-fonttools for font import and inspection") from error
    return TTFont


def inspect_font(path, face=0):
    path = Path(path)
    if path.stat().st_size > MAX_FONT:
        raise ValueError("Font file exceeds 128 MiB")
    TTFont = font_class()
    try:
        with path.open("rb") as stream, TTFont(stream, fontNumber=face, lazy=True) as font:
            def name(number):
                table = font.get("name")
                return (table.getDebugName(number) or "")[:12000] if table else ""
            family = name(16) or name(1) or path.stem
            cmap = font.getBestCmap() or {}
            features = set()
            for tag in ("GSUB", "GPOS"):
                if tag in font:
                    listing = getattr(font[tag].table, "FeatureList", None)
                    if listing:
                        features.update(record.FeatureTag for record in listing.FeatureRecord)
            axes = []
            if "fvar" in font:
                for axis in font["fvar"].axes:
                    limits = (axis.minValue, axis.defaultValue, axis.maxValue)
                    if not all(math.isfinite(v) for v in limits) or not limits[0] <= limits[1] <= limits[2]:
                        continue
                    axes.append({"tag":axis.axisTag,"name":name(axis.axisNameID) or axis.axisTag,
                                 "min":axis.minValue,"default":axis.defaultValue,"max":axis.maxValue})
            return {"family":family[:200],"style":name(17) or name(2),"full_name":name(4),
                    "version":name(5),"designer":name(9),"license":name(13),"license_url":name(14),
                    "glyph_count":len(font.getGlyphOrder()),"codepoints":sorted(c for c in cmap if 0 <= c <= 0x10ffff),
                    "features":sorted(features),"axes":axes,"file":str(path),"face":face,
                    "monospace":bool(getattr(font.get("post"), "isFixedPitch", 0))}
    except Exception as error:
        raise ValueError(f"Could not read font {path.name}: {error}") from error


def main():
    resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024**2, 16 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    operation = sys.argv[1]
    options = json.loads(sys.argv[2])
    source = sys.argv[3] if len(sys.argv) > 3 else '/input'
    output = Path(sys.argv[4] if len(sys.argv) > 4 else '/output')
    if operation == 'inspect':
        result = inspect_font(source, options['face'])
    elif operation == 'render':
        from PIL import Image, ImageDraw, ImageFont
        font = ImageFont.truetype(source, options['size'], index=options['face'])
        variations = options.get('variations', {})
        if variations:
            metadata = inspect_font(source, options['face'])
            font.set_variation_by_axes([variations.get(axis['tag'], axis['default']) for axis in metadata['axes']])
        image = Image.new('RGBA', (2048, 512), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        from PIL import features
        shaping = {'features': options.get('features', [])} if features.check('raqm') else {}
        draw.multiline_text((8, 8), options['text'], fill=(128, 128, 128, 255), font=font, spacing=8, **shaping)
        bounds = image.getbbox() or (0, 0, 1, 1)
        image = image.crop((0, 0, min(2048, bounds[2]+8), min(512, bounds[3]+8)))
        (output/'pixels.rgba').write_bytes(image.tobytes())
        result = [image.width, image.height]
    else:
        raise ValueError('Unsupported font operation')
    (output/'result.json').write_text(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    main()
