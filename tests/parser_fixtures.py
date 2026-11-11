"""Unit-test worker harness. Production never has an unsandboxed fallback."""
import subprocess
import sys
from unittest.mock import patch
_RUN = subprocess.run
_POPEN = subprocess.Popen


def run_worker(source, output, worker, arguments, timeout=45):
    with patch.object(subprocess, "Popen", _POPEN):
        result = _RUN([sys.executable, '-I', str(worker), *map(str, arguments), str(source), str(output)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout, check=False)
    if result.returncode:
        raise ValueError('Worker rejected input: ' + result.stderr.decode(errors='replace')[-200:])
