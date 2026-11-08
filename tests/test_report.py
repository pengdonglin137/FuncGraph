"""End-to-end: run funcgraph.py against a fake kernel tree and check the generated HTML."""
import os
import re
import subprocess
import tempfile
import unittest

import helpers
from fglib import assets

# Markers that must be present in a full-featured report (migrated from test_refactor.sh)
FEATURES = {
    'Theme toggle': 'toggleTheme', 'Fold/unfold': 'toggleFold', 'Keyboard navigation': 'keydown',
    'Filter system': 'applyFilter', 'Filter parser': 'function tokenizeFilter',
    'Graph filter': 'function precomputeGraphSets', 'Expand all': 'expandAll', 'Collapse all': 'collapseAll',
    'Copy visible': 'copyVisibleContent', 'Progress bar': 'updateProgressBar', 'Floating toggle': 'floatingToggle',
    'Anchor links': 'updateAnchor', 'Double click': 'handleDoubleClick', 'Syntax highlight': 'hl-func',
'Fold entry class': 'fold-entry', 'Dark theme': 'data-theme="dark"',
    'Source links': 'location-link', 'Func name links': 'func-name-link', 'Keyboard hint': 'keyboardHint',
    'Duration heatmap': 'data-heat', 'Call depth': 'data-call-depth', 'Fold summary': 'fold-summary',
    'Lazy source blocks': 'id="fgSrc"',
}
FULL = ('--enable-fold', '--func-links', '--filter', '--highlight-code', '--base-url', 'https://example.org/src')


def count_input_lines(path):
    with open(path, errors='replace') as f:
        return sum(1 for _ in f)


class ReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.k = helpers.FakeKernel()
        cls.trace = helpers.fixture('x86')
        cls.html, cls.proc = cls.k.run(cls.trace, *FULL)
        cls.html_tl, _ = cls.k.run(cls.trace, *FULL, '--timeline', out='tl.html')

    @classmethod
    def tearDownClass(cls):
        cls.k.close()

    def test_features_present(self):
        for name, marker in FEATURES.items():
            with self.subTest(feature=name):
                self.assertTrue(marker in self.html, f'marker {marker!r} missing')

    def test_one_container_per_input_line(self):
        n = count_input_lines(self.trace)
        ids = re.findall(r'<div class="line-container[^"]*" id="L(\d+)"', self.html)
        self.assertEqual([int(x) for x in ids], list(range(1, n + 1)))

    def test_source_blocks_are_lazy_and_valid(self):
        pool = helpers.json_script(self.html, 'fgSrc')
        self.assertIsInstance(pool, list)
        refs = re.findall(r'<div class="expanded-content" id="line_\d+_content" data-src="(\d+)" style="display: none;">(.*?)</div>', self.html)
        self.assertEqual(len(refs), 336)
        self.assertTrue(all(body == '' for _, body in refs))
        self.assertTrue(all(int(i) < len(pool) for i, _ in refs))
        self.assertTrue(any('kernel/fake.c:2' in b for b in pool))
        self.assertIn('https://example.org/src/kernel/fake.c#L2', ''.join(pool))

    def test_fold_references_resolve(self):
        entries = set(re.findall(r'data-fold-id="([^"]+)" data-fold-type="entry"', self.html))
        self.assertGreater(len(entries), 50)
        for anc in re.findall(r'data-fold-ancestors="([^"]*)"', self.html):
            for a in anc.split():
                self.assertIn(a, entries)

    def test_no_timeline_without_flag(self):
        for marker in ('id="tlData"', 'class="view-tabs"', '__tlBoot', 'Timeline:</div>', 'No Btf:'):
            self.assertFalse(marker in self.html, f'unexpected {marker!r}')

    def test_timeline_embedded(self):
        d = helpers.json_script(self.html_tl, 'tlData')
        self.assertEqual(d['n'], 336)
        self.assertEqual(d['mode'], 'compressed')
        for marker in ('class="view-tabs"', '<div id="timelineView"></div>', 'function __tlBoot() {',
                       'Timeline:</div><div class="info-value">Yes'):
            self.assertTrue(marker in self.html_tl, f'marker {marker!r} missing')

    def test_text_part_identical_with_and_without_timeline(self):
        def body(h):
            start = h.index('<div id="content">')
            return h[start:h.index('\n        </div>', start)]
        self.assertTrue(body(self.html) == body(self.html_tl), 'text view differs with --timeline')

    def test_stdout_summary(self):
        want = 'Processed %d lines, with 336 expandable entries' % count_input_lines(self.trace)
        self.assertTrue(want in self.proc.stdout, self.proc.stdout[-500:])


class EscapingTest(unittest.TestCase):
    """Untrusted trace text must not break out of HTML or <script> blocks."""

    def test_script_and_markup_injection(self):
        evil = ('# tracer: function_graph\n'
                ' 0)   sh-1    |               |  evil_fn() { /* <-caller+0x1/0x2 */\n'
                ' 0)   sh-1    |               |    /* </script><img src=x onerror=alert(1)> */\n'
                ' 0)   sh-1    |   1.000 us    |    leaf</script>(); /* <-evil_fn+0x1/0x2 ret=-1 */\n'
                ' 0)   sh-1    |   2.000 us    |  } /* evil_fn ret=-1 */\n')
        k = helpers.FakeKernel()
        try:
            p = os.path.join(k.dir, 'evil.txt')
            with open(p, 'w') as f:
                f.write(evil)
            html_text, _ = k.run(p, '--enable-fold', '--filter', '--timeline')
        finally:
            k.close()
        self.assertFalse('<img src=x' in html_text, 'unescaped markup from the trace')
        self.assertTrue('EPERM' in html_text, 'errno name for ret=-1 not rendered')
        for m in re.finditer(r'<script type="application/json" id="(\w+)">(.*?)</script>', html_text, re.S):
            self.assertNotIn('<', m.group(2), m.group(1))


@unittest.skipUnless(helpers.have('node'), 'node not installed')
class JsSyntaxTest(unittest.TestCase):
    def node_check(self, code, label):
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as f:
            f.write(code)
        try:
            p = subprocess.run(['node', '--check', f.name], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, f'{label}: {p.stderr[:800]}')
        finally:
            os.unlink(f.name)

    def test_assets(self):
        self.node_check(assets.read_asset(assets.REPORT_JS), 'report.js')
        self.node_check(assets.timeline_script(), 'timeline')

    def test_generated_inline_scripts(self):
        k = helpers.FakeKernel()
        try:
            html_text, _ = k.run(helpers.fixture('pi4_sched'), *FULL, '--timeline')
        finally:
            k.close()
        scripts = re.findall(r'<script>(.*?)</script>', html_text, re.S)
        self.assertGreaterEqual(len(scripts), 2)
        for i, s in enumerate(scripts):
            self.node_check(s, f'inline script {i}')


class FixtureReportTest(unittest.TestCase):
    """Every captured option combination must produce a report with a timeline."""

    def test_all_fixtures(self):
        k = helpers.FakeKernel()
        try:
            for name in helpers.FIXTURES:
                with self.subTest(fixture=name):
                    html_text, _ = k.run(helpers.fixture(name), '--enable-fold', '--filter', '--timeline', out=name + '.html')
                    d = helpers.json_script(html_text, 'tlData')
                    self.assertIsNotNone(d)
                    self.assertGreater(d['n'], 0)
        finally:
            k.close()


if __name__ == '__main__':
    unittest.main()
