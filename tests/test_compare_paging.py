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


def long_call_counts():
    rows = comparison_rows(805)
    for index, row in enumerate(rows):
        row[1] = 10 ** 15 + index
        row[6] = row[1] + index + 1
    return rows


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

    def open_report(self, rows=None, mode='single', theme='light', width=1280, height=720, touch=False, adjusted=None,
                    names=None):
        context = self.browser.new_context(viewport=dict(width=width, height=height),
                                           is_mobile=touch, has_touch=touch, accept_downloads=True)
        self.addCleanup(context.close)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(self.assertEqual, errors, [])
        page.goto(Path(self.kernel.dir, 'single.html').as_uri() if mode == 'single' else self.server.url)
        page.evaluate('''async ({rows,adjusted,theme,names}) => {
            document.documentElement.dataset.theme = theme;
            const data = await fgData.load('cmpData');
            data.rows = rows;
            data.rows_adj = adjusted;
            if (names) data.names = names;
            if (adjusted) data.overhead = [{child:10,self:5},{child:10,self:5}];
            window.compareOriginal = JSON.stringify(data);
            await fgSwitchView('compare');
        }''', dict(rows=comparison_rows() if rows is None else rows, adjusted=adjusted, theme=theme, names=names))
        self.assertEqual(page.locator('.cmp-pages').count(), 1)
        return page

    def identities(self, page):
        return page.locator('.cmp-table tbody td.l[title]').evaluate_all('''nodes => nodes.map(node =>
            [node.title, node.querySelector('.cmp-tag')?.textContent || ''])''')

    def unchanged(self, page):
        self.assertTrue(page.evaluate("compareOriginal === JSON.stringify(fgData.read('cmpData'))"))

    def scroll_to_row(self, table, ordinal):
        table.evaluate('''async (node, ordinal) => {
            const header = node.querySelector('thead'), row = node.querySelector('tbody').rows[ordinal];
            node.scrollTop += row.getBoundingClientRect().top - header.getBoundingClientRect().bottom + 5;
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        }''', ordinal)

    def reading_anchor(self, table):
        return table.evaluate('''node => {
            const edge = node.querySelector('thead').getBoundingClientRect().bottom;
            const row = Array.from(node.querySelector('tbody').rows).find(row => row.getBoundingClientRect().bottom > edge + 0.5);
            return [row.sectionRowIndex, edge - row.getBoundingClientRect().top, row.getBoundingClientRect().height];
        }''')

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

    def test_direct_page_jump_skips_unvisited_pages_and_exports_the_complete_ranking(self):
        rows = comparison_rows(10000)
        page = self.open_report(rows, mode='sidecar')
        number = page.get_by_role('spinbutton', name='Page', exact=True)
        status = page.locator('.cmp-page-status')
        self.assertEqual(number.input_value(), '1')
        self.assertEqual(number.get_attribute('max'), '25')
        self.assertEqual(page.locator('.cmp-page-total').inner_text(), 'of 25')
        page.evaluate('''() => {
            const body = document.querySelector('.cmp-table tbody');
            window.comparePageWrites = 0;
            new MutationObserver(records => { window.comparePageWrites += records.length; })
                .observe(body, {childList: true});
        }''')
        number.fill('25')
        number.press('Enter')
        self.assertEqual(status.inner_text(), 'Rows 9601–10000 of 10000')
        self.assertEqual(page.evaluate('comparePageWrites'), 1)
        self.assertEqual(self.identities(page), [[row[0], row[11]] for row in rows[399::-1]])
        self.assertTrue(page.locator('.cmp-next').is_disabled())
        self.assertTrue(number.evaluate('node => node === document.activeElement'))
        with page.expect_download() as download:
            page.locator('.cmp-export').click()
        with open(download.value.path(), encoding='utf-8-sig', newline='') as source:
            exported = list(csv.DictReader(source))
        self.assertEqual([(row['function'], row['space']) for row in exported],
                         [(row[0], row[11]) for row in reversed(rows)])
        self.assertEqual(number.input_value(), '25')
        page.locator('.cmp-prev').click()
        self.assertEqual(number.input_value(), '24')
        self.unchanged(page)

    def test_page_jump_rejects_invalid_numbers_and_restores_cancelled_edit(self):
        page = self.open_report()
        number = page.get_by_role('spinbutton', name='Page', exact=True)
        status = page.locator('.cmp-page-status')
        page.locator('.cmp-next').click()
        page.locator('.cmp-table tbody').evaluate('node => { window.oldCompareRow = node.firstElementChild; }')
        for invalid in ('', '0', '5', '2.5'):
            with self.subTest(invalid=invalid):
                number.fill(invalid)
                number.press('Enter')
                self.assertFalse(number.evaluate('node => node.validity.valid'))
                self.assertEqual(status.inner_text(), 'Rows 401–800 of 1207')
        number.press('Escape')
        self.assertEqual(number.input_value(), '2')
        self.assertTrue(number.evaluate('node => node.validity.valid'))
        number.press('Enter')
        self.assertTrue(page.locator('.cmp-table tbody').evaluate('node => node.firstElementChild === oldCompareRow'))
        number.fill('4')
        page.evaluate("async () => { await fgSwitchView('text'); await fgSwitchView('compare'); }")
        self.assertEqual(number.input_value(), '2')
        self.assertEqual(status.inner_text(), 'Rows 401–800 of 1207')

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

    def test_sort_reuses_filtered_population_and_keeps_csv_tie_order(self):
        rows = comparison_rows()
        for index, row in enumerate(rows):
            if index >= 800:
                row[0] = row[0].replace('worker_', 'other_')
        for row in rows[:3]:
            row[6:11] = row[1:6]
        adjusted = [row[:8] + [row[8] - 10] + row[9:] for row in reversed(rows)]
        page = self.open_report(rows, adjusted=adjusted)
        page.evaluate('''() => {
            const test = RegExp.prototype.test;
            window.compareMatches = 0;
            RegExp.prototype.test = function(value) {
                if (this.source === 'worker_' && value.includes('.isra.')) compareMatches++;
                return test.call(this, value);
            };
        }''')
        self.query(page, 'worker_', 797)
        initial_checks = page.evaluate('compareMatches')
        self.assertGreater(initial_checks, 0)
        page.locator('th[data-k=name] button').press('Enter')
        self.assertEqual(self.identities(page), [[row[0], row[11]] for row in rows[3:403]])
        page.locator('th[data-k=err] button').press('Enter')
        page.locator('.cmp-sortby button[data-v=base]').click()
        self.assertEqual(page.evaluate('compareMatches'), initial_checks)

        for source in (rows, adjusted):
            page.locator('.cmp-changed').uncheck()
            page.locator('.cmp-adj input').set_checked(source is adjusted)
            expected = sorted((row for row in source if row[0].startswith('worker_')), key=lambda row: -row[5])
            with page.expect_download() as download:
                page.locator('.cmp-export').click()
            with open(download.value.path(), encoding='utf-8-sig', newline='') as file:
                exported = [(row['function'], row['space'], int(row['current_self_ns'])) for row in csv.DictReader(file)]
            self.assertEqual(exported, [(row[0], row[11], row[8]) for row in expected])
        self.query(page, 'other_', 407)
        self.unchanged(page)

    def test_compact_sort_and_scope_fit_two_touch_rows(self):
        rows = comparison_rows()
        page = self.open_report(rows, width=320, height=480, touch=True, adjusted=rows)
        toggle = page.locator('.cmp-toggle')
        toggle.tap()
        order = page.get_by_role('combobox', name='Sort order')
        self.assertEqual(order.count(), 1)
        self.assertFalse(page.locator('.cmp-sortby').is_visible())
        query = page.locator('.cmp-q').bounding_box()
        sort = order.bounding_box()
        changed = page.locator('.cmp-changed').locator('..').bounding_box()
        overhead = page.locator('.cmp-adj').bounding_box()
        for target in (query, sort, changed, overhead):
            self.assertGreaterEqual(target['height'], 44)
            self.assertLessEqual(target['x'] + target['width'], 320)
        self.assertAlmostEqual(query['y'], sort['y'], delta=1)
        self.assertAlmostEqual(changed['y'], overhead['y'], delta=1)
        self.assertLessEqual(overhead['y'] + overhead['height'] - query['y'], 96)

        page.locator('.cmp-next').click()
        order.select_option('pct')
        self.assertEqual(toggle.get_attribute('aria-label'), 'Filters, active')
        self.assertAlmostEqual(page.locator('.cmp-export').bounding_box()['y'], toggle.bounding_box()['y'], delta=1)
        self.assertEqual(self.identities(page)[0], [rows[0][0], rows[0][11]])
        self.assertEqual(page.locator('.cmp-page-status').inner_text(), 'Rows 1–400 of 1207')
        page.locator('.cmp-next').click()
        toggle.tap()
        toggle.tap()
        self.assertEqual(order.input_value(), 'pct')
        self.assertEqual(page.locator('.cmp-page-status').inner_text(), 'Rows 401–800 of 1207')
        self.unchanged(page)

    def test_sort_focus_and_value_survive_responsive_control_switches(self):
        page = self.open_report()
        button = page.get_by_role('button', name='Sort by current value')
        button.press('Enter')
        page.set_viewport_size(dict(width=390, height=600))
        toggle = page.locator('.cmp-toggle')
        order = page.get_by_role('combobox', name='Sort order')
        toggle.wait_for(state='visible')
        self.assertTrue(toggle.evaluate('node => node === document.activeElement'))
        toggle.press('Space')
        self.assertEqual(order.input_value(), 'cur')
        order.select_option('pct')
        order.focus()
        page.set_viewport_size(dict(width=1280, height=720))
        percent = page.get_by_role('button', name='Sort by percentage difference')
        percent.wait_for(state='visible')
        self.assertTrue(percent.evaluate('node => node === document.activeElement'))
        self.assertEqual(percent.get_attribute('aria-pressed'), 'true')
        page.set_viewport_size(dict(width=390, height=600))
        order.wait_for(state='visible')
        self.assertTrue(order.evaluate('node => node === document.activeElement'))
        self.assertEqual(order.input_value(), 'pct')

    def test_reranking_reuses_visible_rows_and_updates_the_active_metric_bar(self):
        rows = comparison_rows(805)
        page = self.open_report(rows)
        body = page.locator('.cmp-table tbody')
        body.evaluate('''node => {
            window.oldCompareRows = new Map(Array.from(node.rows, row => [row.cells[0].textContent, row]));
            window.compareRowsReused = () => Array.from(node.rows).filter(row =>
                oldCompareRows.get(row.cells[0].textContent) === row).length;
        }''')
        baseline = body.inner_html()
        page.locator('.cmp-sortby button[data-v=base]').press('Enter')
        self.assertEqual(page.evaluate('compareRowsReused()'), 400)
        self.assertEqual(body.inner_html(), baseline)

        page.locator('th[data-k=err] button').press('Space')
        expected = sorted(rows, key=lambda row: -row[5])[:400]
        self.assertEqual(self.identities(page), [[row[0], row[11]] for row in expected])
        originally_visible = {(row[0], row[11]) for row in rows[-400:]}
        self.assertEqual(page.evaluate('compareRowsReused()'),
                         sum((row[0], row[11]) in originally_visible for row in expected))
        self.assertEqual(body.locator('.cmp-dbar').evaluate_all('''bars => bars.map(bar =>
            [bar.parentElement.cellIndex, Number.parseFloat(bar.style.width)])'''),
                         [[18, 20 if abs(row[10] - row[5]) == 1 else 40] for row in expected])
        self.assertTrue(page.locator('th[data-k=err] button').evaluate('node => node === document.activeElement'))
        page.locator('th[data-k=name] button').press('Enter')
        self.assertEqual(body.locator('.cmp-dbar').count(), 0)
        page.locator('th[data-k=self] button').press('Enter')
        self.query(page, '^worker_0039', 20)
        self.assertEqual(self.identities(page)[0], [rows[799][0], 'user'])
        self.assertEqual(body.locator('.cmp-dbar').first.get_attribute('style'), 'width:40.0px')
        self.unchanged(page)

    def test_keyboard_sort_preserves_focus_and_repeated_selection_keeps_the_page(self):
        page = self.open_report()
        headings = page.locator('.cmp-table th[aria-sort]')
        self.assertEqual(headings.count(), 1)
        self.assertEqual(headings.get_attribute('data-k'), 'self')
        self.assertEqual(headings.get_attribute('aria-sort'), 'descending')
        page.locator('.cmp-next').click()
        name = page.get_by_role('button', name='function', exact=True)
        name.press('Enter')
        self.assertTrue(name.evaluate('node => node === document.activeElement'))
        self.assertEqual(headings.get_attribute('data-k'), 'name')
        self.assertEqual(headings.get_attribute('aria-sort'), 'ascending')
        self.assertTrue(page.locator('.cmp-prev').is_disabled())
        self.assertEqual(self.identities(page)[0], ['worker_00000.isra.0', 'kernel'])

        page.locator('.cmp-next').click()
        body = page.locator('.cmp-table tbody')
        body.evaluate('node => { window.beforeRepeatSort = node.firstElementChild; }')
        name.press('Space')
        absolute = page.get_by_role('button', name='Sort by absolute difference')
        absolute.press('Enter')
        self.assertTrue(body.evaluate('node => node.firstElementChild === window.beforeRepeatSort'))
        self.assertEqual(page.locator('.cmp-page-status').inner_text(), 'Rows 401–800 of 1207')

        calls = page.get_by_role('button', name='calls', exact=True)
        calls.press('Space')
        self.assertTrue(calls.evaluate('node => node === document.activeElement'))
        self.assertEqual(headings.get_attribute('data-k'), 'calls')
        self.assertEqual(headings.get_attribute('aria-sort'), 'descending')
        base = page.get_by_role('button', name='Sort by base value')
        base.press('Enter')
        self.assertEqual(base.get_attribute('aria-pressed'), 'true')
        self.assertEqual(absolute.get_attribute('aria-pressed'), 'false')
        self.unchanged(page)

    def test_keyboard_sort_focus_survives_crossing_the_compact_breakpoint(self):
        page = self.open_report()
        self_time = page.get_by_role('button', name='self time', exact=True)
        self_time.focus()
        page.set_viewport_size(dict(width=390, height=600))
        page.wait_for_function("document.querySelectorAll('.cmp-table th[data-k]').length === 2")
        self.assertTrue(self_time.evaluate('node => node === document.activeElement'))
        page.set_viewport_size(dict(width=1280, height=720))
        page.wait_for_function("document.querySelectorAll('.cmp-table th[data-k]').length === 7")
        self.assertTrue(self_time.evaluate('node => node === document.activeElement'))
        page.get_by_role('button', name='inclusive', exact=True).focus()
        page.set_viewport_size(dict(width=390, height=600))
        page.wait_for_function("document.querySelectorAll('.cmp-table th[data-k]').length === 2")
        self.assertTrue(page.locator('.cmp-wrap').evaluate('node => node === document.activeElement'))

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

    def test_large_export_yields_without_reflow_and_uses_its_original_snapshot(self):
        rows = comparison_rows(3007)
        rows[0][0] = '=雪,"worker"\nnext'
        adjusted = [row[:8] + [row[3]] + row[9:] for row in rows]
        page = self.open_report(rows, width=320, height=480, touch=True, adjusted=adjusted)
        with page.expect_download() as download:
            during = page.locator('.cmp-export').evaluate('''async button => {
                const bounds = selector => document.querySelector(selector).getBoundingClientRect().toJSON();
                const geometry = () => ['.cmp-export', '.cmp-wrap', '.cmp-pages'].map(bounds);
                const before = geometry();
                button.focus();
                const timer = new Promise(resolve => setTimeout(() => {
                    const visibleLabel = [...button.children].find(child =>
                        getComputedStyle(child).visibility === 'visible')?.textContent;
                    const sameLayout = JSON.stringify(before) === JSON.stringify(geometry());
                    const keepsFocus = button === document.activeElement;
                    document.querySelector('.cmp-next').click();
                    const range = document.querySelector('.cmp-page-status').textContent;
                    document.querySelector('.cmp-adj input').click();
                    resolve({visibleLabel, sameLayout, keepsFocus, range});
                }, 0));
                button.click();
                return await timer;
            }''')
        self.assertEqual(during, dict(visibleLabel='Cancel CSV', sameLayout=True, keepsFocus=True,
                                      range='Rows 401–800 of 3007'))
        self.assertEqual(download.value.suggested_filename, 'funcgraph-compare-raw.csv')
        content = Path(download.value.path()).read_bytes()
        self.assertTrue(content.startswith(b'\xef\xbb\xbf'))
        self.assertTrue(content.endswith(b'\r\n'))
        with open(download.value.path(), encoding='utf-8-sig', newline='') as stream:
            exported = [(row['function'], row['space'], int(row['current_self_ns'])) for row in csv.DictReader(stream)]
        self.assertEqual(exported, [("'" + row[0] if row[0].startswith('=') else row[0], row[11], row[8])
                                    for row in reversed(rows)])
        self.assertTrue(page.locator('.cmp-adj input').is_checked())
        self.unchanged(page)

    def test_cancel_or_leave_during_export_does_not_download_and_can_retry(self):
        for action in ('cancel', 'leave'):
            with self.subTest(action=action):
                page = self.open_report()
                page.locator('.cmp-next').click()
                during = page.locator('.cmp-export').evaluate('''async (button, action) => {
                    window.compareDownloads = 0;
                    document.querySelector('#compareView').addEventListener('click', event => {
                        if (event.target.matches('a[download]')) window.compareDownloads++;
                    });
                    button.focus();
                    const timer = new Promise(resolve => setTimeout(async () => {
                        const wasBuilding = button.hasAttribute('data-exporting');
                        if (action === 'cancel') button.click();
                        else { await fgSwitchView('text'); await fgSwitchView('compare'); }
                        resolve({wasBuilding, stillBuilding: button.hasAttribute('data-exporting'),
                            focused: button === document.activeElement,
                            message: document.querySelector('.cmp-export-status').textContent});
                    }, 0));
                    button.click();
                    return await timer;
                }''', action)
                self.assertTrue(during['wasBuilding'])
                self.assertFalse(during['stillBuilding'])
                if action == 'cancel':
                    self.assertTrue(during['focused'])
                    self.assertEqual(during['message'], 'CSV export cancelled.')
                else:
                    self.assertEqual(during['message'], '')
                export = page.get_by_role('button', name='Export CSV', exact=True)
                with page.expect_download() as download:
                    export.press('Enter')
                self.assertEqual(download.value.suggested_filename, 'funcgraph-compare-raw.csv')
                self.assertEqual(page.evaluate('compareDownloads'), 1)
                self.assertEqual(page.locator('.cmp-page-status').inner_text(), 'Rows 401–800 of 1207')

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
                    tab = page.get_by_role('button', name='Compare', exact=True).bounding_box()
                    self.assertGreaterEqual(tab['height'], 44)
                    pager = page.locator('.cmp-pages').bounding_box()
                    self.assertLessEqual(pager['y'] + pager['height'], height + 1)
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width + 1)
                    self.assertGreaterEqual(page.locator('.cmp-next').bounding_box()['height'], 44)
                    page.locator('.cmp-next').tap()
                    self.assertIn('401–800', page.locator('.cmp-page-status').inner_text())
                    number = page.get_by_role('spinbutton', name='Page', exact=True)
                    go = page.get_by_role('button', name='Go to comparison page')
                    for control in (number, go):
                        self.assertGreaterEqual(control.bounding_box()['height'], 44)
                        self.assertGreaterEqual(control.bounding_box()['width'], 44)
                    number.fill('4')
                    go.tap()
                    self.assertIn('1201–1207', page.locator('.cmp-page-status').inner_text())
                    self.unchanged(page)

    def test_compact_page_range_reuses_count_line_without_losing_filter_scope(self):
        page = self.open_report(mode='sidecar', width=320, height=480, touch=True)
        compact_range = page.locator('.cmp-compact-range')
        footer_status = page.locator('.cmp-page-status')
        number = page.get_by_role('spinbutton', name='Page', exact=True)
        table = page.locator('.cmp-wrap')
        self.assertEqual(compact_range.inner_text(), 'Rows 1–400 of 1207')
        self.assertEqual(compact_range.get_attribute('aria-hidden'), 'true')
        self.assertEqual(footer_status.bounding_box()['height'], 1)
        self.assertGreaterEqual(table.bounding_box()['height'], 175)
        self.assertIn('Rows 1–400 of 1207', page.locator('.cmp-pages').aria_snapshot())

        toggle = page.locator('.cmp-toggle')
        toggle.tap()
        self.query(page, '^worker_00[0-2]', 600)
        toggle.tap()
        number.fill('2')
        number.press('Enter')
        self.assertEqual(compact_range.inner_text(), 'Rows 401–600 of 600 · 1207 total')
        self.assertEqual(footer_status.inner_text(), 'Rows 401–600 of 600')

        page.set_viewport_size(dict(width=1280, height=720))
        self.assertFalse(compact_range.is_visible())
        self.assertGreater(footer_status.bounding_box()['height'], 1)
        self.assertEqual(page.locator('.cmp-count').inner_text(), '600 of 1207 functions')
        self.assertTrue(number.evaluate('node => node === document.activeElement'))
        page.set_viewport_size(dict(width=320, height=480))
        self.assertTrue(compact_range.is_visible())
        self.assertEqual(compact_range.inner_text(), 'Rows 401–600 of 600 · 1207 total')

        toggle.tap()
        self.query(page, '^worker_00000', 2)
        self.assertFalse(compact_range.is_visible())
        self.assertFalse(page.locator('.cmp-pages').is_visible())
        self.assertGreater(page.locator('.cmp-count').bounding_box()['height'], 1)
        self.assertEqual(page.locator('.cmp-count').inner_text(), '2 of 1207 functions')
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
        self.assertEqual(toggle.get_attribute('data-active'), '')
        self.assertEqual(toggle.get_attribute('aria-label'), 'Filters, active')
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
        page.wait_for_function("document.querySelector('.cmp-toggle').hidden")
        self.assertTrue(page.locator('.cmp-q').is_visible())
        self.assertFalse(page.locator('.cmp-toggle').is_visible())
        self.assertTrue(page.locator('.cmp-q').evaluate('node => node === document.activeElement'))
        self.unchanged(page)

    def test_short_table_keeps_readable_rows_below_the_sticky_header(self):
        for mode, width, theme in (('single', 320, 'light'), ('sidecar', 844, 'dark')):
            with self.subTest(mode=mode, width=width, theme=theme):
                page = self.open_report(mode=mode, theme=theme, width=width, height=240, touch=True)
                wrap = page.locator('.cmp-wrap')
                wrap.evaluate('node => node.scrollIntoView({block:"center"})')
                bounds = wrap.bounding_box()
                page.mouse.move(bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2)
                page.mouse.wheel(0, 120)
                page.wait_for_function('() => document.querySelector(".cmp-wrap").scrollTop > 0')
                facts = wrap.evaluate('''node => {
                    const box = node.getBoundingClientRect();
                    const header = node.querySelector('thead').getBoundingClientRect();
                    const group = node.querySelector('th[data-k=self]').getBoundingClientRect();
                    const sub = node.querySelector('th.sub').getBoundingClientRect();
                    const readable = Array.from(node.querySelectorAll('tbody tr')).filter(row => {
                        const bounds = row.getBoundingClientRect();
                        return bounds.top >= header.bottom && bounds.bottom <= Math.min(box.bottom, innerHeight) &&
                            row.contains(document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2));
                    }).length;
                    return {readable, height:node.clientHeight, headerOffset:header.top - box.top, gap:sub.top - group.bottom};
                }''')
                self.assertGreaterEqual(facts['height'], 160)
                self.assertGreaterEqual(facts['readable'], 4)
                self.assertAlmostEqual(facts['headerOffset'], 1, delta=1)
                self.assertAlmostEqual(facts['gap'], 0, delta=0.5)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
                self.unchanged(page)

    def test_compact_metric_projection_preserves_page_and_csv_scope(self):
        page = self.open_report(mode='sidecar', theme='dark', width=320, height=568, touch=True,
                                names=['base-' * 40 + '.txt', 'current-' * 40 + '.txt'])
        metric = page.get_by_role('combobox', name='Comparison metric')
        table = page.locator('.cmp-wrap')
        self.assertEqual(metric.input_value(), 'self')
        self.assertEqual(table.locator('th[data-k]').all_text_contents(), ['function', 'self time'])
        self.assertTrue(table.evaluate('node => node.scrollWidth === node.clientWidth'))
        self.assertEqual(table.locator('tbody tr').first.locator('td').count(), 4)
        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 321)
        self.assertEqual(page.get_by_role('button', name='Compare', exact=True).get_attribute('aria-current'), 'true')
        self.assertAlmostEqual(page.locator('.view-tabs').bounding_box()['height'], 45, delta=1)
        self.assertGreaterEqual(metric.bounding_box()['height'], 44)
        metric.select_option('calls')
        self.assertEqual(table.locator('th[data-k]').all_text_contents(), ['function', 'calls'])
        page.locator('.cmp-next').tap()
        before = self.identities(page)
        self.assertIn('401–800', page.locator('.cmp-page-status').inner_text())
        with page.expect_download() as download:
            page.get_by_role('button', name='Export CSV').tap()
        with download.value.path().open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 1207)
        self.assertIn('current_incl_ns', rows[-1])
        metric.focus()
        page.set_viewport_size(dict(width=1280, height=720))
        page.wait_for_function("() => document.querySelector('.cmp-metric').hidden")
        self.assertTrue(table.evaluate('node => node === document.activeElement'))
        self.assertEqual(table.locator('tbody tr').first.locator('td').count(), 19)
        self.assertEqual(self.identities(page), before)
        page.set_viewport_size(dict(width=844, height=390))
        page.wait_for_function("() => !document.querySelector('.cmp-metric').hidden")
        self.assertEqual(metric.input_value(), 'calls')
        self.assertEqual(self.identities(page), before)
        self.assertLessEqual(table.locator('tbody tr').first.bounding_box()['y'], 275)
        self.unchanged(page)

    def test_projection_changes_keep_the_row_under_the_sticky_header(self):
        page = self.open_report(long_call_counts(), mode='sidecar')
        page.locator('.cmp-table th[data-k=calls] button').click()
        page.locator('.cmp-next').click()
        table = page.locator('.cmp-wrap')
        wide_scroll_left = table.evaluate('node => { node.scrollLeft = 246; return node.scrollLeft; }')
        self.assertGreater(wide_scroll_left, 0)

        self.scroll_to_row(table, 125)
        before = self.reading_anchor(table)
        self.assertEqual(before[0], 125)
        page.set_viewport_size({'width': 320, 'height': 640})
        page.locator('.cmp-metric').wait_for(state='visible')
        after = self.reading_anchor(table)
        self.assertEqual(after[0], before[0])
        self.assertAlmostEqual(after[1], before[1], delta=1)

        self.scroll_to_row(table, 212)
        before = self.reading_anchor(table)
        self.assertEqual(before[0], 212)
        page.set_viewport_size({'width': 1280, 'height': 720})
        page.locator('.cmp-metric').wait_for(state='hidden')
        after = self.reading_anchor(table)
        self.assertEqual(after[0], before[0])
        self.assertAlmostEqual(after[1], before[1], delta=1)
        self.assertIn('401–800', page.locator('.cmp-page-status').inner_text())
        self.assertEqual(table.evaluate('node => node.scrollLeft'), wide_scroll_left)
        self.unchanged(page)

    def test_compact_width_reflow_keeps_the_visible_row_without_rebuilding(self):
        page = self.open_report(long_call_counts(), mode='sidecar', width=600, height=640)
        metric = page.locator('.cmp-metric')
        metric.select_option('calls')
        page.locator('.cmp-next').click()
        table = page.locator('.cmp-wrap')
        table.evaluate("node => window.compareRow = node.querySelector('tbody tr')")
        for ordinal in (125, 212):
            self.scroll_to_row(table, ordinal)
            before = self.reading_anchor(table)
            self.assertEqual(before[0], ordinal)
            for width in (320, 600):
                with self.subTest(width=width, ordinal=ordinal):
                    page.set_viewport_size(dict(width=width, height=640))
                    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                    after = self.reading_anchor(table)
                    self.assertEqual(after[0], ordinal)
                    self.assertAlmostEqual(after[1], before[1], delta=1)
                    if width == 320:
                        self.assertGreater(after[2], before[2])
                    else:
                        self.assertLess(after[2], before[2])
                    self.assertTrue(metric.is_visible())
                    self.assertEqual(metric.input_value(), 'calls')
                    self.assertEqual(page.locator('.cmp-page-number').input_value(), '2')
                    self.assertTrue(table.evaluate("node => window.compareRow === node.querySelector('tbody tr')"))
                    before = after
        page.locator('.cmp-next').click()
        self.assertEqual(page.locator('.cmp-page-number').input_value(), '3')
        self.assertEqual(table.evaluate('node => node.scrollTop'), 0)
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
        number = page.get_by_role('spinbutton', name='Page', exact=True)
        number.fill('3')
        number.press('Enter')
        self.assertEqual(self.identities(page), [[row[0], row[11]] for row in list(reversed(rows))[800:1200]])
        self.assertTrue(page.locator('.cmp-export').is_disabled())
        page.locator('.cmp-cancel').click()
        self.assertIn('801–1200', page.locator('.cmp-page-status').inner_text())
        self.assertTrue(page.evaluate('compareWorkers.at(-1).terminated'))
        page.locator('.cmp-q').fill('worker_0000[0-2]')
        page.locator('.cmp-q').press('Enter')
        page.wait_for_function('compareWorkers.at(-1)?.batch')
        number.focus()
        page.evaluate('compareWorkers.at(-1).finish()')
        page.wait_for_function("document.querySelector('.cmp-count').textContent === '6 of 1207 functions'")
        self.assertFalse(page.locator('.cmp-pages').is_visible())
        self.assertEqual(page.locator('.cmp-page-number').input_value(), '1')
        self.assertEqual(page.locator('.cmp-page-number').get_attribute('max'), '1')
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
