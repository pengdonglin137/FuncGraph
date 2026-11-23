"""Task browsing bounds live rows without narrowing complete exports."""
import csv
import io
from pathlib import Path
import threading
import unittest

import helpers
from fglib.viewer import create_server
from test_uftrace_import import call, stream


class TaskPagingTest(unittest.TestCase):
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

    def setUp(self):
        self.kernel = helpers.FakeKernel()
        self.addCleanup(self.kernel.close)
        self.serial = 0
        self.page = self.browser.new_page(viewport={'width': 1500, 'height': 900})
        self.addCleanup(self.page.close)
        self.page.set_default_timeout(10000)
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open_report(self, count=205, mode='single', no_text=False, calls_per_task=1, open_panel=True):
        self.serial += 1
        records = []
        for index in range(count):
            comm = '=SUM(1,2)<task>' if index == 0 else f'task_{index:05}'
            for ordinal in range(calls_per_task):
                start = index * 1000 + ordinal * 200
                records += [call('entry', 'user', start, 0, 'work', 1, tid=index + 1, comm=comm),
                            call('exit', 'user', start + 100, 0, 'work', 1, tid=index + 1)]
        trace = Path(self.kernel.dir) / f'capture-{self.serial}.jsonl'
        trace.write_text(stream(records), encoding='utf-8')
        destination = f'report-{self.serial}' + ('.html' if mode == 'single' else '')
        options = ['--no-config', '--timeline', '--filter', '--report-mode', mode]
        if no_text:
            options.append('--no-text-view')
        self.kernel.run(str(trace), *options, out=destination)
        report = Path(self.kernel.dir) / destination
        if mode == 'sidecar':
            server = create_server(report)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 5)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            url = server.url
        else:
            url = report.as_uri()
        self.page.goto(url)
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.page.locator('.tl-canvas').wait_for()
        if open_panel:
            self.open_tasks()

    def open_tasks(self):
        button = self.page.locator('[data-act="an-tasks"]')
        if not button.is_visible():
            self.page.locator('[data-act="options"]').click()
        button.click()

    def status(self):
        return self.page.locator('.an-task-footer [role="status"]').inner_text()

    def ids(self):
        return self.page.locator('[data-task-row]').evaluate_all('(rows) => rows.map(row => +row.dataset.taskRow)')

    def next(self):
        self.page.locator('[data-task-page="1"]').click()

    def export_bytes(self):
        with self.page.expect_download() as download:
            self.page.locator('[data-act="tasks-csv"]').click()
        self.assertEqual(download.value.suggested_filename, 'funcgraph-tasks.csv')
        return Path(download.value.path()).read_bytes()

    def export(self):
        return list(csv.DictReader(io.StringIO(self.export_bytes().decode('utf-8-sig'), newline='')))

    def test_pages_replace_rows_and_keep_keyboard_focus_on_refresh(self):
        self.open_report()
        self.assertEqual(self.status(), 'Tasks 1–100 of 205')
        self.assertTrue(self.page.locator('[data-task-page="-1"]').is_disabled())
        self.page.locator('[data-task-page="1"]').press('Enter')
        self.assertEqual(self.ids(), list(range(100, 200)))
        self.assertEqual(self.status(), 'Tasks 101–200 of 205')
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.taskPage'), '1')
        self.page.evaluate('fgOnFilter()')
        self.assertEqual(self.ids(), list(range(100, 200)))
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.taskPage'), '1')
        self.page.locator('[data-task-page="1"]').press('Space')
        self.assertEqual(self.ids(), list(range(200, 205)))
        self.assertTrue(self.page.locator('[data-task-page="1"]').is_disabled())
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.taskPage'), '-1')
        self.page.locator('[data-task-page="-1"]').press('Enter')
        self.assertEqual(self.ids(), list(range(100, 200)))
        self.page.locator('[data-task-page="-1"]').press('Space')
        self.assertEqual(self.ids(), list(range(100)))
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.taskPage'), '1')

    def test_single_exact_and_partial_page_boundaries(self):
        for count in (1, 100, 101, 205):
            with self.subTest(count=count):
                self.open_report(count=count)
                self.assertEqual(len(self.ids()), min(100, count))
                self.assertEqual(self.page.locator('[data-task-page="1"]').is_disabled(), count <= 100)
                if count > 100:
                    self.next()
                    self.assertEqual(self.ids(), list(range(100, min(200, count))))
                    self.assertEqual(self.page.locator('[data-task-page="1"]').is_disabled(), count <= 200)

    def test_thousands_of_tasks_are_all_reachable_with_at_most_one_hundred_live_rows(self):
        self.open_report(count=5207, no_text=True)
        result = self.page.evaluate('''() => {
            const ids = [], sizes = [];
            while (true) {
                const rows = Array.from(document.querySelectorAll('[data-task-row]'));
                sizes.push(rows.length);
                ids.push(...rows.map(row => +row.dataset.taskRow));
                const next = document.querySelector('[data-task-page="1"]');
                if (next.disabled) break;
                next.click();
            }
            return {ids, sizes};
        }''')
        self.assertEqual(result['ids'], list(range(5207)))
        self.assertEqual(result['sizes'], [100] * 52 + [7])
        self.assertEqual(self.status(), 'Tasks 5201–5207 of 5207')

    def test_csv_includes_unvisited_pages_without_changing_the_current_page(self):
        self.open_report()
        self.next()
        rows = self.export()
        self.assertEqual(len(rows), 205)
        self.assertEqual([row['task_index'] for row in rows], list(map(str, range(205))))
        self.assertEqual(rows[0]['task'], "'=SUM(1,2)<task>")
        self.assertEqual(rows[-1]['calls'], '1')
        self.assertEqual(rows[-1]['span_ns'], '100')
        self.assertEqual(self.status(), 'Tasks 101–200 of 205')
        self.assertEqual(self.page.locator('.an-task-table task').count(), 0)

    def test_cold_tasks_defers_search_metadata_until_it_is_needed(self):
        self.open_report(open_panel=False)
        self.page.evaluate('''() => {
            const task = fgView('timeline').probe().TRACKS[204];
            task.comms = ['unique historical name'];
            window.taskAliasReads = 0;
            task.comms[Symbol.iterator] = function* () {
                taskAliasReads++;
                yield* Array.prototype.values.call(this);
            };
        }''')
        self.open_tasks()
        self.assertEqual(self.status(), 'Tasks 1–100 of 205')
        self.assertEqual(self.page.evaluate('taskAliasReads'), 0)
        self.page.locator('[data-task-query]').fill('historical name')
        self.assertEqual(self.ids(), [204])
        self.assertEqual(self.page.evaluate('taskAliasReads'), 1)
        self.page.locator('[data-task-query]').fill('unique historical')
        self.assertEqual(self.ids(), [204])
        self.assertEqual(self.page.evaluate('taskAliasReads'), 1)
        self.assertIn('unique historical name', self.export()[0]['names'])

    def test_large_csv_pauses_after_progress_and_preserves_sorted_results(self):
        count = 2060
        self.open_report(count=count, mode='sidecar')
        self.page.locator('[data-task-query]').fill(' TASK_0 ')
        self.page.locator('[data-task-sort=name]').click()
        self.page.evaluate("fgNotifyFilter('all entry lines', new Set(fgData.read('tlData').cols.line))")
        downloads = []
        self.page.on('download', lambda download: downloads.append(download))
        button = self.page.locator('[data-act=tasks-csv]')
        status = self.page.locator('.an-task-csv-status')
        self.assertEqual(button.evaluate('''button => new Promise((resolve, reject) => {
            const status = document.querySelector('.an-task-csv-status');
            const clock = performance.now.bind(performance);
            let elapsed = 0;
            performance.now = () => clock() + (elapsed += 7);
            button.click();
            document.querySelector('[data-task-page="1"]').click();
            document.querySelector('[data-task-show]').click();
            const observer = new MutationObserver(() => {
                if (!status.textContent.includes('%')) return;
                observer.disconnect(); clearTimeout(timeout); delete performance.now;
                fgSwitchView('text');
                resolve([button.textContent, status.textContent]);
            });
            const timeout = setTimeout(() => {
                observer.disconnect(); delete performance.now; reject(new Error('No CSV progress'));
            }, 5000);
            observer.observe(status, {childList: true});
        })'''), ['Cancel CSV', 'Preparing CSV… 25%'])
        self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)))')
        self.assertEqual(self.page.evaluate('fgCurrentView()'), 'text')
        self.assertEqual(downloads, [])
        self.assertEqual(status.text_content(), 'Preparing CSV… 25%')
        with self.page.expect_download() as resumed:
            self.page.evaluate("fgSwitchView('timeline')")
        self.assertEqual(resumed.value.suggested_filename, 'funcgraph-tasks.csv')
        self.assertEqual(len(downloads), 1)
        raw = Path(resumed.value.path()).read_bytes()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf') and raw.endswith(b'\r\n'))
        rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline='')))
        self.assertEqual([int(row['task_index']) for row in rows], list(range(1, count)))
        self.assertEqual({(row['query'], row['shared_filter'], row['matched_calls']) for row in rows},
                         {(' TASK_0 ', 'all entry lines', '1')})
        self.assertEqual(self.status(), f'Tasks 101–200 of {count - 1}')
        self.assertTrue(status.is_hidden())
        self.assertEqual(button.inner_text(), 'Export CSV')
        self.assertEqual(self.export_bytes(), raw)

    def test_large_csv_controls_fit_and_discard_cancelled_or_stale_exports(self):
        context = self.browser.new_context(viewport={'width': 320, 'height': 700}, is_mobile=True, has_touch=True)
        self.addCleanup(context.close)
        self.page = context.new_page()
        self.page.set_default_timeout(10000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.open_report(count=2060)
        downloads = []
        self.page.on('download', lambda download: downloads.append(download))
        button = self.page.locator('[data-act=tasks-csv]')
        status = self.page.locator('.an-task-csv-status')
        for change in (
            'button.click()',
            "const input = document.querySelector('[data-task-query]'); input.value = ' '; input.dispatchEvent(new Event('input'));",
            "document.querySelector('[data-task-sort=name]').click()",
            "document.querySelector('[data-task-matching]').click()",
            "const kind = document.querySelector('[data-task-kind]'); kind.value = '0'; kind.dispatchEvent(new Event('change'));",
            "fgNotifyFilter('same counts, different metadata', null)",
            "fgNotifyFilter('only first', new Set([1]))",
            "document.querySelector('.an-task-head .tl-close').click()",
            "fgView('timeline').probe().errors()",
        ):
            with self.subTest(change=change):
                self.assertEqual(button.evaluate(f'''button => {{
                    const pane = document.querySelector('.tl-detail');
                    pane.scrollLeft = pane.scrollWidth;
                    button.click();
                    const box = button.getBoundingClientRect();
                    const status = document.querySelector('.an-task-csv-status');
                    const progress = status.getBoundingClientRect();
                    const hit = document.elementFromPoint((box.left + box.right) / 2, (box.top + box.bottom) / 2);
                    const result = [button.textContent, box.height >= 44 && hit === button,
                        progress.left >= 0 && progress.right <= innerWidth && progress.width > 0];
                    {change};
                    return result;
                }}'''), ['Cancel CSV', True, True])
                self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)))')
                self.assertEqual(downloads, [])
                self.assertTrue(status.is_hidden())
                self.page.evaluate('''() => {
                    fgNotifyFilter('', null);
                    document.querySelector('[data-act=an-tasks]').click();
                    document.querySelector('[data-act=tasks-reset]').click();
                }''')
                self.assertEqual(button.inner_text(), 'Export CSV')

    def test_search_kind_reset_and_empty_results_reset_page_safely(self):
        self.open_report()
        self.next()
        self.next()
        self.page.locator('[data-task-query]').fill('task_001')
        self.assertEqual(self.status(), 'Tasks 1–100 of 100')
        self.assertEqual(self.ids(), list(range(100, 200)))
        self.page.locator('[data-task-query]').fill('[no match]')
        self.assertEqual(self.status(), 'No matching tasks')
        self.assertEqual(self.ids(), [])
        for selector in ('[data-task-page="-1"]', '[data-task-page="1"]', '[data-act="tasks-csv"]'):
            self.assertTrue(self.page.locator(selector).is_disabled())
        self.page.locator('[data-act="tasks-reset"]').click()
        self.assertEqual(self.status(), 'Tasks 1–100 of 205')
        self.page.locator('[data-task-kind]').select_option('1')
        self.assertEqual(self.status(), 'No matching tasks')
        self.page.locator('[data-act="tasks-reset"]').click()
        self.assertEqual(self.ids(), list(range(100)))

    def test_shared_call_matches_filter_paging_and_export_consistently(self):
        self.open_report(mode='sidecar')
        self.next()
        self.page.evaluate("fgNotifyFilter('chosen task', new Set([203]))")
        self.assertEqual(self.status(), 'Tasks 1–100 of 205')
        self.page.locator('[data-task-matching]').check()
        self.assertEqual(self.ids(), [100])
        rows = self.export()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['calls'], '1')
        self.assertEqual(rows[0]['matched_calls'], '1')
        self.assertEqual(rows[0]['shared_filter'], 'chosen task')

    def test_task_match_counts_do_not_rescan_the_shared_call_bitmap(self):
        self.open_report(count=1024, calls_per_task=4)
        reads = self.page.evaluate('''() => {
            const data = fgData.read('tlData'), native = window.Uint8Array;
            let bitmapReads = 0;
            const accepted = new Set(data.cols.eline.filter((line, index) => index % 4 === 1));
            window.Uint8Array = new Proxy(native, {construct(target, args) {
                const array = new target(...args);
                if (args[0] !== data.n) return array;
                return new Proxy(array, {get(bitmap, key) {
                    if (typeof key === 'string' && /^\\d+$/.test(key)) bitmapReads++;
                    return Reflect.get(bitmap, key, bitmap);
                }});
            }});
            try { fgNotifyFilter('one exit per task', accepted); }
            finally { window.Uint8Array = native; }
            return bitmapReads;
        }''')
        self.assertEqual(reads, 0)
        rows = self.export()
        self.assertEqual(len(rows), 1024)
        self.assertTrue(all(row['calls'] == '4' and row['matched_calls'] == '1' for row in rows))

    def test_equal_task_counts_keep_the_page_rows_and_focus(self):
        self.open_report(mode='sidecar', calls_per_task=2)
        self.page.evaluate("fgNotifyFilter('first call', new Set([fgData.read('tlData').cols.line[200]]))")
        self.next()
        show = self.page.locator('[data-task-show="100"]')
        show.focus()
        self.page.evaluate('''() => {
            const columns = fgData.read('tlData').cols;
            fgNotifyFilter('second call', new Set([columns.line[201], columns.eline[201]]));
        }''')
        self.assertEqual(self.status(), 'Tasks 101–200 of 205')
        self.assertTrue(show.evaluate('button => button === document.activeElement'))
        rows = self.export()
        self.assertEqual([int(row['task_index']) for row in rows if row['matched_calls'] == '1'], [100])
        self.assertEqual(rows[100]['shared_filter'], 'second call')
        self.page.evaluate("fgNotifyFilter('different count', new Set([fgData.read('tlData').cols.eline[202]]))")
        self.assertEqual(self.status(), 'Tasks 1–100 of 205')

    def test_sorting_keeps_header_focus_and_reuses_task_facts(self):
        self.open_report()
        self.next()
        self.page.evaluate('''() => {
            for (const task of fgView('timeline').probe().TRACKS) {
                task.runs[Symbol.iterator] = function* () { throw new Error('Task facts rescanned'); };
            }
        }''')
        self.page.locator('[data-task-sort="name"]').press('Enter')
        self.assertEqual(self.status(), 'Tasks 1–100 of 205')
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.taskSort'), 'name')
        self.assertEqual(self.page.locator('th[aria-sort="ascending"]').count(), 1)
        self.next()
        self.assertEqual(self.status(), 'Tasks 101–200 of 205')
        self.assertEqual(len(self.export()), 205)

    def test_query_and_scope_reuse_name_sort_and_normalized_query_keeps_the_page(self):
        self.open_report(count=1024)
        self.page.locator('[data-task-sort="name"]').press('Enter')
        self.page.evaluate('''() => {
            const original = Array.prototype.sort;
            window.taskSorts = [];
            Array.prototype.sort = function(compare) {
                if (this[0]?.task && typeof this[0].calls === 'number') taskSorts.push(this.length);
                return original.call(this, compare);
            };
        }''')
        query = self.page.locator('[data-task-query]')
        query.fill('task_')
        self.next()
        first_row = self.page.locator('.an-task-table tbody tr').first.element_handle()
        query.fill(' TASK_ ')
        page_after_equivalent_query = self.status()
        same_row = first_row.evaluate('row => row.isConnected')
        query.fill('task_000')
        self.assertEqual(self.ids(), list(range(1, 100)))
        query.fill('task_001')
        self.page.locator('[data-task-kind]').select_option('0')
        self.page.evaluate("fgNotifyFilter('one match', new Set([fgData.read('tlData').cols.line[150]]))")
        self.assertEqual(self.ids(), list(range(100, 200)))
        self.assertEqual([int(row['task_index']) for row in self.export()], list(range(100, 200)))
        self.assertEqual(self.page.evaluate('taskSorts'), [])
        self.assertEqual(page_after_equivalent_query, 'Tasks 101–200 of 1023')
        self.assertTrue(same_row)

    def test_matches_sort_keeps_ties_stable_and_reorders_when_counts_change(self):
        self.open_report()
        self.page.evaluate("fgNotifyFilter('scope one', new Set([fgData.read('tlData').cols.line[150]]))")
        self.page.locator('[data-task-sort="matches"]').press('Enter')
        self.assertEqual(self.ids()[:4], [150, 0, 1, 2])
        self.page.evaluate('''() => {
            const lines = fgData.read('tlData').cols.line;
            fgNotifyFilter('scope two', new Set([lines[180], lines[151]]));
        }''')
        self.assertEqual(self.ids()[:4], [151, 180, 0, 1])
        self.page.locator('[data-task-matching]').check()
        self.assertEqual(self.ids(), [151, 180])
        self.assertEqual([int(row['task_index']) for row in self.export()], [151, 180])

    def test_later_page_navigation_and_reopening_preserve_task_page(self):
        self.open_report(no_text=True)
        self.next()
        self.page.locator('[data-task-show="100"]').click()
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), -1)
        self.assertGreater(self.page.evaluate("fgView('timeline').probe().TRACKS[100].h"), 0)
        left, right = self.page.evaluate("fgView('timeline').probe().view()")
        self.assertLessEqual(left, 100000)
        self.assertGreaterEqual(right, 100100)
        self.page.locator('.tl-close').click()
        self.open_tasks()
        self.assertEqual(self.status(), 'Tasks 101–200 of 205')
        self.assertEqual(self.ids(), list(range(100, 200)))

    def test_show_from_expanded_tasks_restores_the_chart_and_keeps_the_page(self):
        for width, height, mode in ((1500, 900, 'single'), (844, 390, 'sidecar')):
            with self.subTest(width=width, height=height, mode=mode):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.check_show_from_expanded_tasks(mode)

    def check_show_from_expanded_tasks(self, mode):
        self.open_report(mode=mode, no_text=True)
        self.next()
        self.page.evaluate('''() => {
            localStorage.setItem('fgTl.detailFrac', '0.07');
            window.dispatchEvent(new Event('resize'));
        }''')
        self.page.locator('[data-detail-expand]').press('Enter')
        self.assertTrue(self.page.locator('#timelineView.tl-detail-expanded').count())
        self.assertFalse(self.page.locator('.tl-canvas').is_visible())
        show = self.page.locator('[data-task-show="100"]')
        row = show.locator('..').element_handle()
        show.press('Enter')
        canvas = self.page.locator('.tl-canvas')
        self.assertTrue(canvas.is_visible())
        self.assertTrue(canvas.evaluate('canvas => document.activeElement === canvas'))
        self.assertTrue(canvas.evaluate('''canvas => {
            const box = canvas.getBoundingClientRect();
            return box.top >= 0 && box.top + 39 <= innerHeight;
        }'''))
        self.assertEqual(self.status(), 'Tasks 101–200 of 205')
        self.assertTrue(row.evaluate('row => row.isConnected'))
        position = self.page.evaluate('''() => {
            const probe = fgView('timeline').probe(), pane = document.querySelector('.tl-scroll');
            return {offset: probe.TRACKS[100].y - pane.scrollTop, range: probe.view(),
                fraction: localStorage.getItem('fgTl.detailFrac')};
        }''')
        self.assertEqual(position['offset'], 4)
        self.assertLessEqual(position['range'][0], 100000)
        self.assertGreaterEqual(position['range'][1], 100100)
        self.assertEqual(position['fraction'], '0.07')

    def test_show_an_expanded_track_does_not_rewrite_task_positions(self):
        self.open_report(count=2048, no_text=True)
        self.next()
        self.page.evaluate('''() => {
            window.taskPositionWrites = 0;
            for (const track of fgView('timeline').probe().TRACKS) {
                let position = track.y;
                Object.defineProperty(track, 'y', {get() { return position; },
                    set(next) { taskPositionWrites++; position = next; }});
            }
        }''')
        for target in (100, 190, 100):
            self.page.locator(f'[data-task-show="{target}"]').press('Enter')
        self.assertEqual(self.page.evaluate('taskPositionWrites'), 0)
        self.assertEqual(self.status(), 'Tasks 101–200 of 2048')
        self.assertEqual(self.page.evaluate('''() => fgView('timeline').probe().TRACKS[100].y -
            document.querySelector('.tl-scroll').scrollTop'''), 4)

    def test_sticky_pager_is_visible_without_scrolling_through_a_page(self):
        self.open_report()
        for scroll in (0, 600, 2400):
            with self.subTest(scroll=scroll):
                self.page.locator('.tl-detail').evaluate('(element, scroll) => { element.scrollTop = scroll; }', scroll)
                next_button = self.page.locator('[data-task-page="1"]')
                pane = self.page.locator('.tl-detail').bounding_box()
                button = next_button.bounding_box()
                self.assertGreaterEqual(button['y'], pane['y'])
                self.assertLessEqual(button['y'] + button['height'], pane['y'] + pane['height'])
        self.next()
        self.assertEqual(self.page.locator('.tl-detail').evaluate('(element) => element.scrollTop'), 0)

    def test_manually_short_panel_keeps_headers_and_pager_scrollable(self):
        self.open_report()
        self.page.evaluate("() => { localStorage.setItem('fgTl.detailFrac', '0.1'); fgView('timeline').resize(); }")
        pane = self.page.locator('.tl-detail-short')
        pane.wait_for()
        controls = [pane.locator(selector) for selector in ('.an-task-head', '.an-task-footer')]
        pane.evaluate('panel => panel.scrollTop = 0')
        positions = [control.bounding_box()['y'] for control in controls]
        pane.evaluate('panel => panel.scrollTop = 32')
        for control, position in zip(controls, positions):
            self.assertAlmostEqual(control.bounding_box()['y'], position - 32, delta=1)
        self.next()
        self.assertEqual(self.status(), 'Tasks 101–200 of 205')
        self.page.locator('[data-task-page="-1"]').click()
        self.assertEqual(self.status(), 'Tasks 1–100 of 205')

    def test_direct_page_entry_keeps_focus_rows_and_full_export(self):
        self.open_report(count=1203, mode='sidecar')
        original = self.export_bytes()
        field = self.page.get_by_role('textbox', name='Page', exact=True)
        go = self.page.get_by_role('button', name='Go to page', exact=True)
        self.assertEqual(field.input_value(), '1')
        self.assertEqual(self.page.locator('#tl-task-page-total').inner_text(), 'of 13')
        for value in ('', '0', '14', '2.5', 'abc'):
            with self.subTest(value=value):
                field.fill(value)
                field.press('Enter')
                self.assertEqual(field.evaluate('input => input.validationMessage'), 'Enter a whole page number from 1 to 13.')
                self.assertEqual(self.status(), 'Tasks 1–100 of 1203')
        field.fill('13')
        field.press('Enter')
        self.assertTrue(field.evaluate('input => input === document.activeElement'))
        self.assertEqual(self.ids(), [1200, 1201, 1202])
        self.assertEqual(self.status(), 'Tasks 1201–1203 of 1203')
        self.assertTrue(self.page.get_by_role('button', name='Next page of tasks').is_disabled())
        field.fill(' 003 ')
        go.click()
        self.assertEqual(field.input_value(), '3')
        self.assertTrue(go.evaluate('button => button === document.activeElement'))
        self.assertEqual(self.ids(), list(range(200, 300)))
        self.assertEqual(self.export_bytes(), original)
        self.page.locator('[data-task-query]').fill('task_01200')
        self.assertEqual(self.ids(), [1200])
        self.assertTrue(field.is_hidden())
        self.assertTrue(self.page.locator('.an-task-pages').is_hidden())
        self.page.locator('[data-task-query]').clear()
        self.assertEqual(field.input_value(), '1')
        self.assertEqual(self.status(), 'Tasks 1–100 of 1203')

    def test_short_touch_pager_is_reachable_before_rows_after_page_changes(self):
        context = self.browser.new_context(viewport={'width': 320, 'height': 700}, is_mobile=True, has_touch=True)
        self.addCleanup(context.close)
        self.page = context.new_page()
        self.page.set_default_timeout(10000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.open_report(count=501, mode='sidecar')
        pane = self.page.locator('.tl-detail')
        pages = pane.locator('.an-task-pages')
        field = self.page.get_by_role('textbox', name='Page', exact=True)
        next_button = self.page.get_by_role('button', name='Next page of tasks')
        go = self.page.get_by_role('button', name='Go to page', exact=True)

        def hit(control):
            point = control.evaluate('''node => {
                const bounds = node.getBoundingClientRect(), x = bounds.left + bounds.width / 2, y = bounds.top + bounds.height / 2;
                return bounds.width >= 44 && bounds.height >= 44 && node.contains(document.elementFromPoint(x, y)) ? [x, y] : null;
            }''')
            self.assertIsNotNone(point, f'Control clipped: {control}')
            self.page.touchscreen.tap(*point)

        for width, height in ((320, 700), (844, 390)):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': height})
                pane.evaluate('''panel => {
                    localStorage.setItem('fgTl.detailFrac', '0.10');
                    fgView('timeline').resize();
                    const group = panel.querySelector('.an-task-pages');
                    panel.scrollTop += group.getBoundingClientRect().top - panel.getBoundingClientRect().top - panel.clientTop;
                    panel.scrollLeft = panel.scrollWidth;
                }''')
                self.assertTrue(pages.evaluate("group => group.parentElement.nextElementSibling.matches('.an-task-table')"))
                hit(field)
                field.fill('2')
                hit(go)
                self.assertEqual(self.status(), 'Tasks 101–200 of 501')
                hit(next_button)
                self.assertEqual(self.status(), 'Tasks 201–300 of 501')
                self.assertTrue(pane.evaluate('panel => panel.scrollLeft >= panel.scrollWidth - panel.clientWidth - 1'))
                field.fill('6')
                hit(go)
                self.assertEqual(self.ids(), [500])
                hit(self.page.get_by_role('button', name='Previous page of tasks'))
                self.assertEqual(self.status(), 'Tasks 401–500 of 501')

    def test_short_touch_panel_filters_are_reachable_and_keep_the_query_visible(self):
        context = self.browser.new_context(viewport={'width': 320, 'height': 700}, is_mobile=True, has_touch=True)
        self.addCleanup(context.close)
        self.page = context.new_page()
        self.page.set_default_timeout(10000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.open_report(mode='sidecar')
        pane = self.page.locator('.tl-detail')
        pane.evaluate('''panel => {
            localStorage.setItem('fgTl.detailFrac', '0.07');
            fgView('timeline').resize();
            panel.scrollTop = 0;
            panel.scrollLeft = panel.scrollWidth;
        }''')
        self.assertTrue(pane.evaluate("panel => panel.classList.contains('tl-detail-short')"))
        self.assertGreater(pane.evaluate('panel => panel.scrollLeft'), 0)

        def hit_test(control):
            return control.evaluate('''element => {
                const box = element.getBoundingClientRect();
                return element.contains(document.elementFromPoint((box.left + box.right) / 2, (box.top + box.bottom) / 2));
            }''')

        toggle = self.page.locator('[data-task-filters]')
        query = self.page.locator('[data-task-query]')
        self.assertTrue(hit_test(toggle), 'Filters requires scrolling through task rows')
        toggle.tap()
        self.assertTrue(query.evaluate('input => input === document.activeElement'))
        self.assertTrue(hit_test(query), 'Focused Find is hidden by the short pane')
        self.page.keyboard.insert_text('task_001')
        self.assertEqual(self.status(), 'Tasks 1–100 of 100')
        self.assertTrue(hit_test(query), 'Typing scrolled the focused query out of view')
        self.page.keyboard.press('Escape')
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'false')
        self.assertTrue(toggle.evaluate('button => button === document.activeElement'))
        self.assertTrue(hit_test(toggle))
        self.assertEqual(self.status(), 'Tasks 1–100 of 100')

    def test_touch_targets_both_themes_orientations_and_sidecar(self):
        self.page.close()
        for theme in ('light', 'dark'):
            context = self.browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
            self.addCleanup(context.close)
            self.page = context.new_page()
            self.page.on('pageerror', lambda error: self.errors.append(str(error)))
            self.page.add_init_script(f"localStorage.setItem('theme', '{theme}')")
            self.open_report(mode='sidecar', no_text=True)
            for width, height in ((390, 844), (844, 390)):
                with self.subTest(theme=theme, width=width):
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.assertEqual(self.page.locator('html').get_attribute('data-theme'), theme)
                    self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), width)
                    self.assertFalse(self.page.locator('.an-task-tools').is_visible())
                    if height > 480:
                        row = self.page.locator('[data-task-row]').first.bounding_box()
                        footer = self.page.locator('.an-task-footer').bounding_box()
                        self.assertLessEqual(row['y'] + row['height'], footer['y'])
                    for selector in ('[data-task-page="-1"]', '[data-task-page="1"]', '[data-act="tasks-csv"]', '[data-task-filters]'):
                        self.assertGreaterEqual(self.page.locator(selector).bounding_box()['height'], 44)
                    self.page.locator('[data-task-page="1"]').tap()
                    self.assertEqual(self.status(), 'Tasks 101–200 of 205')
                    self.page.locator('[data-task-page="-1"]').tap()
                    self.assertEqual(self.status(), 'Tasks 1–100 of 205')
                    self.page.locator('[data-task-filters]').tap()
                    self.assertTrue(self.page.locator('.an-task-tools').is_visible())
                    self.assertEqual(self.page.locator('[data-task-filters]').get_attribute('aria-expanded'), 'true')
                    self.page.locator('[data-task-query]').fill('task_001')
                    self.assertEqual(self.status(), 'Tasks 1–100 of 100')
                    self.assertIn('on', self.page.locator('[data-task-filters]').inner_text())
                    self.page.locator('[data-task-filters]').tap()
                    self.assertFalse(self.page.locator('.an-task-tools').is_visible())
                    self.assertEqual(self.page.locator('[data-task-filters]').get_attribute('aria-expanded'), 'false')
                    self.page.locator('[data-task-filters]').tap()
                    self.page.locator('[data-act="tasks-reset"]').tap()
                    self.page.locator('[data-task-filters]').tap()
                    self.assertEqual(self.status(), 'Tasks 1–100 of 205')
            self.page.set_viewport_size({'width': 1500, 'height': 900})
            self.page.wait_for_function("() => !document.getElementById('timelineView').classList.contains('tl-compact')")
            self.assertTrue(self.page.locator('.an-task-tools').is_visible())
            self.assertFalse(self.page.locator('[data-task-filters]').is_visible())
            self.page.locator('[data-task-query]').fill('task_001')
            self.page.set_viewport_size({'width': 390, 'height': 844})
            self.page.wait_for_function("() => document.getElementById('timelineView').classList.contains('tl-compact')")
            self.assertFalse(self.page.locator('.an-task-tools').is_visible())
            self.assertIn('on', self.page.locator('[data-task-filters]').inner_text())
            self.assertEqual(self.status(), 'Tasks 1–100 of 100')
            context.close()
