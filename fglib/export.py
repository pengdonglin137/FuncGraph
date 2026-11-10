"""--export: analysis results as JSON (everything) or CSV (per-function statistics) for scripts/CI.

JSON layout (durations in ns, times relative to the first event):
    meta        trace, mode, caps, overhead
    functions   [{name, calls, total, self, max, p50, p90, p99, errors}]  (self/total: measured)
    errors      [{origin, top, entry, errno, name, fate, verified, frames, task, pid, start}]
    sections    [{kind, duration, task, pid, start, started_in, ended_in, max_depth}]
    sched_latency [{task, pid, start, duration, max_preempt, irqs_off}]
    atomic_sleep  [{function, reasons, parent, task, pid, start}]
    off_cpu     [{task, pid, start, duration, waiting_in, state, preempted, woken_by}]
"""
import csv
import json

from .compare import function_key, function_stats

FATES = ['handled', 'user', 'escaped']
SECTION_KINDS = ['irqs_off', 'preempt_off', 'bh_off']
ATOMIC_BITS = [(1, 'irqs_off'), (2, 'preempt_off'), (4, 'interrupt'), (8, 'bh_off')]


def _q(sorted_vals, p):
    return sorted_vals[min(len(sorted_vals) - 1, int(p * (len(sorted_vals) - 1) + 0.5))] if sorted_vals else 0


def build(d, errno_map=None, trace_name=''):
    c, S, T = d['cols'], d['strings'], d['tasks']
    n = d['n']
    start, acc, pt = [], 0, -1
    for i in range(n):
        if c['task'][i] != pt:
            pt, acc = c['task'][i], 0
        acc += c['start'][i]
        start.append(acc)
    name = lambda i: S[c['name'][i]] if i >= 0 else None  # noqa: E731
    task = lambda t: (T[t]['comm'], T[t]['pid'])          # noqa: E731
    by_space = 'uftrace' in d
    by_name = {}
    for i in range(n):
        by_name.setdefault(function_key(d, i, by_space), []).append(c['dur'][i])
    functions = []
    for key, st in function_stats(d, by_space=by_space).items():
        durations = sorted(by_name[key])
        row = {'name': key[1] if by_space else key, 'calls': st[0], 'total': st[1], 'self': st[2], 'max': st[3],
               'p50': _q(durations, .5), 'p90': _q(durations, .9), 'p99': _q(durations, .99), 'errors': st[4]}
        if by_space:
            row['space'] = key[0]
        functions.append(row)
    functions.sort(key=lambda r: -r['self'])
    errno_map = errno_map or {}

    def entry_of(i):
        k = i
        while k >= 0:
            nm = name(k)
            if 'sys_' in nm:
                return nm
            if c['parent'][k] < 0:
                return nm
            k = c['parent'][k]
        return None
    errors = []
    for ch in d['chains']:
        o, top, e, fate, ln, ver = ch[:6]
        minor = bool(ch[6]) if len(ch) > 6 else False
        comm, pid = task(c['task'][o])
        errors.append({'origin': name(o), 'top': name(top), 'entry': entry_of(top), 'errno': e,
                       'name': errno_map.get(e, ''), 'fate': FATES[fate], 'verified': bool(ver), 'frames': ln, 'minor': minor,
                       'task': comm, 'pid': pid, 'start': start[o]})
    sections = []
    for r in d.get('sections', []):
        comm, pid = task(r[0])
        sections.append({'kind': SECTION_KINDS[r[1]], 'duration': r[3] - r[2], 'task': comm, 'pid': pid, 'start': r[2],
                         'started_in': name(r[4]), 'ended_in': name(r[5]), 'max_depth': r[6]})
    sections.sort(key=lambda r: -r['duration'])
    lat = []
    for r in d.get('resched', []):
        comm, pid = task(r[0])
        lat.append({'task': comm, 'pid': pid, 'start': r[1], 'duration': r[2] - r[1], 'max_preempt': r[3], 'irqs_off': bool(r[4])})
    lat.sort(key=lambda r: -r['duration'])
    atomic = []
    for i, bits in d.get('atomic', []):
        comm, pid = task(c['task'][i])
        atomic.append({'function': name(i), 'reasons': [nm for b, nm in ATOMIC_BITS if bits & b],
                       'parent': name(c['parent'][i]), 'task': comm, 'pid': pid, 'start': start[i]})
    off = []
    for r in d.get('offcpu', []):
        comm, pid = task(r[0])
        off.append({'task': comm, 'pid': pid, 'start': r[1], 'duration': (r[2] - r[1]) if r[2] is not None else None,
                    'waiting_in': S[r[4]] if r[4] >= 0 else None, 'state': S[r[5]] if r[5] >= 0 else None,
                    'preempted': bool(r[8]), 'woken_by': ('%s %d' % task(r[9])) if r[9] >= 0 else None})
    result = {
        'meta': {'trace': trace_name, 'mode': d['mode'], 'caps': {k: v for k, v in d['caps'].items() if v},
                 'overhead': d.get('overhead'), 'calls': n},
        'functions': functions, 'errors': errors, 'sections': sections, 'sched_latency': lat,
        'atomic_sleep': atomic, 'off_cpu': off,
    }
    if by_space:
        result['meta']['uftrace'] = d['uftrace']
    if 'tail_calls' in d:
        evidence = d['tail_calls']
        result['tail_calls'] = dict(evidence, pairs=[dict(pair, parent=S[pair['parent']], child=S[pair['child']],
            parent_module=S[pair['parent_module']] if pair['parent_module'] >= 0 else None,
            child_module=S[pair['child_module']] if pair['child_module'] >= 0 else None) for pair in evidence['pairs'].values()])
    return result


def write(path, d, errno_map=None, trace_name=''):
    data = build(d, errno_map, trace_name)
    if path.endswith('.csv'):
        cols = ['name', 'calls', 'total', 'self', 'max', 'p50', 'p90', 'p99', 'errors']
        if 'uftrace' in d:
            cols.insert(1, 'space')
        with open(path, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(data['functions'])
    else:
        with open(path, 'w') as f:
            json.dump(data, f, indent=1)
    return data
