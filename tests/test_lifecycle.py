"""Bounded lifecycle browsing and marker rendering keep every recorded milestone."""
import csv
import io
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest

import helpers
from fglib.viewer import create_server
from test_uftrace_import import HEADER, ORIGIN, capture, lifecycle_event, stream


class LifecycleBrowserTest(unittest.TestCase):
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
        self.directory = tempfile.TemporaryDirectory(prefix='fg-lifecycle-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.page = self.browser.new_page(viewport={'width': 1500, 'height': 900})
        self.addCleanup(self.page.close)
        self.page.set_default_timeout(10000)
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open_report(self, count=205, mode='single', no_text=False, metadata=HEADER, rewrite=None, comm=None):
        records = capture()
        if comm is not None:
            for record in records:
                record['comm'] = comm
        records += [lifecycle_event('exec' if index % 2 == 0 else 'exit', index * 1000)
                    for index in range(count)]
        records.sort(key=lambda record: record['time_ns'])
        trace = self.root / 'capture.jsonl'
        trace.write_text(stream(records, metadata), encoding='utf-8')
        options = ['--no-config', '--timeline', '--enable-fold', '--filter', '--report-mode', mode]
        if no_text:
            options.append('--no-text-view')
        html, unused_result = self.kernel.run(str(trace), *options, out='report' if mode == 'sidecar' else 'report.html')
        report = Path(self.kernel.dir) / ('report' if mode == 'sidecar' else 'report.html')
        if rewrite:
            match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
            self.assertIsNotNone(match)
            data = json.loads(match[2])
            rewrite(data)
            report.write_text(html[:match.start(2)] + json.dumps(data) + html[match.end(2):], encoding='utf-8')
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

    def open_panel(self):
        button = self.page.locator('[data-act=process-events]')
        if not button.is_visible():
            self.page.locator('[data-act=options]').click()
        button.click()
        self.page.locator('.tl-process-table').wait_for()

    def status(self):
        return self.page.locator('.tl-process-tools [role=status]').inner_text()

    def test_pages_replace_rows_preserve_order_and_support_keyboard(self):
        self.open_report()
        self.open_panel()
        previous = self.page.locator('[data-process-page="-1"]')
        following = self.page.locator('[data-process-page="1"]')
        self.assertEqual(self.status(), 'Events 1–100 of 205')
        self.assertTrue(previous.is_disabled())
        self.assertEqual(self.page.locator('.tl-process-table tbody tr').count(), 100)
        following.press('Enter')
        self.assertEqual(self.status(), 'Events 101–200 of 205')
        self.assertEqual(self.page.locator('.tl-process-table tbody tr').count(), 100)
        self.assertEqual(self.page.locator('.tl-process-table tbody tr').first.get_attribute('data-process-index'), '100')
        self.assertEqual(self.page.evaluate("document.activeElement.dataset.processPage"), '1')
        following.press('Space')
        self.assertEqual(self.status(), 'Events 201–205 of 205')
        self.assertTrue(following.is_disabled())
        self.assertEqual(self.page.locator('.tl-process-table tbody tr').count(), 5)
        previous.click()
        self.assertEqual(self.status(), 'Events 101–200 of 205')
        self.page.locator('.tl-close').click()
        self.open_panel()
        self.assertEqual(self.status(), 'Events 1–100 of 205')

    def test_empty_single_and_exact_page_boundaries(self):
        for count in (0, 1, 100, 101):
            with self.subTest(count=count):
                self.open_report(count=count)
                if not count:
                    self.assertEqual(self.page.locator('[data-act=process-events]').count(), 0)
                    continue
                self.open_panel()
                self.assertEqual(self.page.locator('.tl-process-table tbody tr').count(), min(100, count))
                self.assertEqual(self.page.locator('[data-process-page="1"]').is_disabled(), count <= 100)
                self.assertIn('exec (new image installed)', self.page.locator('.tl-process-table tbody tr').first.inner_text())

    def test_csv_contains_unloaded_events_exact_timestamps_and_safe_task_names(self):
        self.open_report(comm='=SUM(1,2)<task>')
        self.open_panel()
        self.assertEqual(self.page.locator('.tl-process-table task').count(), 0)
        with self.page.expect_download() as download:
            self.page.locator('[data-process-export]').click()
        rows = list(csv.DictReader(io.StringIO(Path(download.value.path()).read_text(encoding='utf-8-sig'))))
        self.assertEqual(download.value.suggested_filename, 'funcgraph-process-events.csv')
        self.assertEqual(len(rows), 205)
        for index, row in enumerate(rows):
            self.assertEqual(row['event_index'], str(index))
            self.assertEqual(row['event'], 'exec' if index % 2 == 0 else 'exit')
            self.assertEqual(row['trace_time_ns'], str(index * 1000))
            self.assertEqual(row['timestamp_ns'], str(ORIGIN + index * 1000))
            self.assertEqual(row['comm'], "'=SUM(1,2)<task>")
            self.assertEqual(row['clock'], 'mono')
            self.assertEqual(row['clock_aligned'], 'true')
            self.assertGreater(int(row['trace_line']), 0)
        self.assertEqual(self.page.locator('.tl-process-table tbody tr').count(), 100)

    def test_filter_refresh_keeps_the_lifecycle_panel_and_current_page(self):
        self.open_report()
        self.page.evaluate("() => fgView('timeline').probe().select(0)")
        self.open_panel()
        self.page.locator('[data-process-page="1"]').click()
        self.page.fill('.tl-ftext', 'space:user')
        self.page.press('.tl-ftext', 'Enter')
        self.page.wait_for_function("document.querySelector('.tl-filterbar').classList.contains('on')")
        self.assertEqual(self.status(), 'Events 101–200 of 205')
        self.assertIn('Unfiltered recorded kernel milestones', self.page.locator('.tl-process-note').inner_text())
        self.page.locator('[data-act=fclear]').click()
        self.assertEqual(self.status(), 'Events 101–200 of 205')

    def test_time_jump_is_available_without_text_and_keeps_zoom_and_clock_separation(self):
        self.open_report(no_text=True, metadata=dict(HEADER, clock=None))
        self.page.evaluate("() => { const model = fgView('timeline').probe(); model.select(0); model.zoomTo(0, 2000, -1); }")
        self.open_panel()
        before = self.page.evaluate("fgView('timeline').probe().view()")
        self.page.locator('[data-process-time="50"]').press('Enter')
        after = self.page.evaluate("fgView('timeline').probe().view()")
        self.assertAlmostEqual(after[1] - after[0], before[1] - before[0])
        self.assertAlmostEqual((after[1] + after[0]) / 2, 50000)
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), -1)
        self.assertEqual(self.page.evaluate('fgCurrentView()'), 'timeline')
        self.assertEqual(self.status(), 'Events 1–100 of 205')
        with self.page.expect_download() as download:
            self.page.locator('[data-process-export]').click()
        rows = list(csv.DictReader(Path(download.value.path()).read_text(encoding='utf-8-sig').splitlines()))
        self.assertTrue(all(row['clock_aligned'] == 'false' and row['clock'] == '' for row in rows))

    def test_time_jump_keeps_filtered_kernel_tracks_hidden(self):
        self.open_report(metadata=dict(HEADER, clock=None))
        self.open_panel()
        self.page.fill('.tl-ftext', 'space:user')
        self.page.press('.tl-ftext', 'Enter')
        self.page.locator('[data-act=fhide]').click()
        hidden = self.page.evaluate("fgView('timeline').probe().TRACKS.map(task => task.hidden)")
        self.assertIn(True, hidden)
        self.page.locator('[data-process-time="50"]').click()
        self.assertEqual(self.page.locator('.tl-ftext').input_value(), 'space:user')
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().TRACKS.map(task => task.hidden)"), hidden)
        self.assertEqual(self.status(), 'Events 1–100 of 205')

    def test_short_resized_panel_does_not_cover_pagination_or_event_buttons(self):
        self.open_report()
        self.open_panel()
        self.page.evaluate("() => { localStorage.setItem('fgTl.detailFrac', '0.07'); window.dispatchEvent(new Event('resize')); }")
        self.page.locator('.tl-detail-short').wait_for()
        self.assertEqual(self.page.locator('.tl-process-head').evaluate('(element) => getComputedStyle(element).position'), 'static')
        self.page.locator('[data-process-page="1"]').click()
        self.assertEqual(self.status(), 'Events 101–200 of 205')
        self.page.locator('[data-process-time="150"]').click()
        self.assertEqual(self.page.evaluate('fgCurrentView()'), 'timeline')

    def test_sidecar_text_links_load_only_when_requested(self):
        requests = []
        self.page.on('request', lambda request: requests.append(request.url))
        self.open_report(mode='sidecar')
        self.open_panel()
        self.page.locator('[data-process-page="1"]').click()
        self.assertFalse(any('text.html' in url for url in requests))
        link = self.page.locator('.tl-process-table tbody tr').first.locator('[data-line]')
        line = link.get_attribute('data-line')
        link.press('Enter')
        self.page.wait_for_function("fgCurrentView() === 'text'")
        self.page.locator('#L' + line).wait_for()
        self.assertIn('sched_process_exec', self.page.locator('#L' + line).inner_text())

    def test_compact_layout_and_pagination_fit_both_themes(self):
        self.open_report()
        for theme in ('light', 'dark'):
            for width in (390, 1500):
                with self.subTest(theme=theme, width=width):
                    self.page.evaluate('(theme) => document.documentElement.setAttribute("data-theme", theme)', theme)
                    self.page.set_viewport_size({'width': width, 'height': 844})
                    self.open_panel()
                    self.assertTrue(self.page.locator('[data-process-export]').is_visible())
                    self.assertLessEqual(self.page.locator('.tl-detail').evaluate('(element) => element.scrollWidth - element.clientWidth'), 1)
                    self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), width)
                    self.page.locator('[data-process-page="1"]').click()
                    self.assertEqual(self.status(), 'Events 101–200 of 205')
                    self.page.locator('.tl-close').click()

    def test_touch_targets_and_time_navigation_work_in_both_orientations(self):
        context = self.browser.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2,
                                           is_mobile=True, has_touch=True)
        self.addCleanup(context.close)
        self.page = context.new_page()
        self.page.set_default_timeout(10000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.open_report(no_text=True)
        for width, height in ((390, 844), (844, 390)):
            with self.subTest(width=width, height=height):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.open_panel()
                self.page.wait_for_function("document.getElementById('timelineView').classList.contains('tl-compact')")
                for selector in ('[data-process-export]', '[data-process-page="1"]', '[data-process-time="0"]'):
                    self.assertGreaterEqual(self.page.locator(selector).bounding_box()['height'], 44)
                if height == 844:
                    panel = self.page.locator('.tl-detail').bounding_box()
                    event = self.page.locator('[data-process-time="0"]').bounding_box()
                    self.assertLessEqual(event['y'] + event['height'], panel['y'] + panel['height'])
                self.page.locator('[data-process-page="1"]').tap()
                self.assertEqual(self.status(), 'Events 101–200 of 205')
                self.page.locator('[data-process-time="150"]').tap()
                self.assertEqual(self.page.evaluate('fgCurrentView()'), 'timeline')
                self.page.locator('.tl-close').tap()

    def test_unsafe_relative_timestamp_is_not_exported_as_exact_absolute_time(self):
        def rewrite(data):
            data['uftrace']['lifecycle'][0][1] = 2 ** 53

        self.open_report(count=1, rewrite=rewrite)
        self.open_panel()
        with self.page.expect_download() as download:
            self.page.locator('[data-process-export]').click()
        rows = list(csv.DictReader(Path(download.value.path()).read_text(encoding='utf-8-sig').splitlines()))
        self.assertEqual(rows[0]['timestamp_ns'], '')

    def marker_draw(self, start, end):
        return self.page.evaluate('''async ({start, end}) => {
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            window.lifecycleReads = 0;
            window.lifecyclePixels = [];
            const model = fgView('timeline').probe();
            model.zoomTo(start, end, -1);
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const width = document.querySelector('.tl-scroll').clientWidth;
            const [left, right] = model.view(), scale = width / (right - left);
            const reads = lifecycleReads, pixels = lifecyclePixels.slice();
            const expected = Array.from(new Set(fgData.read('tlData').uftrace.lifecycle
                .filter(event => event[1] >= left && event[1] <= right)
                .map(event => Math.min(width - 1, Math.max(0, Math.round((event[1] - left) * scale)))))).sort((left, right) => left - right);
            return {reads, pixels, expected, width};
        }''', dict(start=start, end=end))

    def instrument_markers(self):
        self.page.evaluate('''() => {
            window.lifecycleReads = 0; window.lifecyclePixels = [];
            for (const event of fgData.read('tlData').uftrace.lifecycle) {
                const time = event[1];
                Object.defineProperty(event, '1', {get() { lifecycleReads++; return time; }});
            }
            const fill = CanvasRenderingContext2D.prototype.fillRect;
            CanvasRenderingContext2D.prototype.fillRect = function(x, y, width, height) {
                if (this.canvas.matches('.tl-canvas') && width === 1 && height === 6) lifecyclePixels.push(x);
                return fill.call(this, x, y, width, height);
            };
        }''')

    def test_large_marker_draws_skip_offscreen_and_same_pixel_events(self):
        count = 100000

        def rewrite(data):
            task, unused_time, unused_phase, line = data['uftrace']['lifecycle'][0]
            data['uftrace']['lifecycle'] = [[task, index * 1000, 'exec', line] for index in range(count)]

        self.open_report(count=1, rewrite=rewrite)
        self.instrument_markers()
        sparse = self.marker_draw(50_000_000, 50_003_000)
        self.assertLess(sparse['reads'], 100)
        self.assertEqual(sorted(set(sparse['pixels'])), sparse['expected'])
        dense = self.marker_draw(0, count * 1000)
        self.assertLessEqual(len(dense['pixels']), dense['width'])
        self.assertLess(dense['reads'], dense['width'] * 24 + 100)
        self.assertEqual(sorted(dense['pixels']), dense['expected'])
        self.open_panel()
        self.assertEqual(self.page.locator('.tl-process-table tbody tr').count(), 100)
        self.assertEqual(self.status(), 'Events 1–100 of 100000')

    def test_unordered_and_duplicate_markers_preserve_report_order_and_boundary_pixels(self):
        times = [5000, 0, 5000, 1200, 1201, 2500, 5001, 9999, 10000, 920, 2080, 2081]

        def rewrite(data):
            task, unused_time, unused_phase, line = data['uftrace']['lifecycle'][0]
            data['uftrace']['lifecycle'] = [[task, time, 'exec', line] for time in times]

        self.open_report(count=1, rewrite=rewrite)
        self.instrument_markers()
        for start, end in ((0, 10000), (1000, 2000), (1200, 5000), (5000, 5001), (1100, 1150)):
            with self.subTest(start=start, end=end):
                result = self.marker_draw(start, end)
                self.assertEqual(sorted(result['pixels']), result['expected'])
        self.assertEqual(self.page.evaluate("fgData.read('tlData').uftrace.lifecycle.map(event => event[1])"), times)

    def test_same_pixel_markers_are_kept_on_each_task_including_event_only_tracks(self):
        def rewrite(data):
            task, unused_time, unused_phase, line = data['uftrace']['lifecycle'][0]
            other = len(data['tasks'])
            data['tasks'].append(dict(pid=84, comm='event-only', kind=0, cpu=1))
            data['uftrace']['lifecycle'] = [[task, 5000, 'exec', line], [other, 5000, 'exit', line],
                                           [other, 5000, 'exec', line]]

        self.open_report(count=1, rewrite=rewrite)
        self.instrument_markers()
        result = self.marker_draw(4900, 5100)
        self.assertEqual(len(result['pixels']), 2)
        self.assertEqual(result['pixels'][0], result['pixels'][1])
        self.open_panel()
        self.assertEqual(self.status(), 'Events 1–3 of 3')
        self.assertEqual(self.page.locator('.tl-process-table td', has_text='event-only').count(), 2)


if __name__ == '__main__':
    unittest.main()
