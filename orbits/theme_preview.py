"""Bounded, data-only discovery of raster icon samples; never load an icon theme."""
import os
from pathlib import Path
import stat


def icon_samples(directory, names):
    """Find PNG samples without following links or reading theme configuration/SVG."""
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Icon preview root must be a regular directory')
    wanted = {name for group in names for name in group}
    found = {}
    pending = [(root, 0)]
    visited = 0
    while pending:
        parent, depth = pending.pop()
        with os.scandir(parent) as entries:
            for entry in entries:
                visited += 1
                if visited > 10000:
                    raise ValueError('Icon preview discovery exceeds its entry limit')
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    if depth >= 20:
                        raise ValueError('Icon preview discovery exceeds its depth limit')
                    pending.append((Path(entry.path), depth+1))
                elif stat.S_ISREG(info.st_mode):
                    path = Path(entry.path)
                    if path.suffix.lower() == '.png' and path.stem in wanted and info.st_size <= 32 * 1024**2:
                        found.setdefault(path.stem, path)
    return [next((found[name] for name in group if name in found), None) for group in names]
