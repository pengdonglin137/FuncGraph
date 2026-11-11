"""Task browsing bounds live rows without narrowing complete exports."""
import csv
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

    def open_report(self, count=205, mode='single', no_text=False):
        self.serial += 1
        records = []
        for index in range(count):
            comm = '=SUM(1,2)<task>' if index == 0 else f'task_{index:05}'
            records += [call('entry', 'user', index * 1000, 0, 'work', 1, tid=index + 1, comm=comm),
                        call('exit', 'user', index * 1000 + 100, 0, 'work', 1, tid=index + 1)]
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

    def export(self):
        with self.page.expect_download() as download:
            self.page.locator('[data-act="tasks-csv"]').click()
        self.assertEqual(download.value.suggested_filename, 'funcgraph-tasks.csv')
        return list(csv.DictReader(Path(download.value.path()).read_text(encoding='utf-8-sig').splitlines()))

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
        self.assertTrue(self.page.locator('.tl-detail').evaluate('(element) => element.classList.contains("tl-detail-short")'))
        for selector in ('.an-task-head', '.an-task-footer'):
            self.assertEqual(self.page.locator(selector).evaluate('(element) => getComputedStyle(element).position'), 'static')
        self.next()
        self.assertEqual(self.status(), 'Tasks 101–200 of 205')
        self.page.locator('[data-task-page="-1"]').click()
        self.assertEqual(self.status(), 'Tasks 1–100 of 205')

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
