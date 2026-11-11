"""One streaming extractor for all untrusted ZIP and tar assets."""
from pathlib import Path, PurePosixPath
import os
import stat
import tarfile
import zipfile

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


def extract(archive, destination, wanted=lambda name: True):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    budget, seen, files = Budget(), set(), set()
    declared_total = 0
    def target(name, directory, size):
        nonlocal declared_total
        path = member_path(name)
        key = str(path)
        if key in seen or any(str(parent) in files for parent in path.parents) or (not directory and any(item.startswith(key + '/') for item in seen)):
            raise ValueError('Archive contains duplicate or conflicting paths')
        seen.add(key)
        if not directory:
            files.add(key)
        if len(seen) > MAX_ENTRIES or size < 0 or size > MAX_FILE:
            raise ValueError('Archive entry exceeds its limit')
        declared_total += size
        if declared_total > MAX_BYTES:
            raise ValueError('Archive expands beyond the 1 GiB limit')
        output = destination.joinpath(*path.parts)
        # Destination may be supplied by an importer; never traverse an existing link.
        for parent in (output, *output.parents):
            if parent.is_symlink():
                raise ValueError('Extraction destination contains a symbolic link')
            if parent == destination:
                break
        if not output.resolve().is_relative_to(destination.resolve()) or output.is_symlink():
            raise ValueError('Archive escapes extraction directory')
        return output
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as incoming:
            for member in incoming.infolist():
                mode = stat.S_IFMT(member.external_attr >> 16)
                if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or member.flag_bits & 1:
                    raise ValueError('Archive contains links, special files or encrypted content')
                directory = member.is_dir()
                output = target(member.filename, directory, member.file_size)
                if directory or not wanted(member.filename):
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                stream = output.open('xb')
                try:
                    with incoming.open(member) as source, stream:
                        budget.copy(source, stream, member.file_size)
                    output.chmod(0o644)
                except Exception:
                    output.unlink(missing_ok=True)
                    raise
    else:
        with tarfile.open(archive) as incoming:
            for member in incoming:
                if not (member.isfile() or member.isdir()):
                    raise ValueError('Archive contains links or special files')
                if member.isdir() and member.name in ('.', './'):
                    continue
                output = target(member.name, member.isdir(), member.size)
                if member.isdir() or not wanted(member.name):
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                stream = output.open('xb')
                try:
                    with incoming.extractfile(member) as source, stream:
                        budget.copy(source, stream, member.size)
                    output.chmod(0o644)
                except Exception:
                    output.unlink(missing_ok=True)
                    raise
    return destination


def main():
    import json
    import resource
    import sys
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (512 * 1024**2, 512 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    mode = sys.argv[1]
    source = sys.argv[2] if len(sys.argv) > 2 else '/input'
    output = Path(sys.argv[3] if len(sys.argv) > 3 else '/output')
    if mode == 'probe':
        result = zipfile.is_zipfile(source) or tarfile.is_tarfile(source)
        (output/'result.json').write_text(json.dumps(result))
    elif mode == 'extract':
        extract(source, output/'tree')
    else:
        raise ValueError('Unknown archive worker operation')


if __name__ == '__main__':
    main()
