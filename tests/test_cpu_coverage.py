"""CPU observations must not be mistaken for complete mixed-task scheduling."""

from itertools import product
import json
from pathlib import Path
import threading
import unittest

import helpers
from fglib.viewer import create_server
from test_uftrace_import import HEADER, ORIGIN, build, call, capture, stream
from test_timeline import decode, invariant_errors

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


def scheduling_capture():
    records = capture()
    records.extend([
        call('event', 'kernel', 80, 0, 'sched:sched_switch', 0, tid=10,
             text='prev_comm=before prev_pid=10 prev_prio=120 prev_state=S ==> next_comm=demo next_pid=42 next_prio=120'),
        call('event', 'kernel', 980, 0, 'sched:sched_switch', 0,
             text='prev_comm=demo prev_pid=42 prev_prio=120 prev_state=S ==> next_comm=after next_pid=20 next_prio=120')])
    return sorted(records, key=lambda record: record['time_ns'])


class CpuCoverageDataTest(unittest.TestCase):
    def test_real_mixed_sample_retains_the_gap_without_inventing_cpu_placement(self):
        records = [json.loads(line) for line in (Path(helpers.ROOT) / 'samples/sample_mixed.jsonl').read_text().splitlines()]
        metadata = records[0]
        calls = [record for record in records[1:-1]
                 if not (record['type'] == 'event' and record['space'] == 'kernel')]
        data = build(calls, metadata).to_data()
        index = next(index for index, name in enumerate(data['cols']['name']) if data['strings'][name] == 'main')
        self.assertEqual(data['cols']['start'][index], 0)
        self.assertEqual(data['cols']['dur'][index], 6189320)
        self.assertEqual(data['cols']['cpu'][index], -1)
        self.assertEqual(data['tasks'][0]['runs'], [[5775459, 6142775, 4]])
        self.assertFalse(data['caps']['sched_events'])
        self.assertEqual(metadata['kernel_settings']['events/sched/sched_switch/enable'], '1')
        self.assertFalse(any('sched_switch' in record.get('name', '') for record in calls))

    def test_real_sample_switches_preserve_absolute_calls_and_pre_main_cpu_placement(self):
        records = [json.loads(line) for line in (Path(helpers.ROOT) / 'samples/sample_mixed.jsonl').read_text().splitlines()]
        metadata, records = records[0], records[1:-1]
        data = build(records, metadata).to_data()
        without_scheduling = build([record for record in records
                                   if not (record['type'] == 'event' and record['space'] == 'kernel')], metadata).to_data()
        self.assertEqual(invariant_errors(data), [])
        self.assertTrue(data['caps']['sched_events'])
        self.assertEqual(data['n'], 714)
        self.assertEqual(data['stats']['lost'], 0)
        self.assertEqual(data['stats']['truncated'], 0)

        def calls(model):
            starts, ends = decode(model)
            cols, origin = model['cols'], int(model['uftrace']['origin_ns'])
            return [(model['strings'][cols['name'][index]], model['tasks'][cols['task'][index]]['pid'],
                     origin + starts[index], origin + ends[index], cols['flags'][index],
                     None if cols['parent'][index] < 0 else
                     (model['strings'][cols['name'][cols['parent'][index]]], origin + starts[cols['parent'][index]]))
                    for index in range(model['n'])]

        self.assertEqual(calls(data), calls(without_scheduling))
        task = next(task for task in data['tasks'] if task['pid'] == 9416)
        self.assertEqual(task['runs'][-1], [430595341, 704840737, 4])
        main = next(record for record in calls(data) if record[0] == 'main')
        self.assertEqual(main[2:4], (88351483766336, 88351489955656))
        self.assertEqual(main[2] - (int(data['uftrace']['origin_ns']) + task['runs'][-1][0]), 240612849)

    def test_actual_switches_can_precede_main_and_do_not_change_function_timestamps(self):
        data = build(scheduling_capture()).to_data()
        self.assertTrue(data['caps']['sched_events'])
        task = next(task for task in data['tasks'] if task['pid'] == 42)
        index = next(index for index, name in enumerate(data['cols']['name']) if data['strings'][name] == 'main')
        self.assertEqual(data['cols']['start'][index], 20)
        self.assertEqual(data['cols']['dur'][index], 800)
        self.assertEqual(task['runs'][0][0], 0)
        self.assertEqual(task['runs'][-1][1], 900)

    def test_kernel_switches_work_without_kernel_function_calls(self):
        records = [record for record in scheduling_capture()
                   if record['space'] == 'user' or record['type'] == 'event']
        data = build(records).to_data()
        self.assertTrue(data['caps']['sched_events'])
        self.assertEqual(data['n'], 2)
        self.assertEqual(data['uftrace']['spaces'], ['kernel', 'user'])
        self.assertTrue(data['uftrace']['aligned'])
        task = next(task for task in data['tasks'] if task['pid'] == 42)
        self.assertEqual(task['runs'], [[0, 900, 0]])
        self.assertIsNone(data['cols']['cpu'])
        self.assertEqual(data['cols']['start'][0], 20)
        self.assertEqual(data['cols']['dur'][0], 800)

    def test_enabled_readbacks_without_events_do_not_establish_scheduling(self):
        metadata = {**HEADER, 'kernel_settings': {**HEADER['kernel_settings'],
                    'events/sched/sched_switch/enable': '1',
                    'events/sched/sched_waking/enable': '1',
                    'events/sched/sched_wakeup/enable': '1'}}
        data = build(capture(), metadata).to_data()
        self.assertFalse(data['caps']['switch_markers'])
        self.assertFalse(data['caps']['sched_events'])
        self.assertFalse(data['caps']['sched_waking'])
        self.assertEqual(data['offcpu'], [])
        self.assertEqual(data['tasks'][0]['runs'], [[140, 850, 0]])

    def test_perf_scheduling_does_not_invent_cpu_runs(self):
        records = [record for record in capture() if record['space'] == 'user']
        records.extend([
            dict(type='event', origin='perf', space=None, time_ns=ORIGIN + timestamp,
                 tid=42, cpu=None, stream=7, name=name, out=out, preempt=False)
            for timestamp, name, out in [(80, 'linux:sched-in', False), (980, 'linux:sched-out', True)]])
        builder = build(records)
        data = builder.to_data()
        self.assertFalse(data['caps']['sched_events'])
        self.assertEqual(data['offcpu'], [])
        self.assertTrue(all(not task['runs'] for task in data['tasks']))
        self.assertIsNone(data['cols']['cpu'])
        for name in ('linux:sched-in', 'linux:sched-out'):
            self.assertTrue(any(name in row.raw_line for row in builder.parsed))

    def test_perf_and_kernel_scheduling_are_not_counted_twice(self):
        records = scheduling_capture()
        expected = build(records).to_data()
        records.extend([
            dict(type='event', origin='perf', space=None, time_ns=ORIGIN + timestamp,
                 tid=42, cpu=None, stream=7, name=name, out=out, preempt=False)
            for timestamp, name, out in [(80, 'linux:sched-in', False), (980, 'linux:sched-out', True)]])
        data = build(records).to_data()
        self.assertTrue(data['caps']['sched_events'])
        for key in ('tasks', 'offcpu'):
            self.assertEqual(data[key], expected[key])
        self.assertEqual(data['stats']['offcpu'], expected['stats']['offcpu'])


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class CpuCoverageBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel()
        cls.addClassCleanup(cls.kernel.close)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)
        cls.urls = {}
        for kind, records in [('observed', capture()), ('scheduled', scheduling_capture()),
                              ('user', [record for record in capture() if record['space'] == 'user'])]:
            trace = Path(cls.kernel.dir, kind + '.jsonl')
            trace.write_text(stream(records))
            for mode in ('single', 'sidecar'):
                destination = kind + '-' + mode + ('.html' if mode == 'single' else '')
                cls.kernel.run(str(trace), '--no-config', '--timeline', '--report-mode', mode, out=destination)
                report = Path(cls.kernel.dir, destination)
                if mode == 'single':
                    cls.urls[kind, mode] = report.as_uri()
                else:
                    server = create_server(report)
                    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
                    thread.start()
                    cls.addClassCleanup(thread.join, 5)
                    cls.addClassCleanup(server.server_close)
                    cls.addClassCleanup(server.shutdown)
                    cls.urls[kind, mode] = server.url

    def setUp(self):
        self.context = None
        self.errors = []

    def tearDown(self):
        if self.context:
            self.context.close()
        self.assertEqual(self.errors, [])

    def open(self, kind='observed', mode='single', theme='light', width=1440):
        if self.context:
            self.context.close()
        self.context = self.browser.new_context(viewport={'width': width, 'height': 900}, has_touch=width < 600)
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.urls[kind, mode])
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.page.evaluate('theme => document.documentElement.dataset.theme = theme', theme)
        self.page.evaluate('''() => {
            window.cpuDashCalls = 0;
            const original = CanvasRenderingContext2D.prototype.setLineDash;
            CanvasRenderingContext2D.prototype.setLineDash = function(segments) {
                if (segments.join(',') === '3,3') cpuDashCalls++;
                return original.call(this, segments);
            };
            fgView('timeline').resize();
        }''')

    def hover_cpu_segment(self):
        point = self.page.evaluate('''() => {
            const model = fgView('timeline').probe(), data = fgData.read('tlData');
            const run = data.tasks.find(task => task.pid === 42).runs[0];
            const canvas = document.querySelector('.tl-canvas').getBoundingClientRect();
            const view = model.view(), midpoint = (run[0] + run[1]) / 2;
            return [canvas.x + (midpoint - view[0]) / (view[1] - view[0]) * canvas.width, canvas.y + 28];
        }''')
        self.page.mouse.move(*point)
        self.page.locator('.tl-tip').wait_for(state='visible')
        return self.page.locator('.tl-tip').inner_text()

    def test_missing_switches_are_labelled_observed_in_both_modes_and_themes(self):
        for mode, theme, width in product(('single', 'sidecar'), ('light', 'dark'), (390, 1440)):
            with self.subTest(mode=mode, theme=theme, width=width):
                self.open(mode=mode, theme=theme, width=width)
                self.page.wait_for_function('() => cpuDashCalls > 0')
                tooltip = self.hover_cpu_segment()
                self.assertIn('observation span on CPU', tooltip)
                self.assertIn('first observed', tooltip)
                self.assertIn('last observed', tooltip)
                self.assertIn('not continuous running time', tooltip)
                self.assertIn('CPU placement outside these observations is unknown', tooltip)
                self.assertIn('not task start/exit', self.page.locator('.tl-notes').text_content())

    def test_actual_switches_use_the_scheduling_presentation_without_claiming_main_start(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open('scheduled', mode)
                tooltip = self.hover_cpu_segment()
                self.assertIn('recorded on CPU', tooltip)
                self.assertNotIn('observation span', tooltip)
                self.assertNotIn('No sched_switch records', tooltip)
                self.assertNotIn('No usable kernel sched_switch records', self.page.locator('.tl-notes').text_content())

    def test_user_only_capture_does_not_invent_cpu_tracks(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open('user', mode)
                self.assertFalse(self.page.evaluate('fgData.read("tlData").tasks.some(task => task.runs.length)'))
                self.assertIn('main', self.page.evaluate('fgView("timeline").probe().S.join(" ")'))
                self.assertNotIn('No usable kernel sched_switch records', self.page.locator('.tl-notes').text_content())

    def test_observation_hover_does_not_modify_runs_function_times_or_selection(self):
        self.open()
        before = self.page.evaluate('''() => ({data: JSON.stringify(fgData.read('tlData')),
            selected: fgView('timeline').probe().sel(), view: fgView('timeline').probe().view()})''')
        self.hover_cpu_segment()
        after = self.page.evaluate('''() => ({data: JSON.stringify(fgData.read('tlData')),
            selected: fgView('timeline').probe().sel(), view: fgView('timeline').probe().view()})''')
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
