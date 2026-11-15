"""Compare filters remain complete, cancellable and safe to export."""

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


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class CompareRegexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel()
        cls.addClassCleanup(cls.kernel.close)
        common = ['--no-config', '--compare', helpers.fixture('pi4_default')]
        cls.kernel.run(helpers.fixture('pi4_sched'), *common, out='single.html')
        cls.kernel.run(helpers.fixture('pi4_sched'), *common, '--timeline', '--report-mode', 'sidecar', out='sidecar')
        cls.server = create_server(Path(cls.kernel.dir, 'sidecar'))
        thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(thread.join, 5)
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.page = self.browser.new_page(viewport={'width': 1280, 'height': 720})
        self.addCleanup(self.page.close)
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open_report(self, rows=None, adjusted=None, mode='single'):
        if rows is None:
            names = ['a' * 24 + 'b', 'root', 'ROOT<int>', 'child+symbol', '中文🙂', 'first\nsecond',
                     'quote"\\path', 'z' * 70000] + [f'worker_{index:04d}' for index in range(650)]
            rows = [[name, 2, 100, 50, 70, 0, 2, 200 + index, 100 + index, 120 + index, 0]
                    for index, name in enumerate(names)]
            rows.extend([['same', 2, 100, 50, 70, 0, 2, 200, 100, 120, 0, space]
                         for space in ('user', 'kernel')])
            rows.append(['unchanged', 2, 100, 50, 70, 0, 2, 100, 50, 70, 0])
        url = Path(self.kernel.dir, 'single.html').as_uri() if mode == 'single' else self.server.url
        self.page.goto(url)
        self.page.evaluate('''async ({rows,adjusted}) => {
            const data = await fgData.load('cmpData');
            data.rows = rows;
            data.rows_adj = adjusted;
            if (adjusted) data.overhead = [{child:10,self:5},{child:10,self:5}];
            window.comparePayload = JSON.stringify(data);
            await fgSwitchView('compare');
        }''', dict(rows=rows, adjusted=adjusted))
        self.page.locator('.cmp-table').wait_for()
        self.assertEqual(self.page.locator('.tl-canvas').count(), 0)

    def start(self, query):
        self.page.evaluate('''query => {
            const input = document.querySelector('.cmp-q');
            input.value = query;
            input.dispatchEvent(new Event('input',{bubbles:true}));
            input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));
        }''', query)

    def settled(self):
        self.page.wait_for_function("document.querySelector('.cmp-wrap').getAttribute('aria-busy') !== 'true'")

    def unchanged(self):
        self.assertTrue(self.page.evaluate("comparePayload === JSON.stringify(fgData.read('cmpData'))"))

    def held_worker(self):
        self.page.evaluate('''() => {
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
                reply(values = null) {
                    const data = this.batch;
                    this.batch = null;
                    const matcher = new RegExp(this.query,'i');
                    const buffer = Uint8Array.from(values || data.names.map(name => Number(matcher.test(name)))).buffer;
                    this.onmessage({data:{type:'hits',id:data.id,buffer}});
                }
                finish() { while (this.batch && !this.terminated) this.reply(); }
            };
        }''')

    def wait_worker(self):
        self.page.wait_for_function('compareWorkers.at(-1)?.batch')

    def test_native_regex_results_cover_all_names_spaces_and_batches(self):
        self.open_report()
        for query in ('^(root|child)', '中文|worker_00[12]$', '(?<=child)\\+',
                      '[a-z]{4}_[0-9]+', '^z+$', '^(ROOT|first\\nsecond)$', '^missing(?:name)$'):
            with self.subTest(query=query):
                expected = self.page.evaluate('''query => fgData.read('cmpData').rows.filter(row =>
                    new RegExp(query,'i').test(row[0]) && row.slice(1,6).some((value,index) => value !== row[index+6]))
                    .map(row => row[0])''', query)
                self.start(query)
                self.settled()
                self.assertEqual(self.page.locator('.cmp-count').inner_text().split(' of ')[0], str(len(expected)))
                if self.page.locator('.cmp-table .more').count():
                    self.page.locator('.cmp-table .more').click()
                actual = self.page.locator('.cmp-table td.l[title]').evaluate_all('nodes => nodes.map(node => node.title)')
                self.assertEqual(sorted(actual), sorted(expected))
                self.unchanged()
        self.assertTrue(self.page.locator('.cmp-empty').is_visible())

    def test_literal_anchors_and_universal_filter_do_not_require_worker(self):
        self.open_report()
        self.page.evaluate('''() => {
            window.unexpectedCompareWorkers = 0;
            window.Worker = class { constructor() { unexpectedCompareWorkers++; throw new Error('Unexpected worker'); } };
        }''')
        self.start('^root$')
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '1 of 661 functions')
        self.start('.*')
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '660 of 661 functions')
        self.assertFalse(self.page.locator('.cmp-filter-status').is_visible())
        self.assertEqual(self.page.evaluate('unexpectedCompareWorkers'), 0)

    def test_partial_worker_results_never_replace_table_and_cancel_restores_query(self):
        self.open_report()
        self.held_worker()
        before = self.page.locator('.cmp-count').inner_text()
        self.page.evaluate("window.originalCompareRow = document.querySelector('.cmp-table tbody tr')")
        self.start('root|worker')
        self.wait_worker()
        self.page.evaluate('compareWorkers[0].reply()')
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), before)
        self.assertTrue(self.page.locator('.cmp-export').is_disabled())
        self.assertTrue(self.page.locator('.cmp-filter-status').is_visible())
        self.page.locator('.cmp-cancel').click()
        self.assertEqual(self.page.locator('.cmp-q').input_value(), '')
        self.assertTrue(self.page.locator('.cmp-q').evaluate('node => node === document.activeElement'))
        self.page.evaluate('compareWorkers[0].reply()')
        self.assertTrue(self.page.evaluate('compareWorkers[0].terminated'))
        self.assertTrue(self.page.evaluate("originalCompareRow === document.querySelector('.cmp-table tbody tr')"))
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), before)
        self.assertFalse(self.page.locator('.cmp-export').is_disabled())
        self.unchanged()

    def test_escape_restores_last_complete_expression(self):
        self.open_report()
        self.start('^root$')
        self.held_worker()
        self.start('root|worker')
        self.wait_worker()
        self.page.locator('.cmp-q').press('Escape')
        self.assertEqual(self.page.locator('.cmp-q').input_value(), '^root$')
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '1 of 661 functions')
        self.assertFalse(self.page.locator('.cmp-filter-status').is_visible())
        self.assertTrue(self.page.evaluate('compareWorkers[0].terminated'))

    def test_edit_aborts_immediately_and_old_responses_cannot_overwrite_new_filter(self):
        self.open_report()
        self.held_worker()
        self.start('root|worker')
        self.wait_worker()
        self.page.locator('.cmp-q').fill('^root$')
        self.assertTrue(self.page.evaluate('compareWorkers[0].terminated'))
        self.page.locator('.cmp-q').press('Enter')
        self.page.evaluate('compareWorkers[0].reply()')
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '1 of 661 functions')
        self.assertFalse(self.page.locator('.cmp-filter-error').is_visible())
        self.unchanged()

    def test_view_switch_cancels_background_work_and_keeps_complete_result(self):
        self.open_report()
        self.held_worker()
        self.start('root|worker')
        self.wait_worker()
        self.page.evaluate("fgSwitchView('text')")
        self.assertTrue(self.page.evaluate('compareWorkers[0].terminated'))
        self.page.evaluate("fgSwitchView('compare')")
        self.assertEqual(self.page.locator('.cmp-q').input_value(), '')
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '660 of 661 functions')
        self.assertFalse(self.page.locator('.cmp-filter-status').is_visible())
        self.unchanged()

    def test_runtime_failure_does_not_rerun_regex_in_page_and_retry_is_complete(self):
        self.open_report()
        self.held_worker()
        self.page.evaluate('''() => {
            const original = RegExp.prototype.test;
            RegExp.prototype.test = function(value) {
                if (this.source === 'root|worker') throw new Error('Unsafe fallback after worker started');
                return original.call(this,value);
            };
        }''')
        self.start('root|worker')
        self.wait_worker()
        self.page.evaluate('compareWorkers[0].onerror({preventDefault(){}})')
        self.settled()
        self.assertIn('Background filter failed', self.page.locator('.cmp-filter-error').inner_text())
        self.assertIsNone(self.page.locator('.cmp-q').get_attribute('aria-invalid'))
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '660 of 661 functions')
        self.assertTrue(self.page.locator('.cmp-export').is_disabled())
        self.start('root|child')
        self.wait_worker()
        self.page.evaluate('compareWorkers.at(-1).finish()')
        self.settled()
        self.assertFalse(self.page.locator('.cmp-filter-error').is_visible())
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '3 of 661 functions')
        self.unchanged()

    def test_bad_worker_buffer_is_failure_not_empty_or_partial_result(self):
        self.open_report()
        self.held_worker()
        self.start('root|worker')
        self.wait_worker()
        self.page.evaluate('compareWorkers[0].reply([2])')
        self.settled()
        self.assertTrue(self.page.locator('.cmp-filter-error').is_visible())
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '660 of 661 functions')
        self.assertFalse(self.page.locator('.cmp-empty').is_visible())
        self.assertTrue(self.page.locator('.cmp-export').is_disabled())
        self.start('^root$')
        self.assertFalse(self.page.locator('.cmp-filter-error').is_visible())
        self.assertFalse(self.page.locator('.cmp-export').is_disabled())

    def test_startup_unavailable_keeps_explicit_page_fallback_and_caches_name_hits(self):
        self.open_report()
        self.page.evaluate('''() => {
            window.Worker = class { constructor() { throw new Error('Worker blocked'); } };
            window.compareRegexTests = 0;
            const original = RegExp.prototype.test;
            RegExp.prototype.test = function(value) {
                if (this.source === 'root|worker') compareRegexTests++;
                return original.call(this,value);
            };
        }''')
        self.start('root|worker')
        self.assertIn('Filtering in page', self.page.locator('.cmp-filter-status').inner_text())
        self.settled()
        tested = self.page.evaluate('compareRegexTests')
        self.assertEqual(tested, 660)
        self.page.locator('.cmp-sortby button[data-v=cur]').click()
        self.page.locator('.cmp-table th[data-k=calls]').click()
        self.page.locator('.cmp-changed').uncheck()
        self.assertEqual(self.page.evaluate('compareRegexTests'), tested)
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '652 of 661 functions')
        self.unchanged()

    def test_pending_export_waits_for_all_batches_and_keeps_original_symbols(self):
        self.open_report()
        self.held_worker()
        downloads = []
        self.page.on('download', lambda download: downloads.append(download))
        self.page.evaluate('''() => {
            const input = document.querySelector('.cmp-q');
            input.value = 'worker_|child\\\\+symbol|^same$';
            input.dispatchEvent(new Event('input',{bubbles:true}));
            document.querySelector('.cmp-export').click();
        }''')
        self.wait_worker()
        self.assertEqual(downloads, [])
        self.assertTrue(self.page.locator('.cmp-export').is_disabled())
        with self.page.expect_download() as pending:
            self.page.evaluate('compareWorkers[0].finish()')
        download = pending.value
        with open(download.path(), encoding='utf-8-sig', newline='') as source:
            rows = list(csv.reader(source))
        self.assertEqual(len(rows), 654)
        self.assertEqual(sum(row[0] == 'same' for row in rows[1:]), 2)
        self.assertIn('child+symbol', [row[0] for row in rows[1:]])
        self.assertEqual(self.page.locator('.cmp-table tbody tr').count(), 401)
        self.unchanged()

    def test_adjusted_rows_use_names_not_row_positions_and_do_not_restart_matcher(self):
        rows = [['alpha.isra.0', 1, 100, 50, 70, 0, 1, 200, 100, 120, 0],
                ['beta', 1, 100, 50, 70, 0, 1, 300, 200, 220, 0]]
        adjusted = [rows[1], ['extra.constprop.0', 1, 50, 25, 35, 0, 1, 100, 50, 60, 0], rows[0]]
        self.open_report(rows, adjusted)
        self.held_worker()
        self.start('alpha|extra')
        self.wait_worker()
        self.page.evaluate('compareWorkers[0].finish()')
        self.settled()
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '1 of 2 functions')
        self.page.locator('.cmp-adj input').check()
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '2 of 3 functions')
        self.assertEqual(self.page.evaluate('compareWorkers.length'), 1)
        names = self.page.locator('.cmp-table td.l[title]').evaluate_all('nodes => nodes.map(node => node.title)')
        self.assertEqual(set(names), {'alpha.isra.0', 'extra.constprop.0'})
        self.unchanged()

    def test_cancel_pending_export_cannot_download_old_or_partial_results(self):
        self.open_report()
        self.held_worker()
        downloads = []
        self.page.on('download', lambda download: downloads.append(download))
        self.page.evaluate('''() => {
            const input = document.querySelector('.cmp-q');
            input.value = 'root|worker';
            input.dispatchEvent(new Event('input',{bubbles:true}));
            document.querySelector('.cmp-export').click();
        }''')
        self.wait_worker()
        self.page.locator('.cmp-cancel').click()
        self.page.evaluate('compareWorkers[0].reply()')
        self.page.wait_for_timeout(200)
        self.assertEqual(downloads, [])
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '660 of 661 functions')
        self.assertFalse(self.page.locator('.cmp-export').is_disabled())
        self.unchanged()

    def test_missing_worker_support_falls_back_without_dropping_matches(self):
        self.open_report()
        self.page.evaluate('delete window.Worker')
        self.start('root|child')
        self.settled()
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '3 of 661 functions')
        self.assertFalse(self.page.locator('.cmp-filter-error').is_visible())
        self.unchanged()

    def test_invalid_filter_retains_table_and_escape_restores_export_and_height(self):
        self.open_report()
        self.start('^root$')
        self.start('[')
        self.assertEqual(self.page.locator('.cmp-q').get_attribute('aria-invalid'), 'true')
        self.assertTrue(self.page.locator('.cmp-filter-error').is_visible())
        self.assertTrue(self.page.locator('.cmp-export').is_disabled())
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '1 of 661 functions')
        self.page.locator('.cmp-q').press('Escape')
        self.assertEqual(self.page.locator('.cmp-q').input_value(), '^root$')
        self.assertFalse(self.page.locator('.cmp-filter-error').is_visible())
        self.assertFalse(self.page.locator('.cmp-export').is_disabled())
        self.assertIsNone(self.page.locator('.cmp-q').get_attribute('aria-invalid'))

    def test_ime_input_and_enter_do_not_start_until_composition_ends(self):
        self.open_report()
        self.held_worker()
        self.page.evaluate('''() => {
            const input = document.querySelector('.cmp-q');
            input.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));
            input.value = '中文|root';
            input.dispatchEvent(new InputEvent('input',{bubbles:true,isComposing:true}));
            input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,isComposing:true}));
        }''')
        self.page.wait_for_timeout(200)
        self.assertEqual(self.page.evaluate('compareWorkers.length'), 0)
        self.page.evaluate("document.querySelector('.cmp-q').dispatchEvent(new CompositionEvent('compositionend',{bubbles:true}))")
        self.wait_worker()
        self.page.evaluate('compareWorkers[0].finish()')
        self.settled()
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '3 of 661 functions')
        self.unchanged()

    def test_finite_expensive_regex_keeps_main_thread_heartbeat_live(self):
        self.open_report([['a' * 24 + 'b', 1, 100, 50, 70, 0, 1, 200, 100, 120, 0]])
        observation = self.page.evaluate('''async () => {
            const heartbeat = new Promise(resolve => setTimeout(() => resolve({
                busy:document.querySelector('.cmp-wrap').getAttribute('aria-busy'),
                count:document.querySelector('.cmp-count').textContent
            }),0));
            const input = document.querySelector('.cmp-q');
            input.value = '^(a+)+$';
            input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));
            return await heartbeat;
        }''')
        self.assertEqual(observation, dict(busy='true', count='1 of 1 functions'))
        self.settled()
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '0 of 1 functions')
        self.unchanged()

    def test_http_sidecar_touch_cancel_is_reachable_in_both_themes(self):
        self.page.close()
        self.page = self.browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
        self.addCleanup(self.page.close)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.open_report(mode='sidecar')
        self.held_worker()
        for theme in ('light', 'dark'):
            self.page.evaluate("theme => document.documentElement.setAttribute('data-theme',theme)", theme)
            self.start('root|worker')
            self.wait_worker()
            bounds = self.page.locator('.cmp-cancel').bounding_box()
            self.assertGreaterEqual(bounds['height'], 44)
            self.assertGreaterEqual(bounds['width'], 44)
            self.assertLessEqual(bounds['x'] + bounds['width'], 390)
            self.assertLessEqual(bounds['y'] + bounds['height'], 844)
            self.page.touchscreen.tap(bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2)
            self.assertFalse(self.page.locator('.cmp-filter-status').is_visible())
            self.assertTrue(self.page.evaluate('compareWorkers.at(-1).terminated'))
            self.unchanged()


if __name__ == '__main__':
    unittest.main()
