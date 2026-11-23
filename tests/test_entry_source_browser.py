"""Native entry excerpts remain usable through every supported source delivery."""
from itertools import product
from pathlib import Path
import threading
import unittest
from urllib.parse import urljoin

import helpers
from fglib.viewer import create_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


TRACE_HEADER = '# tracer: function_graph\n'
TRACE = TRACE_HEADER + ' 0)               |  entry() {\n'
LEAF = ' 0)   1.000 us    |    leaf(value=5);\n'
EXIT = ' 0)   2.000 us    |  } /* entry */\n'
CALL_SITE_COUNT = 300


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class NativeEntrySourceBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel(inline_source=True)
        cls.addClassCleanup(cls.kernel.close)
        root = Path(cls.kernel.dir)
        trace = root / 'entry.txt'
        options = ['--no-config', '--no-btf', '--func-links', '--enable-fold', '--filter',
                   '--base-url', 'https://kernel.invalid/src']
        configurations = [('plain.html', [], 1), ('index.html', ['--timeline'], 1),
                          ('sidecar', ['--timeline', '--report-mode', 'sidecar'], 1),
                          ('windowed', ['--timeline', '--report-mode', 'sidecar'], 4500),
                          ('mixed-caller.html', ['--timeline'], 1),
                          ('many-callers.html', ['--timeline'], 1),
                          ('kept.html', ['--timeline', '--no-text-view', '--keep-source'], 1)]
        for destination, extra, count in configurations:
            content = TRACE + LEAF * count + EXIT
            if destination == 'mixed-caller.html':
                content = content.replace('entry() {', 'entry() { /* <-main+0x1/0x20 */')
            if destination == 'many-callers.html':
                content = TRACE_HEADER + ''.join(
                    f' 0)   1.000 us    |  leaf(); /* <-caller_{index}+0x1/0x20 */\n'
                    for index in range(CALL_SITE_COUNT))
            trace.write_text(content)
            cls.kernel.run(str(trace), *options, *extra, out=destination)
        cls.server = create_server(root)
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

    def open_report(self, filename, mobile=False):
        context = self.browser.new_context(viewport=dict(width=390 if mobile else 1500, height=900),
                                           is_mobile=mobile, has_touch=mobile)
        self.addCleanup(context.close)
        context.add_init_script('localStorage.clear()')
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(self.assertEqual, errors, [])
        page.goto(urljoin(self.server.url, filename))
        page.evaluate("theme => document.documentElement.dataset.theme = theme", 'dark' if mobile else 'light')
        return page

    def test_text_source_click_keyboard_and_filters_work_without_a_recorded_call_site(self):
        filenames = ('plain.html', 'index.html', 'sidecar/index.html', 'windowed/index.html?text=windowed')
        for filename, mobile in product(filenames, (False, True)):
            with self.subTest(report=filename, mobile=mobile):
                page = self.open_report(filename, mobile)
                page.evaluate("async () => { if (typeof fgSwitchView === 'function') await fgSwitchView('text'); }")
                self.assertEqual(page.evaluate('!!fgTextWindow'), 'windowed' in filename)
                button = page.locator('#L3 .expand-btn')
                button.click()
                source = page.locator('#line_2_content')
                source.wait_for(state='visible')
                self.assertEqual(button.get_attribute('aria-expanded'), 'true')
                source.locator('.source-line.current-line').wait_for()
                self.assertIn('kernel function entry', source.inner_text())
                self.assertIn('int b;', source.inner_text())
                self.assertEqual(source.locator('[data-source-position] a').get_attribute('href'),
                                 'https://kernel.invalid/src/kernel/fake.c#L2')
                self.assertEqual(source.locator('.source-line.current-line').count(), 1)
                button.click()
                source.wait_for(state='hidden')
                self.assertEqual(button.get_attribute('aria-expanded'), 'false')
                page.locator('#L3').focus()
                page.locator('#L3').press('Enter')
                source.wait_for(state='visible')
                if not page.locator('#filterBox').is_visible():
                    page.locator('#textFilterToggle').press('Enter')
                page.locator('#filterInput').fill('func:leaf')
                page.locator('#filterInput').press('Enter')
                page.wait_for_function("!fgFilterProgress && fgFilterState.text === 'func:leaf'")
                source.locator('.source-line.current-line').wait_for(state='attached')
                self.assertEqual(button.get_attribute('aria-expanded'), 'true' if source.is_visible() else 'false')
                self.assertIn('int b;', page.locator('#line_2_content').inner_text())
                page.close()

    def test_timeline_labels_entry_source_without_inventing_a_caller_or_changing_calls(self):
        for filename in ('index.html', 'sidecar/index.html', 'kept.html', 'mixed-caller.html'):
            with self.subTest(report=filename):
                page = self.open_report(filename)
                page.evaluate("async () => { await fgSwitchView('timeline'); }")
                before = page.evaluate("JSON.stringify(fgData.read('tlData'))")
                page.evaluate("fgView('timeline').probe().select(1)")
                source = page.locator('.tl-cs')
                source.locator('.source-line').first.wait_for(state='attached')
                label = source.locator('summary .tl-cs-title').text_content()
                self.assertEqual(label, 'Kernel function source')
                self.assertIn('kernel function entry', source.inner_text())
                self.assertIn('int b;', source.inner_text())
                if filename == 'mixed-caller.html':
                    self.assertEqual(page.evaluate("fgData.read('tlData').cols.caller[1]"), -1)
                else:
                    self.assertEqual(page.evaluate("fgData.read('tlData').sources.callers"), {})
                self.assertEqual(page.evaluate("fgData.read('tlData').n"), 2)
                self.assertEqual(page.evaluate("JSON.stringify(fgData.read('tlData'))"), before)
                if filename == 'mixed-caller.html':
                    page.evaluate("fgView('timeline').probe().select(0)")
                    page.locator('.tl-cs .source-line').first.wait_for(state='attached')
                    self.assertTrue(page.locator('.tl-cs > summary').text_content().startswith('Call site source'))
                page.close()

    def test_timeline_bounds_call_site_cache_without_changing_revisited_positions(self):
        page = self.open_report('many-callers.html')
        result = page.evaluate('''async count => {
            await fgSwitchView('timeline');
            const model = fgView('timeline').probe();
            const trace = fgData.read('tlData');
            const chains = trace.cols.caller.map(index => trace.sources.callers[index]);
            const known = new Set(chains);
            const set = Map.prototype.set;
            let cache, peak = 0;
            Map.prototype.set = function (key, value) {
                const result = set.call(this, key, value);
                if (known.has(key) && value?.at && typeof value.inl === 'boolean') {
                    cache = this;
                    peak = Math.max(peak, this.size);
                }
                return result;
            };
            const site = index => {
                model.select(index);
                const link = document.querySelector('.tl-cs-at [data-callsite-position]');
                return link && [link.textContent, link.getAttribute('href'), link.parentElement.textContent];
            };
            try {
                const first = site(0), second = site(1);
                for (let index = 2; index < count; index++) {
                    if (index % 32 === 0) site(0);
                    site(index);
                }
                const hotStillCached = cache?.has(chains[0]);
                const coldStillCached = cache?.has(chains[1]);
                return {first, second, revisited:site(1), peak, hotStillCached, coldStillCached,
                    selected:model.sel(), distinct:known.size};
            } finally {
                Map.prototype.set = set;
            }
        }''', CALL_SITE_COUNT)
        self.assertTrue(result['first'], result)
        self.assertTrue(result['second'], result)
        self.assertEqual(result['distinct'], CALL_SITE_COUNT, result)
        self.assertEqual(result['peak'], 256, result)
        self.assertTrue(result['hotStillCached'], result)
        self.assertFalse(result['coldStillCached'], result)
        self.assertEqual(result['revisited'], result['second'])
        self.assertEqual(result['selected'], 1)
