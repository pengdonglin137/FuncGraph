"""compare.py: per-function statistics and differences."""
import unittest

import helpers
from fglib import compare, timeline
from test_timeline import SYNTH_FULL

BASE = """\
1000.000100 |   0)   a-1     |  ...1. |               |  outer() {
1000.000101 |   0)   a-1     |  ...1. |   2.000 us    |    work();
1000.000104 |   0)   a-1     |  ...1. |   4.000 us    |  } /* outer */
"""
CUR = """\
1000.000100 |   0)   a-1     |  ...1. |               |  outer() {
1000.000101 |   0)   a-1     |  ...1. |   2.000 us    |    work();
1000.000104 |   0)   a-1     |  ...1. |   3.000 us    |    work();
1000.000108 |   0)   a-1     |  ...1. |   1.000 us    |    added();
1000.000110 |   0)   a-1     |  ...1. |  10.000 us    |  } /* outer */
"""


def rows(base, cur):
    d = compare.compare(timeline.build_timeline(cur.split('\n')).to_data(), base.split('\n'))
    return {r[0]: r for r in d['rows']}, d


class CompareTest(unittest.TestCase):
    def test_stats_and_diff(self):
        r, d = rows(BASE, CUR)
        # name | base calls incl self max err | cur calls incl self max err
        self.assertEqual(r['work'][1:6], [1, 2000, 2000, 2000, 0])
        self.assertEqual(r['work'][6:11], [2, 5000, 5000, 3000, 0])
        self.assertEqual(r['outer'][3], 2000)         # self = 4us - 2us
        self.assertEqual(r['outer'][8], 4000)         # self = 10 - 2 - 3 - 1
        self.assertEqual(r['added'][1], 0)            # new function
        self.assertEqual(d['calls'], [2, 4])
        self.assertEqual(d['window'], [4000, 10000])

    def test_error_counts(self):
        r, d = rows(BASE, SYNTH_FULL)
        self.assertEqual(r['path_openat'][10], 1)
        self.assertEqual(d['errors'], [0, 1])

    def test_identical_traces(self):
        r, _ = rows(CUR, CUR)
        for row in r.values():
            self.assertEqual(row[1:6], row[6:11])

    def test_adjusted_rows_need_both_estimates(self):
        cur = timeline.build_timeline(CUR.split('\n'), overhead=(500, 0)).to_data()
        d = compare.compare(cur, BASE.split('\n'), overhead=(500, 0))
        r = {x[0]: x for x in d['rows_adj']}
        self.assertEqual(r['outer'][2], 4000 - 500)       # base: one descendant
        self.assertEqual(r['outer'][7], 10000 - 3 * 500)
        self.assertIsNone(compare.compare(timeline.build_timeline(CUR.split('\n')).to_data(), BASE.split('\n'))['rows_adj'])

    def test_cli(self):
        k = helpers.FakeKernel()
        try:
            html_text, p = k.run(helpers.fixture('pi4_full'), '--compare', helpers.fixture('pi4_args'))
        finally:
            k.close()
        d = helpers.json_script(html_text, 'cmpData')
        self.assertGreater(len(d['rows']), 10)
        self.assertIn('data-view="compare"', html_text)
        self.assertNotIn('id="tlData"', html_text)      # compare works without --timeline
        self.assertIn('Compare:', p.stdout)


if __name__ == '__main__':
    unittest.main()
