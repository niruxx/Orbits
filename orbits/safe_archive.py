"""One streaming extractor for all untrusted ZIP and tar assets."""
from pathlib import Path, PurePosixPath
import os
import stat

MAX_BYTES = 1024**3
MAX_FILE = 512 * 1024**2
MAX_ENTRIES = 10000


class Budget:
    def __init__(self):
        self.total = 0

    def copy(self, source, output, declared=None, limit=MAX_FILE):
        count = 0
        while chunk := source.read(65536):
            count += len(chunk)
            self.total += len(chunk)
            if count > limit or self.total > MAX_BYTES or (declared is not None and count > declared):
                raise ValueError('Extracted content exceeds its size limit')
            output.write(chunk)
        if declared is not None and count != declared:
            raise ValueError('Archive member size does not match its contents')
        return count


def member_path(name):
    if not isinstance(name, str) or '\\' in name or '\x00' in name or ':' in name:
        raise ValueError('Invalid archive path')
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or len(path.parts) > 20 or len(name) > 1024 or not path.parts:
        raise ValueError('Unsafe or overly deep archive path')
    return path


def is_archive(source):
    import json
    import tempfile
    from orbits.parser_sandbox import run
    from orbits.image_safety import _read_regular
    from orbits.security import checked_local_file
    with checked_local_file(source, scan=False, archive=False) as captured, tempfile.TemporaryDirectory(prefix='orbits-archive-probe-') as temporary:
        run(captured, temporary, Path(__file__).with_name('archive_worker.py'), ['probe'])
        result = json.loads(_read_regular(Path(temporary)/'result.json', 16))
        if type(result) is not bool:
            raise ValueError('Invalid archive classification')
        return result


def extract(archive, destination, wanted=lambda name: True):
    """Parse only in isolation; independently validate every file crossing back."""
    import tempfile
    from orbits.parser_sandbox import run
    from orbits.security import checked_local_file
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with checked_local_file(archive, scan=False, archive=False) as captured, tempfile.TemporaryDirectory(prefix='orbits-archive-output-') as temporary:
        run(captured, temporary, Path(__file__).with_name('archive_worker.py'), ['extract'])
        root = Path(temporary)/'tree'
        if root.is_symlink() or not root.is_dir():
            raise ValueError('Invalid archive worker output')
        budget, count = Budget(), 0
        for directory, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                source = Path(directory)/name
                relative = member_path(str(source.relative_to(root)))
                count += 1
                if count > MAX_ENTRIES or source.is_symlink():
                    raise ValueError('Unsafe archive worker output')
                info = source.lstat()
                if stat.S_ISDIR(info.st_mode):
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE:
                    raise ValueError('Invalid archive worker file')
                target = destination.joinpath(*relative.parts)
                for parent in (target, *target.parents):
                    if parent.is_symlink():
                        raise ValueError('Unsafe extraction destination')
                    if parent == destination:
                        break
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise ValueError('Unsafe extraction destination')
                if not wanted(str(relative)):
                    # Count all decoded bytes even when the caller does not want this member.
                    budget.total += info.st_size
                    if budget.total > MAX_BYTES:
                        raise ValueError('Archive output exceeds its limit')
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                output = target.open('xb')
                try:
                    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(descriptor, 'rb') as incoming, output:
                        budget.copy(incoming, output, info.st_size)
                    target.chmod(0o644)
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
    return destination
