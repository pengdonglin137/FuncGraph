"""Text event visibility is a reversible presentation choice, never a data filter."""
import io
import json
from pathlib import Path
import threading
import unittest

import helpers
from fglib.textindex import is_trace_event, TextIndexWriter
from fglib.viewer import create_server
from test_kvm import event
from test_uftrace_import import call, stream


class TextEventModelTest(unittest.TestCase):
    def test_classification_keeps_calls_loss_markers_and_headers(self):
        for raw in ('1.0 | 0) task-42 | /* kvm:kvm_entry: vcpu 0 */',
                    '0) | /* sched_wakeup: comm=task pid=42 */',
                    '0) | task-42 => other-43', ' 20) swapper/20-0 => Chrome_ChildIOT-1903485',
                    '0) | /* raw: switch-in */'):
            self.assertTrue(is_trace_event({'raw_line': raw}), raw)
        for raw in ('# tracer: function_graph', 'CPU:0 [LOST 4 EVENTS]',
                    '0) | /* Lost 4 records */', '0) | } /* func ret=0 */',
                    '0) | func();', '0) | /* context is unavailable */',
                    'unrecognized task-42 => other-43'):
            self.assertFalse(is_trace_event({'raw_line': raw}), raw)
        self.assertFalse(is_trace_event({'raw_line': '0) | /* kvm_entry: text */', 'raw_func_name': 'func'}))
        self.assertTrue(is_trace_event({'record_type': 'event', 'display_func_name': 'event_name'}))
        self.assertFalse(is_trace_event({'record_type': 'lost'}))

    def test_index_retains_exact_event_row_numbers_without_changing_legacy_columns(self):
        output = io.BytesIO()
        writer = TextIndexWriter(output, include_raw_functions=True)
        writer.append(2, {}, 'root() {')
        writer.append(5, {'trace-event': '1'}, '/* event: payload */')
        writer.append(9, {}, 'CPU:0 [LOST 2 EVENTS]')
        writer.finish('index.json')
        data = json.loads(output.getvalue())
        self.assertEqual(data['version'], 5)
        self.assertEqual(data['events'], [5])
        self.assertEqual([row[0] for row in data['rows']], [2, 5, 9])


class TextEventBrowserTest(unittest.TestCase):
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
        self.page = self.browser.new_page(viewport={'width': 1400, 'height': 950})
        self.addCleanup(self.page.close)
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open_report(self, mode='single', windowed=False, records=None):
        path = Path(self.kernel.dir, 'events.jsonl')
        if records is None:
            records = [call('entry', 'kernel', 0, 0, 'root', 1), event('kvm_entry', 10),
                       call('entry', 'kernel', 11, 1, 'leaf', 2),
                       call('exit', 'kernel', 12, 1, 'leaf', 2), event('kvm_exit', 20),
                       dict(type='lost', space='kernel', time_ns=event('kvm_entry', 21)['time_ns'],
                            tid=42, cpu=4, count=2), call('exit', 'kernel', 30, 0, 'root', 1)]
        path.write_text(stream(records))
        destination = 'report' if mode == 'sidecar' else 'report.html'
        self.kernel.run(str(path), '--no-config', '--timeline', '--enable-fold', '--filter',
                        '--report-mode', mode, out=destination)
        report = Path(self.kernel.dir, destination)
        if mode == 'sidecar':
            server = create_server(report)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 5)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            url = server.url + ('?text=windowed' if windowed else '')
        else:
            url = report.as_uri()
        self.page.goto(url)
        self.page.evaluate("async () => { await fgSwitchView('text'); }")
        self.page.locator('#textEventsBtn').wait_for()

    def test_toggle_keeps_functions_and_loss_rows_and_does_not_mutate_timeline(self):
        self.open_report()
        self.assertEqual(self.page.locator('[data-trace-event="1"]:visible').count(), 2)
        before = self.page.evaluate("JSON.stringify(fgData.read('tlData'))")
        self.page.locator('#textEventsBtn').click()
        self.assertEqual(self.page.locator('[data-trace-event="1"]:visible').count(), 0)
        self.assertIn('Lost 2 records', self.page.locator('#content').inner_text())
        self.assertIn('leaf', self.page.locator('#content').inner_text())
        self.assertEqual(self.page.locator('#textEventsBtn').get_attribute('aria-pressed'), 'false')
        self.page.locator('#textEventsBtn').click()
        self.assertEqual(self.page.locator('[data-trace-event="1"]:visible').count(), 2)
        self.assertEqual(self.page.evaluate("JSON.stringify(fgData.read('tlData'))"), before)

    def test_hidden_events_stay_hidden_after_filter_reset_folding_and_reload(self):
        self.open_report()
        self.page.locator('#textEventsBtn').click()
        self.page.evaluate("document.getElementById('filterInput').value='func:leaf'; applyFilter()")
        self.page.evaluate('clearFilter()')
        self.page.get_by_role('button', name='Collapse All', exact=True).click()
        self.page.get_by_role('button', name='Expand All', exact=True).click()
        self.assertEqual(self.page.locator('[data-trace-event="1"]:visible').count(), 0)
        self.page.reload()
        self.page.evaluate("async () => { await fgSwitchView('text'); }")
        self.assertEqual(self.page.locator('#textEventsBtn').inner_text(), 'Events: hidden')
        self.assertEqual(self.page.locator('[data-trace-event="1"]:visible').count(), 0)

    def test_sidecar_full_and_windowed_event_navigation_reveals_original_evidence(self):
        for windowed in (False, True):
            with self.subTest(windowed=windowed):
                if self.page.url != 'about:blank':
                    self.page.evaluate('localStorage.clear()')
                self.open_report('sidecar', windowed)
                number = self.page.locator('[data-trace-event="1"]').first.get_attribute('data-line-number')
                self.page.locator('#textEventsBtn').click()
                self.page.wait_for_function("() => Array.from(document.querySelectorAll('[data-trace-event]')).every(row => !row.getClientRects().length)")
                self.page.evaluate('async number => { await fgEnsureTextLine(number); }', int(number))
                self.assertEqual(self.page.locator('#textEventsBtn').inner_text(), 'Events: shown')
                self.assertTrue(self.page.locator('#L' + number).is_visible())

    def test_event_only_and_mobile_both_themes_retain_an_obvious_restore_button(self):
        self.open_report(records=[event('kvm_entry', 10), event('kvm_exit', 20)])
        self.page.set_viewport_size({'width': 390, 'height': 844})
        for theme in ('light', 'dark'):
            self.page.evaluate("theme => document.documentElement.setAttribute('data-theme',theme)", theme)
            self.page.locator('#textEventsBtn').click()
            self.assertTrue(self.page.locator('#textEventsBtn').is_visible())
            self.assertEqual(self.page.locator('[data-trace-event="1"]:visible').count(), 0)
            self.page.locator('#textEventsBtn').click()
            self.assertEqual(self.page.locator('[data-trace-event="1"]:visible').count(), 2)

    def test_plain_function_graph_text_hides_only_event_and_switch_rows(self):
        trace = Path(self.kernel.dir, 'plain.txt')
        trace.write_text('# tracer: function_graph\n'
                         '0) task-42 | root() {\n'
                         '0) task-42 | /* sched_wakeup: comm=other pid=43 */\n'
                         '0) task-42 | /* kvm:kvm_entry: vcpu 0 */\n'
                         ' 0) task-42 => other-43\n'
                         '0) other-43 | /* raw: switch-in */\n'
                         '0) task-42 | leaf();\n'
                         'CPU:0 [LOST 2 EVENTS]\n'
                         '0) task-42 | } /* root */\n')
        self.kernel.run(str(trace), '--no-config', out='plain.html')
        self.page.goto(Path(self.kernel.dir, 'plain.html').as_uri())
        self.page.locator('#textEventsBtn').click()
        self.assertEqual(self.page.locator('[data-trace-event="1"]').count(), 4)
        self.assertEqual(self.page.locator('[data-trace-event="1"]:visible').count(), 0)
        self.assertNotIn('task-42 => other-43', self.page.locator('#content').inner_text())
        self.assertIn('leaf', self.page.locator('#content').inner_text())
        self.assertIn('LOST 2 EVENTS', self.page.locator('#content').inner_text())

    def test_report_without_events_does_not_add_a_toolbar_button(self):
        trace = Path(self.kernel.dir, 'calls.txt')
        trace.write_text('0) task-42 | work();\n')
        self.kernel.run(str(trace), '--no-config', out='calls.html')
        self.page.goto(Path(self.kernel.dir, 'calls.html').as_uri())
        self.assertEqual(self.page.locator('#textEventsBtn').count(), 0)
        self.assertIn('work', self.page.locator('#content').inner_text())
