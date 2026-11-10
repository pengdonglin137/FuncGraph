"""Optional trace-cmd container decoding through a small, locally compiled bridge."""
import atexit
import os
import shlex
import shutil
import subprocess
import tempfile

from .constants import PROJECT_DIR


MAGIC = b'\x17\x08Dtracing'
_workspace = None
_executable = None


class RawInputError(ValueError):
    """A raw input cannot be decoded without losing its recorded semantics."""


def _helper():
    global _workspace, _executable
    if _executable is not None:
        return _executable
    try:
        subprocess.run(['pkg-config', '--atleast-version=1.5.0', 'libtracecmd'],
                       check=True, capture_output=True)
        flags = subprocess.check_output(
            ['pkg-config', '--cflags', '--libs', 'libtracecmd', 'libtraceevent', 'libtracefs'],
            text=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RawInputError('raw input requires pkg-config and the development libraries '
                            'libtracecmd >= 1.5.0, libtraceevent and libtracefs; '
                            'see docs/raw-input.md (or initialize third_party submodules)') from error
    compiler = shlex.split(os.environ.get('CC', 'cc'))
    if not compiler or shutil.which(compiler[0]) is None:
        raise RawInputError('raw input requires a native C compiler (CC, default: cc)')
    workspace = tempfile.TemporaryDirectory(prefix='funcgraph-raw-')
    executable = os.path.join(workspace.name, 'decode')
    source = os.path.join(PROJECT_DIR, 'fglib', 'native', 'raw.c')
    try:
        result = subprocess.run(compiler + ['-std=c11', '-O2', '-Wall', '-Wextra', source,
                                          '-o', executable] + shlex.split(flags),
                                capture_output=True, text=True)
        if result.returncode:
            raise RawInputError('cannot build raw decoder: ' + result.stderr[-4000:])
    except OSError as error:
        workspace.cleanup()
        raise RawInputError('cannot run raw decoder compiler: ' + str(error)) from error
    except BaseException:
        workspace.cleanup()
        raise
    _workspace, _executable = workspace, executable
    atexit.register(workspace.cleanup)
    return executable


def read_lines(path, instance=None):
    """Decode one buffer. Never return partial output when the native reader fails."""
    command = [_helper(), os.path.abspath(path)]
    if instance is not None:
        command.append(instance)
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors) as process:
            try:
                lines = [line.decode('utf-8', errors='replace').rstrip('\r\n') for line in
                         iter(process.stdout.readline, b'')]
            except BaseException:
                process.kill()
                raise
            status = process.wait()
        if status:
            errors.seek(0, os.SEEK_END)
            errors.seek(max(0, errors.tell() - 4000))
            detail = errors.read().decode('utf-8', errors='replace').strip()
            raise RawInputError('cannot decode trace-cmd input: ' + (detail or 'decoder exited ' + str(status)))
    return lines
