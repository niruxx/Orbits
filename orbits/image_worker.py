"""Restricted image decoder entry point. Run only through image_safety.py."""
import json
from pathlib import Path
import resource
import sys
import warnings


def main():
    resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
    resource.setrlimit(resource.RLIMIT_FSIZE, (160_000_000, 160_000_000))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    Image.MAX_IMAGE_PIXELS = 40_000_000
    warnings.simplefilter('error', Image.DecompressionBombWarning)
    size, frames = int(sys.argv[1]), int(sys.argv[2])
    result, total = [], 0
    source = sys.argv[3] if len(sys.argv) > 3 else '/input'
    output = Path(sys.argv[4] if len(sys.argv) > 4 else '/output')
    with Image.open(source) as image:
        count = getattr(image, 'n_frames', 1)
        if count > frames:
            raise ValueError('Too many image frames')
        for index in range(count):
            image.seek(index)
            if image.width * image.height > 40_000_000:
                raise ValueError('Image exceeds 40 megapixels')
            image.load()
            frame = ImageOps.exif_transpose(image).convert('RGBA')
            if size:
                frame.thumbnail((size, size))
            data = frame.tobytes()
            total += len(data)
            if total > 512 * 1024**2:
                raise ValueError('Image frames exceed output limit')
            (output/f'{index}.rgba').write_bytes(data)
            result.append([frame.width, frame.height])
    (output/'frames.json').write_text(json.dumps(result))


if __name__ == '__main__':
    main()
