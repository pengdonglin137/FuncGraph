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

WINDOW_PAGE_SIZE = 100


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
        size = WINDOW_PAGE_SIZE
        for panel in ('sections', 'latency'):
            for count in (1, size, size + 1, size * 2, size * 2 + 1):
                with self.subTest(panel=panel, count=count):
                    self.open_report(count, panel)
                    first = self.ids()
                    self.assertEqual(len(first), min(size, count))
                    self.assertIn(f'1–{min(size, count)} of {count}', self.status())
                    if count <= size:
                        self.assertEqual(self.page.locator('.an-window-pages').count(), 0)
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

    def test_direct_page_entry_keeps_focus_order_and_full_export(self):
        for panel in ('sections', 'latency'):
            with self.subTest(panel=panel):
                self.open_report(503, panel)
                first, exported = self.ids(), self.export()
                chart = self.page.evaluate('fgView("timeline").probe().view()')
                field = self.page.get_by_role('textbox', name='Page', exact=True)
                go = self.page.get_by_role('button', name='Go to page', exact=True)
                self.assertEqual(field.input_value(), '1')
                self.assertEqual(field.get_attribute('inputmode'), 'numeric')
                first_row = self.page.locator('.an-window-table tbody tr').first.element_handle()
                for invalid in ('', '0', '7', '-2', '2.5', '2e0', 'two', '9' * 50):
                    field.fill(invalid)
                    field.press('Enter')
                    self.assertEqual(field.evaluate('input => input.validationMessage'), 'Enter a whole page number from 1 to 6.')
                    self.assertTrue(first_row.evaluate('row => row.isConnected'))
                    self.assertIn('1–100 of 503', self.status())
                field.fill(' 006 ')
                field.press('Enter')
                self.assertEqual(field.input_value(), '6')
                self.assertTrue(field.evaluate('input => input === document.activeElement'))
                self.assertIn('501–503 of 503', self.status())
                self.assertEqual(len(self.ids()), 3)
                self.assertTrue(self.page.locator('[data-act=window-next]').is_disabled())
                self.assertEqual(self.export(), exported)
                self.page.locator('[data-act=window-prev]').click()
                self.assertEqual(field.input_value(), '5')
                field.fill('1')
                go.click()
                self.assertTrue(go.evaluate('button => button === document.activeElement'))
                self.assertEqual(self.ids(), first)
                self.assertEqual(self.page.evaluate('fgView("timeline").probe().view()'), chart)
                self.assertEqual(self.export(), exported)

    def test_all_thousands_of_records_are_reachable_without_growing_dom_or_resorting_cached_profiles(self):
        size = WINDOW_PAGE_SIZE
        for panel in ('sections', 'latency'):
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

    def test_large_window_csv_keeps_scope_while_paging_and_pauses_after_progress(self):
        count = 4096
        unsafe = ['=SUM(1,2)', 'line,"quoted"\n任务']

        def update(data):
            data['strings'][:len(unsafe)] = unsafe

        self.context.close()
        self.context = self.browser.new_context(viewport={'width': 320, 'height': 700}, is_mobile=True, has_touch=True)
        self.addCleanup(self.context.close)
        for panel in ('sections', 'latency'):
            with self.subTest(panel=panel):
                self.open_report(count, panel, update=update)
                self.page.evaluate('count => fgNotifyFilter("all matching", new Set(Array.from({length: count}, (_, index) => index + 1)))', count)
                downloads = []
                self.page.on('download', lambda download: downloads.append(download))
                button = self.page.locator('[data-act=window-csv]')
                status = self.page.locator('.an-window-csv-status')
                self.assertEqual(button.evaluate('''button => new Promise((resolve, reject) => {
                    const status = document.querySelector('.an-window-csv-status');
                    const clock = performance.now.bind(performance);
                    let elapsed = 0;
                    performance.now = () => clock() + (elapsed += 7);
                    button.focus();
                    button.click();
                    const controls = [button, status, document.querySelector('.an-window-head .tl-close')];
                    const accessible = controls.every(node => {
                        const rect = node.getBoundingClientRect();
                        return rect.left >= 0 && rect.right <= innerWidth && rect.width > 0;
                    });
                    const focused = button === document.activeElement;
                    document.querySelector('[data-act=window-next]').click();
                    const observer = new MutationObserver(() => {
                        if (!status.textContent.includes('%')) return;
                        observer.disconnect(); clearTimeout(timeout); delete performance.now;
                        fgSwitchView('text');
                        resolve([accessible, focused, status.textContent]);
                    });
                    const timeout = setTimeout(() => {
                        observer.disconnect(); delete performance.now; reject(Error('No CSV progress'));
                    }, 5000);
                    observer.observe(status, {childList: true});
                })'''), [True, True, 'Preparing CSV… 25%'])
                self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)))')
                self.assertEqual(status.text_content(), 'Preparing CSV… 25%')
                self.assertEqual(downloads, [])
                with self.page.expect_download() as download:
                    self.page.locator('.view-tab[data-view=timeline]').tap()
                stem = 'sections' if panel == 'sections' else 'latency'
                self.assertEqual(download.value.suggested_filename, f'funcgraph-{stem}-raw.csv')
                exported = Path(download.value.path()).read_bytes()
                self.assertTrue(exported.startswith(b'\xef\xbb\xbf'))
                self.assertTrue(exported.endswith(b'\r\n'))
                rows = list(csv.DictReader(io.StringIO(exported.decode('utf-8-sig'), newline='')))
                index_key = 'section_index' if panel == 'sections' else 'window_index'
                expected = sorted(range(count), key=lambda index: (-(1 + index % 999), index))
                self.assertEqual([int(row[index_key]) for row in rows], expected)
                self.assertEqual({row['filter'] for row in rows}, {'all matching'})
                self.assertIn("'" + unsafe[0], [row['top_1_function'] for row in rows])
                self.assertIn(unsafe[1], [row['top_1_function'] for row in rows])
                self.assertEqual(len(self.ids()), WINDOW_PAGE_SIZE)
                self.assertIn('101–200 of 4096', self.status())
                self.assertEqual(button.inner_text(), 'Export CSV')
                self.assertTrue(status.is_hidden())
                self.assertEqual(self.export(), exported)

    def test_large_window_csv_discards_old_output_on_cancel_and_scope_changes(self):
        def update(data):
            data['cols']['adj'] = [value / 2 for value in data['cols']['dur']]

        for panel, sort in (('sections', 'ssort'), ('latency', 'lsort')):
            self.open_report(2048, panel, update=update)
            downloads = []
            self.page.on('download', lambda download: downloads.append(download))
            button = self.page.locator('[data-act=window-csv]')
            actions = {
                'Cancel CSV': 'button.click()',
                'Sort': f'document.querySelector("th[data-{sort}=start] button").click()',
                'Overhead': 'document.querySelector("[data-act=adj]").click()',
                'Filter': 'fgNotifyFilter("one call", new Set([1]))',
                'Close': 'document.querySelector(".an-window-head .tl-close").click()',
                'Other panel': f'fgView("timeline").probe().{"latency" if panel == "sections" else "sections"}()',
            }
            if panel == 'sections':
                actions['Section kind'] = "document.querySelector('[data-sk=\"1\"]').click()"
            for reason, action in actions.items():
                with self.subTest(panel=panel, reason=reason):
                    self.page.evaluate('panel => fgView("timeline").probe()[panel]()', panel)
                    self.assertEqual(button.evaluate(f'''button => {{
                        button.click();
                        const before = button.textContent;
                        {action};
                        return before;
                    }}'''), 'Cancel CSV')
                    self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)))')
                    self.assertEqual(downloads, [])
                    self.assertTrue(self.page.locator('.an-window-csv-status').is_hidden())
                    self.page.evaluate('fgNotifyFilter("", null)')

    def test_large_contribution_rank_keeps_ties_and_global_winners_without_sorting_all_names(self):
        population = 2048
        leaders = (range(20, 23), range(30, 33))

        def update(data):
            names = [index * 1291 % population for index in range(population)]
            data['cols']['name'] = names * 2
            data['cols']['dur'] = [100 if name in top else 90 if name < 11 else 1 + name % 5
                                   for top in leaders for name in names]
            data['resched'] = [[0, window * population * 1000, (window + 1) * population * 1000, 0, 1, 0]
                               for window in range(2)]
            data['sections'] = [[track, 0, start, end, -1, -1, depth, 0, 0]
                                for track, start, end, depth, _, _ in data['resched']]

        self.open_report(2 * population, update=update)
        self.page.evaluate('''() => {
            const sort = Array.prototype.sort;
            window.rankedPopulations = [];
            Array.prototype.sort = function (...args) {
                if (this.length > 8 && Array.isArray(this[0]) && this[0].length === 2 && this[0].every(Number.isFinite))
                    rankedPopulations.push(this.length);
                return sort.apply(this, args);
            };
        }''')
        for panel in ('sections', 'latency'):
            with self.subTest(panel=panel):
                self.page.evaluate('panel => fgView("timeline").probe()[panel]()', panel)
                rows = sorted(self.rows(), key=lambda row: int(row['start_ns']))
                self.assertEqual(len(rows), 2)
                for row, top in zip(rows, leaders):
                    self.assertEqual([row[f'top_{rank}_function'] for rank in range(1, 4)],
                                     [f'function_{name:06d}' for name in top])
                    self.assertEqual([float(row[f'top_{rank}_self_estimate_ns']) for rank in range(1, 4)], [100] * 3)
        self.assertEqual(self.page.locator('.an-blame .an-chip .k').all_text_contents(),
                         [f'function_{name:06d}' for name in range(8)])
        self.assertEqual(self.page.evaluate('rankedPopulations'), [])

    def test_all_three_section_kinds_keep_original_indices_and_independent_pages(self):
        self.open_report(403)
        for kind, label in enumerate(('irqs off', 'preempt off', 'BH off')):
            with self.subTest(kind=kind):
                self.page.locator(f'[data-sk="{kind}"]').click()
                self.assertIn('1–100 of 403', self.status())
                rows = self.rows()
                self.assertEqual({int(row['section_index']) for row in rows}, set(range(kind * 403, (kind + 1) * 403)))
                self.assertEqual({row['kind'] for row in rows}, {label})
                field = self.page.get_by_role('textbox', name='Page', exact=True)
                field.fill(str(kind + 3))
                field.press('Enter')
                self.assertEqual(len(self.ids()), 3 if kind == 2 else WINDOW_PAGE_SIZE)
        for kind, interval in enumerate(('201–300', '301–400', '401–403')):
            self.page.locator(f'[data-sk="{kind}"]').click()
            self.assertIn(f'{interval} of 403', self.status())
            self.assertEqual(self.page.get_by_role('textbox', name='Page', exact=True).input_value(), str(kind + 3))

    def test_panel_and_kind_pages_survive_reopen_refresh_and_text_roundtrips(self):
        self.open_report()
        self.next()
        first_kind = self.ids(), self.status(), self.export()
        self.page.locator('[data-act=window-next]').focus()
        self.page.evaluate("fgView('timeline').probe().sections()")
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'window-next')
        self.page.locator('[data-sk="1"]').press('Enter')
        self.assertIn('1–100 of 650', self.status())
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
        size = WINDOW_PAGE_SIZE
        for panel, attribute in (('sections', 'ssort'), ('latency', 'lsort')):
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

    def test_sort_keeps_panel_controls_and_focus_in_a_horizontally_scrolled_short_pane(self):
        self.context.close()
        self.context = self.browser.new_context(viewport={'width': 320, 'height': 700}, is_mobile=True, has_touch=True)
        self.addCleanup(self.context.close)
        self.context.add_init_script("localStorage.setItem('fgTl.detailFrac', '0.18')")
        for panel, attribute, key, direction in (('sections', 'ssort', 'top', 'descending'),
                                                   ('latency', 'lsort', 'end', 'ascending')):
            with self.subTest(panel=panel):
                self.open_report(203, panel)
                field = self.page.get_by_role('textbox', name='Page', exact=True)
                field.fill('3')
                field.press('Enter')
                self.assertEqual(len(self.ids()), 3)
                field.fill('9')
                field.press('Enter')
                self.assertFalse(field.evaluate('input => input.validity.valid'))
                stable_nodes = [self.page.locator(selector).element_handle() for selector in
                                ('.an-window-head', '.an-window-footer [role=status]', '.an-window-table', '[data-act=window-page]')]
                old_row = self.page.locator('.an-window-table tbody tr').first.element_handle()
                header = self.page.locator(f'th[data-{attribute}={key}]')
                sort = header.locator('button')
                sort.scroll_into_view_if_needed()
                self.assertGreater(self.page.locator('.tl-detail').evaluate('pane => pane.scrollLeft'), 0)
                chart = self.page.evaluate('fgView("timeline").probe().view()')
                sort.press('Enter')
                for node in stable_nodes:
                    self.assertTrue(node.evaluate('node => node.isConnected'))
                self.assertFalse(old_row.evaluate('row => row.isConnected'))
                self.assertEqual(header.get_attribute('aria-sort'), direction)
                self.assertEqual(self.page.locator('th[aria-sort=ascending], th[aria-sort=descending]').count(), 1)
                self.assertEqual(field.input_value(), '1')
                self.assertTrue(field.evaluate('input => input.validity.valid'))
                self.assertIn('1–100 of 203', self.status())
                self.assertTrue(sort.evaluate('''button => {
                    const bounds = button.getBoundingClientRect();
                    return button === document.activeElement && button.contains(document.elementFromPoint(
                        bounds.left + bounds.width / 2, bounds.top + bounds.height / 2));
                }'''))
                self.page.keyboard.press('Tab')
                self.assertTrue(self.page.locator('[data-window-jump]').first.evaluate('button => button === document.activeElement'))
                rows = self.rows()
                self.assertEqual(len(rows), 203)
                expected_ids = [row['section_index'] if panel == 'sections' else f"{row['start_ns']},{row['end_ns']},0" for row in rows]
                self.assertEqual(self.ids(), expected_ids[:WINDOW_PAGE_SIZE])
                self.next()
                second_row = self.page.locator('.an-window-table tbody tr').first.element_handle()
                sort.press('Space')
                self.assertIn('101–200 of 203', self.status())
                self.assertTrue(second_row.evaluate('row => row.isConnected'))
                self.assertEqual(self.page.evaluate('fgView("timeline").probe().view()'), chart)

    def test_overhead_resets_pages_and_changes_only_self_estimates_not_window_geometry(self):
        def update(data):
            data['cols']['adj'] = [duration / 2 for duration in data['cols']['dur']]
        size = WINDOW_PAGE_SIZE
        for panel in ('sections', 'latency'):
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

    def test_keyboard_jumps_to_window_rows_without_tabbing_through_the_page(self):
        size = WINDOW_PAGE_SIZE
        for panel in ('sections', 'latency'):
            with self.subTest(panel=panel):
                self.open_report(panel=panel)
                self.next()
                before = self.ids(), self.status(), self.export()
                chart = self.page.evaluate("fgView('timeline').probe().view()")
                table = self.page.locator('.an-window-table')
                results = table.locator('tbody button[data-window-jump]')
                self.assertEqual(results.count(), size)
                table.locator('thead button').last.focus()
                self.page.keyboard.press('Tab')
                self.assertTrue(results.first.evaluate('button => button === document.activeElement'))
                for key, index in (('ArrowDown', 1), ('End', size - 1), ('Home', 0), ('ArrowUp', 0), ('End', size - 1)):
                    self.page.keyboard.press(key)
                    self.assertTrue(results.nth(index).evaluate('''button => {
                        const rect = button.getBoundingClientRect();
                        return button === document.activeElement && document.elementFromPoint(
                            rect.left + rect.width / 2, rect.top + rect.height / 2) === button;
                    }'''), (key, index))
                    self.assertEqual(table.locator('tbody button[tabindex="0"]').count(), 1)
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().view()"), chart)
                self.page.keyboard.press('Space')
                keyboard_view = self.page.evaluate("fgView('timeline').probe().view()")
                self.assertNotEqual(keyboard_view, chart)
                self.assertTrue(results.last.evaluate('button => button === document.activeElement'))
                self.page.keyboard.press('Tab')
                self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'window-prev')
                self.page.keyboard.press('Shift+Tab')
                self.assertTrue(results.last.evaluate('button => button === document.activeElement'))
                self.page.evaluate("panel => fgView('timeline').probe()[panel]()", panel)
                self.assertTrue(results.last.evaluate('button => button === document.activeElement'))
                table.locator('tbody tr').last.locator('td').nth(1).click()
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().view()"), keyboard_view)
                self.assertEqual((self.ids(), self.status(), self.export()), before)
                results.last.focus()
                self.page.evaluate("fgNotifyFilter('empty', new Set())")
                self.assertTrue(self.page.locator('.tl-detail .tl-close').evaluate('button => button === document.activeElement'))

    def test_wrapped_phone_pager_does_not_cover_focused_results(self):
        self.context.close()
        self.context = self.browser.new_context(viewport={'width': 320, 'height': 700}, is_mobile=True, has_touch=True)
        self.addCleanup(self.context.close)
        self.open_report(panel='latency')
        pane = self.page.locator('.tl-detail')
        table = pane.locator('.an-window-table')
        results = table.locator('tbody [data-window-jump]')
        for fraction, short in (('0.34', False), ('0.213', True), ('0.18', True), ('0.34', False)):
            with self.subTest(fraction=fraction):
                self.page.evaluate('''fraction => {
                    localStorage.setItem('fgTl.detailFrac', fraction);
                    fgView('timeline').resize();
                }''', fraction)
                self.assertEqual(pane.evaluate("pane => pane.classList.contains('tl-detail-short')"), short)
                table.locator('thead button').last.focus()
                self.page.keyboard.press('Tab')
                for key, result in (('End', results.last), ('Home', results.first)):
                    self.page.keyboard.press(key)
                    self.assertTrue(result.evaluate('''button => {
                        const rect = button.getBoundingClientRect();
                        return button === document.activeElement && document.elementFromPoint(
                            rect.left + rect.width / 2, rect.top + rect.height / 2) === button;
                    }'''), key)

    def test_real_text_parser_and_cli_sidecar_produce_reversible_pages(self):
        for panel in ('sections', 'latency'):
            with self.subTest(panel=panel):
                self.open_report(301, panel, sidecar=True)
                self.assertEqual(len(self.rows()), 301)
                original = self.export()
                first = self.ids()
                self.next()
                self.assertEqual(len(self.ids()), WINDOW_PAGE_SIZE)
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
                        for selector in ('[data-act=window-prev]', '[data-act=window-next]', '[data-act=window-page]', '[data-act=window-go]'):
                            button = self.page.locator(selector)
                            button.scroll_into_view_if_needed()
                            bounds = button.bounding_box()
                            self.assertGreaterEqual(bounds['height'], 44)
                            self.assertGreaterEqual(bounds['width'], 44)
                            self.assertGreaterEqual(bounds['x'], 0)
                            self.assertLessEqual(bounds['x'] + bounds['width'], width)
                        self.page.locator('[data-act=window-prev]').tap()
                        page_field = self.page.get_by_role('textbox', name='Page', exact=True)
                        page_field.tap()
                        page_field.fill('7')
                        self.page.get_by_role('button', name='Go to page', exact=True).tap()
                        self.assertIn('601–601 of 601', self.status())
                        self.assertEqual(len(self.ids()), 1)
                        page_field.fill('1')
                        page_field.press('Enter')
                        self.assertIn('1–100 of 601', self.status())
                        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
                        if height < 480:
                            self.page.locator('[data-detail-expand]').tap()
                            self.assertEqual(self.page.locator('.an-window-footer').evaluate('element => getComputedStyle(element).bottom'), 'auto')


    def test_short_panes_offer_paging_before_rows_and_keep_the_focused_button(self):
        self.context.close()
        self.context = self.browser.new_context(viewport={'width': 320, 'height': 700}, is_mobile=True, has_touch=True)
        self.addCleanup(self.context.close)
        self.context.add_init_script("localStorage.setItem('fgTl.detailFrac', '0.18')")
        for panel, kind, size in (('sections', 'window', WINDOW_PAGE_SIZE), ('latency', 'window', WINDOW_PAGE_SIZE), ('range', 'range', 100)):
            with self.subTest(panel=panel):
                self.open_report(size + 1, 'sections' if panel == 'range' else panel)
                if panel == 'range':
                    self.page.evaluate('end => fgView("timeline").probe().range(0, end)', (size + 1) * 1000)
                pane = self.page.locator('.tl-detail')
                self.assertTrue(pane.evaluate("element => element.classList.contains('tl-detail-short')"))
                pane.evaluate('''pane => {
                    const table = pane.querySelector('table');
                    pane.scrollLeft = pane.scrollWidth;
                    pane.scrollTop += table.getBoundingClientRect().top - pane.getBoundingClientRect().top - pane.clientTop - 80;
                }''')
                next_button = pane.locator(f'[data-act={kind}-next]')
                point = next_button.evaluate('''button => {
                    const rect = button.getBoundingClientRect(), x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
                    return button.contains(document.elementFromPoint(x, y)) ? [x, y] : null;
                }''')
                self.assertIsNotNone(point, 'Next should be tappable without scrolling through the page of rows')
                chart = self.page.evaluate('fgView("timeline").probe().view()')
                self.page.touchscreen.tap(*point)
                self.assertEqual(pane.locator('tbody tr').count(), 1)
                self.assertTrue(next_button.is_disabled())
                previous = pane.locator(f'[data-act={kind}-prev]')
                self.assertTrue(previous.evaluate('''button => {
                    const rect = button.getBoundingClientRect();
                    return button === document.activeElement && button.contains(document.elementFromPoint(
                        rect.left + rect.width / 2, rect.top + rect.height / 2));
                }'''))
                self.page.keyboard.press('Enter')
                self.assertEqual(pane.locator('tbody tr').count(), size)
                self.assertTrue(next_button.evaluate('button => document.activeElement === button'))
                self.assertEqual(self.page.evaluate('fgView("timeline").probe().view()'), chart)
                first_row = pane.locator('tbody tr').first.element_handle()
                for fraction, short in (('0.34', False), ('0.18', True)):
                    self.page.evaluate('''fraction => {
                        localStorage.setItem('fgTl.detailFrac', fraction);
                        fgView('timeline').resize();
                    }''', fraction)
                    self.assertEqual(pane.evaluate("pane => pane.classList.contains('tl-detail-short')"), short)
                    self.assertTrue(next_button.evaluate('''(button, short) => {
                        const pager = button.parentElement.parentElement;
                        const neighbor = short ? pager.nextElementSibling : pager.previousElementSibling;
                        return document.activeElement === button && neighbor?.tagName === 'TABLE';
                    }''', short))
                    self.assertTrue(first_row.evaluate('row => row.isConnected'))

    def test_short_pages_reveal_the_new_first_row_without_hiding_navigation(self):
        self.context.close()
        self.context = self.browser.new_context(viewport={'width': 320, 'height': 700}, is_mobile=True, has_touch=True)
        self.addCleanup(self.context.close)
        self.context.add_init_script("localStorage.setItem('fgTl.detailFrac', '0.18')")
        for panel, kind, size in (('sections', 'window', WINDOW_PAGE_SIZE), ('latency', 'window', WINDOW_PAGE_SIZE), ('range', 'range', 100)):
            with self.subTest(panel=panel):
                count = 2 * size + 1
                self.open_report(count, 'sections' if panel == 'range' else panel)
                if panel == 'range':
                    self.page.evaluate('end => fgView("timeline").probe().range(0, end)', count * 1000)
                pane = self.page.locator('.tl-detail.tl-detail-short')
                next_button = pane.locator(f'[data-act={kind}-next]')
                start = next_button.evaluate('''button => {
                    const pane = button.closest('.tl-detail');
                    pane.scrollLeft = pane.scrollWidth;
                    const bounds = button.getBoundingClientRect();
                    pane.scrollTop += bounds.bottom - pane.getBoundingClientRect().top - pane.clientTop - pane.clientHeight + 4;
                    const point = button.getBoundingClientRect();
                    return {x: point.left + point.width / 2, y: point.top + point.height / 2, left: pane.scrollLeft};
                }''')
                self.assertGreater(start['left'], 0)
                self.assertTrue(next_button.evaluate('''(button, point) => button.contains(document.elementFromPoint(point.x, point.y))''', start))
                chart = self.page.evaluate('fgView("timeline").probe().view()')
                self.page.touchscreen.tap(start['x'], start['y'])
                right_positions = []
                for step, (first, last, focus) in enumerate(((size + 1, 2 * size, 'next'), (count, count, 'prev'),
                                                           (size + 1, 2 * size, 'prev'), (1, size, 'next'))):
                    if step == 3:
                        pane.evaluate('pane => { pane.scrollLeft = 60; }')
                    if step:
                        self.page.keyboard.press('Enter')
                    self.assertIn(f'{first}–{last} of {count}', pane.get_by_role('status').inner_text())
                    self.assertEqual(pane.locator('tbody tr').count(), last - first + 1)
                    layout = pane.evaluate('''pane => {
                        const box = pane.getBoundingClientRect();
                        const top = box.top + pane.clientTop, bottom = top + pane.clientHeight;
                        const button = document.activeElement, control = button.getBoundingClientRect();
                        const row = pane.querySelector('tbody tr'), bounds = row.getBoundingClientRect();
                        const hit = document.elementFromPoint(box.left + pane.clientLeft + pane.clientWidth / 2,
                            bounds.top + bounds.height / 2);
                        return {focus: button.dataset.act, left: pane.scrollLeft, max: pane.scrollWidth - pane.clientWidth,
                            vertical: [top, control.top, bounds.top, bounds.bottom, bottom], hit: hit?.tagName,
                            control: control.top >= top && control.bottom <= bottom && button.contains(document.elementFromPoint(
                                control.left + control.width / 2, control.top + control.height / 2)),
                            row: bounds.top >= top && bounds.bottom <= bottom && row.contains(hit)};
                    }''')
                    self.assertEqual(layout['focus'], f'{kind}-{focus}')
                    self.assertTrue(layout['control'], layout)
                    self.assertTrue(layout['row'], layout)
                    if step == 3:
                        self.assertEqual(layout['left'], 60)
                    else:
                        self.assertEqual(layout['left'], layout['max'])
                        right_positions.append(layout['left'])
                self.assertLess(right_positions[1], right_positions[0])
                self.assertEqual(right_positions[0], right_positions[2])
                self.assertEqual(self.page.evaluate('fgView("timeline").probe().view()'), chart)


if __name__ == '__main__':
    unittest.main()
