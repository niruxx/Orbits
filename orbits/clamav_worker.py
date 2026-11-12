"""Exec the private foreground scanner with Linux parent-death cleanup."""
import ctypes
import os
import signal
import sys


def main():
    parent = int(sys.argv[1])
    if os.getppid() != parent:
        raise SystemExit('Scanner parent exited before startup')
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise OSError(ctypes.get_errno(), 'Could not configure scanner cleanup')
    if os.getppid() != parent:
        raise SystemExit('Scanner parent exited during startup')
    os.execv(sys.argv[2], sys.argv[2:])


if __name__ == '__main__':
    main()
