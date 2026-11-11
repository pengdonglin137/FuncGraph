"""Precise Timeline positioning without changing recorded data or analysis scope."""
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest

import helpers
from benchmark_range import fixture
from fglib.viewer import create_server
from test_uftrace_import import HEADER, ORIGIN, capture, lifecycle_event, stream


class PositionBrowserTest(unittest.TestCase):
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
        cls.kernel.run(helpers.fixture('pi4_sched'), '--no-config', '--timeline', '--filter')
        cls.template = Path(cls.kernel.dir) / 'out.html'

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='fg-position-')
        self.addCleanup(self.directory.cleanup)
        self.serial = 0
        self.errors = []
        self.context = self.browser.new_context(viewport={'width': 1500, 'height': 900})
        self.addCleanup(self.context.close)
        self.page = None

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open_report(self, mode='abs', update=None):
        self.serial += 1
        if self.page and not self.page.is_closed():
            self.page.close()
        report = Path(self.directory.name) / f'position-{self.serial}.html'
        fixture(self.template, report, 2000)
        html = report.read_text(encoding='utf-8')
        match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
        data = json.loads(match[2])
        data['mode'] = mode
        if update:
            update(data)
        report.write_text(html[:match.start(2)] + json.dumps(data).replace('<', r'\u003c') + html[match.end(2):], encoding='utf-8')
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(report.as_uri())
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")

    def open_position(self):
        if not self.page.locator('[data-act=position]').is_visible():
            self.page.locator('[data-act=options]').click()
        self.page.locator('[data-act=position]').click()
        self.assertTrue(self.page.locator('.tl-position').evaluate('dialog => dialog.open'))

    def position(self, start, end=''):
        self.page.locator('#tlPositionStart').fill(start)
        self.page.locator('#tlPositionEnd').fill(end)
        self.page.locator('[data-position-go]').click()

    def view(self):
        return self.page.evaluate("fgView('timeline').probe().view()")

    def open_capture(self, records=None, sidecar=False, no_text=False):
        trace = Path(self.directory.name) / 'capture.jsonl'
        trace.write_text(stream(capture() if records is None else records, HEADER), encoding='utf-8')
        destination = Path(self.directory.name) / ('sidecar' if sidecar else 'capture.html')
        options = ['--no-config', '--timeline', '--filter', '--report-mode', 'sidecar' if sidecar else 'single']
        if no_text:
            options.append('--no-text-view')
        self.kernel.run(str(trace), *options, out=str(destination))
        if sidecar:
            server = create_server(destination)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 5)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            url = server.url
        else:
            url = destination.as_uri()
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(url)
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")

    def test_range_and_center_keep_selection_and_analysis_panel(self):
        self.open_report()
        self.page.evaluate("fgView('timeline').probe().select(120)")
        before = self.page.locator('.tl-detail').inner_text()
        self.open_position()
        self.position('100 us', '0.2ms')
        self.assertEqual(self.view(), [100000, 200000])
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 120)
        self.assertEqual(self.page.locator('.tl-detail').inner_text(), before)

        self.open_position()
        self.position('1ms')
        self.assertEqual(self.view(), [950000, 1050000])
        self.assertEqual(self.page.locator('.tl-detail').inner_text(), before)

        self.page.evaluate("fgView('timeline').probe().range(100000, 200000)")
        summary = self.page.locator('.tl-detail').inner_text()
        self.open_position()
        self.position('1ns', '2ns')
        self.assertEqual(self.view(), [1, 2])
        self.assertEqual(self.page.locator('.tl-detail').inner_text(), summary)
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), -1)

        extent = self.page.evaluate("fgView('timeline').probe().sEnd.reduce((maximum, end) => Math.max(maximum, end), 1)")
        self.open_position()
        self.position('0ns', str(extent) + 'ns')
        self.open_position()
        self.position('0ns')
        start, end = self.view()
        self.assertAlmostEqual(end - start, extent)
        self.assertAlmostEqual(start, -extent * 0.02)
        self.assertEqual(self.page.locator('.tl-detail').inner_text(), summary)

    def test_decimal_units_are_exact_and_never_use_exponential_or_hex_parsing(self):
        self.open_report()
        for start, end, expected in (('1ns', '2NS', [1, 2]),
                                     ('.001us', '.002 µs', [1, 2]),
                                     ('+0.001μs', '0.000002ms', [1, 2]),
                                     ('0.000000001s', '0.000000002s', [1, 2]),
                                     ('100000', '200000', [100000, 200000])):
            with self.subTest(start=start):
                self.open_position()
                self.position(start, end)
                self.assertEqual(self.view(), expected)
        for text in ('NaN', 'Infinity', '0x100', '1e3ns', '1.5ns', '1 u', '', '-1ns',
                     '3s', '9007199254740993ns', '1' * 80):
            with self.subTest(text=text):
                before = self.view()
                self.open_position()
                self.position(text)
                self.assertTrue(self.page.locator('.tl-position').evaluate('dialog => dialog.open'))
                self.assertEqual(self.view(), before)
                self.assertEqual(self.page.locator('#tlPositionStart').get_attribute('aria-invalid'), 'true')
                self.assertTrue(self.page.locator('#tlPositionError').inner_text())
                self.page.locator('[data-position-cancel]').click()

    def test_invalid_end_does_not_move_view_and_correction_keeps_query(self):
        self.open_report()
        for end in ('100us', '99us', '0.5ns', '3s'):
            with self.subTest(end=end):
                before = self.view()
                self.open_position()
                self.position('100us', end)
                self.assertEqual(self.view(), before)
                self.assertEqual(self.page.locator('#tlPositionEnd').get_attribute('aria-invalid'), 'true')
                self.assertEqual(self.page.evaluate('document.activeElement.id'), 'tlPositionEnd')
                self.assertEqual(self.page.locator('#tlPositionStart').input_value(), '100us')
                self.page.locator('#tlPositionEnd').fill('200us')
                self.assertEqual(self.page.locator('#tlPositionError').inner_text(), '')
                self.page.locator('#tlPositionEnd').press('Enter')
                self.assertEqual(self.view(), [100000, 200000])

    def test_large_recorded_timestamps_subtract_exact_origin_before_number_conversion(self):
        self.open_capture()
        data = self.page.evaluate("JSON.parse(document.getElementById('tlData').textContent)")
        origin = int(data['uftrace']['origin_ns'])
        self.assertGreater(origin, 2 ** 53)
        self.open_position()
        self.page.locator('#tlPositionBasis').select_option('timestamp')
        self.assertIn(str(origin // 1000000000), self.page.locator('#tlPositionStart').input_value())
        self.position(f'{(origin + 101) // 1000000000}.{(origin + 101) % 1000000000:09d}', f'{origin + 119}ns')
        self.assertEqual(self.view(), [101, 119])
        self.open_position()
        self.page.locator('#tlPositionBasis').select_option('timestamp')
        self.position(str(origin - 1) + 'ns')
        self.assertEqual(self.view(), [101, 119])
        self.assertTrue(self.page.locator('#tlPositionError').inner_text())

    def test_basis_switch_converts_both_fields_without_losing_nanoseconds(self):
        self.open_capture()
        data = self.page.evaluate("JSON.parse(document.getElementById('tlData').textContent)")
        origin = int(data['uftrace']['origin_ns'])
        self.open_position()
        self.page.locator('#tlPositionStart').fill('101ns')
        self.page.locator('#tlPositionEnd').fill('119ns')
        self.page.locator('#tlPositionBasis').select_option('timestamp')
        self.assertEqual(self.page.locator('#tlPositionStart').input_value(),
                         f'{(origin + 101) // 1000000000}.{(origin + 101) % 1000000000:09d} s')
        self.page.locator('#tlPositionBasis').select_option('offset')
        self.assertEqual(self.page.locator('#tlPositionStart').input_value(), '101 ns')
        self.assertEqual(self.page.locator('#tlPositionEnd').input_value(), '119 ns')
        self.page.locator('#tlPositionStart').fill('not a coordinate')
        self.page.locator('#tlPositionBasis').select_option('timestamp')
        self.assertEqual(self.page.locator('#tlPositionBasis').input_value(), 'offset')
        self.assertEqual(self.page.locator('#tlPositionStart').input_value(), 'not a coordinate')
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'tlPositionStart')

    def test_non_timestamped_and_unverified_clocks_never_offer_absolute_positions(self):
        for mode in ('rel', 'compressed', 'count'):
            with self.subTest(mode=mode):
                self.open_report(mode)
                self.open_position()
                self.assertTrue(self.page.locator('[data-position-basis]').is_hidden())
                hint = self.page.locator('#tlPositionHint').inner_text()
                if mode == 'count':
                    self.assertIn('Call-order coordinates only', hint)
                    self.position('100.125u', '200 units')
                    self.assertEqual(self.view(), [100125, 200000])
                    self.open_position()
                    self.position('1ms')
                    self.assertIn('not time', self.page.locator('#tlPositionError').inner_text())
                else:
                    if mode == 'compressed':
                        self.assertIn('not wall-clock time', hint)
                    else:
                        self.assertIn('Duration-based coordinates, not recorded timestamps', hint)
                    self.position('100us', '200us')
                    self.assertEqual(self.view(), [100000, 200000])
        def unverified(data):
            data['uftrace'] = dict(spaces=['user', 'kernel'], origin_ns=str(ORIGIN), aligned=False)
        self.open_report(update=unverified)
        self.open_position()
        self.assertTrue(self.page.locator('[data-position-basis]').is_hidden())
        self.assertIn('Clocks are not verified', self.page.locator('#tlPositionHint').inner_text())
        self.position('100us', '200us')
        self.assertEqual(self.view(), [100000, 200000])

    def test_safe_numeric_origin_is_supported_but_rounded_numeric_origin_is_not(self):
        for origin in (0, 2032711166957000, 2 ** 54):
            with self.subTest(origin=origin):
                self.open_report(update=lambda data: data.update(t0=origin))
                self.open_position()
                self.assertEqual(self.page.locator('[data-position-basis]').is_visible(), origin < 2 ** 53)
                if origin < 2 ** 53:
                    self.page.locator('#tlPositionBasis').select_option('timestamp')
                    self.position(f'{origin + 1}ns', f'{origin + 2}ns')
                    self.assertEqual(self.view(), [1, 2])

    def test_modal_escape_cancel_and_keyboard_never_trigger_underlying_shortcuts(self):
        self.open_report()
        self.page.evaluate("fgView('timeline').probe().select(120)")
        self.page.locator('.tl-canvas').focus()
        before = self.view(), self.page.locator('.tl-detail').inner_text()
        self.page.keyboard.press('g')
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'tlPositionStart')
        for unused in range(12):
            self.page.keyboard.press('Tab')
            self.assertTrue(self.page.evaluate('!!document.activeElement.closest(".tl-position")'))
        self.page.locator('[data-position-cancel]').focus()
        self.page.keyboard.press('Escape')
        self.assertFalse(self.page.locator('.tl-position').evaluate('dialog => dialog.open'))
        self.assertEqual((self.view(), self.page.locator('.tl-detail').inner_text()), before)
        self.assertTrue(self.page.locator('.tl-canvas').evaluate('node => node === document.activeElement'))
        self.page.locator('.tl-search').focus()
        self.page.keyboard.press('g')
        self.assertFalse(self.page.locator('.tl-position').evaluate('dialog => dialog.open'))
        self.assertEqual(self.page.locator('.tl-search').input_value(), 'g')

    def test_event_only_timeline_and_sidecar_without_text_need_no_call_selection(self):
        for sidecar in (False, True):
            with self.subTest(sidecar=sidecar):
                self.open_capture([lifecycle_event('exec', 1000), lifecycle_event('exit', 1000000)], sidecar=sidecar, no_text=True)
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().N"), 0)
                self.open_position()
                self.position('1us', '2us')
                self.assertEqual(self.view(), [1000, 2000])
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), -1)
                self.assertEqual(self.page.evaluate('document.body.dataset.view'), 'timeline')

    def test_view_switch_closes_dialog_and_sidecar_text_load_preserves_position(self):
        self.open_capture(sidecar=True)
        self.open_position()
        self.position('101ns', '119ns')
        self.open_position()
        self.page.evaluate("async () => { await fgSwitchView('text'); }")
        self.assertFalse(self.page.locator('.tl-position').evaluate('dialog => dialog.open'))
        self.assertEqual(self.page.evaluate('document.body.dataset.view'), 'text')
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.assertEqual(self.view(), [101, 119])
        self.open_position()
        self.page.locator('[data-position-cancel]').click()

    def test_touch_dialog_fits_both_themes_and_keeps_controls_reachable(self):
        self.context.close()
        for theme in ('light', 'dark'):
            self.context = self.browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True)
            self.addCleanup(self.context.close)
            self.context.add_init_script(f"localStorage.setItem('theme', '{theme}')")
            self.open_report()
            for width, height in ((390, 844), (320, 700), (844, 390)):
                with self.subTest(theme=theme, width=width):
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.page.wait_for_function("() => document.getElementById('timelineView').classList.contains('tl-compact')")
                    self.open_position()
                    self.assertEqual(self.page.locator('[data-act=options]').get_attribute('aria-expanded'), 'false')
                    dialog = self.page.locator('.tl-position').bounding_box()
                    self.assertGreaterEqual(dialog['x'], 0)
                    self.assertGreaterEqual(dialog['y'], 0)
                    self.assertLessEqual(dialog['x'] + dialog['width'], width)
                    self.assertLessEqual(dialog['y'] + dialog['height'], height)
                    self.assertGreaterEqual(self.page.locator('#tlPositionStart').bounding_box()['height'], 44)
                    self.position('not a coordinate')
                    expected_error = 'rgb(255, 138, 128)' if theme == 'dark' else 'rgb(198, 40, 40)'
                    self.assertEqual(self.page.locator('#tlPositionError').evaluate('node => getComputedStyle(node).color'), expected_error)
                    self.assertEqual(self.page.locator('#tlPositionStart').evaluate('node => getComputedStyle(node).borderTopColor'), expected_error)
                    self.position('100us', '200us')
                    self.assertEqual(self.view(), [100000, 200000])
                    self.open_position()
                    cancel = self.page.locator('[data-position-cancel]')
                    cancel.scroll_into_view_if_needed()
                    self.assertGreaterEqual(cancel.bounding_box()['height'], 44)
                    cancel.tap()
                    self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))


if __name__ == '__main__':
    unittest.main()
