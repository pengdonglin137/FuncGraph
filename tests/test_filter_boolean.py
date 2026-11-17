"""Independent truth tables and real Text controls agree on boolean expressions."""
from itertools import product
import json
from pathlib import Path
import shutil
import subprocess
import threading
import unittest
from urllib.parse import urljoin

import helpers
from fglib.viewer import create_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


ATOMS = ['cpu:="1"', 'pid:="7"', 'comm:="worker"', 'duration:>10', 'preempt:>=10']


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class BooleanFilterTest(unittest.TestCase):
    def check_cases(self, cases):
        source = (Path(helpers.ROOT) / 'assets/report/report.js').read_text()
        source = source[source.index('        function tokenizeFilter'):
                        source.index('        // ====== Filter Tags Management')]
        script = '''const assert = require('node:assert/strict'), _graphFoldIds = {};
            const cases = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
            for (const scenario of cases) {
                const parsed = parseFilterExpr(scenario.expression);
                const rebuilt = parseFilterExpr(astToText(parsed.ast));
                assert.equal(parsed.sortDir, scenario.sort || '', scenario.expression);
                const graphs = Object.fromEntries(Object.entries(scenario.graphs || {}).map(
                    ([key, ids]) => [key, new Set(ids)]));
                for (let mask = 0; mask < (scenario.records?.length ?? 32); mask++) {
                    const attributes = {'data-cpu': mask & 1 ? '1' : '10',
                        'data-pid': mask & 2 ? '7' : '70', 'data-comm': mask & 4 ? 'worker' : 'other',
                        'data-duration': mask & 8 ? '11' : '10', 'data-preempt': mask & 16 ? 'a' : '1'};
                    Object.assign(attributes, scenario.records?.[mask]);
                    const record = {getAttribute: name => attributes[name] ?? null};
                    const expected = scenario.matches.includes(mask);
                    assert.equal(evalExpr(record, parsed.ast, graphs), expected, scenario.expression + ' mask=' + mask);
                    assert.equal(evalExpr(record, rebuilt.ast, graphs), expected, 'round trip: ' + scenario.expression);
                }
            }
        '''
        result = subprocess.run(['node', '-e', source + script], input=json.dumps(cases),
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_all_mixed_conjunction_spellings_retain_every_condition(self):
        cases = []
        for separators in product((' ', ' AND ', ' && '), repeat=4):
            expression = ATOMS[0] + ''.join(separator + atom for separator, atom in zip(separators, ATOMS[1:]))
            cases.append(dict(expression=expression, matches=[31]))
        self.check_cases(cases)

    def test_negation_groups_and_disjunction_keep_boolean_precedence(self):
        cases = []
        for conjunction, disjunction, negation in product((' ', ' AND ', ' && ', ' aNd '),
                                                         (' OR ', ' || ', ' oR '), ('NOT ', '!')):
            expression = (ATOMS[0] + conjunction + ATOMS[1] + ' AND ' + negation + ATOMS[2] +
                          disjunction + ATOMS[3] + conjunction + ATOMS[4])
            matches = [mask for mask in range(32) if
                       (bool(mask & 1) and bool(mask & 2) and not mask & 4) or
                       (bool(mask & 8) and bool(mask & 16))]
            cases.append(dict(expression=expression, matches=matches))
            expression = ('(' + ATOMS[0] + disjunction + ATOMS[1] + ')' + conjunction +
                          negation + '(' + ATOMS[2] + disjunction + ATOMS[3] + ') AND ' + ATOMS[4])
            matches = [mask for mask in range(32) if
                       (bool(mask & 1) or bool(mask & 2)) and not (mask & 4 or mask & 8) and mask & 16]
            cases.append(dict(expression=expression, matches=matches))
        self.check_cases(cases)

    def test_sort_directives_do_not_break_interleaved_conjunctions(self):
        cases = []
        for direction, position in product(('asc', 'desc'), range(6)):
            parts = [ATOMS[0], ATOMS[1], 'AND ' + ATOMS[2], ATOMS[3], '&& ' + ATOMS[4]]
            parts.insert(position, 'sort:' + direction)
            cases.append(dict(expression=' '.join(parts), sort=direction, matches=[31]))
        self.check_cases(cases)

    def test_graph_exclusion_retains_other_descendants_and_the_root_exit(self):
        records = [dict(zip(('data-fold-id', 'data-fold-ancestors'), values), **{'data-cpu': '1'})
                   for values in [('root', ''), ('security', 'root'), ('leaf', 'root security'),
                                  ('other', 'root'), ('outside', ''), ('root', '')]]
        graphs = {'graph:vfs_read': ['root'], 'notrace:security_': ['security']}
        cases = [('graph:vfs_read AND notrace:security_', [0, 3, 5]),
                 ('graph:vfs_read notrace:security_ AND cpu:="1"', [0, 3, 5]),
                 ('graph:vfs_read AND NOT notrace:security_', [1, 2]),
                 ('graph:missing', []), ('notrace:missing', list(range(6)))]
        self.check_cases([dict(expression=expression, matches=matches, records=records, graphs=graphs)
                          for expression, matches in cases])
        for guide in ('guide.cn.md', 'guide.en.md'):
            content = (Path(helpers.ROOT) / 'docs' / guide).read_text()
            self.assertIn('graph:vfs_read AND notrace:security_', content)
            self.assertNotIn('graph:vfs_read AND NOT notrace:security_', content)


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class BooleanFilterBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel(inline_source=True)
        cls.addClassCleanup(cls.kernel.close)
        trace = Path(cls.kernel.dir, 'boolean.txt')
        lines = ['# tracer: function_graph\n']
        for mask in range(8):
            cpu = 1 if mask & 1 else 10
            pid = 7 if mask & 2 else 70
            comm = 'worker' if mask & 4 else 'other'
            lines.append(f'  {mask + 1:.6f} | {cpu:3}) {comm}-{pid} |  ...1. |   {mask + 1}.000 us    |  '
                         f'probe_{mask}(value={mask}); /* <-outer+0x4/0x80 */\n')
        trace.write_text(''.join(lines))
        options = ['--no-config', '--filter', '--enable-fold', '--func-links']
        cls.kernel.run(str(trace), *options, out='plain.html')
        cls.kernel.run(str(trace), *options, '--timeline', out='index.html')
        cls.kernel.run(str(trace), *options, '--timeline', '--report-mode', 'sidecar', out='sidecar')
        cls.server = create_server(Path(cls.kernel.dir))
        cls.addClassCleanup(cls.server.server_close)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      kwargs={'poll_interval': 0.02}, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.thread.join, 5)
        cls.addClassCleanup(cls.server.shutdown)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)

    def test_text_controls_and_timeline_share_complete_boolean_results(self):
        expressions = [
            (ATOMS[0] + ' ' + ATOMS[1] + ' AND ' + ATOMS[2], [7]),
            (ATOMS[0] + ' AND ' + ATOMS[1] + ' ' + ATOMS[2] + ' AND NOT ' + ATOMS[2], []),
            ('NOT ' + ATOMS[0] + ' ' + ATOMS[1] + ' AND ' + ATOMS[2] +
             ' OR ' + ATOMS[0] + ' NOT ' + ATOMS[1], [1, 5, 6]),
            ('(' + ATOMS[0] + ' OR ' + ATOMS[1] + ') NOT ' + ATOMS[2] + ' AND ' + ATOMS[1], [2, 3]),
            (ATOMS[0] + ' ' + ATOMS[1] + ' && ' + ATOMS[2] + ' sort:desc', [7]),
        ]
        for filename, touch, theme in product(('plain.html', 'index.html', 'sidecar/index.html',
                                                'sidecar/index.html?text=windowed'),
                                               (False, True), ('light', 'dark')):
            with self.subTest(filename=filename, touch=touch, theme=theme):
                with self.browser.new_context(viewport=dict(width=390 if touch else 1400, height=844),
                                               has_touch=touch, is_mobile=touch) as context:
                    context.add_init_script('localStorage.clear()')
                    page = context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(urljoin(self.server.url, filename))
                    page.evaluate('''async theme => {
                        document.documentElement.dataset.theme = theme;
                        if (typeof fgSwitchView === 'function') await fgSwitchView('text');
                    }''', theme)
                    if not page.locator('#filterBox').is_visible():
                        page.locator('#textFilterToggle').press('Enter')
                    original = page.evaluate('fgData.ready("tlData") ? JSON.stringify(fgData.read("tlData")) : null')
                    for expression, matches in expressions:
                        page.locator('#filterInput').fill(expression)
                        page.locator('#filterInput').press('Enter')
                        page.wait_for_function('text => !fgFilterProgress && fgFilterState.text === text', arg=expression)
                        names = page.locator('#content .line-container:visible[data-func]').evaluate_all(
                            'rows => rows.map(row => row.getAttribute("data-func")).sort()')
                        expected = ['probe_' + str(mask) for mask in matches]
                        self.assertEqual(names, expected, expression)
                        if filename != 'plain.html':
                            page.evaluate('async () => { await fgSwitchView("timeline"); }')
                            names = page.evaluate('''() => {
                                const data = fgData.read('tlData'), lines = fgFilterState.lines;
                                return data.cols.line.flatMap((number, index) => lines.has(number) ||
                                    lines.has(data.cols.eline?.[index]) ? [data.strings[data.cols.name[index]]] : []).sort();
                            }''')
                            self.assertEqual(names, expected, expression)
                            if original is not None:
                                self.assertEqual(page.evaluate('JSON.stringify(fgData.read("tlData"))'), original)
                            page.evaluate('async () => { await fgSwitchView("text"); }')
                            if not page.locator('#filterBox').is_visible():
                                page.locator('#textFilterToggle').press('Enter')
                    self.assertEqual(errors, [])


if __name__ == '__main__':
    unittest.main()
