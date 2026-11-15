"""Floating Timeline tools and notes remain usable in short viewports."""

from itertools import product
from pathlib import Path
import shutil
import subprocess
import threading
import unittest

import helpers
from fglib.viewer import create_server
from test_uftrace_import import lifecycle_event, stream

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class ToolbarOverlayPlacementTest(unittest.TestCase):
    def test_direction_viewport_offset_and_last_resort_are_bounded(self):
        source = Path(helpers.ROOT, 'assets/timeline/panels.js').read_text()
        source = source[source.index('function toolbarOverlayPlacement('):source.index('function fitToolbarOverlay(')]
        result = subprocess.run(['node', '-e', source + '''
            const assert = require('node:assert/strict');
            const place = (values = {}) => toolbarOverlayPlacement({viewportTop:0, viewportBottom:320,
                belowTop:259, aboveBottom:194, preferredHeight:153, minimumHeight:66, ...values});
            assert.deepEqual(place(), {top:41, height:186});
            assert.deepEqual(place({viewportBottom:900}), {top:259, height:633});
            assert.deepEqual(place({viewportTop:100,viewportBottom:220,belowTop:160,aboveBottom:110}),
                {top:108,height:104});
            for (const viewportTop of [0,80,150]) {
                for (const viewportHeight of [100,220,740]) {
                    for (const anchor of [-200,0,20,120,1000]) {
                        for (const preferredHeight of [12,64,350]) {
                            const bounds = {viewportTop,viewportBottom:viewportTop+viewportHeight,
                                belowTop:anchor+44,aboveBottom:anchor-6,preferredHeight,minimumHeight:66};
                            const result = toolbarOverlayPlacement(bounds);
                            assert.ok(result.top >= viewportTop+8);
                            assert.ok(result.height >= 0);
                            assert.ok(result.top + Math.min(result.height,preferredHeight) <= bounds.viewportBottom-8);
                        }
                    }
                }
            }
        '''], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class ToolbarOverlayBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel()
        cls.addClassCleanup(cls.kernel.close)
        options = ['--no-config', '--timeline', '--enable-fold', '--filter']
        trace = str(Path(helpers.ROOT, 'samples/sample_mixed.jsonl'))
        cls.kernel.run(trace, *options, '--report-mode', 'single', out='single.html')
        cls.kernel.run(trace, *options, '--report-mode', 'sidecar', out='sidecar')
        cls.kernel.run(helpers.fixture('pi4_sched'), '--no-config', '--timeline', '--no-text-view',
                       '--report-mode', 'single', out='kernel.html')
        event_trace = Path(cls.kernel.dir, 'events.jsonl')
        event_trace.write_text(stream([lifecycle_event('exec', 10), lifecycle_event('exit', 50)]))
        cls.kernel.run(str(event_trace), '--no-config', '--timeline', '--no-text-view',
                       '--report-mode', 'single', out='events.html')
        cls.server = create_server(Path(cls.kernel.dir, 'sidecar'))
        thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(thread.join, 5)
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.errors = []

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open_report(self, width=568, height=320, touch=True, mode='single', theme='light', viewport=None):
        context = self.browser.new_context(viewport={'width': width, 'height': height},
                                           is_mobile=touch, has_touch=touch)
        self.addCleanup(context.close)
        self.page = context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        if viewport:
            self.page.add_init_script(viewport)
        url = Path(self.kernel.dir, 'single.html').as_uri() if mode == 'single' else self.server.url
        self.page.goto(url)
        self.page.evaluate("async theme => { document.documentElement.dataset.theme = theme; await fgSwitchView('timeline'); }", theme)
        self.page.locator('.tl-search').fill('.*')
        self.page.locator('.tl-search').press('Enter')
        self.page.wait_for_function("() => document.querySelector('.tl-count').getAttribute('aria-busy') === 'false'")
        self.settle()

    def settle(self):
        self.page.evaluate('''() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))''')

    def state(self):
        return self.page.evaluate('''() => {
            const probe = fgView('timeline').probe();
            const panels = ['.tl-canvas', '.tl-detail', '.tl-toolbar'].map(selector => {
                const box = document.querySelector(selector).getBoundingClientRect();
                return [box.x, box.y, box.width, box.height];
            });
            return JSON.stringify([fgData.read('tlData'), probe.sel(), probe.view(), panels, scrollX, scrollY]);
        }''')

    def assert_inside(self, selector, top=0, bottom=None):
        box = self.page.locator(selector).bounding_box()
        self.assertIsNotNone(box)
        if bottom is None:
            bottom = self.page.evaluate('innerHeight')
        self.assertGreaterEqual(box['y'], top - 1)
        self.assertLessEqual(box['y'] + box['height'], bottom + 1)
        self.assertGreaterEqual(box['height'], 44)
        self.assertGreaterEqual(box['x'], -1)
        self.assertLessEqual(box['x'] + box['width'], self.page.evaluate('innerWidth') + 1)
        return box

    def test_short_window_tools_and_final_actions_fit_without_page_scrolling(self):
        for mode, theme in product(('single', 'sidecar'), ('light', 'dark')):
            for width, height, touch in ((320, 568, True), (568, 320, True), (640, 480, False), (1500, 900, False)):
                with self.subTest(mode=mode, theme=theme, size=(width, height), touch=touch):
                    self.open_report(width, height, touch, mode, theme)
                    before = self.state()
                    self.page.locator('[data-act=options]').click()
                    self.assert_inside('.tl-options')
                    self.assert_surface('.tl-options')
                    self.assert_action_inside('.tl-options :focus')
                    self.page.locator('.tl-options').evaluate('element => element.scrollTop = element.scrollHeight')
                    self.assert_action_inside('[data-act=an-lat]')
                    self.assertEqual(self.state(), before)
                    self.page.context.close()

    def assert_action_inside(self, selector):
        box = self.page.locator(selector).bounding_box()
        self.assertGreaterEqual(box['y'], -1)
        self.assertLessEqual(box['y'] + box['height'], self.page.evaluate('innerHeight') + 1)
        self.assertTrue(self.page.locator(selector).evaluate('''element => {
            const box = element.getBoundingClientRect();
            return element.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
        }'''))

    def assert_surface(self, selector):
        self.assertTrue(self.page.locator(selector).evaluate('''element => {
            const box = element.getBoundingClientRect();
            return [8, box.height / 2, box.height - 8].every(offset =>
                element.contains(document.elementFromPoint(box.x + box.width / 2, box.y + offset)));
        }'''))

    def test_notes_use_the_same_visible_viewport_without_changing_recorded_data(self):
        for mode, theme in product(('single', 'sidecar'), ('light', 'dark')):
            with self.subTest(mode=mode, theme=theme):
                self.open_report(mode=mode, theme=theme)
                before = self.state()
                self.page.locator('[data-act=notes]').click()
                self.assert_inside('.tl-notes')
                self.assert_surface('.tl-notes')
                self.assertEqual(self.state(), before)
                self.page.locator('[data-act=options]').click()
                self.assertFalse(self.page.locator('.tl-notes').is_visible())
                self.assert_inside('.tl-options')

    def test_tools_resize_and_scroll_keep_the_open_menu_and_scroll_position(self):
        self.open_report(width=640, height=480, touch=False)
        self.page.locator('[data-act=options]').click()
        self.page.locator('.tl-options').evaluate('element => element.scrollTop = 100')
        original_scroll = self.page.locator('.tl-options').evaluate('element => element.scrollTop')
        self.page.set_viewport_size({'width': 640, 'height': 450})
        self.settle()
        self.assert_inside('.tl-options')
        self.assertAlmostEqual(self.page.locator('.tl-options').evaluate('element => element.scrollTop'), original_scroll, delta=1)
        self.page.evaluate('window.scrollBy(0, 40)')
        self.settle()
        self.assert_inside('.tl-options')
        self.assertEqual(self.page.locator('[data-act=options]').get_attribute('aria-expanded'), 'true')

    def test_visual_viewport_changes_keep_controls_visible_and_allow_growth(self):
        self.open_report(width=390, height=740, viewport='''
            window.overlayViewport = new EventTarget();
            overlayViewport.offsetTop = 0;
            overlayViewport.height = innerHeight;
            Object.defineProperty(window, 'visualViewport', {value: overlayViewport, configurable: true});
        ''')
        self.page.locator('[data-act=options]').click()
        before = self.page.locator('.tl-options').bounding_box()['height']
        self.page.evaluate('''() => {
            overlayViewport.offsetTop = 80;
            overlayViewport.height = 220;
            overlayViewport.dispatchEvent(new Event('resize'));
        }''')
        self.settle()
        self.assert_inside('.tl-options', 80, 300)
        self.assert_action_inside('.tl-options :focus')
        self.page.evaluate('''() => {
            overlayViewport.offsetTop = 0;
            overlayViewport.height = innerHeight;
            overlayViewport.dispatchEvent(new Event('scroll'));
        }''')
        self.settle()
        self.assert_inside('.tl-options')
        self.assertAlmostEqual(self.page.locator('.tl-options').bounding_box()['height'], before, delta=1)

    def test_missing_visual_viewport_and_both_fullscreen_modes_keep_native_focus(self):
        for native in (True, False):
            with self.subTest(native=native):
                initialization = "Object.defineProperty(window, 'visualViewport', {value: undefined, configurable: true});"
                if not native:
                    initialization += "Object.defineProperty(Element.prototype, 'requestFullscreen', {value: undefined, configurable: true});"
                self.open_report(viewport=initialization)
                self.page.locator('[data-act=max]').click()
                self.page.wait_for_function("() => document.querySelector('#timelineView').classList.contains('tl-fullscreen')")
                self.page.locator('[data-act=options]').click()
                self.assert_inside('.tl-options')
                self.assert_surface('.tl-options')
                self.page.keyboard.press('Escape')
                self.assertFalse(self.page.locator('.tl-options').is_visible())
                self.assertTrue(self.page.locator('[data-act=options]').evaluate('element => document.activeElement === element'))
                self.assertTrue(self.page.locator('#timelineView').evaluate("element => element.classList.contains('tl-fullscreen')"))

    def test_open_content_changes_and_idle_hover_do_not_create_measurement_loops(self):
        self.open_report()
        self.page.locator('[data-act=options]').click()
        self.page.locator('.tl-options').evaluate('''element => {
            const extra = document.createElement('p');
            extra.textContent = 'Status '.repeat(100);
            element.prepend(extra);
        }''')
        self.settle()
        self.assert_inside('.tl-options')
        self.page.locator('.tl-options').evaluate('''element => {
            window.openOverlayReads = 0;
            const measure = element.getBoundingClientRect.bind(element);
            element.getBoundingClientRect = () => { openOverlayReads++; return measure(); };
        }''')
        self.page.mouse.move(500, 300)
        self.page.wait_for_timeout(150)
        self.assertEqual(self.page.evaluate('openOverlayReads'), 0)

    def test_kernel_and_event_only_timeline_reports_without_filtering_keep_actions_reachable(self):
        self.open_report()
        for filename in ('kernel.html', 'events.html'):
            with self.subTest(report=filename):
                self.page.goto(Path(self.kernel.dir, filename).as_uri())
                self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
                self.page.locator('.tl-canvas').wait_for()
                before = self.state()
                self.page.locator('[data-act=options]').click()
                self.assert_inside('.tl-options')
                self.page.locator('.tl-options').evaluate('element => element.scrollTop = element.scrollHeight')
                self.assert_action_inside('[data-act=an-lat]')
                self.assertEqual(self.page.locator('#filterInput').count(), 0)
                self.assertEqual(self.state(), before)

    def test_roomy_desktop_keeps_the_original_dropdown_anchor_and_caps(self):
        self.open_report(width=1500, height=900, touch=False)
        self.page.locator('[data-act=options]').click()
        self.assert_inside('.tl-options')
        menu = self.page.locator('.tl-options').bounding_box()
        toolbar = self.page.locator('.tl-toolbar').bounding_box()
        self.assertAlmostEqual(menu['y'], toolbar['y'] + toolbar['height'], delta=1)
        self.assertLessEqual(menu['height'], 480)

    def test_closed_menus_do_not_measure_overlay_geometry_on_scroll_or_mousemove(self):
        self.open_report(width=1500, height=900, touch=False)
        self.page.evaluate('''() => {
            window.overlayGeometryReads = 0;
            for (const element of document.querySelectorAll('.tl-options,.tl-notes')) {
                const measure = element.getBoundingClientRect.bind(element);
                element.getBoundingClientRect = () => { overlayGeometryReads++; return measure(); };
            }
            window.dispatchEvent(new Event('scroll'));
        }''')
        self.page.mouse.move(500, 400)
        self.settle()
        self.assertEqual(self.page.evaluate('overlayGeometryReads'), 0)
