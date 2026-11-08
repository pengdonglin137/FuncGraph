"""Pre-filter raw function_graph lines before conversion (--pid/--comm/--cpu/--time-range/--graph).

Works on the raw text so that every later stage (text view, address resolution,
timeline) sees a smaller, still well-formed trace:

* header comments are kept;
* call trees are never cut in the middle: --graph keeps whole subtrees of the
  matching functions, and --time-range keeps a call when it *starts* inside the
  window together with everything nested under it;
* context-switch markers are kept when either side of the switch is kept;
* lines that carry no task/CPU (e.g. LOST EVENTS) are kept.
"""
import re

from .timeline import RE_SWITCH, RE_FUNC_HEAD, TimelineBuilder

RE_TIME = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*(ns|us|ms|s)?\s*$')
_UNIT = {'ns': 1, 'us': 1000, 'ms': 1000000, 's': 1000000000, None: 1000000000}


def parse_time(text):
    """'2003249.69' (seconds, absolute) or '1.5ms' -> ns."""
    m = RE_TIME.match(text)
    if not m:
        raise ValueError(f'bad time value {text!r} (use e.g. 2003249.6915, 120us, 3.5ms)')
    return int(float(m.group(1)) * _UNIT[m.group(2)])


def parse_range(spec):
    """'START:END', 'START:' or ':END'.

    Values are absolute trace timestamps in seconds (e.g. 2003249.6915). A '+'
    prefix makes a value relative to the first timestamp of the trace, and units
    may be given: '+1.5ms:+3ms'. Returns (start, end, start_is_rel, end_is_rel) in ns.
    """
    if ':' not in spec:
        raise ValueError('--time-range expects START:END')
    a, b = spec.split(':', 1)
    a, b = a.strip(), b.strip()
    ra = a.startswith('+')
    rb = b.startswith('+')
    start = parse_time(a.lstrip('+')) if a.lstrip('+') else None
    end = parse_time(b.lstrip('+')) if b.lstrip('+') else None
    return start, end, ra, rb


class Filter:
    def __init__(self, pids=None, comms=None, cpus=None, time_range=None, graph=None, notrace=None):
        self.pids = set(pids or [])
        self.comms = [re.compile(c) for c in (comms or [])]
        self.cpus = set(cpus or [])
        self.time_range = parse_range(time_range) if time_range else None
        self.graph = [re.compile(g) for g in (graph or [])]
        self.notrace = [re.compile(g) for g in (notrace or [])]

    def active(self):
        return bool(self.pids or self.comms or self.cpus or self.time_range or self.graph or self.notrace)

    def describe(self):
        parts = []
        if self.pids:
            parts.append('pid ' + ','.join(map(str, sorted(self.pids))))
        if self.comms:
            parts.append('comm ' + ','.join(c.pattern for c in self.comms))
        if self.cpus:
            parts.append('cpu ' + ','.join(map(str, sorted(self.cpus))))
        if self.time_range:
            parts.append('time-range')
        if self.graph:
            parts.append('graph ' + ','.join(g.pattern for g in self.graph))
        if self.notrace:
            parts.append('notrace ' + ','.join(g.pattern for g in self.notrace))
        return ', '.join(parts)


def apply(lines, flt):
    """Return (kept_lines, stats). `lines` are raw strings without newlines."""
    if not flt.active():
        return lines, {'in': len(lines), 'out': len(lines)}
    probe = TimelineBuilder([])
    first_ts = None
    task_of_pid = {}          # pid -> comm (latest)
    cpu_current = {}          # cpu -> pid (from switch markers, for traces without funcgraph-proc)
    # per task key: open frames [depth, KEEP|DROP|PENDING]
    stacks = {}
    keep = [False] * len(lines)
    tr = flt.time_range
    t_lo = t_hi = None

    def task_ok(pid, comm, cpu):
        if flt.pids and pid not in flt.pids:
            return False
        if flt.comms and not (comm and any(c.search(comm) for c in flt.comms)):
            return False
        if flt.cpus and cpu not in flt.cpus:
            return False
        return True

    for i, raw in enumerate(lines):
        if not raw or raw[0] == '#':
            keep[i] = True
            continue
        st = raw.strip()
        if not st or st.startswith('-----'):
            continue  # separators are re-attached to kept switch markers below
        if '=>' in raw and '|' not in raw:
            m = RE_SWITCH.match(raw)
            if m:
                cpu = int(m.group(1)) if m.group(1) else None
                a = task_ok(int(m.group(3)), m.group(2).strip(), cpu)
                b = task_ok(int(m.group(5)), m.group(4).strip(), cpu)
                if cpu is not None:
                    cpu_current[cpu] = int(m.group(5))
                keep[i] = a or b
                continue
        if st.startswith('CPU:') or st.startswith('#####'):
            keep[i] = True
            continue
        ts, cpu, comm, pid, lat, dur, has_dur, pos = probe._prologue(raw)
        if pid is None and cpu is not None:
            pid = cpu_current.get(cpu)
            comm = task_of_pid.get(pid)
        elif pid is not None:
            task_of_pid[pid] = comm
        if ts is not None and first_ts is None:
            first_ts = ts
            if tr:
                s, e, rs, re_ = tr
                t_lo = (first_ts + s if rs else s) if s is not None else None
                t_hi = (first_ts + e if re_ else e) if e is not None else None
        body = raw[pos:]
        b = body.strip()
        depth = (len(body) - len(body.lstrip(' '))) // 2
        key = ('idle', cpu) if pid == 0 else (pid if pid is not None else ('cpu', cpu))
        stack = stacks.setdefault(key, [])
        while stack and stack[-1][0] > depth:      # exits lost
            stack.pop()
        if b.startswith('}'):
            if stack and stack[-1][0] == depth:
                keep[i] = stack.pop()[1] == KEEP
            else:                                   # orphan exit: entry before the window
                keep[i] = _root_state(flt, task_ok(pid, comm, cpu), ts, t_lo, t_hi, None) == KEEP
            continue
        mh = RE_FUNC_HEAD.match(b)
        name = mh.group(1) if mh else None
        is_entry = b.endswith('{') or ') {' in b
        parent = stack[-1][1] if stack else None
        if parent == KEEP:
            state = DROP if (name and flt.notrace and any(g.search(name) for g in flt.notrace)) else KEEP
        elif parent == DROP:
            state = DROP
        else:                                       # no parent, or inside a not-yet-matching --graph frame
            state = _root_state(flt, task_ok(pid, comm, cpu), ts, t_lo, t_hi, name)
        keep[i] = state == KEEP
        if is_entry:
            stack.append([depth, state])
        elif not mh:
            keep[i] = keep[i] or (parent == KEEP)      # comments / events inside a kept tree
    # re-attach the '-----' separators around kept switch markers
    for i, raw in enumerate(lines):
        if keep[i] and '=>' in raw and '|' not in raw:
            for j in (i - 1, i + 1, i + 2):
                if 0 <= j < len(lines) and (lines[j].strip().startswith('-----') or not lines[j].strip()):
                    keep[j] = True
    out = [l for l, k in zip(lines, keep) if k]
    return out, {'in': len(lines), 'out': len(out)}


KEEP, DROP, PENDING = 1, 0, 2


def _in_time(ts, lo, hi):
    if ts is None:
        return True
    if lo is not None and ts < lo:
        return False
    if hi is not None and ts > hi:
        return False
    return True


def _root_state(flt, task_ok, ts, lo, hi, name):
    """State of a call that is not inside a kept/dropped tree."""
    if not task_ok or not _in_time(ts, lo, hi):
        return DROP
    if name and flt.notrace and any(g.search(name) for g in flt.notrace):
        return DROP
    if flt.graph:
        return KEEP if (name and any(g.search(name) for g in flt.graph)) else PENDING
    return KEEP
