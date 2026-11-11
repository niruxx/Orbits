"""Fail-closed sandboxed decoding; only bounded raw pixels cross the boundary."""
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import zlib

MAX_INPUT = 512 * 1024**2
_decoder_slots = threading.BoundedSemaphore(2)


def _read_regular(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise RuntimeError('Invalid or oversized image processing output')
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise RuntimeError('Image processing output exceeds its limit')
        return data


def sandbox_command(source, output, size, frames):
    bwrap = shutil.which('bwrap')
    if not bwrap:
        raise RuntimeError('Protected asset processing requires bubblewrap (bwrap); install it to browse or import assets')
    command = [bwrap, '--die-with-parent', '--new-session', '--unshare-all', '--cap-drop', 'ALL',
               '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin',
               '--setenv', 'PYTHONNOUSERSITE', '1', '--setenv', 'HOME', '/nonexistent']
    for path in ('/usr', '/lib', '/lib64', '/bin', '/etc/ld.so.cache'):
        if Path(path).exists():
            command += ['--ro-bind', path, path]
    command += ['--tmpfs', '/tmp']
    # A packaged Python lives under /usr; a virtual environment needs only its own tree.
    if not Path(sys.prefix).is_relative_to('/usr'):
        command += ['--ro-bind', sys.prefix, sys.prefix]
    worker = Path(__file__).with_name('image_worker.py').resolve()
    command += ['--proc', '/proc', '--dev', '/dev',
                '--ro-bind', str(Path(source).resolve()), '/input',
                '--ro-bind', str(worker), '/worker.py', '--bind', str(output), '/output',
                '--chdir', '/', sys.executable, '-I', '/worker.py', str(size), str(frames)]
    return command


def _chunk(kind, data):
    return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))


def _png(pixels, width, height):
    # Construct PNG ourselves: untrusted decoders cannot smuggle metadata or encoded payloads.
    compressor = zlib.compressobj()
    rows = []
    stride = width * 4
    for offset in range(0, len(pixels), stride):
        rows.append(compressor.compress(b'\0' + pixels[offset:offset + stride]))
    rows.append(compressor.flush())
    return (b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 6, 0, 0, 0))
            + _chunk(b'IDAT', b''.join(rows)) + _chunk(b'IEND', b''))


def normalize_images(source, destination, *, size=0, frames=1, scan=True, publisher=False):
    # Bound concurrent decoders and parent-side pixel buffers during grid loading.
    with _decoder_slots:
        return _normalize_images(source, destination, size=size, frames=frames, scan=scan, publisher=publisher)


def _normalize_images(source, destination, *, size=0, frames=1, scan=True, publisher=False):
    """Snapshot and optionally scan input, then publish fresh PNGs after isolated decoding."""
    from orbits.security import checked_local_file
    if not 0 <= size <= 4096 or not 1 <= frames <= 200:
        raise ValueError('Invalid image processing limits')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with checked_local_file(source, scan=scan, archive=False, publisher=publisher) as captured:
        with tempfile.TemporaryDirectory(prefix='orbits-pixels-') as temporary:
            command = sandbox_command(captured, temporary, size, frames)
            try:
                result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        timeout=40, check=False)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise RuntimeError('Protected image processing failed or timed out') from error
            if result.returncode:
                raise RuntimeError('Protected image processing failed: unsupported or invalid image, resource limit, or unavailable bubblewrap namespaces')
            root = Path(temporary)
            manifest = json.loads(_read_regular(root/'frames.json', 16384))
            if not isinstance(manifest, list) or not 1 <= len(manifest) <= frames:
                raise RuntimeError('Invalid image frame manifest')
            total, encoded = 0, []
            for index, dimensions in enumerate(manifest):
                if not isinstance(dimensions, list) or len(dimensions) != 2:
                    raise RuntimeError('Invalid image dimensions')
                width, height = dimensions
                if any(type(n) is not int or n < 1 for n in dimensions) or width * height > 40_000_000:
                    raise RuntimeError('Image dimensions exceed limits')
                if size and max(dimensions) > size:
                    raise RuntimeError('Preview dimensions exceed limits')
                length = width * height * 4
                total += length
                if total > MAX_INPUT:
                    raise RuntimeError('Image frame output exceeds limits')
                pixels = _read_regular(root/f'{index}.rgba', length)
                if len(pixels) != length:
                    raise RuntimeError('Incomplete image pixel output')
                target = destination/f'frame-{index:03d}.png'
                target.write_bytes(_png(pixels, width, height))
                encoded.append(target)
            return encoded
