"""Compare browsing stays bounded without narrowing results or exports."""

import csv
from pathlib import Path
import threading
import unittest

import helpers
from fglib.viewer import create_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


def comparison_rows(count=1207):
    return [[f'worker_{index // 2:05d}.isra.{index // 2 % 3}', 1, 100 + index, 60 + index,
             100 + index, index % 3, 2, 300 + 3 * index, 120 + 2 * index,
             200 + index, (index + 1) % 3, 'user' if index % 2 else 'kernel']
            for index in range(count)]


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class ComparePagingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel()
        cls.addClassCleanup(cls.kernel.close)
        options = ['--no-config', '--compare', helpers.fixture('pi4_default'), '--timeline']
        cls.kernel.run(helpers.fixture('pi4_sched'), *options, out='single.html')
        cls.kernel.run(helpers.fixture('pi4_sched'), *options, '--report-mode', 'sidecar', out='sidecar')
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

    def open_report(self, rows=None, mode='single', theme='light', width=1280, height=720, touch=False, adjusted=None):
        context = self.browser.new_context(viewport=dict(width=width, height=height),
                                           is_mobile=touch, has_touch=touch, accept_downloads=True)
        self.addCleanup(context.close)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(self.assertEqual, errors, [])
        page.goto(Path(self.kernel.dir, 'single.html').as_uri() if mode == 'single' else self.server.url)
        page.evaluate('''async ({rows,adjusted,theme}) => {
            document.documentElement.dataset.theme = theme;
            const data = await fgData.load('cmpData');
            data.rows = rows;
            data.rows_adj = adjusted;
            if (adjusted) data.overhead = [{child:10,self:5},{child:10,self:5}];
            window.compareOriginal = JSON.stringify(data);
            await fgSwitchView('compare');
        }''', dict(rows=comparison_rows() if rows is None else rows, adjusted=adjusted, theme=theme))
        self.assertEqual(page.locator('.cmp-pages').count(), 1)
        return page

    def identities(self, page):
        return page.locator('.cmp-table tbody td.l[title]').evaluate_all('''nodes => nodes.map(node =>
            [node.title, node.querySelector('.cmp-tag')?.textContent || ''])''')

    def unchanged(self, page):
        self.assertTrue(page.evaluate("compareOriginal === JSON.stringify(fgData.read('cmpData'))"))

    def query(self, page, value, count):
        page.locator('.cmp-q').fill(value)
        page.locator('.cmp-q').press('Enter')
        page.wait_for_function('''count => document.querySelector('.cmp-wrap').getAttribute('aria-busy') !== 'true' &&
            document.querySelector('.cmp-count').textContent.startsWith(count + ' of ')''', arg=count)

    def test_complete_result_is_browsable_with_bounded_rows(self):
        rows = comparison_rows()
        expected = [[row[0], row[11]] for row in reversed(rows)]
        for mode in ('single', 'sidecar'):
            for theme in ('light', 'dark'):
                with self.subTest(mode=mode, theme=theme):
                    page = self.open_report(rows, mode, theme)
                    actual = []
                    while True:
                        current = self.identities(page)
                        first = list(reversed(rows))[len(actual)]
                        width = page.locator('.cmp-dbar').first.evaluate('node => parseFloat(node.style.width)')
                        expected_width = 40 * (first[8] - first[3]) / (rows[-1][8] - rows[-1][3])
                        self.assertAlmostEqual(width, expected_width, delta=0.051)
                        self.assertLessEqual(len(current), 400)
                        self.assertLess(page.locator('.cmp-wrap *').count(), 11500)
                        actual.extend(current)
                        if page.locator('.cmp-next').is_disabled():
                            break
                        page.locator('.cmp-next').click()
                    self.assertEqual(actual, expected)
                    self.assertIn('1201–1207', page.locator('.cmp-page-status').inner_text())
                    page.locator('.cmp-prev').click()
                    self.assertEqual(self.identities(page), expected[800:1200])
                    self.assertEqual(page.locator('.cmp-count').inner_text(), '1207 of 1207 functions')
                    self.unchanged(page)

    def test_empty_single_and_boundary_populations_do_not_leave_stale_pages(self):
        for count in (0, 1, 400, 401):
            with self.subTest(count=count):
                page = self.open_report(comparison_rows(count))
                self.assertEqual(page.locator('.cmp-pages').is_visible(), count > 400)
                self.assertEqual(len(self.identities(page)), min(400, count))
                self.assertTrue(page.locator('.cmp-prev').is_disabled())
                self.assertEqual(page.locator('.cmp-next').is_disabled(), count <= 400)
                self.assertEqual(page.locator('.cmp-empty').is_visible(), count == 0)
                self.unchanged(page)

    def test_native_keyboard_paging_keeps_focus_and_resets_vertical_scroll_only(self):
        page = self.open_report(comparison_rows(801))
        page.evaluate("Object.assign(document.querySelector('.cmp-wrap'), {scrollTop:120,scrollLeft:200})")
        horizontal = page.locator('.cmp-wrap').evaluate('node => node.scrollLeft')
        page.locator('.cmp-next').focus()
        page.locator('.cmp-next').press('Enter')
        self.assertEqual(page.locator('.cmp-wrap').evaluate('node => node.scrollTop'), 0)
        self.assertEqual(page.locator('.cmp-wrap').evaluate('node => node.scrollLeft'), horizontal)
        self.assertTrue(page.locator('.cmp-next').evaluate('node => node === document.activeElement'))
        page.locator('.cmp-next').press('Space')
        self.assertEqual(len(self.identities(page)), 1)
        self.assertTrue(page.locator('.cmp-prev').evaluate('node => node === document.activeElement'))
        page.locator('.cmp-prev').press('Enter')
        page.locator('.cmp-prev').press('Space')
        self.assertTrue(page.locator('.cmp-next').evaluate('node => node === document.activeElement'))
        self.unchanged(page)

    def test_filter_sort_scope_and_adjusted_rows_reset_to_the_first_page(self):
        rows = comparison_rows()
        adjusted = [row[:8] + [row[8] - 10] + row[9:] for row in reversed(rows)]
        page = self.open_report(rows, adjusted=adjusted)
        for action in (lambda: self.query(page, 'worker', len(rows)),
                       lambda: page.locator('.cmp-sortby button[data-v=base]').click(),
                       lambda: page.locator('th[data-k=name]').click(),
                       lambda: page.locator('.cmp-changed').uncheck(),
                       lambda: page.locator('.cmp-adj input').check()):
            page.locator('.cmp-next').click()
            action()
            self.assertIn('1–400', page.locator('.cmp-page-status').inner_text())
            self.assertTrue(page.locator('.cmp-prev').is_disabled())
        self.query(page, '^worker_00000\\.isra\\.0$', 2)
        self.assertFalse(page.locator('.cmp-pages').is_visible())
        self.query(page, '^missing$', 0)
        self.assertEqual(self.identities(page), [])
        self.query(page, '', len(rows))
        self.assertIn('1–400', page.locator('.cmp-page-status').inner_text())
        self.unchanged(page)

    def test_export_keeps_all_original_rows_and_the_current_page(self):
        rows = comparison_rows()
        rows[0][0] = '=SUM(1,2)'
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                page = self.open_report(rows, mode)
                page.locator('.cmp-next').click()
                before = self.identities(page)
                status = page.locator('.cmp-page-status').inner_text()
                with page.expect_download() as download:
                    page.locator('.cmp-export').click()
                with open(download.value.path(), encoding='utf-8-sig', newline='') as source:
                    exported = list(csv.DictReader(source))
                expected = [("'" + row[0] if row[0].startswith('=') else row[0], row[11]) for row in reversed(rows)]
                self.assertEqual([(row['function'], row['space']) for row in exported], expected)
                self.assertEqual([int(row['current_self_ns']) for row in exported], [row[8] for row in reversed(rows)])
                self.assertEqual(self.identities(page), before)
                self.assertEqual(page.locator('.cmp-page-status').inner_text(), status)
                self.unchanged(page)

    def test_view_roundtrip_resize_and_invalid_filter_keep_the_completed_page(self):
        page = self.open_report(mode='sidecar')
        page.locator('.cmp-next').click()
        page.locator('.cmp-next').click()
        before = self.identities(page)
        status = page.locator('.cmp-page-status').inner_text()
        page.evaluate("document.querySelector('.cmp-wrap').scrollTop = 150")
        scroll = page.locator('.cmp-wrap').evaluate('node => node.scrollTop')
        page.evaluate("async () => { await fgSwitchView('text'); await fgSwitchView('compare'); }")
        page.set_viewport_size(dict(width=1100, height=650))
        self.assertEqual(self.identities(page), before)
        self.assertEqual(page.locator('.cmp-page-status').inner_text(), status)
        self.assertEqual(page.locator('.cmp-wrap').evaluate('node => node.scrollTop'), scroll)
        page.locator('.cmp-q').press('Enter')
        self.assertEqual(page.locator('.cmp-page-status').inner_text(), status)
        page.locator('.cmp-q').fill('[')
        page.locator('.cmp-q').press('Enter')
        self.assertTrue(page.locator('.cmp-filter-error').is_visible())
        self.assertEqual(self.identities(page), before)
        page.locator('.cmp-q').press('Escape')
        self.assertEqual(page.locator('.cmp-page-status').inner_text(), status)
        self.unchanged(page)

    def test_pager_fits_touch_viewports_and_does_not_expand_document_width(self):
        for width, height in ((390, 600), (844, 390), (320, 480)):
            for theme in ('light', 'dark'):
                with self.subTest(width=width, height=height, theme=theme):
                    page = self.open_report(mode='sidecar', theme=theme, width=width, height=height, touch=True)
                    pager = page.locator('.cmp-pages').bounding_box()
                    self.assertLessEqual(pager['y'] + pager['height'], height + 1)
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width + 1)
                    self.assertGreaterEqual(page.locator('.cmp-next').bounding_box()['height'], 44)
                    page.locator('.cmp-next').tap()
                    self.assertIn('401–800', page.locator('.cmp-page-status').inner_text())
                    self.unchanged(page)

    def test_compact_filters_keep_the_active_query_page_and_focus_reachable(self):
        page = self.open_report(mode='sidecar', width=390, height=600, touch=True)
        toggle = page.locator('.cmp-toggle')
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'false')
        self.assertFalse(page.locator('.cmp-controls').is_visible())
        toggle.press('Enter')
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'true')
        self.query(page, 'worker', 1207)
        toggle.press('Space')
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'false')
        self.assertIn('active', toggle.inner_text())
        page.locator('.cmp-next').tap()
        self.assertIn('401–800', page.locator('.cmp-page-status').inner_text())
        toggle.tap()
        self.assertEqual(page.locator('.cmp-q').input_value(), 'worker')
        self.assertIn('401–800', page.locator('.cmp-page-status').inner_text())
        self.unchanged(page)

    def test_narrowing_a_desktop_view_does_not_hide_keyboard_focus(self):
        page = self.open_report()
        page.locator('.cmp-q').focus()
        page.set_viewport_size(dict(width=390, height=700))
        page.wait_for_function("document.activeElement === document.querySelector('.cmp-toggle')")
        page.locator('.cmp-toggle').press('Enter')
        self.assertTrue(page.locator('.cmp-q').is_visible())
        page.set_viewport_size(dict(width=1280, height=720))
        self.assertTrue(page.locator('.cmp-q').is_visible())
        self.assertFalse(page.locator('.cmp-toggle').is_visible())
        self.assertTrue(page.locator('.cmp-q').evaluate('node => node === document.activeElement'))
        self.unchanged(page)

    def test_cancelling_a_compact_filter_restores_visible_keyboard_focus(self):
        page = self.open_report(mode='sidecar', width=390, height=600, touch=True)
        page.evaluate('''() => {
            window.Worker = class {
                constructor() { queueMicrotask(() => this.onmessage?.({data:{type:'started'}})); }
                postMessage(data) {
                    if (data.type === 'init') queueMicrotask(() => this.onmessage({data:{type:'ready'}}));
                }
                terminate() {}
            };
        }''')
        page.locator('.cmp-toggle').tap()
        page.locator('.cmp-q').fill('worker_[0-9]')
        page.locator('.cmp-q').press('Enter')
        self.assertTrue(page.locator('.cmp-filter-status').is_visible())
        page.locator('.cmp-toggle').tap()
        page.locator('.cmp-next').tap()
        status = page.locator('.cmp-page-status').inner_text()
        page.locator('.cmp-cancel').tap()
        self.assertFalse(page.locator('.cmp-filter-status').is_visible())
        self.assertTrue(page.locator('.cmp-toggle').evaluate('node => node === document.activeElement'))
        self.assertEqual(page.locator('.cmp-page-status').inner_text(), status)
        self.assertFalse(page.locator('.cmp-export').is_disabled())
        page.locator('.cmp-toggle').press('Enter')
        self.assertEqual(page.locator('.cmp-q').input_value(), '')
        self.unchanged(page)

    def test_pending_filter_allows_complete_old_pages_then_resets_after_success(self):
        rows = comparison_rows()
        page = self.open_report(rows)
        page.locator('.cmp-next').click()
        page.evaluate('''() => {
            window.compareWorkers = [];
            window.Worker = class {
                constructor() {
                    compareWorkers.push(this);
                    queueMicrotask(() => this.onmessage?.({data:{type:'started'}}));
                }
                postMessage(data) {
                    if (data.type === 'init') {
                        this.query = data.query;
                        queueMicrotask(() => this.onmessage({data:{type:'ready'}}));
                    } else this.batch = data;
                }
                terminate() { this.terminated = true; }
                reply() {
                    const batch = this.batch;
                    this.batch = null;
                    const matcher = new RegExp(this.query,'i');
                    const buffer = Uint8Array.from(batch.names.map(name => Number(matcher.test(name)))).buffer;
                    this.onmessage({data:{type:'hits',id:batch.id,buffer}});
                }
                finish() { while (this.batch && !this.terminated) this.reply(); }
            };
        }''')
        page.locator('.cmp-q').fill('worker_0000[0-2]')
        page.locator('.cmp-q').press('Enter')
        page.wait_for_function('compareWorkers.at(-1)?.batch')
        page.evaluate('compareWorkers.at(-1).reply()')
        page.locator('.cmp-next').click()
        self.assertEqual(self.identities(page), [[row[0], row[11]] for row in list(reversed(rows))[800:1200]])
        self.assertTrue(page.locator('.cmp-export').is_disabled())
        page.locator('.cmp-cancel').click()
        self.assertIn('801–1200', page.locator('.cmp-page-status').inner_text())
        self.assertTrue(page.evaluate('compareWorkers.at(-1).terminated'))
        page.locator('.cmp-q').fill('worker_0000[0-2]')
        page.locator('.cmp-q').press('Enter')
        page.wait_for_function('compareWorkers.at(-1)?.batch')
        page.locator('.cmp-next').focus()
        page.evaluate('compareWorkers.at(-1).finish()')
        page.wait_for_function("document.querySelector('.cmp-count').textContent === '6 of 1207 functions'")
        self.assertFalse(page.locator('.cmp-pages').is_visible())
        self.assertTrue(page.locator('.cmp-wrap').evaluate('node => node === document.activeElement'))
        self.unchanged(page)

    def test_hiding_compact_controls_during_composition_does_not_block_later_enter(self):
        page = self.open_report(width=390, height=700, touch=True)
        page.locator('.cmp-toggle').click()
        page.locator('.cmp-q').evaluate('''input => {
            input.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));
            input.value = '^worker_00000';
            input.dispatchEvent(new InputEvent('input',{bubbles:true,isComposing:true}));
        }''')
        page.locator('.cmp-toggle').click()
        page.locator('.cmp-toggle').click()
        page.locator('.cmp-q').press('Enter')
        page.wait_for_function("document.querySelector('.cmp-count').textContent === '2 of 1207 functions'")
        self.assertEqual(page.locator('.cmp-q').input_value(), '^worker_00000')
        self.unchanged(page)
