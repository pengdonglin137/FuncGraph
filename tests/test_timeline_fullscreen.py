"""Timeline full-screen lifecycle, state preservation and compact controls."""

from itertools import product
from pathlib import Path
import threading
import unittest

import helpers
from fglib.viewer import create_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class TimelineFullscreenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel()
        cls.kernel.run(helpers.fixture('pi4_sched'), '--timeline', '--filter', '--enable-fold', out='report.html')
        cls.url = 'file://' + cls.kernel.dir + '/report.html'
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            cls.playwright.stop()
            cls.kernel.close()
            raise unittest.SkipTest(str(error))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.kernel.close()

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1400, 'height': 860})
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def open_report(self, script=None):
        if script:
            self.page.add_init_script(script)
        self.page.goto(self.url)
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.page.locator('.tl-canvas').wait_for()

    def wait_mode(self, active):
        self.page.wait_for_function('active => document.getElementById("timelineView").classList.contains("tl-fullscreen") === active', arg=active)
        self.page.wait_for_function('() => !document.querySelector("[data-act=max]").disabled')
        self.assertEqual(self.page.locator('[data-act=max]').get_attribute('aria-pressed'), str(active).lower())
        self.assertEqual(self.page.locator('[data-act=max]').inner_text(), 'Exit full screen' if active else 'Full screen')

    def remember_state(self):
        self.page.evaluate('''() => {
            localStorage.setItem('fgTl.detailFrac', '0.27');
            const probe = fgView('timeline').probe();
            probe.select(0);
            probe.zoomTo(probe.sStart[0], probe.sEnd[0], 0);
            window.fullscreenOriginal = {starts: probe.sStart, tracks: probe.TRACKS};
        }''')
        return self.page.evaluate('''() => {
            const probe = fgView('timeline').probe();
            return {view:probe.view(), selected:probe.sel(), filter:document.querySelector('.tl-ftext').value,
                split:localStorage.getItem('fgTl.detailFrac')};
        }''')

    def assert_state(self, before):
        self.assertEqual(self.page.evaluate('''() => {
            const probe = fgView('timeline').probe();
            return {view:probe.view(), selected:probe.sel(), filter:document.querySelector('.tl-ftext').value,
                split:localStorage.getItem('fgTl.detailFrac')};
        }'''), before)
        self.assertTrue(self.page.evaluate('''() => {
            const probe = fgView('timeline').probe();
            return probe.sStart === fullscreenOriginal.starts && probe.TRACKS === fullscreenOriginal.tracks;
        }'''))

    def test_native_button_round_trip_preserves_model_zoom_selection_and_filter(self):
        self.open_report()
        helpers.open_timeline_tools(self.page)
        self.page.locator('.tl-ftext').fill('comm:sh')
        self.page.locator('.tl-ftext').press('Enter')
        self.page.wait_for_function('() => !!window.fgFilterState?.lines && !window.fgFilterProgress')
        self.page.locator('[data-act=options]').click()
        before = self.remember_state()
        normal = self.page.locator('#timelineView').bounding_box()
        button = self.page.get_by_role('button', name='Full screen', exact=True)
        self.assertTrue(button.is_visible())
        button.click()
        self.page.wait_for_function('() => document.fullscreenElement === document.getElementById("timelineView")')
        self.wait_mode(True)
        self.assert_state(before)
        full = self.page.locator('#timelineView').bounding_box()
        self.assertLess(full['y'], normal['y'])
        self.assertGreater(full['height'], normal['height'])
        detail = self.page.locator('.tl-detail').bounding_box()
        self.assertLessEqual(detail['y'] + detail['height'], self.page.viewport_size['height'])
        self.assertTrue(self.page.get_by_role('button', name='Timeline help', exact=True).is_visible())
        self.page.get_by_role('button', name='Exit full screen', exact=True).click()
        self.page.wait_for_function('() => !document.fullscreenElement')
        self.wait_mode(False)
        self.assert_state(before)
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'max')

    def test_external_native_exit_resets_button_and_page_chrome(self):
        self.open_report()
        for _ in range(2):
            self.page.locator('[data-act=max]').click()
            self.page.wait_for_function('() => !!document.fullscreenElement')
            self.page.evaluate('document.exitFullscreen()')
            self.wait_mode(False)
            self.assertFalse(self.page.evaluate('document.body.classList.contains("tl-max")'))
            self.assertTrue(self.page.locator('.view-tabs').is_visible())

    def test_desktop_tools_are_grouped_in_a_keyboard_accessible_dropdown(self):
        self.open_report()
        tools = self.page.locator('[data-act=options]')
        self.assertTrue(tools.is_visible())
        self.assertEqual(tools.get_attribute('aria-expanded'), 'false')
        self.assertFalse(self.page.locator('[data-act=an-tasks]').is_visible())
        canvas = self.page.locator('.tl-canvas').bounding_box()
        tools.press('Enter')
        self.assertTrue(self.page.get_by_role('dialog', name='Timeline tools', exact=True).is_visible())
        self.assertTrue(self.page.evaluate('document.querySelector(".tl-options").contains(document.activeElement)'))
        self.assertEqual(self.page.locator('.tl-canvas').bounding_box(), canvas)
        for label in ('Timeline navigation', 'Timeline analysis'):
            self.assertTrue(self.page.get_by_role('group', name=label, exact=True).is_visible())
        self.page.keyboard.press('Escape')
        self.assertFalse(self.page.locator('.tl-options').is_visible())
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'options')
        tools.click()
        self.page.locator('[data-act=an-tasks]').click()
        self.assertFalse(self.page.locator('.tl-options').is_visible())

    def test_outside_click_closes_tools_without_clearing_filter(self):
        self.open_report()
        helpers.open_timeline_tools(self.page)
        self.page.locator('.tl-ftext').fill('comm:sh')
        self.page.locator('.tl-ftext').press('Enter')
        self.page.locator('[data-act=notes]').click()
        self.assertFalse(self.page.locator('.tl-options').is_visible())
        self.assertEqual(self.page.locator('.tl-ftext').input_value(), 'comm:sh')
        self.assertEqual(self.page.evaluate('fgFilterState.text'), 'comm:sh')

    def test_fullscreen_and_help_shortcuts_dismiss_the_tools_dropdown(self):
        self.open_report('Element.prototype.requestFullscreen = undefined;')
        helpers.open_timeline_tools(self.page)
        self.page.locator('[data-act=fhide]').focus()
        self.page.keyboard.press('m')
        self.wait_mode(True)
        self.assertFalse(self.page.locator('.tl-options').is_visible())
        helpers.open_timeline_tools(self.page)
        self.page.locator('[data-act=fhide]').focus()
        self.page.keyboard.press('?')
        self.assertFalse(self.page.locator('.tl-options').is_visible())
        self.assertTrue(self.page.locator('.tl-help').is_visible())
        self.page.keyboard.press('Escape')
        self.wait_mode(True)
        self.page.keyboard.press('Escape')
        self.wait_mode(False)

    def test_unavailable_native_api_uses_viewport_and_escape_exits_even_from_search(self):
        self.open_report('Element.prototype.requestFullscreen = undefined;')
        before = self.remember_state()
        self.page.locator('.tl-canvas').focus()
        self.page.keyboard.press('m')
        self.wait_mode(True)
        self.assertFalse(self.page.evaluate('!!document.fullscreenElement'))
        self.assertIn('page full screen', self.page.locator('.tl-toast').inner_text())
        self.page.locator('.tl-search').focus()
        self.page.keyboard.press('Escape')
        self.wait_mode(False)
        self.assert_state(before)

    def test_rejected_native_request_keeps_usable_page_fullscreen(self):
        self.open_report('Element.prototype.requestFullscreen = () => Promise.reject(new Error("denied"));')
        self.page.locator('[data-act=max]').click()
        self.wait_mode(True)
        self.assertIn('not permitted', self.page.locator('.tl-toast').inner_text())
        self.page.locator('[data-act=max]').click()
        self.wait_mode(False)

    def test_switching_to_text_exits_native_fullscreen_without_rebuilding_timeline(self):
        self.open_report()
        before = self.remember_state()
        self.page.locator('[data-act=max]').click()
        self.page.wait_for_function('() => !!document.fullscreenElement')
        self.page.evaluate('() => fgSwitchView("text")')
        self.page.wait_for_function('() => !document.fullscreenElement')
        self.assertFalse(self.page.evaluate('document.body.classList.contains("tl-max")'))
        self.assertTrue(self.page.locator('.view-tabs').is_visible())
        self.page.evaluate('() => fgSwitchView("timeline")')
        self.wait_mode(False)
        self.assert_state(before)

    def test_late_fullscreen_request_cannot_reopen_after_view_change(self):
        self.open_report('Element.prototype.requestFullscreen = () => new Promise(resolve => window.resolveFullscreen = resolve);')
        self.page.locator('[data-act=max]').click()
        self.assertTrue(self.page.locator('[data-act=max]').is_disabled())
        self.page.evaluate('() => fgSwitchView("text")')
        self.page.evaluate('() => window.resolveFullscreen()')
        self.page.wait_for_function('() => !document.querySelector("[data-act=max]").disabled')
        self.page.evaluate('() => fgSwitchView("timeline")')
        self.wait_mode(False)
        self.assertFalse(self.page.evaluate('document.body.classList.contains("tl-max")'))

    def test_keyboard_cancel_while_native_request_is_pending_stays_cancelled(self):
        self.open_report('Element.prototype.requestFullscreen = () => new Promise(resolve => window.resolveFullscreen = resolve);')
        self.page.locator('.tl-canvas').focus()
        self.page.keyboard.press('m')
        self.page.keyboard.press('Escape')
        self.page.evaluate('() => window.resolveFullscreen()')
        self.wait_mode(False)

    def test_compact_controls_and_fullscreen_layout_in_both_themes(self):
        self.context.close()
        self.context = self.browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.open_report('Element.prototype.requestFullscreen = undefined;')
        for theme, size in product(('light', 'dark'), ((390, 844), (320, 700), (844, 390))):
            with self.subTest(theme=theme, size=size):
                self.page.set_viewport_size(dict(zip(('width', 'height'), size)))
                self.page.evaluate('theme => document.documentElement.setAttribute("data-theme", theme)', theme)
                self.page.locator('[data-act=max]').tap()
                self.wait_mode(True)
                for selector in ('[data-act=max]', '[data-act=fit]', '[data-act=options]', '.tl-search'):
                    bounds = self.page.locator(selector).bounding_box()
                    self.assertIsNotNone(bounds)
                    self.assertGreaterEqual(bounds['height'], 44)
                    self.assertGreaterEqual(bounds['x'], 0)
                    self.assertLessEqual(bounds['x'] + bounds['width'], size[0])
                self.assertGreater(self.page.locator('.tl-canvas').bounding_box()['height'], 0)
                self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
                self.page.locator('[data-act=options]').tap()
                self.assertTrue(self.page.locator('.tl-options').is_visible())
                self.page.keyboard.press('Escape')
                self.wait_mode(True)
                self.page.get_by_role('button', name='Timeline help', exact=True).tap()
                self.assertTrue(self.page.locator('.tl-help').is_visible())
                self.page.keyboard.press('Escape')
                self.page.locator('[data-act=max]').tap()
                self.wait_mode(False)

    def test_mixed_legend_hides_only_in_fullscreen_in_both_delivery_modes(self):
        trace = str(Path(helpers.ROOT) / 'samples' / 'sample_mixed.jsonl')
        for mode in ('single', 'sidecar'):
            output = 'mixed.html' if mode == 'single' else 'mixed-sidecar'
            self.kernel.run(trace, '--timeline', '--filter', '--report-mode', mode, out=output)
            with create_server(Path(self.kernel.dir) / output) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    for native, mobile, theme in product((True, False), (True, False), ('light', 'dark')):
                        with self.subTest(mode=mode, native=native, mobile=mobile, theme=theme):
                            self.context.close()
                            self.context = self.browser.new_context(
                                viewport={'width': 390 if mobile else 1400, 'height': 844},
                                has_touch=mobile, is_mobile=mobile)
                            self.page = self.context.new_page()
                            self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                            if not native:
                                self.page.add_init_script('Element.prototype.requestFullscreen = undefined;')
                            self.page.goto(server.url)
                            self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
                            self.page.evaluate('theme => document.documentElement.dataset.theme = theme', theme)
                            legend = self.page.get_by_label('Address space legend')
                            self.assertTrue(legend.is_visible())
                            self.assertIn('User → kernel', legend.inner_text())
                            self.page.evaluate("() => fgView('timeline').probe().select(0)")
                            self.page.locator('[data-act=max]').click()
                            self.wait_mode(True)
                            self.assertEqual(self.page.evaluate('!!document.fullscreenElement'), native)
                            self.assertFalse(legend.is_visible())
                            self.assertEqual(legend.bounding_box(), None)
                            self.assertGreater(self.page.locator('.tl-canvas').bounding_box()['height'], 0)
                            self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 0)
                            self.page.locator('[data-act=max]').click()
                            self.wait_mode(False)
                            self.assertTrue(legend.is_visible())
                            self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 0)
                finally:
                    server.shutdown()
                    thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
