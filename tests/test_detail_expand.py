"""Short Timeline panes can borrow chart space without changing saved splits."""
from pathlib import Path
import threading
import unittest

import helpers
from fglib.viewer import create_server
from test_uftrace_import import call, lifecycle_event, stream


class DetailExpandTest(unittest.TestCase):
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
        self.errors = []
        self.context = self.browser.new_context(viewport={'width': 1500, 'height': 900})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open_report(self, mode='single', with_calls=False, count=205, no_text=True, function_name='work'):
        self.serial += 1
        records = []
        for index in range(count):
            event = lifecycle_event('exec', index * 1000)
            event.update(tid=index + 1, text=f'filename=/bin/task pid={index + 1}')
            records.append(event)
            if with_calls:
                records.extend([call('entry', 'user', index * 1000 + 20, 0, function_name, 1, tid=index + 1),
                                call('exit', 'user', index * 1000 + 80, 0, function_name, 1, tid=index + 1)])
        trace = Path(self.kernel.dir) / f'capture-{self.serial}.jsonl'
        trace.write_text(stream(records), encoding='utf-8')
        destination = f'report-{self.serial}' + ('.html' if mode == 'single' else '')
        options = ['--no-config', '--timeline', '--report-mode', mode]
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
        self.action('an-tasks')

    def action(self, name):
        button = self.page.locator(f'[data-act="{name}"]')
        if not button.is_visible():
            self.page.locator('[data-act=options]').click()
        button.click()

    def shorten(self):
        self.page.evaluate("() => { localStorage.setItem('fgTl.detailFrac', '0.1'); fgView('timeline').resize(); }")

    def button(self):
        return self.page.locator('[data-detail-expand]')

    def facts(self):
        return self.page.evaluate("""() => {
            const model = fgView('timeline').probe();
            return {range: model.view(), selection: model.sel(), split: localStorage.getItem('fgTl.detailFrac'),
                    tasks: model.TRACKS.length, calls: model.N};
        }""")

    def test_keyboard_expansion_preserves_manual_split_and_selection(self):
        self.open_report(with_calls=True)
        self.assertFalse(self.button().is_visible())
        self.page.evaluate("fgView('timeline').probe().select(0)")
        self.shorten()
        before = self.facts()
        height = self.page.locator('.tl-detail').bounding_box()['height']
        self.button().press('Enter')
        self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
        self.assertFalse(self.page.locator('.tl-scroll').is_visible())
        self.assertGreater(self.page.locator('.tl-detail').bounding_box()['height'], height)
        self.assertEqual(self.facts(), before)
        self.button().press('Space')
        self.assertEqual(self.button().get_attribute('aria-pressed'), 'false')
        self.assertTrue(self.page.locator('.tl-scroll').is_visible())
        self.assertAlmostEqual(self.page.locator('.tl-detail').bounding_box()['height'], height, delta=1)
        self.assertEqual(self.facts(), before)
        self.assertTrue(self.button().evaluate('(element) => element === document.activeElement'))

    def test_landscape_touch_expansion_exposes_rows_both_themes_and_delivery_modes(self):
        self.context.close()
        for theme in ('light', 'dark'):
            for mode in ('single', 'sidecar'):
                with self.subTest(theme=theme, mode=mode):
                    context = self.browser.new_context(viewport={'width': 844, 'height': 390}, is_mobile=True, has_touch=True, device_scale_factor=2)
                    self.addCleanup(context.close)
                    self.page = context.new_page()
                    self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                    self.page.add_init_script(f"localStorage.setItem('theme', '{theme}')")
                    self.open_report(mode=mode)
                    self.assertLess(self.page.locator('.tl-detail').bounding_box()['height'], 140)
                    self.assertGreaterEqual(self.button().bounding_box()['width'], 44)
                    self.assertGreaterEqual(self.button().bounding_box()['height'], 44)
                    before = self.facts()
                    self.button().tap()
                    pane = self.page.locator('.tl-detail').bounding_box()
                    row = self.page.locator('[data-task-row]').first.bounding_box()
                    footer = self.page.locator('.an-task-footer').bounding_box()
                    self.assertGreater(pane['height'], 180)
                    self.assertLessEqual(pane['y'] + pane['height'], 390)
                    self.assertGreaterEqual(row['y'], pane['y'])
                    self.assertLessEqual(row['y'] + row['height'], footer['y'])
                    self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 844)
                    self.assertFalse(self.page.locator('.tl-event-only').is_visible())
                    self.assertEqual(self.facts(), before)
                    self.page.locator('[data-task-page="1"]').tap()
                    self.assertEqual(self.page.locator('.an-task-footer [role=status]').inner_text(), 'Tasks 101–200 of 205')
                    self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
                    self.button().tap()
                    self.assertTrue(self.page.locator('.tl-scroll').is_visible())
                    self.assertTrue(self.page.locator('.tl-event-only').is_visible())
                    self.assertEqual(self.facts(), before)
                    context.close()

    def test_help_and_keyboard_focus_remain_available_without_the_chart(self):
        self.open_report(mode='sidecar')
        self.shorten()
        self.button().click()
        self.page.keyboard.press('?')
        self.assertTrue(self.page.locator('.tl-help').is_visible())
        self.page.locator('.tl-help-close').click()
        self.assertTrue(self.button().evaluate('(element) => element === document.activeElement'))
        self.page.keyboard.press('?')
        self.page.keyboard.press('Escape')
        self.assertFalse(self.page.locator('.tl-help').is_visible())
        self.assertTrue(self.button().evaluate('(element) => element === document.activeElement'))
        self.page.locator('.header-actions button').filter(has_text='?').click()
        self.assertTrue(self.page.locator('.tl-help').is_visible())

    def test_switching_text_and_back_preserves_expansion_and_view_specific_help(self):
        self.open_report(mode='sidecar', with_calls=True, no_text=False)
        self.shorten()
        before = self.facts()
        self.button().click()
        self.page.evaluate("async () => { await fgSwitchView('text'); }")
        self.page.locator('.header-actions button').filter(has_text='?').click()
        self.assertTrue(self.page.locator('#keyboardHint').is_visible())
        self.assertFalse(self.page.locator('.tl-help').is_visible())
        self.page.keyboard.press('Escape')
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
        self.assertEqual(self.facts(), before)
        self.page.locator('.header-actions button').filter(has_text='?').click()
        self.assertTrue(self.page.locator('.tl-help').is_visible())
        self.page.keyboard.press('Escape')
        self.button().click()
        self.assertTrue(self.page.locator('.tl-scroll').is_visible())

    def test_close_and_escape_restore_chart_without_persisting_expansion(self):
        self.open_report()
        self.shorten()
        before = self.facts()
        for close in ('button', 'escape'):
            with self.subTest(close=close):
                self.button().click()
                if close == 'button':
                    self.page.locator('.an-task-head .tl-close').click()
                else:
                    self.page.keyboard.press('Escape')
                self.assertFalse(self.page.locator('.tl-detail').is_visible())
                self.assertTrue(self.page.locator('.tl-scroll').is_visible())
                self.assertFalse(self.page.locator('#timelineView').evaluate('(element) => element.classList.contains("tl-detail-expanded")'))
                self.action('an-tasks')
                self.assertEqual(self.button().get_attribute('aria-pressed'), 'false')
                self.assertEqual(self.facts(), before)
        self.button().click()
        self.page.reload()
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.action('an-tasks')
        self.assertEqual(self.button().get_attribute('aria-pressed'), 'false')
        self.assertEqual(self.page.evaluate("localStorage.getItem('fgTl.detailFrac')"), before['split'])

    def test_replacing_analysis_panels_and_selection_keeps_one_control(self):
        self.open_report(with_calls=True)
        self.shorten()
        self.button().click()
        for action in ('an-errors', 'an-sec', 'an-lat', 'process-events', 'an-tasks'):
            with self.subTest(action=action):
                self.action(action)
                self.assertEqual(self.button().count(), 1)
                self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
                self.assertFalse(self.page.locator('.tl-scroll').is_visible())
        self.page.evaluate("fgView('timeline').probe().select(0)")
        self.assertEqual(self.button().count(), 1)
        self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
        self.button().click()
        self.assertTrue(self.page.locator('.tl-scroll').is_visible())
        self.assertEqual(self.facts()['selection'], 0)

    def test_resize_and_timeline_maximize_preserve_expanded_state_and_saved_fraction(self):
        self.open_report(mode='sidecar')
        self.shorten()
        before = self.facts()
        self.button().click()
        for width, height in ((844, 390), (390, 844), (1500, 900)):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.page.wait_for_function("() => document.querySelector('.tl-detail').getBoundingClientRect().bottom <= innerHeight")
                self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
                self.assertEqual(self.facts(), before)
        self.action('max')
        self.assertFalse(self.page.locator('.view-tabs').is_visible())
        self.assertFalse(self.page.locator('.tl-scroll').is_visible())
        self.action('max')
        self.assertTrue(self.page.locator('.view-tabs').is_visible())
        self.assertEqual(self.facts(), before)
        self.button().click()
        self.assertTrue(self.page.locator('.tl-scroll').is_visible())

    def test_restoring_to_a_tall_panel_keeps_focus_on_a_visible_control(self):
        self.open_report()
        self.shorten()
        self.button().press('Enter')
        self.page.evaluate("() => { localStorage.removeItem('fgTl.detailFrac'); fgView('timeline').resize(); }")
        self.button().press('Enter')
        self.assertFalse(self.button().is_visible())
        self.assertTrue(self.page.locator('.an-task-head .tl-close').evaluate('(element) => element === document.activeElement'))

    def test_duration_histogram_resizes_while_the_main_canvases_are_hidden(self):
        self.open_report(with_calls=True)
        self.shorten()
        self.button().click()
        self.page.evaluate("() => { const model = fgView('timeline').probe(); model.dist(model.sName[0]); }")
        for width, height in ((1500, 900), (800, 420), (390, 844)):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.page.wait_for_function("""() => {
                    const canvas = document.querySelector('.an-hist');
                    return canvas.width > 0 && canvas.width === Math.round(canvas.parentElement.clientWidth * devicePixelRatio);
                }""")
                self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
                self.assertFalse(self.page.locator('.tl-scroll').is_visible())
        self.button().click()
        self.assertTrue(self.page.locator('.tl-scroll').is_visible())

    def test_touch_slice_controls_do_not_overlap_long_function_names(self):
        self.context.close()
        self.context = self.browser.new_context(viewport={'width': 844, 'height': 390}, is_mobile=True, has_touch=True)
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.open_report(with_calls=True, function_name='namespace::' + 'long_function_name_' * 20, count=1)
        self.page.evaluate("fgView('timeline').probe().select(0)")
        head = self.page.locator('.tl-slice-head').bounding_box()
        expand = self.button().bounding_box()
        close = self.page.locator('.tl-slice-head .tl-close').bounding_box()
        title = self.page.locator('.tl-slice-head .tl-detail-title').bounding_box()
        self.assertAlmostEqual(expand['y'], head['y'], delta=1)
        self.assertLessEqual(expand['x'] + expand['width'], close['x'])
        self.assertLessEqual(title['x'] + title['width'], expand['x'])
        self.button().tap()
        self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
        self.assertLessEqual(self.page.locator('.tl-detail').bounding_box()['y'] + self.page.locator('.tl-detail').bounding_box()['height'], 390)

    def test_csv_stays_byte_identical_while_page_and_expansion_are_preserved(self):
        self.open_report(mode='sidecar')
        self.shorten()
        self.button().click()
        self.page.locator('[data-task-page="1"]').click()
        def export():
            with self.page.expect_download() as download:
                self.page.locator('[data-act=tasks-csv]').click()
            return Path(download.value.path()).read_bytes()
        expanded = export()
        self.assertEqual(self.page.locator('.an-task-footer [role=status]').inner_text(), 'Tasks 101–200 of 205')
        self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
        self.button().click()
        self.assertEqual(export(), expanded)
        self.assertEqual(self.page.locator('.an-task-footer [role=status]').inner_text(), 'Tasks 101–200 of 205')
