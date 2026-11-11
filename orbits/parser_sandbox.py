"""Restricted worker launcher shared by font and archive parsers."""
from pathlib import Path
import subprocess
import sys
import threading

_slots = threading.BoundedSemaphore(2)


def command(source, output, worker, arguments):
    from orbits.image_safety import sandbox_command
    result = sandbox_command(source, output, 0, 1)
    # Reuse the audited mounts/environment, replacing only the trusted worker and arguments.
    worker_index = result.index('/worker.py')
    result[worker_index - 1] = str(Path(worker).resolve())
    result[-5:] = [sys.executable, '-I', '/worker.py', *map(str, arguments)]
    return result


def run(source, output, worker, arguments, timeout=45):
    with _slots:
        try:
            result = subprocess.run(command(source, output, worker, arguments),
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError('Protected asset processing failed or timed out') from error
        if result.returncode:
            raise ValueError('Protected asset processing rejected this file, exceeded limits, or could not start bubblewrap namespaces')
