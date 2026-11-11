"""Duration-call pages stay bounded without changing statistical/export scope."""
import csv
import io
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest

import helpers
from benchmark_distcalls import fixture
from fglib.viewer import create_server
from test_uftrace_import import call, stream


class DistributionPagingTest(unittest.TestCase):
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
        cls.kernel = helpers.FakeKernel()
        cls.addClassCleanup(cls.kernel.close)
        trace = Path(cls.kernel.dir) / 'template.jsonl'
        trace.write_text(stream([call('entry', 'user', 10, 0, 'work', 1), call('exit', 'user', 100, 0, 'work', 1)]))
        cls.kernel.run(str(trace), '--no-config', '--timeline', '--no-text-view')
        cls.template = Path(cls.kernel.dir) / 'out.html'

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='fg-dist-page-')
        self.addCleanup(self.directory.cleanup)
        self.serial = 0
        self.errors = []
        self.context = self.browser.new_context(viewport={'width': 1500, 'height': 900})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.set_default_timeout(10000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open_report(self, count=205, update=None, sidecar=False):
        self.serial += 1
        report = Path(self.directory.name) / f'calls-{self.serial}.html'
        if sidecar:
            records = []
            for index in range(count):
                records.extend([call('entry', 'user', index * 20000, 0, 'work', 1),
                                call('exit', 'user', index * 20000 + 1 + index, 0, 'work', 1)])
            trace = Path(self.kernel.dir) / f'actual-{self.serial}.jsonl'
            trace.write_text(stream(records), encoding='utf-8')
            destination = Path(self.directory.name) / f'sidecar-{self.serial}'
            self.kernel.run(str(trace), '--no-config', '--timeline', '--no-text-view', '--report-mode', 'sidecar', out=str(destination))
            server = create_server(destination)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 5)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            url = server.url
        else:
            fixture(self.template, report, count)
            if update:
                html = report.read_text(encoding='utf-8')
                match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
                data = json.loads(match[2])
                update(data)
                encoded = json.dumps(data).replace('<', r'\u003c')
                report.write_text(html[:match.start(2)] + encoded + html[match.end(2):], encoding='utf-8')
            url = report.as_uri()
        self.page.goto(url)
        self.page.evaluate("async () => { await fgSwitchView('timeline'); const model = fgView('timeline').probe(); model.dist(model.sName[0]); }")

    def ids(self):
        return self.page.locator('.an-list [data-slice]').evaluate_all('(rows) => rows.map(row => +row.dataset.slice)')

    def status(self):
        return self.page.locator('.an-dist-count').inner_text()

    def next(self):
        self.page.locator('[data-act=dist-next]').click()

    def export(self):
        with self.page.expect_download() as download:
            self.page.locator('[data-act=dist-csv]').click()
        return Path(download.value.path()).read_bytes()

    def rows(self):
        return list(csv.DictReader(io.StringIO(self.export().decode('utf-8-sig'))))

    def test_page_boundaries_and_native_keyboard_endpoint_focus(self):
        for count in (1, 50, 51, 103):
            with self.subTest(count=count):
                self.open_report(count)
                self.assertEqual(self.ids(), list(range(count - 1, max(-1, count - 51), -1)))
                self.assertTrue(self.page.locator('[data-act=dist-prev]').is_disabled())
                self.assertEqual(self.page.locator('[data-act=dist-next]').is_disabled(), count <= 50)
                if count > 50:
                    self.page.locator('[data-act=dist-next]').press('Enter')
                    self.assertEqual(self.ids(), list(range(count - 51, max(-1, count - 101), -1)))
                    if count <= 100:
                        self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'dist-prev')
                    self.page.locator('[data-act=dist-prev]').press('Space')
                    self.assertEqual(self.status(), f'Calls 1–50 of {count} (slowest first)')
                    self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'dist-next')

    def test_all_thousands_of_calls_are_reachable_without_resorting_or_growing_rows(self):
        self.open_report(5207)
        self.page.evaluate("() => { Array.prototype.sort = () => { throw new Error('Paging must reuse prepared ordering'); }; }")
        result = self.page.evaluate("""() => {
            const ids = [], sizes = [];
            while (true) {
                const rows = Array.from(document.querySelectorAll('.an-list [data-slice]'));
                sizes.push(rows.length);
                ids.push(...rows.map(row => +row.dataset.slice));
                const next = document.querySelector('[data-act=dist-next]');
                if (next.disabled) break;
                next.click();
            }
            return {ids, sizes};
        }""")
        self.assertEqual(result['ids'], sorted(range(5207), key=lambda index: (-(1 + index % 999), index)))
        self.assertEqual(result['sizes'], [50] * 104 + [7])
        self.assertIn('5201–5207 of 5207', self.status())

    def test_complete_csv_is_independent_of_pages_and_preserves_formula_escaping(self):
        def update(data):
            data['strings'] = ['=SUM(1,2)', '\tcaller,"\n函数']
        self.open_report(320, update=update)
        before = self.export()
        self.next()
        rows = self.rows()
        self.assertEqual(len(rows), 320)
        self.assertEqual({row['function'] for row in rows}, {"'=SUM(1,2)"})
        self.assertEqual({row['caller'] for row in rows}, {"'\tcaller,\"\n函数"})
        self.assertEqual([int(row['trace_line']) for row in rows], list(range(320, 0, -1)))
        self.assertEqual(self.export(), before)
        self.assertIn('51–100 of 320', self.status())
        self.assertEqual(len(self.ids()), 50)

    def test_bucket_pages_do_not_change_population_statistics_or_export_scope(self):
        def update(data):
            data['cols']['dur'] = [10 ** (index // 60 + 1) for index in range(240)]
        self.open_report(240, update=update)
        statistics = self.page.locator('.an-dist .tl-kv').inner_text()
        last = self.page.locator('[data-act=dist-bucket] option').last.get_attribute('value')
        self.page.locator('[data-act=dist-bucket]').select_option(last)
        self.next()
        self.assertEqual(len(self.ids()), 10)
        self.assertIn('51–60 of 60', self.status())
        rows = self.rows()
        self.assertEqual(len(rows), 60)
        self.assertEqual({float(row['duration_ns']) for row in rows}, {10000})
        self.assertEqual(self.page.locator('.an-dist .tl-kv').inner_text(), statistics)
        self.page.locator('[data-act=dist-reset-bucket]').click()
        self.assertIn('1–50 of 240', self.status())
        self.assertEqual(len(self.rows()), 240)

    def test_site_and_shared_filter_changes_reset_pages_and_keep_empty_scope_reversible(self):
        def update(data):
            data['strings'] += ['caller_b']
            data['cols']['caller'] = [1] * 120 + [2] * 120
        self.open_report(240, update=update)
        self.page.locator('tr[data-site="caller_b"] button').click()
        self.next()
        self.assertIn('51–100 of 120', self.status())
        self.page.evaluate("fgNotifyFilter('first site', new Set(Array.from({length: 120}, (_, index) => index + 1)))")
        self.assertEqual(self.status(), 'No matching calls')
        self.assertEqual(self.ids(), [])
        for action in ('dist-prev', 'dist-next', 'dist-csv'):
            self.assertTrue(self.page.locator(f'[data-act={action}]').is_disabled())
        self.page.locator('[data-act=dist-reset-site]').click()
        self.assertIn('1–50 of 120', self.status())
        self.assertEqual({row['caller'] for row in self.rows()}, {'caller+0x10/0x20'})

    def test_refresh_reopening_and_row_selection_preserve_page_and_keyboard_focus(self):
        self.open_report()
        self.page.locator('[data-act=dist-next]').press('Enter')
        before = self.ids()
        self.page.evaluate("() => { const model = fgView('timeline').probe(); model.dist(model.sName[0]); }")
        self.assertEqual(self.ids(), before)
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'dist-next')
        self.page.locator('.an-list [data-slice]').first.click()
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), before[0])
        self.assertEqual(self.ids(), before)
        self.page.locator('.an-dist-head .tl-close').click()
        self.page.evaluate("() => { const model = fgView('timeline').probe(); model.dist(model.sName[0]); }")
        self.assertEqual(self.ids(), before)

    def test_overhead_refresh_resets_page_but_retains_measured_positions(self):
        def update(data):
            data['cols']['adj'] = [duration / 2 for duration in data['cols']['dur']]
        self.open_report(update=update)
        self.next()
        before = self.rows()
        self.page.locator('[data-act=adj]').click()
        after = self.rows()
        self.assertIn('1–50 of 205', self.status())
        self.assertEqual([(row['start_ns'], row['end_ns']) for row in after], [(row['start_ns'], row['end_ns']) for row in before])
        self.assertEqual([float(row['duration_ns']) for row in after], [float(row['duration_ns']) / 2 for row in before])

    def test_csv_time_bases_remain_truthful_in_each_mode(self):
        for mode in ('abs', 'rel', 'compressed', 'count'):
            with self.subTest(mode=mode):
                def update(data):
                    data['mode'] = mode
                    data['cols']['dur'] = [1000] * 205
                self.open_report(update=update)
                self.next()
                rows = self.rows()
                self.assertEqual(len(rows), 205)
                self.assertEqual({row['time_mode'] for row in rows}, {mode})
                if mode == 'count':
                    self.assertEqual({row['duration_units'] for row in rows}, {'1'})
                    self.assertNotIn('duration_ns', rows[0])
                else:
                    self.assertEqual({row['duration_ns'] for row in rows}, {'1000'})

    def test_touch_paging_and_expansion_both_themes_orientations_and_sidecar(self):
        self.context.close()
        for theme in ('light', 'dark'):
            context = self.browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True)
            self.addCleanup(context.close)
            self.page = context.new_page()
            self.page.on('pageerror', lambda error: self.errors.append(str(error)))
            self.page.add_init_script(f"localStorage.setItem('theme', '{theme}')")
            self.open_report(sidecar=True)
            for width, height in ((390, 844), (844, 390)):
                with self.subTest(theme=theme, width=width):
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.page.wait_for_function("() => document.getElementById('timelineView').classList.contains('tl-compact')")
                    if height < 480:
                        self.page.locator('[data-detail-expand]').tap()
                    for action in ('dist-prev', 'dist-next', 'dist-csv'):
                        self.assertGreaterEqual(self.page.locator(f'[data-act={action}]').bounding_box()['height'], 44)
                    self.page.locator('[data-act=dist-next]').tap()
                    self.assertIn('51–100 of 205', self.status())
                    self.page.locator('[data-act=dist-prev]').tap()
                    self.assertIn('1–50 of 205', self.status())
                    self.assertEqual(len(self.rows()), 205)
                    self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), width)
            context.close()

    def test_pager_stays_inside_the_call_browser_not_over_the_following_sections(self):
        def update(data):
            data['strings'] += ['other_caller']
            data['cols']['caller'] = [1] * 100 + [2] * 105
        self.open_report(update=update)
        self.next()
        pane = self.page.locator('.tl-detail').bounding_box()
        pager = self.page.locator('.an-dist-pager').bounding_box()
        self.assertGreaterEqual(pager['y'], pane['y'])
        self.assertLessEqual(pager['y'] + pager['height'], pane['y'] + pane['height'])
        self.page.locator('.an-sites').scroll_into_view_if_needed()
        sites = self.page.locator('.an-sites').bounding_box()
        pager = self.page.locator('.an-dist-pager').bounding_box()
        self.assertLessEqual(pager['y'] + pager['height'], sites['y'])
