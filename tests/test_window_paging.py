"""Bounded section/window pages retain complete estimates and native navigation."""
import csv
import io
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest

import helpers
from benchmark_windows import fixture
from fglib.viewer import create_server


def text_trace(count):
    lines = ['# tracer: function_graph\n']
    for index in range(count):
        start = 1000000 + index * 4000
        for offset, flags, name in ((0, 'dN.2.', 'active'), (2000, '.....', 'quiet')):
            lines.append(f'  {(start + offset) / 1000000000:.9f} |   0) demo-1 |  {flags} |   1.000 us    |  {name}();\n')
    return ''.join(lines)


class WindowPagingTest(unittest.TestCase):
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
        cls.kernel.run(helpers.fixture('pi4_sched'), '--no-config', '--timeline')
        cls.template = Path(cls.kernel.dir) / 'out.html'

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='fg-window-page-')
        self.addCleanup(self.directory.cleanup)
        self.serial = 0
        self.errors = []
        self.context = self.browser.new_context(viewport={'width': 1500, 'height': 900})
        self.addCleanup(self.context.close)
        self.page = None

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open_report(self, count=650, panel='sections', update=None, sidecar=False):
        self.serial += 1
        if self.page and not self.page.is_closed():
            self.page.close()
        self.page = self.context.new_page()
        self.page.set_default_timeout(10000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        if sidecar:
            trace = Path(self.directory.name) / f'window-{self.serial}.txt'
            trace.write_text(text_trace(count), encoding='utf-8')
            destination = Path(self.directory.name) / f'sidecar-{self.serial}'
            self.kernel.run(str(trace), '--no-config', '--timeline', '--report-mode', 'sidecar', out=str(destination))
            server = create_server(destination)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 5)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            url = server.url
        else:
            report = Path(self.directory.name) / f'window-{self.serial}.html'
            fixture(self.template, report, count)
            if update:
                html = report.read_text(encoding='utf-8')
                match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
                data = json.loads(match[2])
                update(data)
                report.write_text(html[:match.start(2)] + json.dumps(data).replace('<', r'\u003c') + html[match.end(2):], encoding='utf-8')
            url = report.as_uri()
        self.page.goto(url)
        self.page.evaluate("async panel => { await fgSwitchView('timeline'); fgView('timeline').probe()[panel](); }", panel)

    def ids(self):
        return self.page.locator('tr[data-sec], tr[data-win]').evaluate_all('(rows) => rows.map(row => row.dataset.sec ?? row.dataset.win)')

    def status(self):
        return self.page.locator('.an-window-footer [role=status]').inner_text()

    def export(self):
        with self.page.expect_download() as download:
            self.page.locator('[data-act=window-csv]').click()
        return Path(download.value.path()).read_bytes()

    def rows(self):
        return list(csv.DictReader(io.StringIO(self.export().decode('utf-8-sig'))))

    def next(self):
        self.page.locator('[data-act=window-next]').click()

    def test_both_panel_boundaries_native_keyboard_and_endpoint_focus(self):
        for panel, size in (('sections', 200), ('latency', 300)):
            for count in (1, size, size + 1, size * 2, size * 2 + 1):
                with self.subTest(panel=panel, count=count):
                    self.open_report(count, panel)
                    first = self.ids()
                    self.assertEqual(len(first), min(size, count))
                    self.assertIn(f'1–{min(size, count)} of {count}', self.status())
                    if count <= size:
                        self.assertEqual(self.page.locator('[data-act=window-prev], [data-act=window-next]').count(), 0)
                        continue
                    original = self.page.locator('tr[data-sec], tr[data-win]').first.element_handle()
                    self.assertTrue(self.page.locator('[data-act=window-prev]').is_disabled())
                    self.page.locator('[data-act=window-next]').press('Enter')
                    self.assertFalse(original.evaluate('element => element.isConnected'))
                    self.assertEqual(len(self.ids()), min(size, count - size))
                    if count <= size * 2:
                        self.assertTrue(self.page.locator('[data-act=window-next]').is_disabled())
                        self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'window-prev')
                    self.page.locator('[data-act=window-prev]').press('Space')
                    self.assertEqual(self.ids(), first)
                    self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'window-next')

    def test_all_thousands_of_records_are_reachable_without_growing_dom_or_resorting_cached_profiles(self):
        for panel, size in (('sections', 200), ('latency', 300)):
            with self.subTest(panel=panel):
                self.open_report(5003, panel)
                exported = self.rows()
                result = self.page.evaluate('''() => {
                    const original = Array.prototype.sort;
                    Array.prototype.sort = () => { throw new Error('Prepared order and profiles must be reused'); };
                    try {
                        const ids = [], sizes = [];
                        while (true) {
                            const rows = [...document.querySelectorAll('tr[data-sec], tr[data-win]')];
                            sizes.push(rows.length);
                            ids.push(...rows.map(row => row.dataset.sec ?? row.dataset.win));
                            const next = document.querySelector('[data-act=window-next]');
                            if (next.disabled) break;
                            next.click();
                        }
                        return {ids, sizes};
                    } finally { Array.prototype.sort = original; }
                }''')
                expected = [row['section_index'] if panel == 'sections' else f"{row['start_ns']},{row['end_ns']},0" for row in exported]
                self.assertEqual(result['ids'], expected)
                self.assertEqual(result['sizes'], [size] * (5003 // size) + [5003 % size])
                self.assertEqual(len(set(result['ids'])), 5003)

    def test_full_csv_statistics_and_row_order_remain_page_independent(self):
        def update(data):
            data['strings'][0] = '=SUM(1,2)'
            data['tasks'][0]['comm'] = '\tquoted,"\n任务'
        for panel in ('sections', 'latency'):
            with self.subTest(panel=panel):
                self.open_report(panel=panel, update=update)
                original, heading = self.export(), self.page.locator('.an-window-head').inner_text()
                while self.page.locator('[data-act=window-next]').is_enabled():
                    self.next()
                self.assertEqual(self.export(), original)
                self.assertEqual(self.page.locator('.an-window-head').inner_text(), heading)
                rows = self.rows()
                self.assertEqual(len(rows), 650)
                row = next(row for row in rows if row['top_1_function'] == "'=SUM(1,2)")
                self.assertIn('任务', row['task'])
                self.page.locator('[data-act=window-prev]').click()
                self.assertEqual(self.export(), original)

    def test_all_three_section_kinds_keep_original_indices_and_independent_pages(self):
        self.open_report(403)
        for kind, label in enumerate(('irqs off', 'preempt off', 'BH off')):
            with self.subTest(kind=kind):
                self.page.locator(f'[data-sk="{kind}"]').click()
                self.assertIn('1–200 of 403', self.status())
                rows = self.rows()
                self.assertEqual({int(row['section_index']) for row in rows}, set(range(kind * 403, (kind + 1) * 403)))
                self.assertEqual({row['kind'] for row in rows}, {label})
                self.next()
                self.next()
                self.assertEqual(len(self.ids()), 3)
        for kind in range(3):
            self.page.locator(f'[data-sk="{kind}"]').click()
            self.assertIn('401–403 of 403', self.status())

    def test_panel_and_kind_pages_survive_reopen_refresh_and_text_roundtrips(self):
        self.open_report()
        self.next()
        first_kind = self.ids(), self.status(), self.export()
        self.page.locator('[data-act=window-next]').focus()
        self.page.evaluate("fgView('timeline').probe().sections()")
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'window-next')
        self.page.locator('[data-sk="1"]').press('Enter')
        self.assertIn('1–200 of 650', self.status())
        self.next()
        self.next()
        second_kind = self.ids(), self.status(), self.export()
        self.page.locator('[data-sk="0"]').press('Space')
        self.assertEqual((self.ids(), self.status(), self.export()), first_kind)
        self.page.locator('[data-sk="1"]').press('Enter')
        self.assertEqual((self.ids(), self.status(), self.export()), second_kind)
        self.page.evaluate("fgView('timeline').probe().latency()")
        self.next()
        latency = self.ids(), self.status(), self.export()
        self.page.evaluate("fgView('timeline').probe().sections()")
        self.assertEqual((self.ids(), self.status(), self.export()), second_kind)
        self.page.evaluate("async () => { await fgSwitchView('text'); }")
        self.assertEqual(self.page.evaluate('document.body.dataset.view'), 'text')
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.assertEqual((self.ids(), self.status()), second_kind[:2])
        self.page.locator('.tl-close').click()
        self.page.evaluate("fgView('timeline').probe().latency()")
        self.assertEqual((self.ids(), self.status()), latency[:2])

    def test_sort_and_shared_filter_changes_reset_pages_and_keep_empty_state_reversible(self):
        for panel, attribute, size in (('sections', 'ssort', 200), ('latency', 'lsort', 300)):
            with self.subTest(panel=panel):
                self.open_report(panel=panel)
                self.next()
                self.page.locator(f'th[data-{attribute}=start] button').press('Enter')
                self.assertIn(f'1–{size} of 650', self.status())
                self.assertTrue(self.page.locator(f'th[data-{attribute}=start] button').evaluate('element => element === document.activeElement'))
                self.next()
                self.page.evaluate("fgNotifyFilter('first 301', new Set(Array.from({length: 301}, (_, index) => index + 1)))")
                self.assertIn(f'1–{size} of 301', self.status())
                self.next()
                self.assertEqual(len(self.rows()), 301)
                self.page.evaluate("fgNotifyFilter('empty', new Set())")
                self.assertEqual(self.ids(), [])
                self.assertTrue(self.page.locator('[data-act=window-csv]').is_disabled())
                self.assertEqual(self.page.locator('.an-window-footer').count(), 0)
                self.assertIn('matching the current filter', self.page.locator('.tl-detail').inner_text())
                self.page.evaluate("fgNotifyFilter('', null)")
                self.assertIn(f'1–{size} of 650', self.status())

    def test_overhead_resets_pages_and_changes_only_self_estimates_not_window_geometry(self):
        def update(data):
            data['cols']['adj'] = [duration / 2 for duration in data['cols']['dur']]
        for panel, size in (('sections', 200), ('latency', 300)):
            with self.subTest(panel=panel):
                self.open_report(panel=panel, update=update)
                before = self.rows()
                self.next()
                helpers.click_timeline_tool(self.page, '[data-act=adj]')
                self.assertIn(f'1–{size} of 650', self.status())
                after = self.rows()
                for raw, adjusted in zip(before, after):
                    for column in ('start_ns', 'end_ns', 'window_duration_ns', 'max_preempt_depth'):
                        self.assertEqual(raw[column], adjusted[column])
                    self.assertEqual(float(adjusted['top_1_self_estimate_ns']), float(raw['top_1_self_estimate_ns']) / 2)
                    self.assertEqual(adjusted['self_duration_basis'], 'overhead_subtracted_estimate')
                    self.assertEqual(adjusted['window_duration_basis'], 'timestamp_span')

    def test_all_four_parser_modes_retain_truthful_csv_bases_during_paging(self):
        for mode in ('abs', 'rel', 'compressed', 'count'):
            for panel in ('sections', 'latency'):
                with self.subTest(mode=mode, panel=panel):
                    self.open_report(panel=panel, update=lambda data: data.update(mode=mode))
                    before = self.export()
                    self.next()
                    self.assertEqual(self.export(), before)
                    rows = self.rows()
                    self.assertEqual({row['time_mode'] for row in rows}, {mode})
                    self.assertEqual({row['window_duration_basis'] for row in rows},
                                     {'timestamp_span' if mode in ('abs', 'rel') else 'call_order' if mode == 'count' else 'packed_estimate'})
                    unit = 'units' if mode == 'count' else 'ns'
                    self.assertIn('window_duration_' + unit, rows[0])
                    if mode in ('compressed', 'count'):
                        self.assertIn('not measured scheduling latency', self.page.locator('.tl-detail').inner_text())

    def test_row_navigation_does_not_replace_page_or_narrow_complete_csv(self):
        for panel in ('sections', 'latency'):
            with self.subTest(panel=panel):
                self.open_report(panel=panel)
                self.next()
                before = self.ids(), self.status(), self.export()
                self.page.locator('tr[data-sec], tr[data-win]').first.click()
                self.assertEqual((self.ids(), self.status(), self.export()), before)
                view = self.page.evaluate("fgView('timeline').probe().view()")
                self.page.locator('[data-act=window-prev]').click()
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().view()"), view)

    def test_real_text_parser_and_cli_sidecar_produce_reversible_pages(self):
        for panel in ('sections', 'latency'):
            with self.subTest(panel=panel):
                self.open_report(301, panel, sidecar=True)
                self.assertEqual(len(self.rows()), 301)
                original = self.export()
                first = self.ids()
                self.next()
                self.assertEqual(len(self.ids()), 101 if panel == 'sections' else 1)
                self.assertEqual(self.export(), original)
                self.page.locator('[data-act=window-prev]').press('Space')
                self.assertEqual(self.ids(), first)

    def test_touch_layout_and_horizontal_controls_in_both_themes_and_orientations(self):
        self.context.close()
        for theme in ('light', 'dark'):
            self.context = self.browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True)
            self.addCleanup(self.context.close)
            self.context.add_init_script(f"localStorage.setItem('theme', '{theme}')")
            for panel in ('sections', 'latency'):
                self.open_report(601, panel, sidecar=True)
                for width, height in ((390, 844), (320, 700), (844, 390)):
                    with self.subTest(theme=theme, panel=panel, width=width):
                        self.page.set_viewport_size({'width': width, 'height': height})
                        self.page.wait_for_function("() => document.getElementById('timelineView').classList.contains('tl-compact')")
                        if height < 480:
                            self.page.locator('[data-detail-expand]').tap()
                        self.assertEqual(self.page.locator('.an-window-head').evaluate('element => getComputedStyle(element).top'), 'auto')
                        self.page.evaluate("document.querySelector('.tl-detail').scrollLeft = 1e6")
                        for selector in ('[data-act=window-csv]', '.tl-close'):
                            bounds = self.page.locator(selector).bounding_box()
                            self.assertGreaterEqual(bounds['x'], 0)
                            self.assertLessEqual(bounds['x'] + bounds['width'], width)
                        self.page.locator('[data-act=window-next]').tap()
                        for selector in ('[data-act=window-prev]', '[data-act=window-next]'):
                            button = self.page.locator(selector)
                            button.scroll_into_view_if_needed()
                            bounds = button.bounding_box()
                            self.assertGreaterEqual(bounds['height'], 44)
                            self.assertGreaterEqual(bounds['width'], 44)
                            self.assertGreaterEqual(bounds['x'], 0)
                            self.assertLessEqual(bounds['x'] + bounds['width'], width)
                        self.page.locator('[data-act=window-prev]').tap()
                        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
                        if height < 480:
                            self.page.locator('[data-detail-expand]').tap()
                            self.assertEqual(self.page.locator('.an-window-footer').evaluate('element => getComputedStyle(element).bottom'), 'auto')


if __name__ == '__main__':
    unittest.main()
