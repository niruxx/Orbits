"""Explicit validation of older managed assets before recording a first baseline."""
from pathlib import Path
import tempfile


def revalidate(assets):
    from orbits.security import configured_scan, checked_local_file
    from orbits.integrity import MANIFEST, verify_tree, record_tree
    from orbits.image_safety import normalize_images
    from orbits.font_safety import inspect_font
    from orbits.asset_validation import FONT_SUFFIXES, validate_theme_tree
    from orbits.look_assets import rewrite_slideshow, IMAGE_EXTS
    checked = 0
    for kind, directory in assets:
        root = Path(directory)
        if root.is_symlink() or not root.is_dir():
            raise ValueError('Legacy asset root must be a regular directory')
        if (root/MANIFEST).exists() or (root/MANIFEST).is_symlink():
            verify_tree(root)
            continue  # Never replace a recorded baseline, including a failed one.
        before = {}
        from orbits.integrity import _tree_hashes
        before = _tree_hashes(root)
        for name in before:
            configured_scan(root/name)
        if kind == 'font':
            files = [root/name for name in before if Path(name).suffix.lower() in FONT_SUFFIXES]
            if not files:
                raise ValueError('No fonts found in legacy font directory')
            for name in before:
                path = root/name
                if path.suffix.lower() not in FONT_SUFFIXES and not path.name.upper().startswith(('OFL', 'LICENSE', 'COPYING')):
                    raise ValueError('Unexpected file in legacy font directory')
            for path in files:
                inspect_font(path)
        elif kind in ('wallpaper', 'look'):
            for name in before:
                path = root/name
                if path.suffix.lower() in IMAGE_EXTS:
                    with tempfile.TemporaryDirectory(prefix='orbits-legacy-image-') as temporary:
                        normalize_images(path, temporary)
                elif path.suffix.lower() == '.xml':
                    if path.stat().st_size > 2 * 1024**2:
                        raise ValueError('Legacy slideshow is too large')
                    def resolve(value):
                        target = Path(value)
                        target = target if target.is_absolute() else path.parent/target
                        target = target.resolve()
                        if not target.is_relative_to(root.resolve()) or target.suffix.lower() not in IMAGE_EXTS or str(target.relative_to(root.resolve())) not in before:
                            raise ValueError('Legacy slideshow references an external file')
                        return str(target)
                    rewrite_slideshow(path.read_text(), resolve)
                elif kind == 'wallpaper':
                    raise ValueError('Unexpected file in legacy wallpaper directory')
                elif name != 'look.json':
                    raise ValueError('Unexpected file in legacy Look directory')
            if kind == 'look':
                import json
                from orbits.looks import sanitize
                metadata = root/'look.json'
                if metadata.stat().st_size > 2 * 1024**2:
                    raise ValueError('Legacy Look metadata is too large')
                raw = json.loads(metadata.read_text())
                clean = sanitize(raw)
                for field in ('interface', 'assets', 'app_colors', 'shell_theme', 'libadwaita_theme', 'login'):
                    if field in raw and raw[field] != clean.get(field):
                        raise ValueError('Legacy Look contains unsupported settings; reimport it instead')
                values = [(raw.get('wallpaper') or {}).get(key) for key in ('light', 'dark')]
                values += [raw.get('lockscreen'), raw.get('screenshot')]
                values += [(raw.get('login') or {}).get(key) for key in ('bg_image', 'bg_image_light', 'bg_image_dark')]
                for value in filter(None, values):
                    target = Path(value).resolve()
                    if not target.is_relative_to(root.resolve()) or str(target.relative_to(root.resolve())) not in before:
                        raise ValueError('Legacy Look references external assets; reimport it instead')
        else:
            validate_theme_tree(root, kind)
        if before != _tree_hashes(root):
            raise ValueError('Legacy asset changed during validation; baseline was not recorded')
        record_tree(root)
        checked += 1
    return f'Validated and recorded {checked} legacy asset folders; publisher identity remains unverified'
