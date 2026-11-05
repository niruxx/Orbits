"""Image transforms, monitor layouts and GNOME time-of-day backgrounds.

No desktop dependencies: these operations can also run in the timer process.
"""
import hashlib
import math
from pathlib import Path
import xml.etree.ElementTree as ET


def image_modules():
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    except ImportError as exc:
        raise RuntimeError("Install python-pillow to process wallpapers") from exc
    return Image, ImageEnhance, ImageFilter, ImageOps


def open_image(path):
    Image, _, _, ImageOps = image_modules()
    if Path(path).suffix.lower() in (".heic", ".heif"):
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError as exc:
            raise RuntimeError("Install python-pillow-heif to import HEIC wallpapers") from exc
    with Image.open(path) as image:
        return ImageOps.exif_transpose(image).convert("RGB")


def edit_image(path, destination, size=None, crop=None, blur=0, dim=0, tint="#000000", tint_amount=0):
    """Save an edited copy. Crop is a normalized (left, top, right, bottom) box."""
    Image, ImageEnhance, ImageFilter, ImageOps = image_modules()
    image = open_image(path)
    if crop is not None:
        left, top, right, bottom = map(float, crop)
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
            raise ValueError("Crop coordinates must form a rectangle within the image")
        image = image.crop((int(left * image.width), int(top * image.height),
                            max(int(right * image.width), int(left * image.width) + 1),
                            max(int(bottom * image.height), int(top * image.height) + 1)))
    if size:
        width, height = map(int, size)
        if width < 1 or height < 1 or width * height > 100_000_000:
            raise ValueError("Invalid output dimensions")
        image = ImageOps.fit(image, (width, height), method=Image.Resampling.LANCZOS)
    if not all(math.isfinite(float(v)) for v in (blur, dim, tint_amount)):
        raise ValueError("Effects must be finite numbers")
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(max(0, min(100, float(blur)))))
    image = ImageEnhance.Brightness(image).enhance(1 - max(0, min(1, float(dim))))
    if tint_amount:
        image = Image.blend(image, Image.new("RGB", image.size, tint), max(0, min(1, float(tint_amount))))
    image.save(destination, "PNG")
    return Path(destination)


def compose_monitors(assignments, destination):
    """Assignments are {path,x,y,width,height} in the same logical coordinate space.

    Scaling all displays in logical pixels preserves GNOME's spanned layout at mixed DPI.
    """
    Image, _, _, ImageOps = image_modules()
    if not assignments:
        raise ValueError("Choose at least one monitor")
    left = min(int(a["x"]) for a in assignments)
    top = min(int(a["y"]) for a in assignments)
    right = max(int(a["x"]) + int(a["width"]) for a in assignments)
    bottom = max(int(a["y"]) + int(a["height"]) for a in assignments)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0 or width * height > 100_000_000:
        raise ValueError("Monitor layout is too large or empty")
    output = Image.new("RGB", (width, height), "black")
    for a in assignments:
        w, h = int(a["width"]), int(a["height"])
        if w < 1 or h < 1:
            raise ValueError("Monitor dimensions must be positive")
        image = ImageOps.fit(open_image(a["path"]), (w, h), method=Image.Resampling.LANCZOS)
        output.paste(image, (int(a["x"]) - left, int(a["y"]) - top))
    output.save(destination, "PNG")
    return Path(destination)


def slideshow_xml(frames, transition=5):
    """Create a repeating 24h slideshow from (seconds after midnight, absolute path)."""
    frames = sorted((int(seconds), str(Path(path).resolve())) for seconds, path in frames)
    if len(frames) < 2 or frames[0][0] != 0:
        raise ValueError("Use at least two frames, starting at 00:00")
    times = [s for s, _ in frames]
    if len(set(times)) != len(times) or any(s < 0 or s >= 86400 for s in times):
        raise ValueError("Frame times must be unique and within one day")
    root = ET.Element("background")
    start = ET.SubElement(root, "starttime")
    for name, value in zip(("year", "month", "day", "hour", "minute", "second"), (2020, 1, 1, 0, 0, 0)):
        ET.SubElement(start, name).text = str(value)
    for i, (seconds, path) in enumerate(frames):
        following, next_path = frames[(i + 1) % len(frames)]
        duration = (following - seconds) % 86400
        fade = min(max(0, float(transition)), duration / 2)
        static = ET.SubElement(root, "static")
        ET.SubElement(static, "duration").text = str(duration - fade)
        ET.SubElement(static, "file").text = path
        if fade:
            change = ET.SubElement(root, "transition", type="overlay")
            ET.SubElement(change, "duration").text = str(fade)
            ET.SubElement(change, "from").text = path
            ET.SubElement(change, "to").text = next_path
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def heic_frames(path, destination):
    """Extract HEIC frames in container order; Apple solar metadata is not interpreted."""
    Image, _, _, _ = image_modules()
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError as exc:
        raise RuntimeError("Install python-pillow-heif to import HEIC wallpapers") from exc
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    paths = []
    with Image.open(path) as image:
        count = getattr(image, "n_frames", 1)
        if count > 200:
            raise ValueError("HEIC contains too many frames (maximum 200)")
        for i in range(count):
            image.seek(i)
            target = destination / f"frame-{i:03d}.png"
            image.convert("RGB").save(target)
            paths.append(target)
    return paths


def duplicate_groups(items):
    """Exact SHA-256 duplicates; returned groups contain (entry key, file path)."""
    groups = {}
    seen = set()
    for entry in items:
        for rec in entry["files"]:
            for tone in ("light", "dark"):
                path = rec.get(tone)
                if not path or path in seen or Path(path).suffix == ".xml":
                    continue
                seen.add(path)
                try:
                    with open(path, "rb") as stream:
                        digest = hashlib.file_digest(stream, "sha256").hexdigest()
                except OSError:
                    continue
                groups.setdefault(digest, []).append((entry["key"], path))
    return [group for group in groups.values() if len(group) > 1]


def fits_resolution(resolution, width, height, tolerance=0.08):
    import re
    match = re.fullmatch(r"(\d+)\s*[x×]\s*(\d+)", resolution or "")
    if not match:
        return False
    w, h = map(int, match.groups())
    return w >= width and h >= height and abs(w / max(h, 1) / (width / height) - 1) <= tolerance
