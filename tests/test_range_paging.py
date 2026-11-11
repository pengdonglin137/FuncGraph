"""Range pages stay bounded without changing aggregation, filtering or full CSV."""
import csv
import io
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest

import helpers
from benchmark_range import fixture
from fglib.viewer import create_server
from test_uftrace_import import call, stream


class RangePagingTest(unittest.TestCase):
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
        cls.kernel.run(str(trace), '--no-config', '--timeline')
        cls.template = Path(cls.kernel.dir) / 'out.html'

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='fg-range-page-')
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

    def open_report(self, count=650, update=None, sidecar=False):
        self.serial += 1
        if sidecar:
            records = []
            for index in range(count):
                name, start = f'function_{index:06d}', index * 1000
                records.extend([call('entry', 'user', start, 0, name, 1),
                                call('entry', 'kernel', start + 10, 0, name, 2),
                                call('exit', 'kernel', start + 20, 0, name, 2),
                                call('exit', 'user', start + 30, 0, name, 1)])
            trace = Path(self.directory.name) / f'mixed-{self.serial}.jsonl'
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
            report = Path(self.directory.name) / f'range-{self.serial}.html'
            fixture(self.template, report, count)
            if update:
                html = report.read_text(encoding='utf-8')
                match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
                data = json.loads(match[2])
                update(data)
                report.write_text(html[:match.start(2)] + json.dumps(data).replace('<', r'\u003c') + html[match.end(2):], encoding='utf-8')
            url = report.as_uri()
        self.page.goto(url)
        self.page.evaluate("async end => { await fgSwitchView('timeline'); fgView('timeline').probe().range(0, end); }", count * 1000)

    def names(self):
        return self.page.locator('.tl-range-table tbody tr[data-name]').evaluate_all('(rows) => rows.map(row => row.dataset.name)')

    def status(self):
        return self.page.locator('.tl-range-footer [role=status]').inner_text()

    def next(self):
        self.page.locator('[data-act=range-next]').click()

    def export(self):
        with self.page.expect_download() as download:
            self.page.locator('[data-act=range-csv]').click()
        return Path(download.value.path()).read_bytes()

    def rows(self):
        return list(csv.DictReader(io.StringIO(self.export().decode('utf-8-sig'))))

    def test_boundaries_reversible_native_keyboard_and_endpoint_focus(self):
        for count in (1, 300, 301, 600, 601):
            with self.subTest(count=count):
                self.open_report(count)
                original = self.names()
                self.assertEqual(len(original), min(300, count))
                self.assertIn(f'1–{min(300, count)} of {count}', self.status())
                if count <= 300:
                    self.assertEqual(self.page.locator('[data-range-page]').count(), 0)
                    continue
                self.assertTrue(self.page.locator('[data-act=range-prev]').is_disabled())
                first = self.page.locator('.tl-range-table tbody tr').first.element_handle()
                self.page.locator('[data-act=range-next]').press('Enter')
                self.assertFalse(first.evaluate('element => element.isConnected'))
                self.assertEqual(len(self.names()), min(300, count - 300))
                if count <= 600:
                    self.assertTrue(self.page.locator('[data-act=range-next]').is_disabled())
                    self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'range-prev')
                self.page.locator('[data-act=range-prev]').press('Space')
                self.assertEqual(self.names(), original)
                self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'range-next')

    def test_all_functions_reachable_without_reaggregation_resorting_or_dom_growth(self):
        self.open_report(10003)
        result = self.page.evaluate('''() => {
            const originalSort = Array.prototype.sort, originalSet = Map.prototype.set;
            Array.prototype.sort = () => { throw new Error('Paging must not sort'); };
            Map.prototype.set = function(key, value) {
                if (value && typeof value.calls === 'number' && typeof value.incl === 'number') throw new Error('Paging must not aggregate');
                return originalSet.call(this, key, value);
            };
            try {
                const names = [], sizes = [];
                while (true) {
                    const rows = Array.from(document.querySelectorAll('.tl-range-table tbody tr[data-name]'));
                    sizes.push(rows.length);
                    names.push(...rows.map(row => row.dataset.name));
                    const next = document.querySelector('[data-act=range-next]');
                    if (next.disabled) break;
                    next.click();
                }
                return {names, sizes};
            } finally { Array.prototype.sort = originalSort; Map.prototype.set = originalSet; }
        }''')
        expected = sorted(range(10003), key=lambda index: (-(1 + index % 999), index))
        self.assertEqual(result['names'], [f'function_{index:06d}' for index in expected])
        self.assertEqual(result['sizes'], [300] * 33 + [103])
        self.assertIn('9901–10003 of 10003', self.status())

    def test_csv_stays_complete_and_byte_identical_on_every_page_with_safe_names(self):
        names = ['=SUM(1,2)', '+cmd', '-cmd', '@cmd', '\tcmd', ' quoted,"\n函数']
        def update(data):
            data['strings'][:len(names)] = names
        self.open_report(update=update)
        self.page.locator('th[data-sort=name] button').click()
        before = self.export()
        expected = sorted(names + [f'function_{index:06d}' for index in range(6, 650)])
        self.assertEqual([row['function'] for row in self.rows()], ["'" + name if name in names[:5] else name for name in expected])
        self.next()
        self.assertEqual(self.export(), before)
        self.next()
        self.assertEqual(self.export(), before)
        self.assertEqual(len(self.names()), 50)
        self.page.locator('[data-act=range-prev]').click()
        self.assertEqual(self.export(), before)

    def test_reopening_refresh_and_text_roundtrip_preserve_page_but_sort_resets(self):
        self.open_report()
        self.next()
        original = self.names(), self.status(), self.export()
        self.page.evaluate("fgView('timeline').probe().range(0, 650000)")
        self.assertEqual((self.names(), self.status(), self.export()), original)
        self.page.evaluate("async () => { await fgSwitchView('text'); }")
        self.assertEqual(self.page.evaluate('document.body.dataset.view'), 'text')
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.assertEqual((self.names(), self.status(), self.export()), original)
        self.page.locator('.tl-close').click()
        self.page.evaluate("fgView('timeline').probe().range(0, 650000)")
        self.assertEqual((self.names(), self.status(), self.export()), original)
        self.page.locator('th[data-sort=name] button').press('Enter')
        self.assertEqual(self.names()[0], 'function_000000')
        self.assertIn('1–300 of 650', self.status())
        self.assertTrue(self.page.locator('th[data-sort=name] button').evaluate('element => element === document.activeElement'))

    def test_changed_range_track_filter_and_overhead_reset_pages_not_export_scope(self):
        def update(data):
            data['cols']['adj'] = [duration / 2 for duration in data['cols']['dur']]
        self.open_report(update=update)
        self.next()
        self.page.evaluate("fgView('timeline').probe().range(0, 649000)")
        self.assertIn('1–300 of 649', self.status())
        self.next()
        self.page.evaluate("fgView('timeline').probe().range(0, 649000, 0)")
        self.assertIn('1–300 of 649', self.status())
        self.next()
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        self.assertIn('1–300 of 649', self.status())
        self.assertEqual({row['duration_basis'] for row in self.rows()}, {'overhead_subtracted_estimate'})
        self.next()
        self.page.evaluate("fgNotifyFilter('first 301', new Set(Array.from({length: 301}, (_, index) => index + 1)))")
        self.assertIn('1–300 of 301', self.status())
        self.next()
        self.assertEqual(len(self.names()), 1)
        self.assertEqual(len(self.rows()), 301)
        self.page.evaluate("fgNotifyFilter('empty', new Set())")
        self.assertEqual(self.names(), [])
        self.assertEqual(self.status(), 'Showing 0 of 0 functions')
        self.assertTrue(self.page.locator('[data-act=range-csv]').is_disabled())
        self.assertEqual(self.page.locator('[data-range-page]').count(), 0)
        self.page.evaluate("fgNotifyFilter('', null)")
        self.assertIn('1–300 of 649', self.status())

    def test_all_four_time_modes_keep_complete_csv_bytes_during_paging(self):
        for mode in ('abs', 'rel', 'compressed', 'count'):
            with self.subTest(mode=mode):
                self.page.close()
                self.page = self.context.new_page()
                self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                self.open_report(update=lambda data: data.update(mode=mode))
                before = self.export()
                self.next()
                self.assertEqual(self.export(), before)
                rows = self.rows()
                self.assertEqual(len(rows), 650)
                self.assertEqual({row['time_mode'] for row in rows}, {mode})
                unit = 'units' if mode == 'count' else 'ns'
                self.assertEqual(rows[0]['range_end_' + unit], '650' if mode == 'count' else '650000')

    def test_paging_aligns_first_row_above_the_pinned_footer(self):
        self.open_report()
        self.next()
        geometry = self.page.evaluate('''() => {
            const panel = document.querySelector('.tl-detail').getBoundingClientRect();
            const head = document.querySelector('.tl-range-head').getBoundingClientRect();
            const footer = document.querySelector('.tl-range-footer').getBoundingClientRect();
            const row = document.querySelector('.tl-range-table tbody tr').getBoundingClientRect();
            const next = document.querySelector('[data-act=range-next]'), button = next.getBoundingClientRect();
            return {panel: panel.toJSON(), head: head.toJSON(), footer: footer.toJSON(), row: row.toJSON(),
                position: getComputedStyle(document.querySelector('.tl-range-footer')).position,
                hit: document.elementFromPoint(button.x + button.width / 2, button.y + button.height / 2) === next};
        }''')
        self.assertEqual(geometry['position'], 'sticky')
        self.assertTrue(geometry['hit'])
        self.assertGreaterEqual(geometry['row']['top'], geometry['head']['bottom'])
        self.assertLessEqual(geometry['row']['bottom'], geometry['footer']['top'])
        self.assertLessEqual(geometry['footer']['bottom'], geometry['panel']['bottom'])

    def test_row_search_keeps_page_and_range_without_selecting_a_call(self):
        self.open_report()
        self.next()
        before = self.names(), self.status(), self.export()
        row = self.page.locator('.tl-range-table tbody tr[data-name]').first
        name = row.get_attribute('data-name')
        row.click()
        self.assertEqual(self.page.locator('.tl-search').input_value(), '^' + name + '$')
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), -1)
        self.assertEqual((self.names(), self.status(), self.export()), before)

    def test_same_name_user_and_kernel_functions_stay_separate_in_real_sidecar(self):
        self.open_report(301, sidecar=True)
        before = self.export()
        rows = self.rows()
        self.assertEqual(len(rows), 602)
        self.assertEqual({(row['function'], row['space']) for row in rows},
                         {(f'function_{index:06d}', space) for index in range(301) for space in ('user', 'kernel')})
        self.next()
        self.assertEqual(self.export(), before)
        self.next()
        self.assertEqual(len(self.names()), 2)
        self.assertEqual(self.export(), before)
        self.assertEqual(self.page.locator('.tl-stack-space').all_text_contents(), ['K', 'K'])

    def test_touch_themes_orientations_sticky_fallback_and_reachable_controls(self):
        self.context.close()
        for theme in ('light', 'dark'):
            context = self.browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True)
            self.addCleanup(context.close)
            self.page = context.new_page()
            self.page.on('pageerror', lambda error: self.errors.append(str(error)))
            self.page.add_init_script(f"localStorage.setItem('theme', '{theme}')")
            self.open_report(301, sidecar=True)
            for width, height in ((390, 844), (320, 700), (844, 390)):
                with self.subTest(theme=theme, width=width):
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.page.wait_for_function("() => document.getElementById('timelineView').classList.contains('tl-compact')")
                    if height < 480:
                        self.page.locator('[data-detail-expand]').tap()
                    self.assertEqual(self.page.locator('.tl-range-head').evaluate('element => getComputedStyle(element).top'), 'auto')
                    self.page.evaluate("document.querySelector('.tl-detail').scrollLeft = 1e6")
                    for selector in ('[data-act=range-csv]', '.tl-close'):
                        bounds = self.page.locator(selector).bounding_box()
                        self.assertGreaterEqual(bounds['x'], 0)
                        self.assertLessEqual(bounds['x'] + bounds['width'], width)
                    self.page.locator('[data-act=range-next]').tap()
                    for selector in ('[data-act=range-prev]', '[data-act=range-next]'):
                        control = self.page.locator(selector)
                        control.scroll_into_view_if_needed()
                        bounds = control.bounding_box()
                        self.assertGreaterEqual(bounds['height'], 44)
                        self.assertGreaterEqual(bounds['width'], 44)
                        self.assertGreaterEqual(bounds['x'], 0)
                        self.assertLessEqual(bounds['x'] + bounds['width'], width)
                    self.page.locator('[data-act=range-prev]').tap()
                    self.assertIn('1–300 of 602', self.status())
                    self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
                    if height < 480:
                        self.page.locator('[data-detail-expand]').tap()
                        self.assertEqual(self.page.locator('.tl-range-footer').evaluate('element => getComputedStyle(element).bottom'), 'auto')


if __name__ == '__main__':
    unittest.main()
