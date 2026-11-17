"""FtraceParser: field extraction and fold pairing."""
import os
import tempfile
import unittest

import helpers  # noqa: F401  (sets sys.path)
from fglib.parser import FtraceParser
from latency_reference import DEPTH_BYTES, linux_latency

SAMPLE = """\
# tracer: function_graph
 6)    bash-513    |  ...1. |               |  mutex_unlock(lock=0xffffffff837a1fc0) { /* <-rb_simple_write+0xc9/0x120 */
 6)    bash-513    |  ...1. |   0.296 us    |    __mutex_unlock_slowpath(lock=0xffffffff837a1fc0, ip=0xffffffff81314790); /* <-rb_simple_write+0xc9/0x120 */
 6)    bash-513    |  ...1. |   1.561 us    |  } /* mutex_unlock */
2003237.699097 |   0)  true-130265   |  d.h1. | + 12.500 us   |                link_path_walk(); /* <-path_lookupat+0x84/0x180 ret=-2 */
 3)               |  foo() {
 3)   2.167 us    |    bar();
 3) ! 123.456 us  |  }
 2) mod_fn [mymod]() { /* <-caller+0x1/0x2 [mymod] */
"""


def parse(text, fold=True):
    with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:
        f.write(text)
    try:
        return FtraceParser().parse(f.name, enable_fold=fold)
    finally:
        os.unlink(f.name)


class ParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lines = parse(SAMPLE)

    def test_one_entry_per_input_line(self):
        self.assertEqual(len(self.lines), SAMPLE.count('\n'))

    def test_all_linux_latency_characters_and_combinations_reach_text_filters(self):
        for timestamp in ('', '1000.000100 |  '):
            for columns in (4, 5):
                with self.subTest(timestamp=bool(timestamp), columns=columns):
                    expected = [linux_latency(flags, preempt)[:columns]
                                for flags in range(256) for preempt in DEPTH_BYTES]
                    text = '\n'.join(timestamp + ' 0) task-42 |  ' + latency +
                                     ' |   1.000 us    |  work();' for latency in expected)
                    rows = parse(text)
                    self.assertEqual(len(rows), len(expected))
                    self.assertEqual([''.join((row.irqs_off, row.need_resched,
                                              row.hardirq_softirq, row.preempt_depth)) for row in rows],
                                     [latency[:4] for latency in expected])
                    self.assertTrue(all(row.raw_func_name == 'work' for row in rows))

    def test_legacy_unknown_irq_state_is_not_reported_as_enabled(self):
        row = parse(' 0) task-42 |  Xp.f. |   1.000 us    |  work();\n')[0]
        self.assertEqual((row.irqs_off, row.need_resched, row.preempt_depth), ('X', 'p', 'f'))

    def test_latency_filter_fields_do_not_depend_on_optional_cpu_task_or_time_columns(self):
        for timestamp in ('', '1000.000100 |  ', '      100 us |  '):
            for identity in ('', ' 0) ', 'task-42 | ', ' 0) task-42 | '):
                with self.subTest(timestamp=timestamp, identity=identity):
                    rows = parse('\n'.join(timestamp + identity + ' D' + code +
                                           'Haf |   1.000 us    |  work();' for code in '.nNplLbB'))
                    self.assertEqual([(row.irqs_off, row.need_resched, row.hardirq_softirq, row.preempt_depth)
                                      for row in rows], [('D', code, 'H', 'a') for code in '.nNplLbB'])

    def test_header_line_is_plain(self):
        self.assertFalse(self.lines[0].expandable)
        self.assertIsNone(self.lines[0].cpu)

    def test_latency_mode_entry(self):
        l = self.lines[1]
        self.assertEqual((l.cpu, l.pid, l.comm), (6, 513, 'bash'))
        self.assertEqual(l.raw_func_name, 'mutex_unlock')
        self.assertEqual(l.params, 'lock=0xffffffff837a1fc0')
        self.assertEqual(l.func_info, 'rb_simple_write+0xc9/0x120')
        self.assertEqual((l.irqs_off, l.need_resched, l.hardirq_softirq, l.preempt_depth), ('.', '.', '.', '1'))
        self.assertTrue(l.expandable)

    def test_leaf_duration(self):
        l = self.lines[2]
        self.assertAlmostEqual(l.duration, 0.296)
        self.assertEqual(l.duration_raw, '0.296 us')

    def test_plain_calls_keep_raw_and_display_names_without_fabricated_call_sites(self):
        lines = parse(' 0)               |  worker.isra.0() {\n'
                      ' 0)   1.000 us    |    module_worker.constprop.1 [test-module](value=7);\n'
                      ' 0)   2.000 us    |  }\n'
                      ' 0)               |  /* worker.isra.0() */\n')
        self.assertEqual([line.raw_func_name for line in lines],
                         ['worker.isra.0', 'module_worker.constprop.1', None, None])
        self.assertEqual([line.display_func_name for line in lines[:2]], ['worker', 'module_worker'])
        self.assertEqual(lines[1].module_name, 'test-module')
        self.assertTrue(all(line.func_info is None and not line.expandable for line in lines))

    def test_abstime_with_flags_and_retval(self):
        l = self.lines[4]
        self.assertEqual((l.pid, l.comm, l.ret_value), (130265, 'true', '-2'))
        self.assertEqual((l.irqs_off, l.hardirq_softirq), ('d', 'h'))
        self.assertAlmostEqual(l.duration, 12.5)
        self.assertGreater(l.sort_duration, l.duration)   # '+' marker raises the heat level

    def test_overhead_marker(self):
        l = self.lines[7]
        self.assertAlmostEqual(l.duration, 123.456)
        self.assertEqual(l.duration_raw, '! 123.456 us')

    def test_module_name(self):
        l = self.lines[8]
        self.assertEqual((l.raw_func_name, l.module_name), ('mod_fn', 'mymod'))
        self.assertEqual(l.func_info, 'caller+0x1/0x2 [mymod]')

    def test_fold_pairing(self):
        entry, inner, exit_ = self.lines[1], self.lines[2], self.lines[3]
        self.assertEqual(entry.fold_type, 'entry')
        self.assertEqual(inner.fold_type, 'inner')
        self.assertEqual(exit_.fold_type, 'exit')
        self.assertEqual(entry.fold_id, inner.fold_id)
        self.assertEqual(entry.fold_id, exit_.fold_id)
        self.assertEqual(inner.fold_ancestors, entry.fold_id)

    def test_fold_ids_are_short_and_unique(self):
        ids = [l.fold_id for l in self.lines if l.fold_type == 'entry']
        self.assertEqual(len(ids), len(set(ids)))
        for i in ids:
            self.assertRegex(i, r'^f[0-9a-z]+$')

    def test_no_fold_without_flag(self):
        self.assertTrue(all(l.fold_id is None for l in parse(SAMPLE, fold=False)))

    def test_dict_compat_accessors(self):
        l = self.lines[1]
        self.assertEqual(l['pid'], 513)
        self.assertEqual(l.get('missing', 'x'), 'x')
        self.assertIn('cpu', l)

    def test_bundled_x86_sample(self):
        lines = FtraceParser().parse(helpers.fixture('x86'), enable_fold=True)
        self.assertEqual(sum(1 for l in lines if l.expandable), 336)
        entries = {l.fold_id for l in lines if l.fold_type == 'entry'}
        for l in lines:
            for a in (l.fold_ancestors or '').split():
                self.assertIn(a, entries)


if __name__ == '__main__':
    unittest.main()
