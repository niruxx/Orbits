"""Isolated FontTools/FreeType operations; no preview-font registration in the GUI."""
import json
import math
from pathlib import Path
import tempfile

from orbits.image_safety import _png, _read_regular


def _process(path, operation, options):
    from orbits.parser_sandbox import run
    from orbits.security import checked_local_file
    with checked_local_file(path, archive=False) as captured, tempfile.TemporaryDirectory(prefix='orbits-font-worker-') as temporary:
        run(captured, temporary, Path(__file__).with_name('font_worker.py'), [operation, json.dumps(options, allow_nan=False)])
        root = Path(temporary)
        result = json.loads(_read_regular(root/'result.json', 8 * 1024**2))
        if operation == 'render':
            if not isinstance(result, list) or len(result) != 2 or any(type(n) is not int for n in result):
                raise ValueError('Invalid font raster dimensions')
            width, height = result
            if not 1 <= width <= 2048 or not 1 <= height <= 512:
                raise ValueError('Font preview exceeds its bounds')
            pixels = _read_regular(root/'pixels.rgba', width * height * 4)
            if len(pixels) != width * height * 4:
                raise ValueError('Incomplete font raster')
            return _png(pixels, width, height)
        return result


def inspect_font(path, face=0):
    if type(face) is not int or not 0 <= face < 256:
        raise ValueError('Invalid font face')
    result = _process(path, 'inspect', {'face': face})
    if not isinstance(result, dict):
        raise ValueError('Invalid font metadata')
    for key in ('family', 'style', 'full_name', 'version', 'designer', 'license', 'license_url'):
        if not isinstance(result.get(key), str) or len(result[key]) > 12000:
            raise ValueError('Invalid font metadata string')
    points = result.get('codepoints')
    if not isinstance(points, list) or len(points) > 300000 or any(type(n) is not int or not 0 <= n <= 0x10ffff for n in points):
        raise ValueError('Invalid font codepoints')
    if type(result.get('glyph_count')) is not int or not 0 <= result['glyph_count'] <= 1000000:
        raise ValueError('Invalid glyph count')
    features = result.get('features')
    if not isinstance(features, list) or len(features) > 1024 or any(not isinstance(n, str) or len(n) != 4 or not n.isascii() for n in features):
        raise ValueError('Invalid font features')
    axes = result.get('axes')
    if not isinstance(axes, list) or len(axes) > 64:
        raise ValueError('Invalid font axes')
    for axis in axes:
        if not isinstance(axis, dict) or not isinstance(axis.get('tag'), str) or len(axis['tag']) != 4 or not isinstance(axis.get('name'), str) or len(axis['name']) > 12000:
            raise ValueError('Invalid font axis')
        values = [axis.get(key) for key in ('min', 'default', 'max')]
        if any(type(n) not in (int, float) or not math.isfinite(n) for n in values) or not values[0] <= values[1] <= values[2]:
            raise ValueError('Invalid font axis limits')
    count = result.get('face_count', 1)
    if type(count) is not int or not 1 <= count <= 256 or face >= count:
        raise ValueError('Invalid font face count')
    result.update(file=str(Path(path)), face=face, face_count=count, monospace=bool(result.get('monospace')))
    return result


def render_font(path, text='Aa', size=24, face=0, variations=None, features=None):
    if not isinstance(text, str) or len(text) > 2048 or type(size) not in (int, float) or not 8 <= size <= 96 or type(face) is not int or not 0 <= face < 256:
        raise ValueError('Invalid font preview parameters')
    variations = variations or {}
    if not isinstance(variations, dict) or len(variations) > 64 or any(not isinstance(key, str) or len(key) != 4 or type(value) not in (int, float) or not math.isfinite(value) for key, value in variations.items()):
        raise ValueError('Invalid font preview variations')
    features = features or []
    if not isinstance(features, list) or len(features) > 100 or any(not isinstance(tag, str) or not tag.lstrip('-').isalnum() or len(tag.lstrip('-')) != 4 for tag in features):
        raise ValueError('Invalid font preview features')
    return _process(path, 'render', {'text': text, 'size': int(size), 'face': face, 'variations': variations, 'features': features})
