#!/usr/bin/env python3
"""timeline.py: slice reconstruction invariants and per-option behaviour.

    python3 -m unittest tests/test_timeline.py
    FG_TRACES="a.txt b.txt" python3 -m unittest tests/test_timeline.py   # also check extra traces
"""
import os
import unittest

import helpers
from fglib import timeline
from fglib.btf import ReturnTypeDB


def build(text, db=None):
    return timeline.build_timeline(text.split('\n'), rettypes=db).to_data()


def decode(d):
    c = d['cols']
    start, acc, pt = [], 0, -1
    for i in range(d['n']):
        if c['task'][i] != pt:
            pt, acc = c['task'][i], 0
        acc += c['start'][i]
        start.append(acc)
    end = [s + x for s, x in zip(start, c['dur'])]
    return start, end


def invariant_errors(d):
    """Return a list of violated invariants (empty when the model is consistent)."""
    c, n = d['cols'], d['n']
    start, end = decode(d)
    errs = []
    if any(x < 0 for x in c['dur']):
        errs.append('negative duration')
    for i in range(n):
        p = c['parent'][i]
        if p < 0:
            continue
        if not (start[p] <= start[i] and end[i] <= end[p]):
            errs.append(f'slice {i} outside parent {p}')
        if c['depth'][p] != c['depth'][i] - 1 or c['task'][p] != c['task'][i]:
            errs.append(f'slice {i}: parent depth/task mismatch')
    rows = {}
    for i in range(n):
        rows.setdefault((c['task'][i], c['depth'][i]), []).append(i)
    for idx in rows.values():
        idx.sort(key=lambda i: start[i])
        for a, b in zip(idx, idx[1:]):
            if start[b] < end[a]:
                errs.append(f'overlap {a}/{b}')
    for ch in d['chains']:
        if not (0 <= ch[0] < n and 0 <= ch[1] < n):
            errs.append('chain index out of range')
    return errs[:10]


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
SYNTH_VOID = """\
1000.000100 |   0)   sh-7     |  ...1. |   1.000 us    |  do_raw_spin_unlock(); /* <-x+0x1/0x2 ret=-1 */
1000.000102 |   0)   sh-7     |  ...1. |   1.000 us    |  search_cmp_ftr_reg(); /* <-x+0x1/0x2 ret=-32 */
1000.000104 |   0)   sh-7     |  ...1. |   1.000 us    |  real_errno(); /* <-x+0x1/0x2 ret=-22 */
1000.000106 |   0)   sh-7     |  ...1. |   1.000 us    |  get_ptr(); /* <-x+0x1/0x2 ret=0x0 */
"""


SYNTH_WAKE = """\
1000.000100 |   0)   cat-20   |  ...1. |               |  anon_pipe_read() {
1000.000101 |   0)   cat-20   |  d..2. |               |    schedule() {
1000.000102 |   0)   cat-20   |  d..2. |               |  /* sched_switch: prev_comm=cat prev_pid=20 prev_prio=120 prev_state=S ==> next_comm=sh next_pid=21 next_prio=120 */
 ------------------------------------------
 0)   cat-20   =>   sh-21
 ------------------------------------------

1000.000200 |   0)   sh-21    |  ...1. |               |  anon_pipe_write() {
1000.000201 |   0)   sh-21    |  d..2. |               |    try_to_wake_up() {
1000.000202 |   0)   sh-21    |  d..3. |               |  /* sched_waking: comm=cat pid=20 prio=120 target_cpu=000 */
1000.000203 |   0)   sh-21    |  d..2. |   2.000 us    |    } /* try_to_wake_up */
1000.000205 |   0)   sh-21    |  ...1. |   5.000 us    |  } /* anon_pipe_write */
1000.000206 |   1)  <idle>-0  |  d.h2. |               |  /* sched_wakeup: comm=cat pid=20 prio=120 target_cpu=000 */
 ------------------------------------------
 0)   sh-21    =>   cat-20
 ------------------------------------------

1000.000300 |   0)   cat-20   |  ...1. | ! 199.000 us  |    } /* schedule */
1000.000301 |   0)   cat-20   |  ...1. | ! 201.000 us  |  } /* anon_pipe_read */
"""


SYNTH_SECTIONS = """\
1000.000100 |   0)   t-5     |  ...1. |               |  outer() {
1000.000101 |   0)   t-5     |  ...1. |               |    _raw_spin_lock_irqsave() {
1000.000102 |   0)   t-5     |  d..2. |   1.000 us    |      preempt_count_add();
1000.000104 |   0)   t-5     |  d..2. |   3.000 us    |    } /* _raw_spin_lock_irqsave */
1000.000110 |   0)   t-5     |  d..2. |   1.000 us    |    mutex_lock();
1000.000120 |   0)   t-5     |  d.h3. |   1.000 us    |    irq_handler();
1000.000130 |   0)   t-5     |  ...2. |   1.000 us    |    work();
1000.000140 |   0)   t-5     |  ...1. |   1.000 us    |    done();
1000.000150 |   0)   t-5     |  ...1. |  51.000 us    |  } /* outer */
"""


class SyntheticTest(unittest.TestCase):
    def check(self, d, mode):
        self.assertEqual(invariant_errors(d), [])
        self.assertEqual(d['mode'], mode)

    def name(self, d, i):
        return d['strings'][d['cols']['name'][i]]

    def test_full_error_chain(self):
        d = build(SYNTH_FULL)
        self.check(d, 'abs')
        self.assertEqual(len(d['chains']), 1)
        origin, top, errno, fate = d['chains'][0][:4]
        self.assertEqual(self.name(d, origin), 'path_openat')
        self.assertEqual(self.name(d, top), '__arm64_sys_openat')
        self.assertEqual((errno, fate), (-2, 1))          # reaches user space

    def test_hardirq_context_from_flags(self):
        d = build(SYNTH_FULL)
        names = [d['strings'][x] for x in d['cols']['name']]
        self.assertEqual(d['cols']['flags'][names.index('handle_irq_x')] & timeline.F_CTX_MASK, timeline.CTX_HARDIRQ)

    def test_no_proc_per_cpu_tracks(self):
        d = build(SYNTH_NOPROC)
        self.check(d, 'compressed')
        self.assertEqual(sorted(t['kind'] for t in d['tasks']), [2, 2])

    def test_orphans_and_truncation(self):
        d = build(SYNTH_ORPHAN)
        self.check(d, 'compressed')
        self.assertEqual((d['stats']['orphans'], d['stats']['truncated']), (2, 1))

    def test_no_duration_estimates_from_timestamps(self):
        d = build(SYNTH_NODUR)
        self.check(d, 'abs')
        self.assertTrue(all(f & timeline.F_ESTIMATED for f in d['cols']['flags']))

    def test_context_switch_offcpu_and_runs(self):
        d = build(SYNTH_SWITCH)
        self.check(d, 'abs')
        a = [r for r in d['offcpu'] if d['tasks'][r[0]]['pid'] == 10]
        self.assertEqual(len(a), 1)
        self.assertIsNotNone(a[0][2])
        self.assertEqual(a[0][3], 1)                       # drawn below the schedule() frame
        runs = next(t['runs'] for t in d['tasks'] if t['pid'] == 10)
        self.assertEqual(len(runs), 2)                     # split at switch-in

    def test_wakeup_prefers_waking_event(self):
        d = build(SYNTH_WAKE)
        self.check(d, 'abs')
        self.assertTrue(d['caps']['sched_waking'])
        self.assertEqual(len(d['wakeups']), 1)                  # sched_wakeup from the IPI is ignored
        waker, ts, target, ctx, slc, off = d['wakeups'][0]
        self.assertEqual((d['tasks'][waker]['pid'], d['tasks'][target]['pid'], ctx), (21, 20, 0))
        self.assertEqual(self.name(d, slc), 'try_to_wake_up')
        r = d['offcpu'][off]
        self.assertEqual(d['strings'][r[4]], 'anon_pipe_read')  # what cat was waiting in
        self.assertEqual(d['strings'][r[5]], 'S')
        self.assertEqual(r[9], waker)

    def test_overhead_correction(self):
        d = timeline.build_timeline(SYNTH_FULL.split('\n'), overhead=(1000, 100)).to_data()
        names = [d['strings'][x] for x in d['cols']['name']]
        adj = dict(zip(names, d['cols']['adj']))
        dur = dict(zip(names, d['cols']['dur']))
        self.assertEqual(adj['path_openat'], dur['path_openat'] - 100)            # leaf: self overhead only
        self.assertEqual(adj['__arm64_sys_openat'], dur['__arm64_sys_openat'] - 3 * 1000 - 100)  # 3 descendants
        self.assertEqual(d['overhead']['source'], 'option')

    def test_overhead_from_header_and_absent(self):
        hdr = '# fgcapture: overhead child=500ns self=50ns (x)\n' + SYNTH_FULL
        d = build(hdr)
        self.assertEqual((d['overhead']['child'], d['overhead']['self'], d['overhead']['source']), (500, 50, 'trace header'))
        self.assertIsNone(build(SYNTH_FULL)['cols']['adj'])
        self.assertTrue(all(a >= 0 for a in build(hdr)['cols']['adj']))

    def test_critical_sections_and_atomic_sleep(self):
        d = build(SYNTH_SECTIONS)
        self.check(d, 'abs')
        self.assertEqual(d['preempt_base'], 1)
        irq = [r for r in d['sections'] if r[1] == 0]
        pre = [r for r in d['sections'] if r[1] == 1]
        self.assertEqual(len(irq), 1)
        # irqs-off ends before the interrupt (irqs were re-enabled in untraced code)
        self.assertEqual(irq[0][3] - irq[0][2], 8000)
        self.assertEqual(self.name(d, irq[0][4]), '_raw_spin_lock_irqsave')
        self.assertEqual(len(pre), 1)
        self.assertEqual(pre[0][3] - pre[0][2], 38000)          # until 'done' at preempt depth 1
        names = [d['strings'][x] for x in d['cols']['name']]
        at = {names[a[0]]: a[1] for a in d['atomic']}
        self.assertEqual(at, {'mutex_lock': 1 | 2})               # irqs off + preemption disabled

    def test_btf_aware_kernel_retval_formats(self):
        t = ("1000.000100 |   0)   sh-7     |  ...1. |   1.000 us    |  is_ok(); /* <-x+0x1/0x2 ret=false */\n"
             "1000.000102 |   0)   sh-7     |  ...1. |   1.000 us    |  get_time(); /* <-x+0x1/0x2 ret=1764235(trunc) */\n"
             "1000.000104 |   0)   sh-7     |  ...1. |   1.000 us    |  lookup(); /* <-x+0x1/0x2 ret=-2 */\n"
             "1000.000106 |   0)   sh-7     |  ...1. |   1.000 us    |  count(); /* <-x+0x1/0x2 ret=4294967295 */\n"
             "1000.000108 |   0)   sh-7     |  ...1. |   1.000 us    |  void_fn(); /* <-x+0x1/0x2 */\n")
        d = build(t)
        self.assertTrue(d['caps']['retval_btf'])
        errs = {self.name(d, r[0]): (r[1], r[4]) for r in d['errs']}
        self.assertEqual(errs, {'lookup': (-2, 1)})            # kernel-typed: verified without host BTF
        rets = {self.name(d, i): d['strings'][x] for i, x in enumerate(d['cols']['ret']) if x >= 0}
        self.assertEqual(rets['get_time'], '1764235(trunc)')
        self.assertEqual(rets['is_ok'], 'false')
        self.assertNotIn('void_fn', rets)

    def test_minor_errors(self):
        t = ("1000.000100 |   0)   sh-7     |  ...1. |               |  mod_state() { /* <-x+0x1/0x2 */\n"
             "1000.000101 |   0)   sh-7     |  ...1. |   1.000 us    |    pages_delta(); /* <-mod_state+0x1/0x2 ret=-4 */\n"
             "1000.000103 |   0)   sh-7     |  ...1. |   3.000 us    |  } /* mod_state ret=0 */\n") + SYNTH_FULL.split('\n', 1)[1]
        d = build(t)
        minor = {self.name(d, c[0]): c[6] for c in d['chains']}
        self.assertEqual(minor, {'pages_delta': 1, 'path_openat': 0})   # never propagated vs reaches user

    def test_btf_filters_void_comparator_and_null(self):
        db = ReturnTypeDB()
        db.funcs.update({'do_raw_spin_unlock': ('void', 0), 'search_cmp_ftr_reg': ('sint', 4),
                         'real_errno': ('sint', 4), 'get_ptr': ('ptr', 8)})
        d = build(SYNTH_VOID, db)
        errs = {self.name(d, r[0]) for r in d['errs']}
        self.assertEqual(errs, {'real_errno'})
        names = [d['strings'][x] for x in d['cols']['name']]
        self.assertTrue(d['cols']['flags'][names.index('get_ptr')] & timeline.F_NULL_RET)

    def test_without_btf_guesses_from_value(self):
        d = build(SYNTH_VOID)
        errs = {self.name(d, r[0]) for r in d['errs']}
        self.assertIn('do_raw_spin_unlock', errs)          # unverified false positive is expected
        self.assertTrue(all(r[4] == 0 for r in d['errs']))  # ...and marked unverified


class FixtureTest(unittest.TestCase):
    """Real traces from a Raspberry Pi 4 captured with different option sets."""
    EXPECT = {
        'pi4_full': ('abs', {'proc', 'latency', 'retval', 'retaddr', 'tail'}, set()),
        'pi4_default': ('compressed', {'cpu', 'duration'}, {'proc', 'latency', 'retval', 'abs'}),
        'pi4_sched': ('abs', {'sched_events', 'switch_markers', 'latency'}, set()),
        'pi4_noproc': ('abs', {'latency', 'retval'}, {'proc'}),
        'pi4_nodur': ('abs', {'proc', 'retval'}, {'duration', 'latency'}),
        'pi4_args': ('abs', {'args', 'retval'}, {'retaddr'}),
        'vm72_full': ('abs', {'args', 'retaddr', 'retval', 'retval_btf', 'latency'}, set()),
        'x86': ('compressed', {'latency', 'retval', 'retaddr', 'args'}, {'abs'}),
    }

    def test_fixtures(self):
        for name, (mode, has, lacks) in self.EXPECT.items():
            with self.subTest(fixture=name):
                with open(helpers.fixture(name), errors='replace') as f:
                    d = build(f.read())
                self.assertEqual(invariant_errors(d), [])
                self.assertEqual(d['mode'], mode)
                caps = {k for k, v in d['caps'].items() if v}
                self.assertTrue(has <= caps, f'missing caps {has - caps}')
                self.assertFalse(lacks & caps, f'unexpected caps {lacks & caps}')
                self.assertGreater(d['n'], 100)

    def test_sched_fixture_has_offcpu(self):
        with open(helpers.fixture('pi4_sched')) as f:
            d = build(f.read())
        closed = [r for r in d['offcpu'] if r[2] is not None]   # None: not back on CPU before the window ended
        self.assertGreater(len(closed), 0)
        self.assertTrue(all(r[2] >= r[1] for r in closed))
        self.assertTrue(any(r[5] >= 0 for r in d['offcpu']))   # prev_state taken from sched_switch

    @unittest.skipUnless(os.environ.get('FG_TRACES'), 'set FG_TRACES to extra trace files')
    def test_extra_traces(self):
        for p in os.environ['FG_TRACES'].split():
            with self.subTest(trace=p):
                with open(p, errors='replace') as f:
                    self.assertEqual(invariant_errors(build(f.read())), [])


if __name__ == '__main__':
    unittest.main()
