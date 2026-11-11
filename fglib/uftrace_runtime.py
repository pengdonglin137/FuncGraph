"""Locate the optional native uftrace payload and forward recording commands."""
import os
from pathlib import Path
import shutil
import sys


PAYLOAD = Path(__file__).resolve().parent / '_native' / 'uftrace'


def command(executable=None):
    if executable:
        resolved = shutil.which(os.fspath(executable))
        if not resolved:
            raise OSError('uftrace override is not executable: ' + os.fspath(executable))
        return [resolved]
    bundled = PAYLOAD / 'bin' / 'uftrace'
    if PAYLOAD.exists():
        libraries = PAYLOAD / 'lib' / 'uftrace'
        if not bundled.is_file() or not os.access(bundled, os.X_OK) or not (libraries / 'libmcount.so').is_file():
            raise OSError('bundled uftrace is incomplete; reinstall the Linux wheel or use --uftrace-bin PATH')
        return [str(bundled), '--libmcount-path=' + str(libraries)]
    resolved = shutil.which('uftrace')
    if not resolved:
        raise OSError('uftrace is unavailable; install the Linux wheel with bundled uftrace, '
                      'put a patched uftrace on PATH, or use --uftrace-bin PATH')
    return [resolved]


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        invocation = command() + arguments
        os.execv(invocation[0], invocation)
    except OSError as error:
        if arguments in (['--help'], ['-h']):
            print('Usage: fguftrace [uftrace command and options]\n'
                  'Run the bundled recorder, falling back to patched uftrace on PATH.\n' + str(error))
            return 0
        print('fguftrace: ' + str(error), file=sys.stderr)
        return 1
