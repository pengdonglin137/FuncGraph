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

    def open_short_report(self, url, native, touch, theme, height=240):
        self.context.close()
        self.context = self.browser.new_context(viewport={'width': 320, 'height': height},
                                                has_touch=touch, is_mobile=touch)
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        if not native:
            self.page.add_init_script('Element.prototype.requestFullscreen = undefined;')
        self.page.goto(url)
        self.page.evaluate('''async theme => {
            document.documentElement.dataset.theme = theme;
            await fgSwitchView('timeline');
            fgView('timeline').probe().select(0);
            window.shortViewportInput = JSON.stringify(fgData.read('tlData'));
        }''', theme)
        before = self.remember_state()
        self.page.locator('[data-act=max]').click()
        self.wait_mode(True)
        self.assertEqual(self.page.evaluate('!!document.fullscreenElement'), native)
        self.page.locator('#timelineView').evaluate('element => element.scrollTop = 0')
        return before

    def wait_for_root_scroll(self):
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        try:
            self.page.wait_for_function('() => document.getElementById("timelineView").scrollTop > 0', timeout=2000)
        except PlaywrightTimeoutError:
            pass
        self.assertGreater(self.page.locator('#timelineView').evaluate('element => element.scrollTop'), 0,
                           'The short full-screen report must be scrollable without programmatic scrolling')

    def test_short_fullscreen_wheel_reaches_detail_controls_without_shrinking_chart(self):
        mixed = str(Path(helpers.ROOT) / 'samples/sample_mixed.jsonl')
        for population, trace, mode in [('kernel', helpers.fixture('pi4_sched'), 'single'),
                                        ('kernel', helpers.fixture('pi4_sched'), 'sidecar'),
                                        ('mixed', mixed, 'single'), ('mixed', mixed, 'sidecar')]:
            output = 'short-' + population + ('.html' if mode == 'single' else '-sidecar')
            self.kernel.run(trace, '--timeline', '--filter', '--enable-fold', '--report-mode', mode, out=output)
            with create_server(Path(self.kernel.dir) / output) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    for native, touch, theme in product((True, False), (True, False), ('light', 'dark')):
                        with self.subTest(population=population, mode=mode, native=native, touch=touch, theme=theme):
                            before = self.open_short_report(server.url, native, touch, theme)
                            root = self.page.locator('#timelineView')
                            self.assertGreater(root.evaluate('element => element.scrollHeight - element.clientHeight'), 0)
                            requests = []
                            self.page.on('request', lambda request: requests.append(request.url))
                            self.page.mouse.move(3, 120)
                            self.page.mouse.wheel(0, 600)
                            self.wait_for_root_scroll()
                            self.assertGreaterEqual(self.page.locator('.tl-stage').bounding_box()['height'], 160)
                            self.assert_state(before)
                            expand = self.page.locator('[data-detail-expand]')
                            expand.scroll_into_view_if_needed()
                            expand.click()
                            self.assertTrue(root.evaluate('element => element.classList.contains("tl-detail-expanded")'))
                            self.assert_state(before)
                            expand.click()
                            self.assertGreaterEqual(self.page.locator('.tl-stage').bounding_box()['height'], 160)
                            self.assert_state(before)
                            self.assertTrue(self.page.evaluate("JSON.stringify(fgData.read('tlData')) === shortViewportInput"))
                            self.assertEqual(requests, [])
                            close = self.page.locator('.tl-detail .tl-close')
                            close.scroll_into_view_if_needed()
                            close.click()
                            self.assertFalse(self.page.locator('.tl-detail').is_visible())
                            exit_button = self.page.locator('[data-act=max]')
                            exit_button.scroll_into_view_if_needed()
                            exit_button.click()
                            self.wait_mode(False)
                            self.assertEqual(root.evaluate('element => element.scrollTop'), 0)
                finally:
                    server.shutdown()
                    thread.join(timeout=5)

    def test_short_fullscreen_touch_scroll_preserves_view_and_stops_at_boundaries(self):
        for native in (True, False):
            with self.subTest(native=native):
                before = self.open_short_report(self.url, native, True, 'dark')
                session = self.context.new_cdp_session(self.page)
                def swipe(distance):
                    start = 120 - distance / 2
                    session.send('Input.dispatchTouchEvent', dict(type='touchStart',
                        touchPoints=[dict(id=0, x=3, y=start)]))
                    for step in range(1, 9):
                        session.send('Input.dispatchTouchEvent', dict(type='touchMove',
                            touchPoints=[dict(id=0, x=3, y=start + distance * step / 8)]))
                    session.send('Input.dispatchTouchEvent', dict(type='touchEnd', touchPoints=[]))
                    self.page.evaluate('''async () => {
                        for (let frame = 0; frame < 8; frame++) await new Promise(requestAnimationFrame);
                    }''')
                swipe(-160)
                self.wait_for_root_scroll()
                self.assertEqual(self.page.evaluate('scrollY'), 0)
                self.assert_state(before)
                for repeat in range(3):
                    swipe(-160)
                self.assertEqual(self.page.evaluate('scrollY'), 0)
                for repeat in range(4):
                    swipe(160)
                self.page.wait_for_function('() => document.getElementById("timelineView").scrollTop === 0')
                self.assert_state(before)
                self.assertTrue(self.page.evaluate("JSON.stringify(fgData.read('tlData')) === shortViewportInput"))
                session.detach()

    def test_fullscreen_root_scroll_dismisses_stale_hover_and_moved_actions(self):
        self.open_short_report(self.url, False, False, 'light', height=320)
        coordinates = self.page.evaluate('fgView("timeline").probe().xyOf(0)')
        self.page.mouse.move(*coordinates)
        self.page.locator('.tl-tip').wait_for(state='visible')
        self.page.locator('#timelineView').evaluate('element => element.scrollTop = 12')
        self.page.locator('.tl-tip').wait_for(state='hidden')
        trigger = self.page.locator('[data-call-actions]')
        trigger.scroll_into_view_if_needed()
        trigger.click()
        menu = self.page.locator('.tl-call-actions-panel')
        self.assertTrue(menu.is_visible())
        self.page.locator('#timelineView').evaluate('element => element.scrollTop = element.scrollTop > 0 ? 0 : 12')
        menu.wait_for(state='hidden')
        self.assertEqual(trigger.get_attribute('aria-expanded'), 'false')
        self.assertTrue(trigger.evaluate('element => element === document.activeElement'))
        self.page.locator('#timelineView').evaluate('element => element.scrollTop = 0')
        self.page.locator('[data-act=options]').click()
        self.page.locator('#timelineView').evaluate('element => element.scrollTop = 12')
        self.page.evaluate('''async () => {
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        }''')
        tools = self.page.locator('.tl-options')
        bounds = tools.bounding_box()
        self.assertGreaterEqual(bounds['y'], 0)
        self.assertLessEqual(bounds['y'] + bounds['height'], 320)
        self.assertTrue(tools.evaluate('''element => { const bounds = element.getBoundingClientRect();
            return element.contains(document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2)); }'''))

    def test_short_fullscreen_tab_reaches_expand_and_native_keyboard_activation(self):
        before = self.open_short_report(self.url, True, False, 'dark')
        for attempt in range(30):
            if self.page.evaluate('document.activeElement.matches("[data-detail-expand]")'):
                break
            self.page.keyboard.press('Tab')
        self.assertTrue(self.page.evaluate('document.activeElement.matches("[data-detail-expand]")'))
        self.page.keyboard.press('Enter')
        self.assertTrue(self.page.locator('#timelineView').evaluate('element => element.classList.contains("tl-detail-expanded")'))
        self.assert_state(before)
        self.page.keyboard.press('Space')
        self.assertFalse(self.page.locator('#timelineView').evaluate('element => element.classList.contains("tl-detail-expanded")'))
        self.assert_state(before)

    def test_regular_fullscreen_does_not_gain_an_unnecessary_outer_scrollbar(self):
        self.open_report()
        before = self.remember_state()
        self.page.locator('[data-act=max]').click()
        self.wait_mode(True)
        root = self.page.locator('#timelineView')
        self.assertLessEqual(root.evaluate('element => element.scrollHeight - element.clientHeight'), 1)
        self.page.mouse.move(3, 400)
        self.page.mouse.wheel(0, 400)
        self.page.evaluate('''async () => {
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        }''')
        self.assertEqual(root.evaluate('element => element.scrollTop'), 0)
        self.assert_state(before)

    def wait_mode(self, active):
        self.page.wait_for_function('active => document.getElementById("timelineView").classList.contains("tl-fullscreen") === active', arg=active)
        self.page.wait_for_function('() => !document.querySelector("[data-act=max]").disabled')
        self.assertEqual(self.page.locator('[data-act=max]').get_attribute('aria-pressed'), str(active).lower())
        self.assertEqual(self.page.locator('[data-act=max]').get_attribute('aria-label'),
                         'Exit full screen' if active else 'Full screen')

    def test_help_fits_short_normal_and_scrolled_fullscreen_viewports(self):
        for mode in ('normal', 'fallback', 'native'):
            with self.subTest(mode=mode):
                if mode == 'normal':
                    self.page.set_viewport_size({'width': 320, 'height': 360})
                    self.open_report()
                    before = self.remember_state()
                else:
                    before = self.open_short_report(self.url, mode == 'native', True, 'dark')
                    self.assertGreater(self.page.locator('#timelineView').evaluate('root => { root.scrollTop = root.scrollHeight; return root.scrollTop; }'), 0)
                origin = self.page.locator('.tl-canvas')
                geometry = '''() => [window.scrollY, document.getElementById('timelineView').scrollTop]'''
                scroll = self.page.evaluate(geometry)
                origin.evaluate('canvas => canvas.focus({preventScroll:true})')
                self.page.keyboard.press('?')
                panel = self.page.get_by_role('dialog', name='Timeline help')
                panel.wait_for()
                close = panel.get_by_role('button', name='Close help')
                self.assertTrue(close.evaluate('button => button === document.activeElement'))
                self.assertEqual(self.page.evaluate(geometry), scroll)
                if mode != 'normal':
                    self.assertGreaterEqual(close.bounding_box()['height'], 44)
                self.assertTrue(panel.evaluate('''element => {
                    const bounds = element.getBoundingClientRect(), viewport = window.visualViewport;
                    return bounds.top >= viewport.offsetTop && bounds.bottom <= viewport.offsetTop + viewport.height &&
                        bounds.left >= viewport.offsetLeft && bounds.right <= viewport.offsetLeft + viewport.width &&
                        bounds.height >= 144;
                }'''))
                panel.evaluate('element => { element.scrollTop = element.scrollHeight; }')
                self.assertTrue(close.evaluate('''button => {
                    const bounds = button.getBoundingClientRect(), viewport = window.visualViewport;
                    return bounds.top >= viewport.offsetTop && bounds.bottom <= viewport.offsetTop + viewport.height &&
                        document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2) === button;
                }'''))
                self.assertEqual(self.page.evaluate(geometry), scroll)
                self.page.keyboard.press('Escape')
                panel.wait_for(state='hidden')
                self.assertTrue(origin.evaluate('canvas => canvas === document.activeElement'))
                self.assertEqual(self.page.evaluate(geometry), scroll)
                self.assert_state(before)
                if mode != 'normal':
                    self.wait_mode(True)

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

    def test_touch_range_picker_has_reachable_controls_and_preserves_summary_on_cancel(self):
        self.context.close()
        self.context = self.browser.new_context(viewport={'width': 320, 'height': 680}, has_touch=True, is_mobile=True)
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.open_report()
        canvas = self.page.locator('.tl-canvas')
        group = self.page.get_by_role('group', name='Select range', exact=True)
        status = group.get_by_role('status')
        scope = group.get_by_role('combobox', name='Range scope')
        cancel = group.get_by_role('button', name='Cancel', exact=True)
        anchor = self.page.locator('.tl-range-anchor')
        range_head = self.page.locator('.tl-range-head')
        self.assertFalse(group.is_visible())

        def begin():
            self.page.locator('[data-act=options]').tap()
            self.page.get_by_role('button', name='Select range', exact=True).tap()
            self.assertEqual(status.inner_text(), 'Pick range start')
            self.assertFalse(self.page.locator('.tl-options').is_visible())

        def point(fraction, track=False):
            bounds = canvas.bounding_box()
            y = 16 if not track else self.page.evaluate('''() => {
                const scroll = document.querySelector('.tl-scroll');
                const {TRACKS} = fgView('timeline').probe();
                return Math.min(...TRACKS.filter(track => !track.hidden && track.h && track.y >= scroll.scrollTop)
                    .map(track => track.y - scroll.scrollTop)) + 8;
            }''')
            canvas.tap(position={'x': bounds['width'] * fraction, 'y': y})

        for selected_scope in ('all', 'track'):
            begin()
            for control in (scope, cancel):
                self.assertTrue(control.evaluate('''control => {
                    const rect = control.getBoundingClientRect();
                    return rect.height >= 44 && rect.left >= 0 && rect.right <= innerWidth;
                }'''))
            scope.select_option(selected_scope)
            on_track = selected_scope == 'track'
            if on_track:
                point(.8)
                self.assertEqual(status.inner_text(), 'Pick a task or CPU trace track')
                self.assertFalse(anchor.is_visible())
            point(.8, on_track)
            anchor.wait_for(state='visible')
            self.assertEqual(status.inner_text(), 'Pick range end')
            point(.8, on_track)
            self.assertEqual(status.inner_text(), 'Pick a different end')
            self.page.evaluate('''() => document.querySelector('.tl-canvas').addEventListener('click',
                event => event.stopImmediatePropagation(), {capture:true, once:true})''')
            point(.2)
            range_head.wait_for(state='visible')
            self.assertEqual('all tracks' in range_head.inner_text(), selected_scope == 'all')
            self.assertFalse(group.is_visible())
            self.assertFalse(anchor.is_visible())

        original = range_head.inner_text()
        begin()
        point(.25)
        anchor.wait_for(state='visible')
        cancel.tap()
        self.assertFalse(group.is_visible())
        self.assertFalse(anchor.is_visible())
        self.assertEqual(range_head.inner_text(), original)

    def test_range_picker_supports_keyboard_panning_and_cancels_on_view_change(self):
        self.open_report()
        canvas = self.page.locator('.tl-canvas')
        group = self.page.get_by_role('group', name='Select range', exact=True)
        def begin():
            self.page.locator('[data-act=options]').press('Enter')
            self.page.get_by_role('button', name='Select range', exact=True).press('Enter')
            self.assertTrue(canvas.evaluate('element => document.activeElement === element'))

        begin()
        self.page.keyboard.press('+')
        self.page.keyboard.press('Enter')
        marker = self.page.locator('.tl-range-anchor')
        marker.wait_for(state='visible')
        initial = marker.evaluate('element => element.style.left')
        self.page.keyboard.press('ArrowRight')
        self.page.wait_for_function('left => document.querySelector(".tl-range-anchor").style.left !== left', arg=initial)
        self.page.keyboard.press('Enter')
        self.assertIn('all tracks', self.page.locator('.tl-range-head').inner_text())
        self.assertFalse(group.is_visible())
        begin()
        self.page.keyboard.press('Enter')
        self.page.keyboard.press('Escape')
        self.assertFalse(group.is_visible())
        self.assertTrue(self.page.locator('.tl-range-head').is_visible())
        begin()
        self.page.keyboard.press('Enter')
        self.page.locator('.view-tab[data-view=text]').click()
        self.page.locator('.view-tab[data-view=timeline]').click()
        self.assertFalse(group.is_visible())
        self.assertFalse(marker.is_visible())
        self.assertTrue(self.page.locator('.tl-range-head').is_visible())

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
