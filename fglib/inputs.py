"""Reading trace input: files, '-' (stdin), .gz/.xz/.bz2, several files merged by time."""
import bz2
import gzip
import heapq
import io
import lzma
import os
import re
import shutil
import subprocess
import sys
import tempfile

from . import raw

RE_TS = re.compile(r'^\s*(\d+)\.(\d{6,9}) \|')
OPENERS = {'.gz': gzip.open, '.xz': lzma.open, '.lzma': lzma.open, '.bz2': bz2.open}


class UftraceInput:
    def __init__(self, stream):
        from .uftrace import read_export
        self.metadata, self.records = read_export(stream)

    def build(self, enable_fold=False, include_text=True, **options):
        from .uftrace import UftraceTimeline
        return UftraceTimeline(self.metadata, self.records, **options).build(enable_fold=enable_fold, include_text=include_text)


def read_uftrace_directory(directory, executable=None):
    from .uftrace import UftraceInputError
    with subprocess.Popen([executable or 'uftrace', 'dump', '--fgraph', '--srcline', '-d', os.path.abspath(directory)],
                          stdout=subprocess.PIPE, text=True, encoding='utf-8') as process:
        try:
            trace = UftraceInput(process.stdout)
        except BaseException:
            if process.poll() is None:
                process.kill()
            raise
        if process.wait():
            raise UftraceInputError('uftrace dump failed; use a patched build (scripts/uftrace.sh build)')
        return trace


def _read_stream(stream, path, input_format, raw_instance, as_lines):
    prefix = stream.read(4096)
    stream.seek(0)
    binary = prefix.startswith(raw.MAGIC)
    if input_format == 'uftrace' or (as_lines and input_format == 'auto' and prefix.lstrip().startswith(b'{') and
                                    b'funcgraph.uftrace.v' in prefix):
        if not as_lines:
            raise ValueError('structured uftrace input cannot be read as ftrace text')
        with io.TextIOWrapper(stream, encoding='utf-8') as text:
            return UftraceInput(text)
    if input_format == 'raw' and not binary:
        raise raw.RawInputError('raw input must be a trace-cmd trace.dat container; '
                                'bare trace_pipe_raw pages need event formats, symbols and clock metadata')
    if input_format == 'text' and binary:
        raise raw.RawInputError('binary trace-cmd input cannot be read as text (use --input-format auto or raw)')
    if binary:
        if path is not None:
            lines = raw.read_lines(path, instance=raw_instance)
        else:
            with tempfile.TemporaryDirectory(prefix='funcgraph-input-') as directory:
                temporary = directory + '/trace.dat'
                with open(temporary, 'wb') as output:
                    shutil.copyfileobj(stream, output, length=1024 * 1024)
                lines = raw.read_lines(temporary, instance=raw_instance)
        return lines if as_lines else '\n'.join(lines) + '\n'
    if b'\0' in prefix:
        raise raw.RawInputError('unrecognized binary trace: use a trace-cmd trace.dat container, '
                                'not bare trace_pipe_raw/perf/Perfetto data')
    with io.TextIOWrapper(stream, errors='replace') as text:
        content = text.read()
    return split_lines(content) if as_lines else content


def _read_input(path, input_format, raw_instance, as_lines):
    if input_format not in ('auto', 'text', 'raw', 'uftrace'):
        raise ValueError('unknown input format: ' + input_format)
    if path == '-':
        if not hasattr(sys.stdin, 'buffer'):
            if input_format == 'uftrace' and as_lines:
                return UftraceInput(sys.stdin)
            if input_format == 'raw':
                raise raw.RawInputError('raw stdin needs a binary input stream')
            content = sys.stdin.read()
            return split_lines(content) if as_lines else content
        with tempfile.TemporaryFile() as stream:
            shutil.copyfileobj(sys.stdin.buffer, stream, length=1024 * 1024)
            stream.seek(0)
            return _read_stream(stream, None, input_format, raw_instance, as_lines)
    for ext, op in OPENERS.items():
        if path.endswith(ext):
            with op(path, 'rb') as stream:
                return _read_stream(stream, None, input_format, raw_instance, as_lines)
    with open(path, 'rb') as stream:
        return _read_stream(stream, path, input_format, raw_instance, as_lines)


def read_text(path, input_format='auto', raw_instance=None):
    return _read_input(path, input_format, raw_instance, as_lines=False)


def split_lines(text):
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return [l.rstrip('\r') for l in lines]


def _chunks(lines):
    """Leading '#' header, then chunks: a timestamped line plus the untimed lines after it."""
    i = 0
    head = []
    while i < len(lines) and (not lines[i] or lines[i].startswith('#')):
        head.append(lines[i])
        i += 1
    chunks, cur, ts = [], [], None
    timed = True
    for l in lines[i:]:
        m = RE_TS.match(l)
        if m:
            if cur:
                chunks.append((ts, cur))
            ts = int(m.group(1)) * 1000000000 + int(m.group(2).ljust(9, '0'))
            cur = [l]
        else:
            if ts is None and l.strip() and not l.startswith('#'):
                timed = False
            cur.append(l)
    if cur:
        chunks.append((ts if ts is not None else 0, cur))
    return head, chunks, timed


def read_lines(paths, input_format='auto', raw_instance=None):
    """Return (lines or UftraceInput, note). Merge timed ftrace files by timestamp."""
    if len(paths) == 1:
        return _read_input(paths[0], input_format, raw_instance, as_lines=True), None
    parts = []
    for path in paths:
        lines = _read_input(path, input_format, raw_instance, as_lines=True)
        if isinstance(lines, UftraceInput):
            from .uftrace import UftraceInputError
            raise UftraceInputError('use one uftrace recording or JSONL stream per report')
        parts.append(_chunks(lines))
    head = max((p[0] for p in parts), key=len)     # per-CPU files repeat the header: keep one
    if not all(p[2] for p in parts):
        out = list(head)
        for _h, ch, _ in parts:
            for _, c in ch:
                out.extend(c)
        return out, 'inputs without funcgraph-abstime: concatenated in the given order'
    streams = [[(ts, k, n, c) for n, (ts, c) in enumerate(ch)] for k, (_, ch, _) in enumerate(parts)]
    out = list(head)
    for _, _, _, c in heapq.merge(*streams):
        out.extend(c)
    return out, f'merged {len(paths)} inputs by timestamp'
