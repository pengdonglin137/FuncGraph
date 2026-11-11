"""Scheduling and lifecycle reports need no fabricated function calls."""
import contextlib
import csv
import io
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest

import helpers
from fglib import timeline
from fglib.cli import build_timeline_data
from fglib.viewer import create_server
from test_timeline import SYNTH_WAKE, invariant_errors
from test_uftrace_import import HEADER, ORIGIN, build, call, invoke, lifecycle_event, stream


def event(name, timestamp, tid=42, text=''):
    return dict(type='event', space='kernel', time_ns=ORIGIN + timestamp, tid=tid, cpu=0,
                name=name, text=text)


def scheduling():
    return [event('sched:sched_switch', 100,
                  text='prev_comm=demo prev_pid=42 prev_prio=120 prev_state=S ==> '
                       'next_comm=worker next_pid=7 next_prio=120'),
            event('sched:sched_waking', 200, tid=7, text='comm=demo pid=42 prio=120 target_cpu=000'),
            event('sched:sched_switch', 300, tid=7,
                  text='prev_comm=worker prev_pid=7 prev_prio=120 prev_state=R ==> '
                       'next_comm=demo next_pid=42 next_prio=120')]


def text_scheduling():
    return '\n'.join(line for line in SYNTH_WAKE.splitlines()
                     if 'sched_' in line or '=>' in line or line.strip().startswith('---')) + '\n'


def eligible(data):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return build_timeline_data([], None, [], SimpleNamespace(tail_calls=False), structured_data=data)


class EventOnlyModelTest(unittest.TestCase):
    def test_lifecycle_only_preserves_exact_origin_lines_and_zero_calls(self):
        data = build([lifecycle_event('exec', 10), lifecycle_event('exit', 10000)]).to_data()
        self.assertIs(eligible(data), data)
        self.assertEqual(data['n'], 0)
        self.assertEqual(data['mode'], 'abs')
        self.assertEqual(data['t0'], ORIGIN + 10)
        self.assertEqual(data['uftrace']['origin_ns'], str(ORIGIN + 10))
        self.assertEqual(data['uftrace']['spaces'], ['kernel'])
        self.assertEqual(data['uftrace']['lifecycle'], [[0, 0, 'exec', 3], [0, 9990, 'exit', 4]])
        self.assertEqual(invariant_errors(data), [])

    def test_single_milestone_is_a_point_not_a_measured_lifetime(self):
        for phase in ('exec', 'exit'):
            with self.subTest(phase=phase):
                data = build([lifecycle_event(phase, 10)]).to_data()
                self.assertIsNotNone(eligible(data))
                self.assertEqual(data['tasks'][0]['runs'], [[0, 0, 0]])
                self.assertEqual(data['uftrace']['lifecycle'], [[0, 0, phase, 3]])

    def test_scheduling_only_has_observed_runs_waits_wakes_and_cpu_zero(self):
        data = build(scheduling()).to_data()
        self.assertIsNotNone(eligible(data))
        self.assertEqual(data['n'], 0)
        self.assertEqual(data['uftrace']['spaces'], ['kernel'])
        self.assertTrue(data['caps']['sched_events'])
        self.assertEqual(data['offcpu'][0][1:3], [0, 200])
        self.assertEqual(data['wakeups'][0][1], 100)
        self.assertTrue(any(run == [0, 200, 0] for task in data['tasks'] for run in task['runs']))
        self.assertEqual(invariant_errors(data), [])

    def test_wakeup_only_is_eligible_without_a_switch_or_cpu_duration(self):
        for name in ('sched:sched_waking', 'sched:sched_wakeup', 'sched:sched_wakeup_new'):
            with self.subTest(name=name):
                data = build([event(name, 100, tid=7, text='comm=demo pid=42 prio=120 target_cpu=000')]).to_data()
                self.assertIsNotNone(eligible(data))
                self.assertEqual(data['offcpu'], [])
                self.assertEqual(len(data['wakeups']), 1)
                self.assertFalse(any(run[1] > run[0] for task in data['tasks'] for run in task['runs']))

    def test_text_scheduling_is_eligible_without_function_calls(self):
        data = timeline.build_timeline(text_scheduling().splitlines()).to_data()
        self.assertIsNotNone(eligible(data))
        self.assertEqual(data['n'], 0)
        self.assertTrue(data['offcpu'])
        self.assertTrue(data['wakeups'])
        self.assertEqual(invariant_errors(data), [])

    def test_empty_metadata_loss_and_unknown_events_do_not_invent_a_timeline(self):
        fixtures = [[], [dict(type='event', origin='perf', time_ns=ORIGIN, name='comm')],
                    [dict(type='lost', space='kernel', time_ns=ORIGIN, tid=None, cpu=0, count=8)],
                    [event('unknown', 100), event('unknown', 200)],
                    [event('sched:sched_switch', 100, text='invalid payload')]]
        for records in fixtures:
            with self.subTest(records=records):
                self.assertIsNone(eligible(build(records).to_data()))

    def test_untimed_events_do_not_turn_call_order_into_measured_time(self):
        data = timeline.build_timeline(['0) worker-7 | /* sched_waking: comm=demo pid=42 prio=120 target_cpu=000 */']).to_data()
        self.assertEqual(data['n'], 0)
        self.assertNotIn(data['mode'], ('abs', 'rel'))
        self.assertIsNone(eligible(data))

    def test_kernel_events_keep_unverified_user_clocks_in_separate_task_namespaces(self):
        users = [call('entry', 'user', 10, 0, 'main', 1), call('exit', 'user', 400, 0, 'main', 1)]
        for clock, count in (('mono', 1), ('local', 2)):
            for kernel in (scheduling(), [event('unknown', 100)]):
                with self.subTest(clock=clock, kernel=kernel):
                    records = sorted(users + kernel, key=lambda record: record['time_ns'])
                    data = build(records, dict(HEADER, clock=clock)).to_data()
                    self.assertEqual(data['uftrace']['spaces'], ['kernel', 'user'])
                    self.assertEqual(data['uftrace']['aligned'], clock == 'mono')
                    self.assertEqual(sum(task['pid'] == 42 for task in data['tasks']), count)
                    self.assertEqual(data['stats']['user_kernel_boundaries'], 0)
                    self.assertEqual(data['n'], 1)
                    self.assertEqual(invariant_errors(data), [])

    def test_perf_metadata_and_loss_do_not_turn_user_calls_into_mixed_observations(self):
        records = [call('entry', 'user', 10, 0, 'main', 1),
                   dict(type='event', origin='perf', time_ns=ORIGIN + 20, name='comm'),
                   dict(type='lost', space='kernel', time_ns=ORIGIN + 30, tid=None, cpu=0, count=1),
                   call('exit', 'user', 40, 0, 'main', 1)]
        data = build(records, dict(HEADER, clock='local')).to_data()
        self.assertEqual(data['uftrace']['spaces'], ['user'])
        self.assertEqual(len(data['tasks']), 1)


class EventOnlyReportTest(unittest.TestCase):
    def setUp(self):
        self.kernel = helpers.FakeKernel()
        self.addCleanup(self.kernel.close)

    def test_delivery_modes_with_and_without_text(self):
        trace = Path(self.kernel.dir) / 'events.jsonl'
        trace.write_text(stream([lifecycle_event('exec', 10), *scheduling(), lifecycle_event('exit', 400)]))
        for mode, threshold in (('single', None), ('sidecar', None), ('auto', 0), ('auto', 10 ** 9)):
            for no_text in (False, True):
                with self.subTest(mode=mode, threshold=threshold, no_text=no_text):
                    options = ['--no-config', '--timeline', '--filter', '--report-mode', mode]
                    if threshold is not None:
                        options += ['--sidecar-threshold', str(threshold)]
                    if no_text:
                        options += ['--no-text-view']
                    html, result = self.kernel.run(str(trace), *options, out=f'{mode}-{threshold}-{no_text}')
                    self.assertIn('id="timelineView"', html)
                    self.assertIn('0 slices', result.stdout)
                    self.assertIn('observations only', result.stderr)
                    self.assertNotIn('timeline tab omitted', result.stderr)
                    if no_text:
                        self.assertTrue('data-default-view="timeline"' in html, 'Timeline-only reports must open Timeline')
                    else:
                        self.assertTrue('data-view="text"' in html, 'Text navigation must remain available')

    def test_scheduling_text_and_unknown_events_keep_distinct_cli_behavior(self):
        trace = Path(self.kernel.dir) / 'events.txt'
        trace.write_text(text_scheduling())
        html, result = self.kernel.run(str(trace), '--no-config', '--timeline', '--no-text-view')
        self.assertIn('id="timelineView"', html)
        self.assertIn('observations only', result.stderr)
        trace = Path(self.kernel.dir) / 'unknown.jsonl'
        trace.write_text(stream([event('unknown', 100), event('unknown', 200)]))
        html, result = self.kernel.run(str(trace), '--no-config', '--timeline')
        self.assertNotIn('id="timelineView"', html)
        self.assertIn('timeline tab omitted', result.stderr)

    def test_recorded_events_need_no_kernel_source_or_binary(self):
        trace = Path(self.kernel.dir) / 'standalone.jsonl'
        trace.write_text(stream([lifecycle_event('exec', 10), lifecycle_event('exit', 400)]))
        output = Path(self.kernel.dir) / 'standalone.html'
        result = invoke(trace, '--timeline', '--no-text-view', '--output', output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(output.is_file())
        data = helpers.json_script(output.read_text(), 'tlData')
        self.assertEqual(data['n'], 0)
        self.assertTrue(data['no_text'])
        trace.write_text(stream([event('unknown', 100)]))
        result = invoke(trace, '--timeline', '--no-text-view', '--output', output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('timeline tab omitted', result.stderr)


class EventOnlyBrowserTest(unittest.TestCase):
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
        self.directory = tempfile.TemporaryDirectory(prefix='fg-event-only-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.page = self.browser.new_page(viewport={'width': 1500, 'height': 900})
        self.addCleanup(self.page.close)
        self.page.set_default_timeout(10000)
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open_report(self, records, mode='single', no_text=False, metadata=HEADER, text=False):
        trace = self.root / ('events.txt' if text else 'events.jsonl')
        trace.write_text(text_scheduling() if text else stream(records, metadata))
        options = ['--no-config', '--timeline', '--filter', '--enable-fold', '--report-mode', mode]
        if no_text:
            options.append('--no-text-view')
        destination = 'report.html' if mode == 'single' else 'report'
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

    def action(self, name):
        button = self.page.locator(f'[data-act="{name}"]')
        if not button.is_visible():
            self.page.locator('[data-act="options"]').click()
        button.click()

    def test_lifecycle_only_navigation_csv_tasks_and_text(self):
        self.open_report([lifecycle_event('exec', 10), lifecycle_event('exit', 10000)])
        self.assertTrue(self.page.locator('.tl-event-only').is_visible())
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().N"), 0)
        self.action('process-events')
        self.assertEqual(self.page.locator('.tl-process-table tbody tr').count(), 2)
        with self.page.expect_download() as download:
            self.page.locator('[data-process-export]').click()
        rows = list(csv.DictReader(io.StringIO(Path(download.value.path()).read_text(encoding='utf-8-sig'))))
        self.assertEqual([row['timestamp_ns'] for row in rows], [str(ORIGIN + 10), str(ORIGIN + 10000)])
        self.page.locator('[data-process-time="1"]').click()
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), -1)
        self.page.locator('.tl-close').click()
        self.action('an-tasks')
        row = self.page.locator('[data-task-row]').first
        self.assertEqual(row.locator('td').nth(3).inner_text(), '0')
        self.assertEqual(row.locator('td').nth(7).inner_text(), '9.990 us')
        self.page.locator('[data-task-show]').first.click()
        self.page.locator('.tl-close').click()
        self.action('process-events')
        self.page.locator('.tl-process-table [data-line]').first.click()
        self.assertEqual(self.page.evaluate('fgCurrentView()'), 'text')

    def test_sched_only_cpu_wait_range_and_zero_call_analysis(self):
        self.open_report(scheduling(), mode='sidecar', no_text=True)
        tracks = self.page.evaluate("fgView('timeline').probe().TRACKS.map(task => ({pid: task.pid, y: task.y, h: task.h}))")
        self.assertTrue(all(track['h'] > 0 for track in tracks if track['pid'] in (7, 42)))
        self.page.evaluate("fgView('timeline').probe().range(0, 200)")
        self.assertIn('No calls overlap this range', self.page.locator('.tl-detail').inner_text())
        self.page.locator('.tl-close').click()
        self.page.evaluate("() => { const model = fgView('timeline').probe(); model.wait(model.TRACKS.find(task => task.off.length).off[0]); }")
        detail = self.page.locator('.tl-detail').inner_text()
        self.assertIn('worker', detail)
        self.assertNotIn('undefined', detail)
        self.assertNotIn('NaN', detail)
        self.page.locator('.tl-close').click()
        self.action('an-tasks')
        with self.page.expect_download() as download:
            self.page.locator('[data-act="tasks-csv"]').click()
        rows = list(csv.DictReader(Path(download.value.path()).read_text(encoding='utf-8-sig').splitlines()))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['calls'] == '0' for row in rows))

    def test_wakeup_only_includes_both_identities_without_inventing_cpu_time(self):
        self.open_report([scheduling()[1]])
        facts = self.page.evaluate("fgView('timeline').probe().TRACKS.map(task => ({pid: task.pid, h: task.h}))")
        self.assertEqual({fact['pid'] for fact in facts}, {7, 42})
        self.assertTrue(all(fact['h'] > 0 for fact in facts))
        self.action('an-tasks')
        self.assertEqual(self.page.locator('[data-task-row]').count(), 2)
        self.assertEqual(self.page.locator('[data-task-row] td:nth-child(8)').all_inner_texts(), ['0 ns', '0 ns'])
        self.open_report([], text=True)
        notes = self.page.locator('.tl-notes').inner_text()
        for absent in ('No BTF', 'No return values', 'No call sites', 'tracing overhead', 'kernel limitation'):
            self.assertNotIn(absent, notes)

    def test_shared_text_filter_does_not_hide_event_context(self):
        self.open_report([lifecycle_event('exec', 10), *scheduling(), lifecycle_event('exit', 400)])
        self.action('process-events')
        before = self.page.evaluate("fgView('timeline').probe().TRACKS.map(task => task.hidden)")
        helpers.open_timeline_tools(self.page)
        self.page.locator('.tl-ftext').fill('pid:99999')
        self.page.locator('.tl-ftext').press('Enter')
        self.page.wait_for_function("window.fgFilterState && window.fgFilterState.lines !== null && !window.fgFilterProgress")
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().TRACKS.map(task => task.hidden)"), before)
        self.assertTrue(self.page.locator('[data-act="fhide"]').is_disabled())
        self.assertIn('event tracks unfiltered', self.page.locator('.tl-fcount').inner_text())
        self.assertEqual(self.page.locator('.tl-process-table tbody tr').count(), 2)
        self.page.locator('.tl-close').click()
        self.page.evaluate("() => { const model = fgView('timeline').probe(); model.wait(model.TRACKS.find(task => task.off.length).off[0]); }")
        self.assertIn('worker', self.page.locator('.tl-detail').inner_text())

    def test_mobile_single_point_in_both_themes_has_no_overflow_or_fake_lifetime(self):
        self.page.close()
        for theme in ('light', 'dark'):
            with self.subTest(theme=theme):
                context = self.browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
                self.addCleanup(context.close)
                self.page = context.new_page()
                self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                self.page.add_init_script(f"localStorage.setItem('theme', '{theme}')")
                self.open_report([lifecycle_event('exec', 10)], no_text=True)
                self.assertEqual(self.page.locator('html').get_attribute('data-theme'), theme)
                for width, height in ((390, 844), (844, 390)):
                    with self.subTest(width=width, height=height):
                        self.page.set_viewport_size({'width': width, 'height': height})
                        self.assertTrue(self.page.locator('.tl-event-only').is_visible())
                        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                        self.action('an-tasks')
                        self.assertEqual(self.page.locator('[data-task-row] td').nth(7).inner_text(), '0 ns')
                        self.assertTrue(self.page.locator('[data-task-show]').is_enabled())
                        self.page.locator('[data-task-show]').tap()
                        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), -1)
                        self.page.screenshot(path=str(self.root / f'event-only-{theme}-{width}.png'))
                        self.page.locator('.tl-close').tap()
                context.close()

    def test_unverified_mixed_clock_warning_includes_kernel_sched_without_kernel_calls(self):
        records = [call('entry', 'user', 10, 0, 'main', 1), *scheduling(), call('exit', 'user', 400, 0, 'main', 1)]
        self.open_report(records, metadata=dict(HEADER, clock='local'))
        self.assertEqual(self.page.locator('.tl-event-only').count(), 0)
        self.assertIn('Clock alignment is not verified', self.page.locator('.tl-notes').inner_text())
        self.assertIn('User', self.page.locator('.tl-space-key').inner_text())
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().TRACKS.filter(task => task.pid === 42).length"), 2)

    def test_reports_with_calls_keep_existing_sched_only_track_visibility(self):
        records = [call('entry', 'user', 10, 0, 'main', 1), scheduling()[1],
                   call('exit', 'user', 400, 0, 'main', 1)]
        self.open_report(records)
        self.assertEqual(self.page.locator('.tl-event-only').count(), 0)
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().TRACKS.find(task => task.pid === 7).h"), 0)
        self.assertTrue(self.page.locator('[data-act="fhide"]').is_enabled())
