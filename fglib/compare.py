"""Per-function comparison of two traces (--compare BASE).

Both traces go through the timeline model, so slices are paired the same way
(per task, orphans/truncation handled). For every function we report calls,
inclusive time, self time (inclusive minus direct children), mean and max, and
the difference current - base. Errors are counted per errno origin.

Durations come from function_graph and include tracing overhead; the overhead
is similar in both traces when they were captured with the same options, so
differences remain meaningful (see docs/design.md).
"""
from . import timeline


def function_key(data, index, by_space):
    name = data['strings'][data['cols']['name'][index]]
    if not by_space:
        return name
    return ('user' if data['cols']['flags'][index] & timeline.F_USER else 'kernel', name)


def function_stats(data, adjusted=False, by_space=False):
    """data: TimelineBuilder.to_data() -> {name: [calls, incl, self, max, errors]}."""
    c = data['cols']
    n = data['n']
    dur = c['adj'] if adjusted and c.get('adj') else c['dur']
    parent = c['parent']
    self_t = list(dur)
    for i in range(n):
        p = parent[i]
        if p >= 0:
            self_t[p] -= dur[i]
    errs = {}
    for ch in data['chains']:
        if len(ch) > 6 and ch[6]:
            continue          # minor: not counted as an error
        errs[ch[0]] = errs.get(ch[0], 0) + 1
    out = {}
    for i in range(n):
        name = function_key(data, i, by_space)
        st = out.get(name)
        if st is None:
            st = out[name] = [0, 0, 0, 0, 0]
        st[0] += 1
        st[1] += dur[i]
        st[2] += max(0, self_t[i])
        if dur[i] > st[3]:
            st[3] = dur[i]
        st[4] += errs.get(i, 0)
    return out


def window(data):
    """Total traced time: sum of top-level slice durations (ns)."""
    c = data['cols']
    return sum(d for d, p in zip(c['dur'], c['parent']) if p < 0)


def _rows(a, b):
    rows = []
    for key in sorted(set(a) | set(b)):
        name = key[1] if isinstance(key, tuple) else key
        rows.append([name] + a.get(key, [0, 0, 0, 0, 0]) + b.get(key, [0, 0, 0, 0, 0]) +
                    ([key[0]] if isinstance(key, tuple) else []))
    rows.sort(key=lambda r: -abs(r[8] - r[3]))     # default order: largest change of self time
    return rows


def compare(cur_data, base_lines, rettypes=None, overhead=None, base_name='base', cur_name='current', rettype_resolver=None):
    """overhead: applied to both traces (read from each trace's header when None)."""
    from .inputs import UftraceInput
    if isinstance(base_lines, UftraceInput):
        base = base_lines.build(rettypes=rettypes, overhead=overhead, include_text=False,
                                rettype_resolver=rettype_resolver).to_data()
    else:
        base = timeline.build_timeline(base_lines, rettypes=rettypes, overhead=overhead,
                                       rettype_resolver=rettype_resolver).to_data()
    by_space = 'uftrace' in base or 'uftrace' in cur_data
    rows = _rows(function_stats(base, by_space=by_space), function_stats(cur_data, by_space=by_space))
    both_adj = bool(base['cols'].get('adj') and cur_data['cols'].get('adj'))
    rows_adj = _rows(function_stats(base, True, by_space), function_stats(cur_data, True, by_space)) if both_adj else None
    return {
        'rows_adj': rows_adj,
        'overhead': [base.get('overhead'), cur_data.get('overhead')],
        'v': 1,
        'names': [base_name, cur_name],
        'modes': [base['mode'], cur_data['mode']],
        'calls': [base['n'], cur_data['n']],
        'window': [window(base), window(cur_data)],
        'errors': [sum(1 for c in base['chains'] if not c[6]), sum(1 for c in cur_data['chains'] if not c[6])],
        # row: name, base[calls, incl, self, max, err], cur[calls, incl, self, max, err]
        'rows': rows,
    }
