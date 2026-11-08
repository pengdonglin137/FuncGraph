"""Reading trace input: files, '-' (stdin), .gz/.xz/.bz2, several files merged by time."""
import bz2
import gzip
import heapq
import lzma
import re
import sys

RE_TS = re.compile(r'^\s*(\d+)\.(\d{6}) \|')
OPENERS = {'.gz': gzip.open, '.xz': lzma.open, '.lzma': lzma.open, '.bz2': bz2.open}


def read_text(path):
    if path == '-':
        return sys.stdin.read()
    for ext, op in OPENERS.items():
        if path.endswith(ext):
            with op(path, 'rt', errors='replace') as f:
                return f.read()
    with open(path, 'r', errors='replace') as f:
        return f.read()


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
            ts = int(m.group(1)) * 1000000 + int(m.group(2))
            cur = [l]
        else:
            if ts is None and l.strip() and not l.startswith('#'):
                timed = False
            cur.append(l)
    if cur:
        chunks.append((ts if ts is not None else 0, cur))
    return head, chunks, timed


def read_lines(paths):
    """Return (lines, note). Several files are merged by absolute timestamp when all have one."""
    if len(paths) == 1:
        return split_lines(read_text(paths[0])), None
    parts = [_chunks(split_lines(read_text(p))) for p in paths]
    head = parts[0][0]
    if not all(p[2] for p in parts):
        out = list(head)
        for h, ch, _ in parts:
            for _, c in ch:
                out.extend(c)
        return out, 'inputs without funcgraph-abstime: concatenated in the given order'
    streams = [[(ts, k, n, c) for n, (ts, c) in enumerate(ch)] for k, (_, ch, _) in enumerate(parts)]
    out = list(head)
    for _, _, _, c in heapq.merge(*streams):
        out.extend(c)
    return out, f'merged {len(paths)} inputs by timestamp'
