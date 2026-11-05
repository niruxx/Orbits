"""Render login backgrounds without changing any desktop or system settings."""
from pathlib import Path

from orbits.wallpaper_tools import compose_monitors, open_image


def background_path(values, variant="dark"):
    return values.get(f"bg_image_{variant}") or values.get("bg_image", "")


def render_backgrounds(values, directory):
    """Return light/dark JPEGs, including per-monitor composition and blur/dim.

    Monitor coordinates are logical pixels and must match GDM's display layout.
    Empty monitor choices inherit the corresponding global background.
    """
    from PIL import ImageEnhance, ImageFilter

    if values.get("bg_mode") != "image":
        return None
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    result = {}
    for variant in ("light", "dark"):
        fallback = background_path(values, variant)
        assignments = []
        for monitor in values.get("bg_monitors", []):
            rec = dict(monitor)
            rec["path"] = monitor.get(variant) or fallback
            if not rec["path"]:
                raise ValueError(f"Choose a {variant} background for {monitor.get('name', 'each monitor')}")
            assignments.append(rec)
        if assignments:
            source = compose_monitors(assignments, directory / f"layout-{variant}.png")
        elif fallback:
            source = fallback
        else:
            raise ValueError(f"Choose a {variant} login background first")
        image = open_image(source).convert("RGB")
        # Keep individual and composed backgrounds bounded while preserving their aspect ratio.
        image.thumbnail((8192, 8192))
        blur = max(0, min(100, float(values.get("bg_blur", 0)))) * image.width / 1920
        if blur:
            image = image.filter(ImageFilter.GaussianBlur(blur))
        dim = max(0, min(90, float(values.get("bg_dim", 0)))) / 100
        image = ImageEnhance.Brightness(image).enhance(1 - dim)
        target = directory / f"background-{variant}.jpg"
        image.save(target, "JPEG", quality=93)
        result[variant] = str(target)
    return result
