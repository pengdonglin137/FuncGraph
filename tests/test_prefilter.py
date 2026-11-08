"""prefilter.py: conversion-time filtering keeps whole, well-formed call trees."""
import unittest

import helpers
from fglib import prefilter, timeline
from test_timeline import invariant_errors

TRACE = """\
# tracer: function_graph
1000.000100 |   0)   a-10     |  ...1. |               |  outer() {
1000.000101 |   0)   a-10     |  ...1. |               |    target() {
1000.000102 |   0)   a-10     |  ...1. |   1.000 us    |      leaf1();
1000.000104 |   0)   a-10     |  ...1. |   3.000 us    |    } /* target */
1000.000105 |   0)   a-10     |  ...1. |   0.500 us    |    noisy();
1000.000106 |   0)   a-10     |  ...1. |   6.000 us    |  } /* outer */
1000.000200 |   1)   b-11     |  ...1. |               |  target() {
1000.000201 |   1)   b-11     |  ...1. |   1.000 us    |    noisy();
1000.000203 |   1)   b-11     |  ...1. |   3.000 us    |  } /* target */
 ------------------------------------------
 1)   b-11     =>   c-12
 ------------------------------------------

1000.000300 |   1)   c-12     |  ...1. |   1.000 us    |  other();
"""


def run(**kw):
    lines = TRACE.split('\n')[:-1]
    out, st = prefilter.apply(lines, prefilter.Filter(**kw))
    return out, st


def names(out):
    d = timeline.build_timeline(out).to_data()
    return sorted(d['strings'][x] for x in d['cols']['name']), d


class PrefilterTest(unittest.TestCase):
    def test_inactive_is_identity(self):
        out, st = run()
        self.assertEqual(out, TRACE.split('\n')[:-1])

    def test_pid(self):
        out, _ = run(pids=[11])
        n, d = names(out)
        self.assertEqual(n, ['noisy', 'target'])
        self.assertTrue(out[0].startswith('#'))
        self.assertTrue(any('=>' in l for l in out))      # switch b -> c kept (b is kept)

    def test_comm_regex(self):
        n, _ = names(run(comms=['^c$'])[0])
        self.assertEqual(n, ['other'])

    def test_cpu(self):
        n, _ = names(run(cpus=[0])[0])
        self.assertEqual(n, ['leaf1', 'noisy', 'outer', 'target'])

    def test_graph_keeps_subtree_only(self):
        out, _ = run(graph=['^target$'])
        n, d = names(out)
        self.assertEqual(n, ['leaf1', 'noisy', 'target', 'target'])
        self.assertEqual(invariant_errors(d), [])
        self.assertFalse(any('outer' in l for l in out))

    def test_notrace_drops_subtree(self):
        n, d = names(run(notrace=['^target$'])[0])
        self.assertEqual(n, ['noisy', 'other', 'outer'])
        self.assertEqual(invariant_errors(d), [])

    def test_time_range_relative_keeps_started_calls_whole(self):
        n, d = names(run(time_range='+90us:+150us')[0])
        self.assertEqual(n, ['noisy', 'target'])          # b's target starts at +100us, kept with its child
        self.assertEqual(invariant_errors(d), [])

    def test_time_range_absolute(self):
        n, _ = names(run(time_range='1000.000250:')[0])
        self.assertEqual(n, ['other'])

    def test_bad_range(self):
        for bad in ('abc', '1:2:x', 'x:'):
            with self.subTest(spec=bad):
                with self.assertRaises(ValueError):
                    prefilter.parse_range(bad)

    def test_real_fixture_invariants(self):
        with open(helpers.fixture('pi4_sched')) as f:
            lines = f.read().split('\n')
        for kw in ({'graph': ['do_el0_svc']}, {'pids': [131108]}, {'time_range': '+0.5ms:+2ms'},
                   {'notrace': ['^_raw_spin']}):
            with self.subTest(**{k: str(v) for k, v in kw.items()}):
                out, st = prefilter.apply(lines, prefilter.Filter(**kw))
                self.assertLess(st['out'], st['in'])
                d = timeline.build_timeline(out).to_data()
                self.assertEqual(invariant_errors(d), [])

    def test_cli_end_to_end(self):
        k = helpers.FakeKernel()
        try:
            html_text, p = k.run(helpers.fixture('pi4_full'), '--timeline', '--graph', '__arm64_sys_openat', '--enable-fold')
        finally:
            k.close()
        self.assertIn('Pre-filter (graph __arm64_sys_openat): kept', p.stdout)
        self.assertGreater(helpers.json_script(html_text, 'tlData')['n'], 0)


if __name__ == '__main__':
    unittest.main()
