#!/usr/bin/env python3
"""Invariant tests for timeline.py / btf.py.

Usage: python3 tests/test_timeline.py [trace files...]
Without arguments runs the bundled ftrace.txt plus synthetic format variants.
Optional env: FG_VMLINUX=<vmlinux with .BTF> to also check BTF classification.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import timeline  # noqa: E402
import btf  # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)
    return cond


def invariants(name, data, expect_mode=None):
    c = data['cols']
    n = data['n']
    start, dur, task, depth, parent = [], c['dur'], c['task'], c['depth'], c['parent']
    acc, pt = 0, -1
    for i in range(n):
        if task[i] != pt:
            pt, acc = task[i], 0
        acc += c['start'][i]
        start.append(acc)
    end = [start[i] + dur[i] for i in range(n)]
    bad_dur = sum(1 for d in dur if d < 0)
    bad_nest = 0
    bad_depth = 0
    for i in range(n):
        p = parent[i]
        if p < 0:
            continue
        if not (start[p] <= start[i] and end[i] <= end[p]):
            bad_nest += 1
        if depth[p] != depth[i] - 1 or task[p] != task[i]:
            bad_depth += 1
    rows = {}
    for i in range(n):
        rows.setdefault((task[i], depth[i]), []).append(i)
    overlap = 0
    for idx in rows.values():
        idx.sort(key=lambda i: start[i])
        for a, b in zip(idx, idx[1:]):
            if start[b] < end[a]:
                overlap += 1
    check(bad_dur == 0, f"{name}: {bad_dur} negative durations")
    check(bad_nest == 0, f"{name}: {bad_nest} children outside parent")
    check(bad_depth == 0, f"{name}: {bad_depth} parent depth/task mismatch")
    check(overlap == 0, f"{name}: {overlap} overlapping slices in a row")
    if expect_mode:
        check(data['mode'] == expect_mode, f"{name}: mode {data['mode']} != {expect_mode}")
    caps = ','.join(k for k, v in data['caps'].items() if v)
    print(f"  {name:28s} mode={data['mode']:10s} slices={n:7d} tasks={len(data['tasks']):3d} "
          f"chains={len(data['chains']):4d} off={len(data['offcpu']):3d} trunc={data['stats']['truncated']} "
          f"orph={data['stats']['orphans']}  [{caps}]")


def run(name, text, db=None, expect_mode=None):
    lines = text.split('\n')
    b = timeline.build_timeline(lines, rettypes=db)
    d = b.to_data()
    invariants(name, d, expect_mode)
    return d


SYNTH_FULL = """\
# tracer: function_graph
1000.000100 |   0)   cat-42     |  ...1. |               |  __arm64_sys_openat() { /* <-invoke_syscall+0x50/0x120 */
1000.000101 |   0)   cat-42     |  ...1. |               |    do_sys_openat2() { /* <-__arm64_sys_openat+0x7c/0xd0 */
1000.000102 |   0)   cat-42     |  d.h1. |   1.000 us    |      handle_irq_x(); /* <-el1_interrupt+0x30/0x60 ret=0x0 */
1000.000104 |   0)   cat-42     |  ...1. |   3.000 us    |      path_openat(); /* <-do_filp_open+0x60/0x100 ret=-2 */
1000.000108 |   0)   cat-42     |  ...1. |   7.000 us    |    } /* do_sys_openat2 ret=-2 */
1000.000109 |   0)   cat-42     |  ...1. |   9.000 us    |  } /* __arm64_sys_openat ret=-2 */
"""
SYNTH_NOPROC = """\
 0)               |  foo() {
 0)   1.000 us    |    bar();
 1)               |  baz() {
 1)   2.000 us    |    qux();
 0)   3.000 us    |  }
 1)   4.000 us    |  }
"""
SYNTH_ORPHAN = """\
 0)   cat-1   |   1.000 us    |      leaf_a();
 0)   cat-1   |   5.000 us    |    } /* mid */
 0)   cat-1   | + 10.000 us   |  } /* top */
 0)   cat-1   |               |  next() {
"""
SYNTH_NODUR = """\
1000.000100 |   0)   sh-7     | outer() {
1000.000102 |   0)   sh-7     |   inner();
1000.000105 |   0)   sh-7     | } /* outer */
"""
SYNTH_SWITCH = """\
1000.000100 |   0)   a-10     |  ...1. |               |  schedule() {
1000.000101 |   0)   a-10     |  d..2. |   0.500 us    |    __switch_to();
 ------------------------------------------
 0)   a-10     =>   b-11
 ------------------------------------------

1000.000200 |   0)   b-11     |  ...1. |   1.000 us    |  work();
 ------------------------------------------
 0)   b-11     =>   a-10
 ------------------------------------------

1000.000300 |   0)   a-10     |  ...1. | ! 200.000 us  |  } /* schedule */
"""


def main():
    paths = sys.argv[1:]
    vml = os.environ.get('FG_VMLINUX')
    db = btf.build_return_db(vml) if vml else None
    print("synthetic:")
    d = run('full', SYNTH_FULL, expect_mode='abs')
    S = d['strings']
    ch = d['chains']
    check(len(ch) == 1, f"full: expected 1 error chain, got {len(ch)}")
    if ch:
        check(S[d['cols']['name'][ch[0][0]]] == 'path_openat' and ch[0][3] == 1, "full: chain origin/fate wrong")
    check(d['cols']['flags'][[S[x] for x in d['cols']['name']].index('handle_irq_x')] & 3 == 2, "full: hardirq ctx not set")
    run('noproc', SYNTH_NOPROC, expect_mode='compressed')
    d = run('orphan', SYNTH_ORPHAN, expect_mode='compressed')
    check(d['stats']['orphans'] == 2 and d['stats']['truncated'] == 1, f"orphan: stats {d['stats']}")
    run('nodur', SYNTH_NODUR, expect_mode='abs')
    d = run('switch', SYNTH_SWITCH, expect_mode='abs')
    a_off = [r for r in d['offcpu'] if d['tasks'][r[0]]['pid'] == 10]
    check(len(a_off) == 1 and a_off[0][2] is not None and a_off[0][3] == 1, f"switch: offcpu {d['offcpu']}")
    if not paths:
        paths = [os.path.join(os.path.dirname(HERE), 'ftrace.txt')]
    print("files:")
    for p in paths:
        with open(p, errors='replace') as f:
            run(os.path.basename(p), f.read(), db=db)
    if db is not None:
        for fn, want in (('do_raw_spin_unlock', 'void'), ('mutex_unlock', 'void')):
            r, _ = db.lookup(fn)
            check(r is not None and r[0] == want, f"btf: {fn} -> {r}, want {want}")
    if FAIL:
        print("FAILED:")
        for m in FAIL:
            print("  " + m)
        sys.exit(1)
    print("ALL TIMELINE TESTS PASSED")


if __name__ == '__main__':
    main()
