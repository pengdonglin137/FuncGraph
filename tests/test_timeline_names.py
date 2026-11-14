"""Readable slice labels and detail titles retain exact symbol identities."""

from pathlib import Path
import csv
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
                assert.equal(symbolLabel(raw), expected, raw);
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

    def open_report(self, mode='single', theme='light', configuration=None):
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
        page.evaluate('''async ({theme, configuration}) => {
            document.documentElement.setAttribute('data-theme', theme);
            if (configuration) {
                const data = await fgData.load('tlData');
                Object.assign(data.cols, configuration.columns || {});
                Object.assign(data, configuration.extra || {});
            }
            await fgSwitchView('timeline');
        }''', dict(theme=theme, configuration=configuration))
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

    def test_readable_titles_keep_tooltip_search_and_original_symbols_distinct(self):
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
                self.assertEqual(page.locator('.tl-detail-title').inner_text(), 'work')
                self.assertEqual(page.locator('.tl-detail-title').get_attribute('title'), names[1])
                self.assertIn(names[0], page.locator('.tl-context-row').all_inner_texts()[0])
                for index in [2, 1]:
                    page.locator('.tl-search').fill('^' + names[index].replace('.', r'\.') + '$')
                    page.locator('.tl-search').press('Enter')
                    page.wait_for_function('() => document.querySelector(".tl-count").getAttribute("aria-busy") === "false"')
                    self.assertEqual(page.evaluate('fgView("timeline").probe().sel()'), index)
                    self.assertEqual(page.locator('.tl-detail-title').inner_text(), 'work')
                    self.assertEqual(page.locator('.tl-detail-title').get_attribute('title'), names[index])
                self.assertEqual(page.evaluate('JSON.stringify(fgData.read("tlData"))'), original)

    def test_distribution_titles_do_not_merge_compiler_variants(self):
        for mode in ('single', 'sidecar'):
            for theme in ('light', 'dark'):
                with self.subTest(mode=mode, theme=theme):
                    page = self.open_report(mode, theme)
                    original = page.evaluate('JSON.stringify(fgData.read("tlData"))')
                    for index in (1, 2):
                        page.evaluate('index => { const model = fgView("timeline").probe(); model.dist(model.sName[index]); }', index)
                        self.assertEqual(page.locator('.tl-detail-title').inner_text(), 'work')
                        self.assertEqual(page.locator('.tl-detail-title').get_attribute('title'), f'work.constprop.{index}')
                        self.assertEqual(page.locator('.an-dist-heading .tl-pill').first.inner_text(), '1 calls')
                    self.assertEqual(page.evaluate('JSON.stringify(fgData.read("tlData"))'), original)

    def test_inline_and_untraced_titles_use_the_same_readable_names(self):
        names = ['inode_has_perm.isra.0', 'work.constprop.1', 'work.constprop.2',
                 'missing.llvm.deadbeef+0x1/0x20']
        configuration = dict(columns=dict(name=[0, 1, 2], caller=[-1, 3, 3], depth=[0, 3, 3]), extra=dict(
            strings=names, inline={'3': [['inode_has_perm.isra.0', 'root.c', 10],
                                       ['helper.isra.3.constprop.2', 'helper.h', 20]]}))
        for mode in ('single', 'sidecar'):
            for theme in ('light', 'dark'):
                with self.subTest(mode=mode, theme=theme):
                    page = self.open_report(mode, theme, configuration)
                    frames = page.evaluate('''() => {
                        const model = fgView('timeline').probe();
                        model.inline(true);
                        return Array.from({length:model.NA - model.N}, (_, offset) => model.N + offset)
                            .map(index => ({index, name:model.nameOf(index), ghost:model.isGhost(index)}));
                    }''')
                    self.assertEqual(len(frames), 3)
                    original = page.evaluate('JSON.stringify(fgData.read("tlData"))')
                    for frame in frames:
                        page.evaluate('index => fgView("timeline").probe().select(index)', frame['index'])
                        expected = 'unknown frame' if frame['name'] == '?' else 'missing' if frame['ghost'] else 'helper'
                        self.assertEqual(page.locator('.tl-detail-title').inner_text(), expected)
                        raw = 'unknown frame' if frame['name'] == '?' else frame['name']
                        self.assertEqual(page.locator('.tl-detail-title').get_attribute('title'), raw)
                        if not frame['ghost']:
                            self.assertIn('into inode_has_perm', page.locator('.tl-slice-head .tl-pill').all_inner_texts())
                            self.assertEqual(page.locator('.tl-slice-head .tl-pill[title]').get_attribute('title'), names[0])
                    self.assertEqual(page.evaluate('JSON.stringify(fgData.read("tlData"))'), original)

    def test_kernel_user_and_module_titles_keep_original_identity_and_exports(self):
        names = ['inode_has_perm.isra.0', 'work.constprop.1', 'work.constprop.2', 'driver.isra.7']
        for mode in ('single', 'sidecar'):
            for user in (False, True):
                with self.subTest(mode=mode, user=user):
                    configuration = dict(columns=dict(name=[0, 1, 2], flags=[(1 << 25) if user else 0] * 3,
                                                      module=None if user else [-1, 3, -1]),
                                         extra=dict(strings=names))
                    page = self.open_report(mode, configuration=configuration)
                    original = page.evaluate('JSON.stringify(fgData.read("tlData"))')
                    page.evaluate('fgView("timeline").probe().select(1)')
                    expected = 'work' if user else 'work [driver.isra.7]'
                    self.assertEqual(page.locator('.tl-detail-title').inner_text(), expected)
                    self.assertEqual(page.locator('.tl-detail-title').get_attribute('title'), names[1])
                    with page.expect_download() as download:
                        page.locator('[data-context-export=stack]').click()
                    with open(download.value.path(), encoding='utf-8-sig', newline='') as source:
                        rows = list(csv.DictReader(source))
                    self.assertEqual([row['function'] for row in rows], names[:2])
                    self.assertEqual(page.evaluate('JSON.stringify(fgData.read("tlData"))'), original)

    def test_unknown_suffixes_and_escaped_symbols_remain_literal_in_titles(self):
        names = ['work.isra.0.extra', 'work.plt', '<img src=x onerror="window.titleInjected=1">.isra.0']
        configuration = dict(columns=dict(name=[0, 1, 2]), extra=dict(strings=names))
        for mode in ('single', 'sidecar'):
            for theme in ('light', 'dark'):
                with self.subTest(mode=mode, theme=theme):
                    page = self.open_report(mode, theme, configuration)
                    original = page.evaluate('JSON.stringify(fgData.read("tlData"))')
                    for index, raw in enumerate(names):
                        page.evaluate('index => fgView("timeline").probe().select(index)', index)
                        expected = raw if index < 2 else raw[:-len('.isra.0')]
                        self.assertEqual(page.locator('.tl-detail-title').inner_text(), expected)
                        self.assertEqual(page.locator('.tl-detail-title').get_attribute('title'), raw)
                        self.assertEqual(page.locator('.tl-detail-title img').count(), 0)
                    self.assertTrue(page.evaluate('window.titleInjected === undefined'))
                    self.assertEqual(page.evaluate('JSON.stringify(fgData.read("tlData"))'), original)
