"""Help remains readable, keyboard-reachable and separate from trace navigation."""
from pathlib import Path
import threading
import unittest
from urllib.parse import urljoin

import helpers
from fglib.viewer import create_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class HelpLifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel()
        cls.addClassCleanup(cls.kernel.close)
        options = ['--timeline', '--enable-fold', '--filter', '--func-links',
                   '--compare', helpers.fixture('pi4_default')]
        cls.kernel.run(helpers.fixture('pi4_sched'), *options, out='index.html')
        cls.kernel.run(helpers.fixture('pi4_sched'), *options, '--report-mode', 'sidecar', out='sidecar')
        cls.kernel.run(helpers.fixture('pi4_sched'), '--timeline', '--no-text-view', out='timeline.html')
        cls.kernel.run(helpers.fixture('pi4_default'), out='plain.html')
        cls.server = create_server(Path(cls.kernel.dir))
        cls.addClassCleanup(cls.server.server_close)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.thread.join, 5)
        cls.addClassCleanup(cls.server.shutdown)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch(ignore_default_args=['--hide-scrollbars'])
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)

    def open_report(self, view='text', report='index.html', width=1500, height=900, touch=False, theme='light'):
        context = self.browser.new_context(viewport=dict(width=width, height=height), is_mobile=touch, has_touch=touch)
        self.addCleanup(context.close)
        context.add_init_script('localStorage.clear()')
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(self.assertEqual, errors, [])
        page.goto(urljoin(self.server.url, report), wait_until='load')
        page.evaluate('''async ({view,theme}) => {
            document.documentElement.dataset.theme = theme;
            if (typeof fgSwitchView === 'function') await fgSwitchView(view);
        }''', dict(view=view, theme=theme))
        return page

    def show_help(self, page, view):
        button = page.locator('#viewHelpBtn')
        button.focus()
        button.press('Enter')
        panel = page.locator('#timelineHelp' if view == 'timeline' else '#keyboardHint')
        panel.wait_for(state='visible')
        close = panel.get_by_role('button', name='Close help', exact=True)
        self.assertTrue(close.evaluate('button => button === document.activeElement'))
        self.assertEqual(button.get_attribute('aria-expanded'), 'true')
        self.assertEqual(button.get_attribute('aria-controls'), panel.get_attribute('id'))
        return panel, close

    def reading_position(self, page):
        return page.evaluate('''() => {
            const timeline = typeof fgCurrentView === 'function' && fgCurrentView() === 'timeline'
                ? fgView('timeline').probe() : null;
            return {page:[scrollX,scrollY], text:document.getElementById('textViewport')?.scrollLeft || 0,
                tracks:document.querySelector('.tl-scroll')?.scrollTop || 0,
                view:timeline?.view() || null, selected:timeline?.sel() ?? null};
        }''')

    def test_wheel_stays_inside_help_at_both_boundaries_even_when_it_fits(self):
        for report, views in [('index.html', ['text', 'compare', 'timeline']),
                              ('sidecar/index.html', ['text', 'compare', 'timeline']),
                              ('plain.html', ['text']), ('timeline.html', ['timeline'])]:
            for view in views:
                for width, height, theme in [(390, 500, 'dark'), (1500, 900, 'light')]:
                    with self.subTest(report=report, view=view, width=width):
                        page = self.open_report(view, report=report, width=width, height=height, theme=theme)
                        panel, close = self.show_help(page, view)
                        page.evaluate('''() => {
                            scrollTo(0, Math.min(300, (document.documentElement.scrollHeight - innerHeight) / 2));
                            window.helpWheelEvents = [];
                        }''')
                        panel.evaluate('''element => {
                            element.scrollTop = 0;
                            element.addEventListener('wheel', event => helpWheelEvents.push(event.defaultPrevented), {passive:true});
                        }''')
                        maximum = panel.evaluate('element => element.scrollHeight - element.clientHeight')
                        before = self.reading_position(page)
                        bounds = panel.bounding_box()
                        page.mouse.move(bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] * 0.7)
                        steps = [(-400, 0), (60, min(60, maximum)), (4000, maximum),
                                 (400, maximum), (-60, max(0, maximum - 60)), (-4000, 0), (-400, 0)]
                        for ordinal, (delta, target) in enumerate(steps, 1):
                            page.mouse.wheel(0, delta)
                            page.wait_for_function('count => helpWheelEvents.length === count', arg=ordinal, timeout=5000)
                            page.evaluate('''async () => {
                                for (let frame = 0; frame < 8; frame++) await new Promise(requestAnimationFrame);
                            }''')
                            self.assertTrue(panel.is_visible())
                            self.assertAlmostEqual(panel.evaluate('element => element.scrollTop'), target, delta=1)
                            self.assertEqual(self.reading_position(page), before)
                            self.assertTrue(close.evaluate('button => button === document.activeElement'))
                        self.assertEqual(page.evaluate('helpWheelEvents'), [False] * len(steps))
                        if view == 'text':
                            self.assertEqual(maximum > 0, width == 390)
                        close.press('Escape')
                        self.assertFalse(panel.is_visible())
                        self.assertEqual(self.reading_position(page), before)

    def test_help_does_not_lock_report_scrolling_outside_or_after_close(self):
        for report, views in [('index.html', ['text', 'compare']),
                              ('sidecar/index.html', ['text', 'compare']), ('plain.html', ['text'])]:
            for view in views:
                with self.subTest(report=report, view=view):
                    page = self.open_report(view, report=report, width=390, height=500)
                    panel, close = self.show_help(page, view)
                    self.assertGreater(page.evaluate('document.documentElement.scrollHeight - innerHeight'), 0)
                    page.mouse.move(387, 250)
                    page.mouse.wheel(0, 300)
                    page.wait_for_function('() => scrollY > 0', timeout=5000)
                    page.evaluate('''async () => {
                        for (let frame = 0; frame < 8; frame++) await new Promise(requestAnimationFrame);
                    }''')
                    self.assertTrue(panel.is_visible())
                    after = self.reading_position(page)
                    bounds = panel.bounding_box()
                    close.press('Escape')
                    self.assertFalse(panel.is_visible())
                    self.assertEqual(self.reading_position(page), after)
                    page.evaluate('scrollTo(0,0)')
                    page.mouse.move(bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] * 0.7)
                    page.mouse.wheel(0, 300)
                    page.wait_for_function('() => scrollY > 0', timeout=5000)

    def test_touch_swipes_at_help_edges_do_not_drag_the_reading_position(self):
        for report, views in [('index.html', ['text', 'compare', 'timeline']),
                              ('sidecar/index.html', ['text', 'compare', 'timeline']),
                              ('plain.html', ['text']), ('timeline.html', ['timeline'])]:
            for view in views:
                for width, height in [(390, 500), (844, 390)]:
                    with self.subTest(report=report, view=view, width=width):
                        page = self.open_report(view, report=report, width=width, height=height, touch=True)
                        panel, close = self.show_help(page, view)
                        page.evaluate('scrollTo(0, Math.min(300, (document.documentElement.scrollHeight - innerHeight) / 2))')
                        before = self.reading_position(page)
                        bounds = panel.bounding_box()
                        horizontal = bounds['x'] + bounds['width'] / 2
                        vertical = bounds['y'] + bounds['height'] / 2
                        distance = min(45, bounds['height'] / 4)
                        client = page.context.new_cdp_session(page)
                        try:
                            def swipe(offset):
                                client.send('Input.dispatchTouchEvent', dict(type='touchStart',
                                    touchPoints=[dict(id=0, x=horizontal, y=vertical)]))
                                for step in range(1, 9):
                                    client.send('Input.dispatchTouchEvent', dict(type='touchMove',
                                        touchPoints=[dict(id=0, x=horizontal,
                                            y=vertical + offset * step / 8)]))
                                client.send('Input.dispatchTouchEvent', dict(type='touchEnd', touchPoints=[]))
                                page.evaluate('''async () => {
                                    for (let frame = 0; frame < 8; frame++) await new Promise(requestAnimationFrame);
                                }''')

                            for bottom in [False, True]:
                                panel.evaluate('(element, bottom) => { element.scrollTop = bottom ? element.scrollHeight : 0; }', bottom)
                                swipe(-distance if bottom else distance)
                                self.assertTrue(panel.is_visible())
                                self.assertEqual(self.reading_position(page), before)
                            if panel.evaluate('element => element.scrollHeight > element.clientHeight'):
                                panel.evaluate('element => { element.scrollTop = 0; }')
                                swipe(-distance)
                                self.assertGreater(panel.evaluate('element => element.scrollTop'), 0)
                                self.assertEqual(self.reading_position(page), before)
                            close.press('Escape')
                            self.assertFalse(panel.is_visible())
                            if page.evaluate('document.documentElement.scrollHeight > innerHeight'):
                                page.evaluate('scrollTo(0,0)')
                                horizontal = width - 3
                                vertical = height / 2
                                swipe(-distance)
                                page.wait_for_function('() => scrollY > 0', timeout=5000)
                        finally:
                            client.detach()

    def test_explicit_help_stays_open_beyond_the_old_timeout_in_all_views(self):
        pages = []
        for view in ['text', 'compare', 'timeline']:
            page = self.open_report(view)
            panel, close = self.show_help(page, view)
            pages.append((page, panel, close))
        pages[0][0].wait_for_timeout(8500)
        for page, panel, close in pages:
            self.assertTrue(panel.is_visible())
            self.assertTrue(close.evaluate('button => button === document.activeElement'))
            page.keyboard.press('Escape')
            self.assertFalse(panel.is_visible())
            self.assertTrue(page.locator('#viewHelpBtn').evaluate('button => button === document.activeElement'))
            self.assertEqual(page.locator('#viewHelpBtn').get_attribute('aria-expanded'), 'false')

    def test_scrolled_help_keeps_close_reachable_in_both_themes_and_touch_orientations(self):
        for view in ['text', 'compare', 'timeline']:
            for width, height, theme, touch in [(1500, 400, 'light', False), (390, 500, 'dark', True), (844, 390, 'light', True)]:
                with self.subTest(view=view, width=width, height=height):
                    page = self.open_report(view, width=width, height=height, theme=theme, touch=touch)
                    panel, close = self.show_help(page, view)
                    panel.evaluate('panel => { panel.scrollTop = panel.scrollHeight; }')
                    facts = panel.evaluate('''panel => {
                        const bounds = panel.getBoundingClientRect();
                        const close = panel.querySelector('[aria-label="Close help"]').getBoundingClientRect();
                        const hit = document.elementFromPoint(close.left + close.width / 2, close.top + close.height / 2);
                        return {bounds:[bounds.left,bounds.right,bounds.top,bounds.bottom],
                            closeInside:close.left>=bounds.left && close.right<=bounds.right && close.top>=bounds.top && close.bottom<=bounds.bottom,
                            reachable:!!hit?.closest('[aria-label="Close help"]'),
                            hit:hit?.className,
                            opacity:getComputedStyle(panel).opacity};
                    }''')
                    self.assertTrue(facts['closeInside'], facts)
                    self.assertTrue(facts['reachable'], facts)
                    self.assertEqual(facts['opacity'], '1')
                    self.assertGreaterEqual(facts['bounds'][0], 0)
                    self.assertLessEqual(facts['bounds'][1], width)
                    self.assertGreaterEqual(facts['bounds'][2], 0)
                    self.assertLessEqual(facts['bounds'][3], height)
                    close.click()
                    self.assertFalse(panel.is_visible())
                    self.assertTrue(page.locator('#viewHelpBtn').evaluate('button => button === document.activeElement'))

    def test_help_keys_do_not_navigate_or_restyle_the_underlying_trace(self):
        for view, height in [('text',500), ('text',900), ('timeline',500), ('timeline',900)]:
            with self.subTest(view=view, height=height):
                page = self.open_report(view, height=height)
                if view == 'timeline':
                    page.evaluate('fgView("timeline").probe().select(3)')
                panel, close = self.show_help(page, view)
                snapshot = '''() => {
                    const probe = typeof fgView === 'function' && fgView('timeline')?.probe();
                    return JSON.stringify({selected:keyboardSelectedLine?.id,folded:foldedBlocks,
                        source:expandedLines,sel:probe?.sel(),view:probe?.view(),
                        inline:document.querySelector('[data-act=inl]')?.textContent,
                        adjusted:document.querySelector('[data-act=adj]')?.textContent,
                        scroll:scrollY,left:document.getElementById('textViewport').scrollLeft});
                }'''
                before = page.evaluate(snapshot)
                for key in ['j', 'k', 'h', 'l', 'ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageDown', 'PageUp', 'g', '0', '1', 'o', 'i']:
                    close.press(key)
                page.wait_for_timeout(200)
                self.assertEqual(page.evaluate(snapshot), before)
                self.assertTrue(panel.is_visible())
                self.assertTrue(close.evaluate('button => button === document.activeElement'))
                close.press('?')
                self.assertFalse(panel.is_visible())

    def test_keyboard_open_and_close_preserve_the_reading_origin_and_scroll(self):
        for view in ['text', 'timeline']:
            with self.subTest(view=view):
                page = self.open_report(view)
                if view == 'text':
                    page.evaluate('''() => {
                        const row = document.querySelectorAll('#content .line-container.expandable')[30];
                        setKeyboardSelectedLine(fgTextNavigation.indexOf(+row.dataset.lineNumber), false);
                        window.scrollTo(0, row.getBoundingClientRect().top + scrollY - 150);
                    }''')
                else:
                    page.locator('.tl-canvas').focus()
                before = page.evaluate('''() => {
                    window.helpOrigin = document.activeElement;
                    return {scroll:scrollY,left:document.getElementById('textViewport').scrollLeft};
                }''')
                page.keyboard.press('?')
                panel = page.locator('#timelineHelp' if view == 'timeline' else '#keyboardHint')
                panel.wait_for(state='visible')
                self.assertTrue(panel.evaluate('panel => panel.contains(document.activeElement)'))
                page.keyboard.press('Escape')
                self.assertTrue(page.evaluate('document.activeElement === helpOrigin'))
                self.assertEqual(page.evaluate('''() => ({scroll:scrollY,left:document.getElementById('textViewport').scrollLeft})'''), before)

    def test_repeat_entry_toggles_and_outside_click_keeps_native_focus(self):
        for view in ['text', 'compare', 'timeline']:
            with self.subTest(view=view):
                page = self.open_report(view)
                panel, close = self.show_help(page, view)
                page.locator('#viewHelpBtn').click()
                self.assertFalse(panel.is_visible())
                panel, close = self.show_help(page, view)
                destination = 'compare' if view != 'compare' else 'text'
                target = page.locator('.view-tab[data-view="' + destination + '"]')
                target.click()
                self.assertFalse(panel.is_visible())
                self.assertNotEqual(page.locator('#viewHelpBtn').get_attribute('aria-expanded'), 'true')
                self.assertTrue(target.evaluate('button => button === document.activeElement'))

    def test_plain_sidecar_and_timeline_only_reports_share_the_same_lifecycle(self):
        for report, view in [('plain.html', 'text'), ('sidecar/index.html', 'text'), ('timeline.html', 'timeline')]:
            with self.subTest(report=report):
                page = self.open_report(view, report)
                panel, close = self.show_help(page, view)
                close.press('Space')
                self.assertFalse(panel.is_visible())
                self.assertTrue(page.locator('#viewHelpBtn').evaluate('button => button === document.activeElement'))

    def test_removed_opener_falls_back_to_a_visible_help_entry(self):
        page = self.open_report()
        page.evaluate('''() => {
            window.helpRemovedOrigin = document.querySelector('#content .fold-icon');
            helpRemovedOrigin.focus({preventScroll:true});
        }''')
        page.keyboard.press('?')
        page.locator('#keyboardHint').wait_for(state='visible')
        page.evaluate('helpRemovedOrigin.remove()')
        page.keyboard.press('Escape')
        self.assertTrue(page.locator('#viewHelpBtn').evaluate('button => button === document.activeElement'))

    def test_maximized_help_escape_preserves_the_chart_and_returns_to_the_fallback(self):
        page = self.open_report('timeline')
        page.evaluate('fgView("timeline").probe().select(3)')
        page.locator('[data-act=max]').click()
        fallback = page.locator('#timelineHelpBtn')
        fallback.press('Enter')
        page.locator('#timelineHelp').wait_for(state='visible')
        page.keyboard.press('Escape')
        self.assertTrue(page.locator('#timelineView').evaluate('root => root.classList.contains("tl-fullscreen")'))
        self.assertTrue(fallback.evaluate('button => button === document.activeElement'))
        self.assertEqual(page.evaluate('fgView("timeline").probe().sel()'), 3)

    def test_deferred_text_completion_does_not_take_focus_from_open_help(self):
        context = self.browser.new_context()
        self.addCleanup(context.close)
        page = context.new_page()
        pending = []
        page.route('**/text.html', lambda route: pending.append(route))
        page.goto(urljoin(self.server.url, 'sidecar/index.html'), wait_until='load')
        page.evaluate('() => { window.helpDeferredText = fgSwitchView("text"); }')
        page.wait_for_timeout(50)
        self.assertEqual(len(pending), 1)
        panel, close = self.show_help(page, 'text')
        pending[0].continue_()
        self.assertTrue(page.evaluate('async () => { await helpDeferredText; return fgTextInitialized; }'))
        self.assertTrue(panel.is_visible())
        self.assertTrue(close.evaluate('button => button === document.activeElement'))
