"""Bounded error/atomic pages preserve complete diagnostics and occurrence state."""
import csv
import io
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest

import helpers
from benchmark_errors import fixture
from fglib.viewer import create_server


class ErrorPagingTest(unittest.TestCase):
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
        self.directory = tempfile.TemporaryDirectory(prefix='fg-error-page-')
        self.addCleanup(self.directory.cleanup)
        self.serial = 0
        self.errors = []
        self.context = self.browser.new_context(viewport={'width': 1500, 'height': 900})
        self.addCleanup(self.context.close)
        self.page = None

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open_report(self, count=650, atomic_count=None, update=None):
        self.serial += 1
        if self.page and not self.page.is_closed():
            self.page.close()
        self.page = self.context.new_page()
        self.page.set_default_timeout(10000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        report = Path(self.directory.name) / f'errors-{self.serial}.html'
        fixture(self.template, report, count, atomic_count)
        if update:
            html = report.read_text(encoding='utf-8')
            match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
            data = json.loads(match[2])
            update(data)
            report.write_text(html[:match.start(2)] + json.dumps(data).replace('<', r'\u003c') + html[match.end(2):], encoding='utf-8')
        self.page.goto(report.as_uri())
        self.page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().errors(); }")

    def ids(self, atomic=False):
        return self.page.locator('tr[data-atomic-group]' if atomic else 'tr[data-chains]').evaluate_all(
            'rows => rows.map(row => row.dataset.atomicGroup ?? row.dataset.chains)')

    def status(self, atomic=False):
        return self.page.locator('[data-error-footer="' + ('atomic' if atomic else 'errors') + '"] [role=status]').inner_text()

    def button(self, atomic=False, direction='next'):
        return self.page.locator('[data-act="' + ('atomic' if atomic else 'errors') + '-' + direction + '"]')

    def export(self, kind='groups'):
        selector = {'groups': 'errors-csv', 'occurrences': 'error-occurrences-csv', 'atomic': 'atomic-csv'}[kind]
        with self.page.expect_download() as download:
            self.page.locator('[data-act="' + selector + '"]').click()
        return Path(download.value.path()).read_bytes()

    def rows(self, kind='groups'):
        return list(csv.DictReader(io.StringIO(self.export(kind).decode('utf-8-sig'))))

    def test_independent_pages_replace_rows_and_preserve_complete_exports(self):
        self.open_report()
        original = {kind: self.export(kind) for kind in ('groups', 'occurrences', 'atomic')}
        first = self.ids(), self.ids(True)
        first_row = self.page.locator('tr[data-chains]').first.element_handle()
        self.button().press('Enter')
        self.assertEqual(len(self.ids()), 200)
        self.assertFalse(first_row.evaluate('row => row.isConnected'))
        self.assertEqual(self.ids(True), first[1])
        self.assertIn('201–400 of 650 error groups', self.status())
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'errors-next')
        error_page = self.ids()
        self.button(True).press('Space')
        self.assertEqual(self.ids(), error_page)
        self.assertEqual(len(self.ids(True)), 100)
        self.assertIn('101–200 of 650 atomic groups', self.status(True))
        for kind, content in original.items():
            self.assertEqual(self.export(kind), content)
        self.button(direction='prev').click()
        self.button(True, 'prev').click()
        self.assertEqual((self.ids(), self.ids(True)), first)

    def test_exact_boundaries_and_keyboard_endpoint_focus(self):
        for atomic, size in ((False, 200), (True, 100)):
            for count in (1, size, size + 1, size * 2, size * 2 + 1):
                with self.subTest(atomic=atomic, count=count):
                    self.open_report(count)
                    first = self.ids(atomic)
                    self.assertEqual(len(first), min(count, size))
                    if count <= size:
                        self.assertEqual(self.button(atomic).count(), 0)
                        self.assertEqual(self.button(atomic, 'prev').count(), 0)
                        continue
                    self.assertTrue(self.button(atomic, 'prev').is_disabled())
                    while self.button(atomic).is_enabled():
                        self.button(atomic).press('Enter')
                    self.assertEqual(len(self.ids(atomic)), (count - 1) % size + 1)
                    prefix = 'atomic' if atomic else 'errors'
                    self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), prefix + '-prev')
                    while self.button(atomic, 'prev').is_enabled():
                        self.button(atomic, 'prev').press('Space')
                    self.assertEqual(self.ids(atomic), first)
                    self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), prefix + '-next')

    def test_thousands_of_groups_remain_reachable_without_dom_growth_or_reaggregation(self):
        self.open_report(5003)
        for atomic, size in ((False, 200), (True, 100)):
            with self.subTest(atomic=atomic):
                expected = [row['group_id'] for row in self.rows('atomic' if atomic else 'groups')]
                result = self.page.evaluate('''atomic => {
                    const original = Array.prototype.sort;
                    Array.prototype.sort = () => { throw new Error('Prepared groups and order must be reused'); };
                    try {
                        const ids = [], sizes = [], kind = atomic ? 'atomic' : 'errors';
                        while (true) {
                            const rows = [...document.querySelectorAll(atomic ? 'tr[data-atomic-group]' : 'tr[data-chains]')];
                            sizes.push(rows.length);
                            ids.push(...rows.map(row => row.dataset.atomicGroup ?? row.dataset.chains));
                            const next = document.querySelector('[data-act="' + kind + '-next"]');
                            if (next.disabled) break;
                            next.click();
                        }
                        return {ids, sizes};
                    } finally { Array.prototype.sort = original; }
                }''', atomic)
                self.assertEqual(result['ids'], expected)
                self.assertEqual(result['sizes'], [size] * (5003 // size) + [5003 % size])
                self.assertEqual(len(set(result['ids'])), 5003)

    def test_occurrence_positions_survive_page_replacement_reopen_and_text_roundtrips(self):
        def update(data):
            data['cols']['name'][:75] = [0] * 75
            for index in range(75):
                data['chains'][index][2] = 2
                data['atomic'][index][1] = 1
        self.open_report(725, update=update)
        for atomic in (False, True):
            selector = 'tr[data-atomic-group]' if atomic else 'tr[data-chains]'
            row = self.page.locator(selector).first
            row.locator('[data-error-step="-1"]').press('Enter')
            self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 74)
            self.page.evaluate('''selector => {
                const row = document.querySelector(selector);
                for (let index = 0; index < 52; index++) row.click();
            }''', selector)
            self.assertEqual(row.locator('[data-error-position]').inner_text(), '52 / 75')
            self.button(atomic).click()
            self.button(atomic, 'prev').click()
            self.assertEqual(self.page.locator(selector).first.locator('[data-error-position]').inner_text(), '52 / 75')
        self.button().click()
        self.button(True).click()
        before = self.ids(), self.ids(True), self.status(), self.status(True)
        self.button().focus()
        self.page.evaluate("fgView('timeline').probe().errors()")
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'errors-next')
        self.page.locator('.tl-close').click()
        self.page.evaluate("fgView('timeline').probe().errors()")
        self.assertEqual((self.ids(), self.ids(True), self.status(), self.status(True)), before)
        self.page.evaluate("async () => { await fgSwitchView('text'); await fgSwitchView('timeline'); }")
        self.assertEqual((self.ids(), self.ids(True), self.status(), self.status(True)), before)
        for atomic in (False, True):
            self.button(atomic, 'prev').click()
            selector = 'tr[data-atomic-group]' if atomic else 'tr[data-chains]'
            self.assertEqual(self.page.locator(selector).first.locator('[data-error-position]').inner_text(), '52 / 75')

    def test_sort_minor_and_shared_filter_invalidation_keep_independent_scope(self):
        def update(data):
            data['chains'][-1][6] = 1
        self.open_report(update=update)
        self.button().click()
        self.button(True).click()
        atomic = self.ids(True), self.status(True)
        self.page.locator('th[data-esort=origin] button').press('Enter')
        self.assertIn('1–200 of 649', self.status())
        self.assertEqual((self.ids(True), self.status(True)), atomic)
        self.assertTrue(self.page.locator('th[data-esort=origin] button').evaluate('node => node === document.activeElement'))
        self.button().click()
        self.page.locator('[data-act=minor]').press('Space')
        self.assertIn('1–200 of 650', self.status())
        self.assertEqual((self.ids(True), self.status(True)), atomic)
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'minor')
        self.page.evaluate("fgNotifyFilter('last call', new Set([650]))")
        self.assertEqual(len(self.ids()), 1)
        self.assertEqual(len(self.ids(True)), 1)
        self.assertEqual(self.button().count(), 0)
        self.assertEqual(self.button(True).count(), 0)
        self.page.evaluate("fgNotifyFilter('', null)")
        self.assertIn('1–200 of 650', self.status())
        self.assertIn('1–100 of 650', self.status(True))

    def test_overhead_retains_pages_positions_and_original_geometry(self):
        self.open_report()
        self.button().click()
        self.button(True).click()
        before = self.ids(), self.ids(True), self.status(), self.status(True)
        raw = {kind: self.rows(kind) for kind in ('groups', 'occurrences', 'atomic')}
        self.page.locator('[data-act=adj]').click()
        self.assertEqual((self.ids(), self.ids(True), self.status(), self.status(True)), before)
        for kind in ('occurrences', 'atomic'):
            adjusted = self.rows(kind)
            duration = 'origin_duration_ns' if kind == 'occurrences' else 'duration_ns'
            start = 'origin_start_ns' if kind == 'occurrences' else 'start_ns'
            end = 'origin_end_ns' if kind == 'occurrences' else 'end_ns'
            self.assertEqual(len(adjusted), 650)
            for original, changed in zip(raw[kind], adjusted):
                self.assertEqual((original[start], original[end]), (changed[start], changed[end]))
                self.assertEqual(float(changed[duration]), float(original[duration]) / 2)
                self.assertEqual(changed['duration_basis'], 'overhead_subtracted_estimate')
        self.assertEqual(self.rows(), raw['groups'])

    def test_full_csv_quoting_and_canonical_modes_do_not_depend_on_page(self):
        def update(data):
            data['strings'][0] = '=SUM(1,2)'
            data['tasks'][0]['comm'] = '\tquoted,"\n任务'
        for mode in ('abs', 'rel', 'compressed', 'count'):
            with self.subTest(mode=mode):
                def configured(data):
                    update(data)
                    data['mode'] = mode
                self.open_report(405, update=configured)
                original = {kind: self.export(kind) for kind in ('groups', 'occurrences', 'atomic')}
                self.button().click()
                self.button(True).click()
                for kind, content in original.items():
                    self.assertEqual(self.export(kind), content)
                    rows = list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
                    field = 'function' if kind == 'atomic' else 'origin'
                    self.assertEqual(rows[0][field], "'=SUM(1,2)")
                    if kind != 'groups':
                        self.assertEqual({row['time_mode'] for row in rows}, {mode})
                        unit = 'units' if mode == 'count' else 'ns'
                        self.assertIn('origin_start_' + unit if kind == 'occurrences' else 'start_' + unit, rows[0])
                        self.assertIn('任务', rows[0]['task'])

    def test_empty_single_scope_and_atomic_only_populations_have_no_paging_clutter(self):
        for count, atomic_count in ((0, 0), (1, 0), (0, 1), (200, 100)):
            with self.subTest(count=count, atomic_count=atomic_count):
                self.open_report(count, atomic_count)
                self.assertEqual(self.page.locator('.an-error-pages').count(), 0)
                self.assertEqual(self.page.locator('[data-act=errors-csv]').is_disabled(), not count)
                self.assertEqual(len(self.ids()), count)
                self.assertEqual(len(self.ids(True)), atomic_count)
        self.open_report()
        self.page.evaluate("fgNotifyFilter('nothing', new Set())")
        self.assertEqual((self.ids(), self.ids(True)), ([], []))
        self.assertEqual(self.page.locator('.an-error-pages').count(), 0)
        self.assertIn('No visible error chains match', self.page.locator('.tl-detail').inner_text())

    def test_touch_controls_in_both_themes_portrait_landscape_and_short_panes(self):
        self.context.close()
        for theme in ('light', 'dark'):
            self.context = self.browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True)
            self.addCleanup(self.context.close)
            self.context.add_init_script(f"localStorage.setItem('theme', '{theme}')")
            self.open_report(405)
            for width, height in ((390, 844), (320, 700), (844, 390)):
                with self.subTest(theme=theme, width=width):
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.page.wait_for_function("() => document.getElementById('timelineView').classList.contains('tl-compact')")
                    self.assertEqual(self.page.locator('.an-error-head').evaluate('node => getComputedStyle(node).top'), 'auto')
                    if height < 480:
                        self.page.locator('[data-detail-expand]').tap()
                    self.page.evaluate("document.querySelector('.tl-detail').scrollLeft = 1e6")
                    for selector in ('[data-act=errors-csv]', '[data-act=atomic-csv]', '[data-act=error-occurrences-csv]', '.tl-close'):
                        bounds = self.page.locator(selector).bounding_box()
                        self.assertGreaterEqual(bounds['x'], 0)
                        self.assertLessEqual(bounds['x'] + bounds['width'], width)
                    for atomic in (False, True):
                        self.button(atomic).tap()
                        for direction in ('prev', 'next'):
                            button = self.button(atomic, direction)
                            button.scroll_into_view_if_needed()
                            bounds = button.bounding_box()
                            self.assertGreaterEqual(bounds['height'], 44)
                            self.assertGreaterEqual(bounds['width'], 44)
                            self.assertGreaterEqual(bounds['x'], 0)
                            self.assertLessEqual(bounds['x'] + bounds['width'], width)
                        self.button(atomic, 'prev').tap()
                    self.page.locator('.an-occurrence .tl-btn').first.scroll_into_view_if_needed()
                    bounds = self.page.locator('.an-occurrence .tl-btn').first.bounding_box()
                    self.assertGreaterEqual(bounds['width'], 44)
                    self.assertGreaterEqual(bounds['height'], 44)
                    self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
                    if height < 480:
                        self.page.locator('[data-detail-expand]').tap()
                        self.assertEqual(self.page.locator('.an-error-table th').first.evaluate('node => getComputedStyle(node).position'), 'static')

    def test_actual_parser_and_cli_sidecar_keep_every_original_error_chain(self):
        trace = Path(self.directory.name) / 'failures.txt'
        trace.write_text('# tracer: function_graph\n' + ''.join(
            f'  {0.001 + index * 0.000002:.9f} |   0) demo-1 |  ..... |   1.000 us    |  __x64_sys_failure_{index}(); /* ret=-2 */\n'
            for index in range(405)), encoding='utf-8')
        destination = Path(self.directory.name) / 'sidecar'
        self.kernel.run(str(trace), '--no-config', '--timeline', '--report-mode', 'sidecar', out=str(destination))
        server = create_server(destination)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(server.url)
        self.page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().errors(); }")
        original = self.export('occurrences')
        rows = list(csv.DictReader(io.StringIO(original.decode('utf-8-sig'))))
        self.assertEqual(len(rows), 405)
        self.assertEqual({row['fate'] for row in rows}, {'to_user'})
        self.assertEqual({row['origin_line'] for row in rows}, {str(index + 2) for index in range(405)})
        first = self.ids()
        self.button().click()
        self.button().click()
        self.assertEqual(len(self.ids()), 5)
        self.assertEqual(self.export('occurrences'), original)
        self.button(direction='prev').click()
        self.button(direction='prev').click()
        self.assertEqual(self.ids(), first)


if __name__ == '__main__':
    unittest.main()
