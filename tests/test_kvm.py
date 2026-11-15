"""KVM event evidence uses the normal Timeline without inventing function calls."""
import csv
import io
from pathlib import Path
import threading
import unittest

import helpers
from fglib import export, timeline
from fglib.viewer import create_server
from test_event_only import eligible
from test_timeline import invariant_errors
from test_uftrace_import import ORIGIN, HEADER, build, call, stream


def event(name, timestamp, tid=42, cpu=4, text=None):
    if text is None:
        text = 'vcpu 4 reason CPUID rip 0xffffabcd' if name == 'kvm_exit' else 'vcpu 4, rip 0xffff1234'
    return dict(type='event', space='kernel', time_ns=ORIGIN + timestamp, tid=tid, cpu=cpu,
                name='kvm:' + name, text=text)


def capture():
    return [event('kvm_entry', 10), event('kvm_cpuid', 15, text='function 0, rax 0x1234'),
            event('kvm_exit', 20), event('kvm_entry', 30), event('kvm_exit', 40),
            event('kvm_pio', 50, text='pio_write at 0x3f8 size 1 count 1')]


def text_record(record):
    seconds, nanoseconds = divmod(record['time_ns'], 10 ** 9)
    return (f'{seconds}.{nanoseconds:09d} |   {record["cpu"]}) qemu-{record["tid"]} | '
            f'/* {record["name"]}: {record["text"]} */')


def text_model(records, clock='mono'):
    lines = [f'# raw: timestamps in ns; clock={clock}']
    lines.extend(text_record(record) if isinstance(record, dict) else record for record in records)
    return timeline.build_timeline(lines).to_data()


class KvmModelTest(unittest.TestCase):
    def test_text_and_uftrace_share_lossless_events_and_exact_window_geometry(self):
        for data in (text_model(capture()), build(capture()).to_data()):
            with self.subTest(kind='uftrace' if 'uftrace' in data else 'text'):
                self.assertIs(eligible(data), data)
                self.assertEqual(data['n'], 0)
                self.assertEqual(data['cols']['dur'], [])
                self.assertTrue(data['caps']['kvm'])
                self.assertEqual(data['t0'], ORIGIN + 10)
                self.assertEqual(data['kvm']['origin_ns'], str(ORIGIN + 10))
                self.assertEqual([record[1:5] for record in data['kvm']['intervals']],
                                 [[0, 10, 0, 4], [10, 20, 1, 4], [20, 30, 0, 4], [30, 40, 1, 4]])
                self.assertEqual([record[7] for record in data['kvm']['intervals']], [0, 0, 0, 1])
                self.assertEqual([record[5:7] for record in data['kvm']['intervals']],
                                 [[0, 2], [2, 3], [3, 4], [4, -1]])
                self.assertEqual([data['strings'][record[5]] for record in data['kvm']['events']],
                                 [record['text'] for record in capture()])
                self.assertEqual(data['kvm']['issues'], [])
                self.assertEqual(invariant_errors(data), [])

    def test_other_vm_with_same_vcpu_number_has_a_different_host_thread(self):
        records = [event('kvm_entry', 10), event('kvm_entry', 12, tid=100),
                   event('kvm_exit', 20), event('kvm_exit', 32, tid=100)]
        data = text_model(records)
        closed = [record for record in data['kvm']['intervals'] if not record[7]]
        self.assertEqual(len(closed), 2)
        self.assertNotEqual(closed[0][0], closed[1][0])
        self.assertEqual([record[2] - record[1] for record in closed], [10, 20])

    def test_missing_initial_entry_does_not_fabricate_a_guest_window(self):
        data = text_model([event('kvm_exit', 10), event('kvm_entry', 20)])
        intervals = data['kvm']['intervals']
        self.assertEqual(intervals[0][1:4], [0, 10, 1])
        self.assertEqual(intervals[1][1:4], [10, 10, 0])
        self.assertEqual(intervals[1][6:8], [-1, 1])

    def test_single_boundary_and_auxiliary_event_remain_points_not_calls(self):
        for name in ('kvm_entry', 'kvm_exit', 'kvm_cpuid', 'vcpu_match_mmio'):
            with self.subTest(name=name):
                data = text_model([event(name, 10)])
                self.assertIsNotNone(eligible(data))
                self.assertEqual(data['n'], 0)
                self.assertEqual(len(data['kvm']['events']), 1)
                self.assertTrue(all(record[1] == record[2] for record in data['kvm']['intervals']))

    def test_repeated_boundary_leaves_an_unknown_gap_and_preserves_every_event(self):
        data = text_model([event('kvm_entry', 10), event('kvm_entry', 20), event('kvm_exit', 30)])
        self.assertEqual(data['kvm']['issues'], [[0, 1, 'repeated_boundary']])
        self.assertEqual(data['kvm']['intervals'][0][1:3], [10, 20])
        self.assertEqual(len(data['kvm']['events']), 3)

    def test_loss_on_any_cpu_invalidates_open_windows_without_invented_boundaries(self):
        for loss in ('CPU:4 [LOST 8 EVENTS]', 'CPU:9 [LOST ? EVENTS]'):
            data = text_model([event('kvm_entry', 10), loss, event('kvm_exit', 30)])
            self.assertTrue(data['kvm']['losses'])
            self.assertEqual(data['kvm']['issues'], [[0, -1, 'lost_events']])
            self.assertFalse(any(not record[7] for record in data['kvm']['intervals']))
        records = [event('kvm_entry', 10), dict(type='lost', space='kernel', time_ns=ORIGIN + 20,
                   tid=None, cpu=4, count=2), event('kvm_exit', 30)]
        data = build(records).to_data()
        self.assertFalse(any(not record[7] for record in data['kvm']['intervals']))

    def test_cpu_migration_requires_a_verified_shared_clock(self):
        for clock, closed in (('mono', 1), ('global', 1), ('local', 0), ('unknown', 0)):
            with self.subTest(clock=clock):
                data = text_model([event('kvm_entry', 10), event('kvm_exit', 20, cpu=5)], clock)
                self.assertEqual(sum(not record[7] for record in data['kvm']['intervals']), closed)
                if not closed:
                    self.assertEqual(data['kvm']['issues'][0][2], 'unverified_migration_clock')

    def test_changed_vcpu_id_is_not_silently_joined(self):
        data = text_model([event('kvm_entry', 10), event('kvm_exit', 20, text='vcpu 5 reason HLT')])
        self.assertEqual(data['kvm']['issues'], [[0, 1, 'vcpu_changed']])
        self.assertFalse(any(not record[7] for record in data['kvm']['intervals']))

    def test_without_vcpu_id_thread_identity_is_enough_but_id_is_not_guessed(self):
        data = text_model([event('kvm_entry', 10, text='vcpu_pc: 0xffff0000'),
                           event('kvm_exit', 20, text='IRQ: 0x0')])
        self.assertEqual(data['kvm']['intervals'][0][4], -1)
        self.assertEqual(data['kvm']['intervals'][0][7], 0)

    def test_backwards_time_does_not_create_negative_or_overlapping_windows(self):
        data = text_model([event('kvm_entry', 20), event('kvm_exit', 10), event('kvm_entry', 30),
                           event('kvm_exit', 40)])
        self.assertEqual(data['kvm']['issues'], [[0, 1, 'out_of_order']])
        self.assertTrue(all(record[2] >= record[1] for record in data['kvm']['intervals']))
        self.assertEqual(data['kvm']['intervals'][0][1:3], [20, 30])

    def test_vcpu_field_variants_and_unqualified_text_events(self):
        for value in ('vcpu 4', 'vcpu_id=4', 'vcpu_id: 4', 'vcpu=4'):
            records = [event('kvm_entry', 10, text=value), event('kvm_exit', 20, text=value)]
            lines = [text_record(record).replace('kvm:kvm_', 'kvm_') for record in records]
            data = timeline.build_timeline(lines).to_data()
            self.assertEqual(data['kvm']['intervals'][0][4], 4)

    def test_no_thread_identity_never_pairs_by_host_cpu_alone(self):
        lines = [text_record(record).replace('qemu-42 | ', '') for record in capture()]
        data = timeline.build_timeline(lines).to_data()
        self.assertEqual(len(data['kvm']['events']), 6)
        self.assertEqual(data['kvm']['intervals'], [])
        self.assertTrue(any(issue[2] == 'missing_host_tid' for issue in data['kvm']['issues']))

    def test_function_calls_keep_original_names_durations_and_counts(self):
        records = [call('entry', 'kernel', 0, 0, 'kvm_arch_vcpu_ioctl_run', 1), *capture(),
                   call('exit', 'kernel', 100, 0, 'kvm_arch_vcpu_ioctl_run', 1)]
        data = build(records).to_data()
        self.assertEqual(data['n'], 1)
        self.assertEqual(data['cols']['dur'], [100])
        self.assertEqual(data['strings'][data['cols']['name'][0]], 'kvm_arch_vcpu_ioctl_run')
        self.assertEqual(data['kvm']['intervals'][-1][2], 100)
        self.assertEqual(invariant_errors(data), [])

    def test_non_kvm_and_untimed_inputs_keep_the_previous_schema(self):
        for lines in ([], ['0) qemu-42 | /* kvm_entry: vcpu 4 */'],
                      ['1.000000 | 0) qemu-42 | /* unknown: kvm_entry: vcpu 4 */']):
            data = timeline.build_timeline(lines).to_data()
            self.assertNotIn('kvm', data)

    def test_json_export_retains_all_original_evidence_without_counting_calls(self):
        data = build(capture()).to_data()
        result = export.build(data)
        self.assertEqual(result['functions'], [])
        self.assertEqual(result['meta']['calls'], 0)
        self.assertEqual(len(result['kvm']['events']), 6)
        self.assertEqual(result['kvm']['events'][1]['timestamp_ns'], str(ORIGIN + 15))
        self.assertEqual(result['kvm']['events'][1]['payload'], 'function 0, rax 0x1234')
        self.assertEqual(result['kvm']['intervals'][0]['kind'], 'guest_window')
        self.assertIsNone(result['kvm']['intervals'][-1]['end_event'])

    def test_unverified_clock_does_not_extend_open_window_to_another_cpu(self):
        data = text_model([event('kvm_entry', 10), event('kvm_cpuid', 10000, tid=55, cpu=0)], clock='local')
        self.assertEqual(data['kvm']['intervals'][0][1:3], [0, 0])

    def test_serializing_twice_does_not_change_the_event_or_string_tables(self):
        builder = timeline.build_timeline([text_record(event('kvm_entry', 10))])
        first = builder.to_data()
        strings = first['strings'][:]
        second = builder.to_data()
        self.assertEqual(first['kvm'], second['kvm'])
        self.assertEqual(second['strings'], strings)

    def test_known_host_tid_with_no_cpu_column_is_not_mistaken_for_migration(self):
        lines = [text_record(record).replace('   4) ', '  ') for record in
                 [event('kvm_entry', 10), event('kvm_exit', 20)]]
        data = timeline.build_timeline(lines).to_data()
        self.assertEqual(data['kvm']['issues'], [])
        self.assertEqual(data['kvm']['intervals'][0][1:3], [0, 10])

    def test_same_timestamp_boundaries_are_not_stretched_into_fake_durations(self):
        data = text_model([event('kvm_entry', 10), event('kvm_exit', 10), event('kvm_entry', 10)])
        self.assertEqual(len(data['kvm']['events']), 3)
        self.assertEqual(len(data['kvm']['intervals']), 3)
        self.assertTrue(all(record[1] == record[2] == 0 for record in data['kvm']['intervals']))
        self.assertEqual(data['kvm']['issues'], [])

    def test_structured_auxiliary_payload_is_preserved_including_newlines_and_spaces(self):
        payload = 'first line\nsecond line </script>  \n'
        data = build([event('auxiliary_event', 10, text=payload)]).to_data()
        self.assertEqual(data['strings'][data['kvm']['events'][0][5]], payload)
        self.assertEqual(data['kvm']['intervals'], [])
        self.assertIsNotNone(eligible(data))

    def test_explicit_kvm_namespace_retains_auxiliary_names_without_guessing_boundaries(self):
        data = text_model([event('auxiliary_event', 10, text='counter=1')])
        self.assertEqual(data['strings'][data['kvm']['events'][0][4]], 'auxiliary_event')
        self.assertEqual(data['kvm']['intervals'], [])

    def test_clock_name_prefix_does_not_establish_shared_clock_authority(self):
        data = text_model([event('kvm_entry', 10), event('kvm_exit', 20, cpu=5)], clock='mono-unverified')
        self.assertFalse(any(not record[7] for record in data['kvm']['intervals']))


class KvmBrowserTest(unittest.TestCase):
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
        self.page = self.browser.new_page(viewport={'width': 1500, 'height': 950})
        self.addCleanup(self.page.close)
        self.page.set_default_timeout(10000)
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open_report(self, records=None, mode='single', no_text=False, viewport=None):
        path = Path(self.kernel.dir, 'kvm.jsonl')
        path.write_text(stream(capture() if records is None else records))
        options = ['--no-config', '--timeline', '--filter', '--enable-fold', '--report-mode', mode]
        if no_text:
            options.append('--no-text-view')
        destination = 'report' if mode == 'sidecar' else 'report.html'
        self.kernel.run(str(path), *options, out=destination)
        report = Path(self.kernel.dir, destination)
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
        if viewport:
            self.page.set_viewport_size(viewport)
        self.page.goto(url)
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.page.locator('.tl-canvas').wait_for()

    def window_point(self, offset=5):
        return self.page.evaluate('''offset => {
            const api = fgView('timeline').probe().kvm;
            const index = api.intervals.findIndex(record => record[1] <= offset && record[2] > offset);
            return api.xyOf({kind:'interval', index});
        }''', offset)

    def click_window(self):
        point = self.window_point()
        self.page.mouse.click(point['x'], point['y'])
        self.page.locator('.tl-kvm-table').wait_for()

    def test_kvm_only_single_file_hover_select_navigation_original_events_and_csv(self):
        self.open_report()
        self.assertIn('KVM', self.page.locator('.tl-event-only').inner_text())
        point = self.window_point()
        self.page.mouse.move(point['x'], point['y'])
        self.page.wait_for_function("document.querySelector('.tl-tip').textContent.includes('Guest window')")
        self.click_window()
        self.assertIn('Guest window · CPUID', self.page.locator('.tl-detail-title').inner_text())
        self.assertEqual(self.page.locator('.tl-kvm-table tbody tr').count(), 3)
        self.assertIn('function 0, rax 0x1234', self.page.locator('.tl-detail').inner_text())
        with self.page.expect_download() as download:
            self.page.locator('[data-kvm-export]').click()
        rows = list(csv.DictReader(Path(download.value.path()).read_text().splitlines()))
        self.assertEqual([row['timestamp_ns'] for row in rows], [str(ORIGIN + value) for value in (10, 15, 20)])
        self.page.locator('[data-kvm-step="1"]').click()
        self.assertIn('Host window · CPUID', self.page.locator('.tl-detail-title').inner_text())
        self.page.locator('[data-kvm-fit]').click()
        self.assertIn('kvm_exit → kvm_entry', self.page.locator('.tl-detail').inner_text())
        self.page.locator('[data-kvm-step="-1"]').click()
        self.assertIn('Guest window', self.page.locator('.tl-detail-title').inner_text())
        self.page.locator('.tl-kvm-table [data-line]').first.click()
        self.page.wait_for_function("fgCurrentView() === 'text'")
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.page.locator('.tl-kvm-table').wait_for()

    def test_sidecar_and_no_text_reuse_the_same_timeline(self):
        self.open_report(mode='sidecar', no_text=True)
        self.click_window()
        self.assertEqual(self.page.locator('.tl-kvm-table [data-line]').count(), 0)
        self.assertIn('line 3', self.page.locator('.tl-kvm-table').inner_text())
        self.assertEqual(self.page.evaluate("fgData.read('tlData').n"), 0)

    def test_open_end_is_explicit_and_collapse_does_not_remove_evidence(self):
        self.open_report()
        point = self.window_point(35)
        self.page.mouse.click(point['x'], point['y'])
        self.assertIn('end not recorded', self.page.locator('.tl-detail').inner_text())
        self.assertIn('observed extent', self.page.locator('.tl-kvm-facts').inner_text())
        header = self.page.evaluate('''() => {
            const track = fgView('timeline').probe().TRACKS.find(task => task.kvmEvents.length);
            const canvas = document.querySelector('.tl-canvas').getBoundingClientRect();
            return {x:canvas.x + 20, y:canvas.y + track.y + 10};
        }''')
        self.page.mouse.click(header['x'], header['y'])
        self.assertTrue(self.page.evaluate("fgView('timeline').probe().TRACKS.find(task => task.kvmEvents.length).collapsed"))
        point = self.window_point()
        self.page.mouse.move(point['x'], point['y'])
        self.page.wait_for_function("document.querySelector('.tl-tip').textContent.includes('Guest window')")
        self.assertEqual(self.page.evaluate("fgData.read('tlData').kvm.events.length"), 6)

    def test_event_payload_is_escaped_not_executed(self):
        self.open_report([event('kvm_cpuid', 10, text='<img src=x onerror="window.kvmInjected=1">')])
        point = self.page.evaluate("fgView('timeline').probe().kvm.xyOf({kind:'event', index:0})")
        self.page.mouse.click(point['x'], point['y'])
        self.page.locator('.tl-kvm-table').wait_for()
        self.assertIn('<img', self.page.locator('.tl-kvm-payload').inner_text())
        self.assertEqual(self.page.locator('.tl-detail img').count(), 0)
        self.assertIsNone(self.page.evaluate('window.kvmInjected'))

    def test_many_events_are_paged_and_csv_is_not_limited_to_visible_rows(self):
        records = [event('kvm_entry', 10)] + [event('kvm_cpuid', value, text=f'function {value}')
                  for value in range(11, 170)] + [event('kvm_exit', 200)]
        self.open_report(records)
        point = self.page.evaluate("fgView('timeline').probe().kvm.xyOf({kind:'interval', index:0})")
        self.page.mouse.click(point['x'], point['y'])
        self.assertEqual(self.page.locator('.tl-kvm-table tbody tr').count(), 50)
        self.assertIn('of 161', self.page.locator('.tl-process-tools').inner_text())
        self.page.locator('[data-kvm-page="1"]').click()
        self.assertIn('Events 51–100', self.page.locator('.tl-process-tools').inner_text())
        with self.page.expect_download() as download:
            self.page.locator('[data-kvm-export]').click()
        self.assertEqual(len(list(csv.DictReader(Path(download.value.path()).read_text().splitlines()))), 161)

    def test_mobile_fullscreen_and_detail_resize_keep_canvas_visible(self):
        self.open_report(no_text=True, viewport={'width': 390, 'height': 844})
        self.click_window()
        self.page.locator('[data-act=max]').click()
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)
        resizer = self.page.locator('.tl-resizer').bounding_box()
        self.page.mouse.move(resizer['x'] + 20, resizer['y'] + 2)
        self.page.mouse.down()
        self.page.mouse.move(resizer['x'] + 20, resizer['y'] - 40)
        self.page.mouse.up()
        self.assertGreater(self.page.locator('.tl-canvas').bounding_box()['height'], 40)
        self.assertEqual(self.page.evaluate("fgData.read('tlData').kvm.events.length"), 6)

    def test_call_filter_does_not_hide_the_kvm_track_in_a_function_report(self):
        records = [call('entry', 'kernel', 0, 0, 'kvm_arch_vcpu_ioctl_run', 1), *capture(),
                   call('exit', 'kernel', 100, 0, 'kvm_arch_vcpu_ioctl_run', 1)]
        self.open_report(records)
        helpers.open_timeline_tools(self.page)
        self.page.locator('.tl-ftext').fill('func:not_a_recorded_function')
        self.page.locator('.tl-ftext').press('Enter')
        self.page.wait_for_function("fgFilterState.lines !== null && !fgFilterProgress")
        self.page.locator('[data-act=fhide]').click()
        self.assertTrue(self.page.evaluate("fgView('timeline').probe().TRACKS.filter(task => task.kvmEvents.length).every(task => !task.hidden)"))
        self.page.locator('[data-act=options]').click()
        self.page.evaluate("fgView('timeline').probe().kvm.select({kind:'interval', index:0})")
        self.assertEqual(self.page.locator('.tl-kvm-table tbody tr').count(), 3)

    def test_touch_selection_and_both_themes_keep_details_readable(self):
        self.page.close()
        self.page = self.browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
        self.addCleanup(self.page.close)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.open_report(no_text=True)
        for theme in ('light', 'dark'):
            self.page.evaluate("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
            point = self.window_point()
            self.page.touchscreen.tap(point['x'], point['y'])
            self.page.locator('.tl-kvm-table').wait_for()
            self.assertTrue(self.page.locator('.tl-detail-title').is_visible())
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)
            self.page.locator('.tl-close').tap()

    def test_coincident_points_keep_every_event_reachable_without_text(self):
        self.open_report([event('kvm_entry', 10), event('kvm_exit', 10),
                          event('kvm_entry', 10)], no_text=True)
        point = self.page.evaluate("fgView('timeline').probe().kvm.xyOf({kind:'event',index:0})")
        self.page.mouse.click(point['x'], point['y'])
        self.assertEqual(self.page.locator('.tl-kvm-table tbody tr').count(), 3)
        self.assertIn('Events 1–3 of 3', self.page.locator('.tl-process-tools').inner_text())
