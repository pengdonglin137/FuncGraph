"""Synthetic inline selections reuse recorded source without inventing events."""

from itertools import product
from pathlib import Path
import threading
import unittest

import helpers
from fglib.viewer import create_server
from test_tailcalls import TRACE


class InlineSourceBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise unittest.SkipTest(str(error))
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)
        cls.reports = {}
        for listing, mode, text in product((False, True), ('single', 'sidecar'), (False, True)):
            kernel = helpers.FakeKernel(inline_source=True, listing=listing)
            cls.addClassCleanup(kernel.close)
            resolver = helpers.FAKE_INLINE_FADDR2LINE.replace(
                "       printf ' (inlined by) traced_outer",
                "       printf ' (inlined by) inline_middle at %s/%s:1\\n' \"$FAKE_SRC\" \"$FAKE_SOURCE_FILE\"\n"
                "       if \"$listed\"; then printf '>1<\\tint a;\\n 2\\tint b;\\n'; fi\n"
                "       printf ' (inlined by) traced_outer")
            Path(kernel.ksrc, 'scripts/faddr2line').write_text(resolver)
            trace = Path(kernel.dir, 'trace.txt')
            trace.write_text(TRACE.replace('parent() { /* <-root+0x1/0x20 */', 'parent() {'))
            extra = [] if text else ['--no-text-view', '--keep-source']
            destination = 'report.html' if mode == 'single' else 'report'
            kernel.run(str(trace), '--no-config', '--no-btf', '--timeline', '--enable-fold',
                       '--report-mode', mode, '--base-url', 'https://source.invalid/src',
                       *extra, out=destination)
            path = Path(kernel.dir, destination)
            if mode == 'sidecar':
                server = create_server(path)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                cls.addClassCleanup(server.server_close)
                cls.addClassCleanup(server.shutdown)
                url = server.url
            else:
                url = path.as_uri()
            cls.reports[listing, mode, text] = url

    def open_report(self, listing=True, mode='single', text=True):
        page = self.browser.new_page(viewport={'width': 1500, 'height': 1000})
        self.addCleanup(page.close)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(self.assertEqual, errors, [])
        page.goto(self.reports[listing, mode, text])
        page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().inline(true); }")
        return page

    def select_inline(self, page, level=1, mouse=False):
        index = page.evaluate('''level => {
            const probe = fgView('timeline').probe();
            return probe.N + probe.SYN.findIndex(frame => frame.k === level && frame.kids.includes(1));
        }''', level)
        self.assertGreaterEqual(index, page.evaluate("fgView('timeline').probe().N"))
        if mouse:
            page.evaluate("index => fgView('timeline').probe().select(index)", index)
            page.evaluate("fgView('timeline').probe().select(-1)")
            point = page.evaluate("index => fgView('timeline').probe().xyOf(index)", index)
            page.mouse.click(*point)
        else:
            page.evaluate("index => fgView('timeline').probe().select(index)", index)
        self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), index)
        return index

    def test_mouse_click_opens_selected_inline_source_with_and_without_text_or_sidecars(self):
        for mode, text in product(('single', 'sidecar'), (False, True)):
            with self.subTest(mode=mode, text=text):
                page = self.open_report(mode=mode, text=text)
                snapshot = page.evaluate("JSON.stringify(fgData.read('tlData'))")
                for level, name, line in [(1, 'inline_middle', 1), (2, 'inline_helper', 2)]:
                    selected = self.select_inline(page, level, mouse=True)
                    page.locator('.tl-cs .source-line').first.wait_for(state='attached')
                    self.assertEqual(page.locator('.tl-detail-title').inner_text(), name)
                    self.assertIn('first traced call', page.locator('.tl-cs').inner_text())
                    self.assertEqual(page.locator('.tl-cs .src-frame[open] summary b').all_text_contents(), [name])
                    self.assertTrue(page.locator('.tl-cs .src-frame[open] .source-line').first.is_visible())
                    self.assertEqual(page.locator('.tl-cs .src-frame[open] summary a').get_attribute('href'),
                                     f'https://source.invalid/src/kernel/fake.c#L{line}')
                    self.assertEqual(page.locator('.tl-cs .source-line').count(), 7)
                    self.assertEqual(page.locator('[data-call-actions]').count(), 0)
                    page.locator('.tl-cs [data-fg-source-mode]').click()
                    self.assertEqual(page.locator('.tl-cs .src-frame').count(), 0)
                    self.assertEqual(page.locator('.tl-cs .source-line').count(), 7)
                    page.locator('.tl-cs [data-fg-source-mode]').click()
                    self.assertEqual(page.locator('.tl-cs .src-frame[open] summary b').all_text_contents(), [name])
                    self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), selected)
                self.assertEqual(page.evaluate("JSON.stringify(fgData.read('tlData'))"), snapshot)
                page.close()

    def test_plain_positions_keep_links_and_explain_missing_excerpts(self):
        for mode, text in product(('single', 'sidecar'), (False, True)):
            with self.subTest(mode=mode, text=text):
                page = self.open_report(listing=False, mode=mode, text=text)
                self.select_inline(page)
                page.wait_for_function("document.querySelector('.tl-cs-src')?.textContent.includes('Source excerpt not included')")
                self.assertEqual(page.locator('.tl-cs .source-line').count(), 0)
                self.assertEqual(page.locator('[data-context-list=inline] .tl-context-source a').count(), 3)
                self.assertEqual(page.locator('.tl-cs [data-fg-source-mode]').count(), 0)
                page.close()

    def test_missing_source_block_is_explicit_without_changing_inline_selection(self):
        page = self.open_report()
        page.evaluate("fgSourceForLine = () => null")
        selected = self.select_inline(page)
        self.assertIn('Source excerpt not included', page.locator('.tl-cs-src').inner_text())
        self.assertEqual(page.locator('[data-context-list=inline] .tl-context-source a').count(), 3)
        self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), selected)

    def test_source_disclosure_preference_and_real_call_focus_remain_independent(self):
        page = self.open_report()
        self.select_inline(page)
        page.locator('.tl-cs .source-line').first.wait_for(state='attached')
        page.locator('.tl-cs > summary').click()
        page.wait_for_function("localStorage.getItem('fgTl.csOpen') === '0'")
        self.select_inline(page, level=2)
        self.assertFalse(page.locator('.tl-cs').evaluate('element => element.open'))
        page.locator('.tl-cs > summary').click()
        page.locator('.tl-cs .source-line').first.wait_for(state='attached')
        page.evaluate("fgView('timeline').probe().select(1)")
        self.assertEqual(page.locator('.tl-cs .src-frame[open] summary b').all_text_contents(), ['inline_helper'])
        self.assertEqual(page.locator('.tl-cs > summary').text_content().split('Source:')[0].strip(), 'Call site source')

    def test_legacy_source_metadata_still_opens_the_selected_inline_level(self):
        page = self.open_report()
        page.evaluate("delete fgData.read('tlData').sources")
        self.select_inline(page)
        self.assertEqual(page.locator('.tl-cs .src-frame[open] summary b').all_text_contents(), ['inline_middle'])
        self.assertEqual(page.locator('.tl-cs .source-line').count(), 7)

    def test_failed_source_load_offers_retry_without_selecting_a_recorded_child(self):
        page = self.open_report(mode='sidecar')
        page.evaluate('''() => {
            const original = fgData.loadSource;
            fgData.sourceReady = () => false;
            let attempts = 0;
            fgData.loadSource = (...args) => ++attempts === 1 ? Promise.reject(new Error('test failure')) : original(...args);
        }''')
        selected = self.select_inline(page)
        page.locator('.tl-cs [role=alert]').wait_for(state='attached')
        self.assertIn('test failure', page.locator('.tl-cs [role=alert]').inner_text())
        page.get_by_role('button', name='Retry', exact=True).click()
        page.locator('.tl-cs .source-line').first.wait_for(state='attached')
        self.assertEqual(page.locator('.tl-cs .src-frame[open] summary b').all_text_contents(), ['inline_middle'])
        self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), selected)

    def test_pending_source_is_cancelled_on_selection_change_and_can_be_retried(self):
        page = self.open_report(mode='sidecar')
        page.evaluate('''() => {
            window.sourceRequests = [];
            window.originalLoadSource = fgData.loadSource;
            fgData.sourceReady = () => false;
            fgData.loadSource = (source, {signal}) => new Promise(resolve => sourceRequests.push({source, signal, resolve}));
        }''')
        self.select_inline(page)
        page.wait_for_function('sourceRequests.length === 1')
        self.assertEqual(page.locator('.tl-cs-src [role=status]').inner_text(), 'Loading source…')
        self.select_inline(page, level=2)
        page.wait_for_function('sourceRequests.length === 2')
        self.assertTrue(page.evaluate('sourceRequests[0].signal.aborted'))
        page.evaluate('''async () => {
            const text = await originalLoadSource(sourceRequests[1].source);
            sourceRequests[1].resolve(text);
            sourceRequests[0].resolve('<div class="source-line">stale source</div>');
        }''')
        page.locator('.tl-cs .source-line').first.wait_for(state='attached')
        self.assertNotIn('stale source', page.locator('.tl-cs-src').inner_text())
        self.assertEqual(page.locator('.tl-cs .src-frame[open] summary b').all_text_contents(), ['inline_helper'])

    def test_focus_matches_function_file_and_line_without_hiding_other_source_candidates(self):
        page = self.open_report()
        result = page.evaluate('''() => {
            const source = document.createElement('div');
            source.innerHTML = '<div class="location-link">same at /tree/first.c:2</div><div class="source-line">one</div>' +
                '<div class="location-link"> (inlined by) same at /tree/second.c:3</div><div class="source-line">two</div>' +
                '<div class="location-link"> (inlined by) outer at /tree/root.c:4</div><div class="source-line">three</div>' +
                '<div class="location-link">other at /tree/other.c:5</div><div class="source-line">four</div>';
            fgChainView(source, ['same', 'second.c', 3]);
            return {
                open: Array.from(source.querySelectorAll('.src-frame[open] summary .src-at'), element => element.textContent),
                lines: Array.from(source.querySelectorAll('.source-line'), element => element.textContent).sort(),
                candidates: source.querySelector(':scope > .location-link').textContent
            };
        }''')
        self.assertEqual(result['open'], ['/tree/second.c:3'])
        self.assertEqual(result['lines'], ['four', 'one', 'three', 'two'])
        self.assertEqual(result['candidates'], 'other at /tree/other.c:5')


if __name__ == '__main__':
    unittest.main()
