"""Readable slice labels retain original symbol identity and detailed evidence."""

from pathlib import Path
import json
import shutil
import subprocess
import threading
import unittest

import helpers
from fglib.viewer import create_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class SliceLabelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(helpers.ROOT, 'assets/timeline/render.js').read_text()
        cls.source = source[source.index('const COMPILER_SUFFIX'):source.index('const nameHash')]

    def check(self, pairs):
        script = 'const assert = require("node:assert/strict");\n' + self.source + '''
            const pairs = ''' + json.dumps(pairs) + ''';
            const names = pairs.map(pair => pair[0]);
            function nameOf(index) { return names[index]; }
            pairs.forEach(([raw, expected], index) => {
                assert.equal(sliceLabel(index), expected, raw);
                assert.equal(nameOf(index), raw);
                assert.equal(sliceLabel(index), expected);
            });
            assert.equal(sliceNameCache.size, new Set(names).size);
        '''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_gcc_numeric_and_chained_suffixes(self):
        self.check([(f'inode_has_perm.{suffix}.12', 'inode_has_perm')
                    for suffix in ['isra', 'constprop', 'part', 'clone', 'lto', 'lto_priv']] + [
            ('inode_has_perm.isra.0.constprop.1.cold', 'inode_has_perm'),
            ('inode_has_perm.cold.42', 'inode_has_perm'),
            ('inode_has_perm.hot', 'inode_has_perm'),
            ('inode_has_perm.hot.3', 'inode_has_perm'),
        ])

    def test_llvm_hashes_and_demangled_clone_annotations(self):
        self.check([
            ('work.llvm.123456', 'work'),
            ('work.llvm.a0B1c2D3', 'work'),
            ('work.llvm.deadbeef.cold', 'work'),
            ('work<int>(int) [clone .isra.0]', 'work<int>(int)'),
            ('work<int>(int).constprop.2 [clone .cold]', 'work<int>(int)'),
            ('work<int>(int) [clone .constprop.0.isra.1]', 'work<int>(int)'),
        ])

    def test_unrecognized_and_nontrailing_suffixes_keep_their_meaning(self):
        names = ['inode_has_perm', 'foo.bar', 'foo.123', 'foo.isra.0.extra', 'foo.isra.0x',
                 'foo.constprop', 'foo.part.-1', 'foo.llvm.nothex', 'foo.coldish', 'foo.hotter',
                 'foo.plt', 'foo@plt', 'foo.ifunc', 'foo.const', 'foo.pure', 'foo._omp_fn.0',
                 'work<int>(int) [clone .unknown.0]', 'work<int>(int) [clone .isra.0.extra]',
                 '.isra.0', '?', 'std::operator++', '<script>alert(1)</script>']
        self.check([(name, name) for name in names])

    def test_same_display_name_does_not_merge_raw_symbols(self):
        self.check([('work', 'work'), ('work.isra.0', 'work'), ('work.isra.1', 'work'),
                    ('work.constprop.0', 'work'), ('work.isra.0', 'work')])


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class SliceLabelBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel()
        cls.addClassCleanup(cls.kernel.close)
        trace = Path(cls.kernel.dir, 'names.txt')
        trace.write_text('''# tracer: function_graph
1.000000 |   0)   task-42   |  ...1. |              |  inode_has_perm.isra.0() {
1.000002 |   0)   task-42   |  ...1. |   8.000 us   |    work.constprop.1();
1.000012 |   0)   task-42   |  ...1. |   6.000 us   |    work.constprop.2();
1.000020 |   0)   task-42   |  ...1. |  20.000 us   |  } /* inode_has_perm.isra.0 */
''')
        cls.kernel.run(str(trace), '--timeline', '--filter', out='single.html')
        cls.kernel.run(str(trace), '--timeline', '--filter', '--report-mode', 'sidecar', out='sidecar')
        cls.server = create_server(Path(cls.kernel.dir, 'sidecar'))
        cls.addClassCleanup(cls.server.server_close)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
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

    def open_report(self, mode='single', theme='light'):
        context = self.browser.new_context(viewport={'width': 1440, 'height': 900})
        self.addCleanup(context.close)
        context.add_init_script('''
            window.drawnLabels = [];
            const drawText = CanvasRenderingContext2D.prototype.fillText;
            CanvasRenderingContext2D.prototype.fillText = function(text, ...parameters) {
                drawnLabels.push(String(text));
                return drawText.call(this, text, ...parameters);
            };
        ''')
        page = context.new_page()
        page.set_default_timeout(10000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(lambda: self.assertEqual(errors, []))
        page.goto(Path(self.kernel.dir, 'single.html').as_uri() if mode == 'single' else self.server.url)
        page.evaluate('''async theme => {
            document.documentElement.setAttribute('data-theme', theme);
            await fgSwitchView('timeline');
        }''', theme)
        page.locator('.tl-canvas').wait_for()
        return page

    def test_slice_labels_are_friendly_in_both_themes_and_report_modes(self):
        for mode in ['single', 'sidecar']:
            for theme in ['light', 'dark']:
                with self.subTest(mode=mode, theme=theme):
                    page = self.open_report(mode, theme)
                    page.wait_for_function('drawnLabels.includes("inode_has_perm") && drawnLabels.includes("work")')
                    labels = page.evaluate('drawnLabels')
                    self.assertNotIn('inode_has_perm.isra.0', labels)
                    self.assertNotIn('work.constprop.1', labels)
                    self.assertNotIn('work.constprop.2', labels)

    def test_tooltip_detail_search_and_raw_data_keep_distinct_original_symbols(self):
        for mode in ['single', 'sidecar']:
            with self.subTest(mode=mode):
                page = self.open_report(mode)
                original = page.evaluate('JSON.stringify(fgData.read("tlData"))')
                names = page.evaluate('''() => {
                    const model = fgView('timeline').probe();
                    return Array.from({length:model.N}, (unused, index) => model.nameOf(index));
                }''')
                self.assertEqual(names, ['inode_has_perm.isra.0', 'work.constprop.1', 'work.constprop.2'])
                page.mouse.move(*page.evaluate('fgView("timeline").probe().xyOf(0)'))
                self.assertEqual(page.locator('.tl-tip .t-name').inner_text(), names[0])
                page.evaluate('fgView("timeline").probe().select(1)')
                self.assertEqual(page.locator('.tl-detail-title').inner_text(), names[1])
                self.assertEqual(page.locator('.tl-detail-title').get_attribute('title'), names[1])
                self.assertIn(names[0], page.locator('.tl-context-row').all_inner_texts()[0])
                for index in [2, 1]:
                    page.locator('.tl-search').fill('^' + names[index].replace('.', r'\.') + '$')
                    page.locator('.tl-search').press('Enter')
                    page.wait_for_function('() => document.querySelector(".tl-count").getAttribute("aria-busy") === "false"')
                    self.assertEqual(page.evaluate('fgView("timeline").probe().sel()'), index)
                    self.assertEqual(page.locator('.tl-detail-title').inner_text(), names[index])
                self.assertEqual(page.evaluate('JSON.stringify(fgData.read("tlData"))'), original)
