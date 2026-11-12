"""Horizontal Text scrolling stays separate from report navigation and layout."""
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
class TextViewportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel(inline_source=True)
        trace = Path(cls.kernel.dir) / 'wide-trace.txt'
        cls.argument = 'abcdef0123456789' * 180 + 'END_OF_RAW_ARGUMENT'
        trace.write_text('# tracer: function_graph\n'
                         ' 3)               |  root() { /* <-main+0x4/0x80 */\n' +
                         (' 3)   0.050 us    |    leaf(arg=' + cls.argument + '); /* <-root+0x4/0x80 */\n') +
                         ' 3)   0.050 us    |    leaf(); /* <-root+0x4/0x80 */\n' * 4200 +
                         ' 3) 300.000 us    |  } /* root */\n')
        cls.kernel.run(str(trace), '--timeline', '--enable-fold', '--filter', '--func-links', out='index.html')
        cls.kernel.run(str(trace), '--timeline', '--enable-fold', '--filter', '--func-links',
                       '--report-mode', 'sidecar', out='sidecar')
        cls.server = create_server(Path(cls.kernel.dir))
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        cls.thread.start()
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(ignore_default_args=['--hide-scrollbars'])
        except Exception as error:
            cls.playwright.stop()
            cls.server.shutdown()
            cls.thread.join(timeout=5)
            cls.server.server_close()
            cls.kernel.close()
            raise unittest.SkipTest(str(error))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()
        cls.kernel.close()

    def setUp(self):
        self.pages = []
        self.errors = []

    def tearDown(self):
        for page in self.pages:
            page.context.close()
        self.assertEqual(self.errors, [])

    def open_report(self, mode='single', width=390, theme='light', touch=None):
        for previous in self.pages:
            previous.context.close()
        self.pages.clear()
        mobile = width < 640 if touch is None else touch
        context = self.browser.new_context(viewport={'width': width, 'height': 844},
                                           is_mobile=mobile, has_touch=mobile)
        page = context.new_page()
        self.pages.append(page)
        page.on('pageerror', lambda error: self.errors.append(str(error)))
        page.add_init_script('localStorage.clear();localStorage.setItem("theme", ' + repr(theme) + ');')
        suffix = 'index.html' if mode == 'single' else 'sidecar/index.html'
        page.goto(urljoin(self.server.url, suffix), wait_until='load')
        page.evaluate('async () => { await fgSwitchView("text"); }')
        return page

    def facts(self, page):
        return page.evaluate('''() => {
            const viewport = document.getElementById('textViewport');
            return {rootWidth:document.documentElement.scrollWidth, width:document.documentElement.clientWidth,
                innerWidth, x:scrollX, y:scrollY, left:viewport.scrollLeft,
                extent:viewport.scrollWidth, viewport:viewport.clientWidth,
                chrome:['.page-header','.view-tabs','.controls'].map(selector => {
                    const bounds = document.querySelector(selector).getBoundingClientRect();
                    return [bounds.left,bounds.right];
                })};
        }''')

    def test_long_rows_pan_without_expanding_or_displacing_page_chrome(self):
        for mode in ['single', 'sidecar']:
            for width in [320, 390, 1500]:
                for theme in ['light', 'dark']:
                    with self.subTest(mode=mode, width=width, theme=theme):
                        page = self.open_report(mode, width, theme)
                        self.assertEqual(page.locator('#textViewport > #content').count(), 1)
                        before = self.facts(page)
                        self.assertGreater(before['extent'], 10000)
                        self.assertLessEqual(before['rootWidth'], before['width'])
                        self.assertEqual(before['innerWidth'], width)
                        page.locator('#textViewport').evaluate('node => node.scrollLeft = 400')
                        after = self.facts(page)
                        self.assertEqual(after['left'], 400)
                        self.assertEqual(after['x'], 0)
                        self.assertEqual(after['chrome'], before['chrome'])
                        for left, right in after['chrome']:
                            self.assertGreaterEqual(left, 0)
                            self.assertLessEqual(right, width)

    def test_horizontal_keys_use_text_scroller_without_moving_vertical_position(self):
        for mode in ['single', 'sidecar']:
            with self.subTest(mode=mode):
                page = self.open_report(mode, width=1500)
                page.evaluate('window.scrollTo(0, 500)')
                page.locator('#L20').evaluate('node => node.focus({preventScroll:true})')
                before = self.facts(page)
                page.keyboard.press('l')
                page.wait_for_function('() => document.getElementById("textViewport").scrollLeft >= 99')
                self.assertEqual(self.facts(page)['x'], 0)
                self.assertEqual(self.facts(page)['y'], before['y'])
                page.keyboard.press('h')
                page.wait_for_function('() => document.getElementById("textViewport").scrollLeft <= 1')
                page.keyboard.press('Shift+L')
                page.wait_for_function('() => document.getElementById("textViewport").scrollLeft >= 99')
                self.assertEqual(self.facts(page)['y'], before['y'])
                page.keyboard.press('Shift+H')
                page.wait_for_function('() => document.getElementById("textViewport").scrollLeft <= 1')

    def test_narrow_desktop_toolbar_keeps_legend_pairs_and_filter_actions_inside_window(self):
        for mode in ['single', 'sidecar']:
            for theme in ['light', 'dark']:
                with self.subTest(mode=mode, theme=theme):
                    page = self.open_report(mode, width=320, theme=theme, touch=False)
                    self.assertFalse(page.locator('.controls').evaluate('node => node.classList.contains("text-compact")'))
                    pairs = page.locator('.heatmap-legend-level')
                    self.assertEqual(pairs.count(), 7)
                    for pair in pairs.all():
                        self.assertEqual(pair.locator('.heatmap-legend-item').count(), 1)
                        self.assertEqual(pair.locator('.heatmap-legend-label').count(), 1)
                        bounds = pair.bounding_box()
                        self.assertGreaterEqual(bounds['x'], 0)
                        self.assertLessEqual(bounds['x'] + bounds['width'], 320)
                    for selector in ['#filterInput', '#filterBox button[onclick="applyFilter()"]', '.clear-btn', '.filter-help-btn']:
                        bounds = page.locator(selector).bounding_box()
                        self.assertGreater(bounds['width'], 0)
                        self.assertLessEqual(bounds['x'] + bounds['width'], 320)
                    facts = self.facts(page)
                    self.assertLessEqual(facts['rootWidth'], facts['width'])

    def test_view_roundtrip_retains_horizontal_reading_position_without_overflow(self):
        for mode in ['single', 'sidecar']:
            with self.subTest(mode=mode):
                page = self.open_report(mode)
                page.locator('#textViewport').evaluate('node => node.scrollLeft = 550')
                page.evaluate('async () => { await fgSwitchView("timeline"); }')
                self.assertFalse(page.locator('#textViewport').is_visible())
                self.assertEqual(page.evaluate('scrollX'), 0)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
                page.evaluate('async () => { await fgSwitchView("text"); }')
                self.assertEqual(self.facts(page)['left'], 550)
                self.assertEqual(self.facts(page)['x'], 0)

    def test_horizontal_keys_do_not_intercept_filter_editing(self):
        page = self.open_report(width=1500)
        page.locator('#textViewport').evaluate('node => node.scrollLeft = 300')
        if not page.locator('#filterInput').is_visible():
            page.locator('#textFilterToggle').click()
        page.locator('#filterInput').focus()
        page.keyboard.type('hlHL')
        self.assertEqual(page.locator('#filterInput').input_value(), 'hlHL')
        self.assertEqual(self.facts(page)['left'], 300)
        self.assertEqual(self.facts(page)['x'], 0)

    def test_vertical_keyboard_and_anchor_navigation_preserve_horizontal_reading_position(self):
        for mode in ['single', 'sidecar']:
            for width in [390, 1500]:
                with self.subTest(mode=mode, width=width):
                    page = self.open_report(mode, width=width)
                    page.locator('#textViewport').evaluate('node => node.scrollLeft = 550')
                    for target, action in [('L4101', '''history.replaceState(null, '', '#L4101');
                            handleAnchorNavigation();'''), ('L2', 'navigateToTextBoundary(false);')]:
                        page.evaluate('() => {' + action + '}')
                        page.wait_for_function('''id => {
                            const row = document.getElementById(id).getBoundingClientRect();
                            const previous = window.viewportNavigationPosition;
                            const settled = previous?.id === id && previous.y === scrollY ? previous.settled + 1 : 0;
                            window.viewportNavigationPosition = {id, y:scrollY, settled};
                            return settled >= 3 && row.top >= document.querySelector('.controls').getBoundingClientRect().bottom &&
                                row.bottom <= innerHeight;
                        }''', arg=target)
                        bounds = page.locator('#' + target).bounding_box()
                        toolbar = page.locator('.controls').bounding_box()
                        self.assertGreaterEqual(bounds['y'], toolbar['y'] + toolbar['height'])
                        self.assertLessEqual(bounds['y'] + bounds['height'], 844)
                        self.assertEqual(self.facts(page)['left'], 550)
                        self.assertEqual(page.locator('#textViewport').evaluate('node => node.scrollTop'), 0)
                        self.assertEqual(self.facts(page)['x'], 0)

    def test_horizontal_pan_repaints_guides_and_clips_them_to_the_text_region(self):
        page = self.open_report('sidecar', width=1500)
        page.wait_for_function('() => !!document.querySelector(".fold-guide-layer")')
        page.evaluate('''() => {
            window.guideRepaints = 0;
            const clear = CanvasRenderingContext2D.prototype.clearRect;
            CanvasRenderingContext2D.prototype.clearRect = function(...args) {
                if (this.canvas.classList.contains('fold-guide-layer')) guideRepaints++;
                return clear.apply(this, args);
            };
            const viewport = document.getElementById('textViewport');
            const indent = document.querySelector('#L3 .call-indent');
            const step = parseFloat(getComputedStyle(indent, '::before').width);
            viewport.scrollLeft = indent.getBoundingClientRect().right - step * 1.5 -
                (viewport.getBoundingClientRect().left - 3);
        }''')
        page.wait_for_function('() => guideRepaints > 0')
        result = page.evaluate('''() => {
            const canvas = document.querySelector('.fold-guide-layer');
            const context = canvas.getContext('2d');
            const viewport = document.getElementById('textViewport').getBoundingClientRect();
            const toolbar = document.querySelector('.controls').getBoundingClientRect();
            const left = context.getImageData(0, 0, Math.floor(viewport.left), canvas.height).data;
            const top = context.getImageData(0, 0, canvas.width, Math.floor(toolbar.bottom)).data;
            return {left:Array.from(left).some((value,index) => index % 4 === 3 && value),
                top:Array.from(top).some((value,index) => index % 4 === 3 && value)};
        }''')
        self.assertEqual(result, dict(left=False, top=False))

    def test_mouse_wheel_and_touch_swipe_pan_only_text(self):
        for width in [390, 1500]:
            with self.subTest(width=width):
                page = self.open_report(width=width)
                before = self.facts(page)
                bounds = page.locator('#L3').bounding_box()
                middle = bounds['y'] + bounds['height'] / 2
                if width == 390:
                    session = page.context.new_cdp_session(page)
                    session.send('Input.dispatchTouchEvent', dict(type='touchStart', touchPoints=[dict(x=320, y=middle)]))
                    for position in range(300, 59, -20):
                        session.send('Input.dispatchTouchEvent', dict(type='touchMove', touchPoints=[dict(x=position, y=middle)]))
                        page.wait_for_timeout(16)
                    session.send('Input.dispatchTouchEvent', dict(type='touchEnd', touchPoints=[]))
                    session.detach()
                else:
                    page.mouse.move(300, middle)
                    page.mouse.wheel(350, 0)
                page.wait_for_function('() => document.getElementById("textViewport").scrollLeft > 50')
                after = self.facts(page)
                self.assertEqual(after['x'], 0)
                self.assertEqual(after['y'], before['y'])
                self.assertEqual(after['chrome'], before['chrome'])

    def test_horizontal_keys_fall_back_to_document_for_legacy_markup(self):
        page = self.open_report(width=1500)
        page.evaluate('''() => {
            const viewport = document.getElementById('textViewport');
            viewport.replaceWith(document.getElementById('content'));
            document.getElementById('L20').focus({preventScroll:true});
        }''')
        page.keyboard.press('l')
        page.wait_for_function('() => scrollX >= 99')
        page.keyboard.press('h')
        page.wait_for_function('() => scrollX <= 1')

    def test_source_expansion_and_filtering_preserve_data_and_local_horizontal_scroll(self):
        for mode in ['single', 'sidecar']:
            with self.subTest(mode=mode):
                page = self.open_report(mode)
                page.locator('#textViewport').evaluate('node => node.scrollLeft = 500')
                before = self.facts(page)
                line_id = page.locator('#L3').get_attribute('data-line-id')
                page.evaluate('id => toggleExpand(id)', line_id)
                page.wait_for_timeout(100)
                self.assertTrue(page.locator('#' + line_id + '_content').is_visible())
                self.assertEqual(self.facts(page)['left'], before['left'])
                self.assertEqual(self.facts(page)['x'], 0)
                self.assertIn(self.argument, page.locator('#L3').inner_text())
                page.evaluate('''() => {
                    document.getElementById('filterInput').value = 'func:leaf';
                    applyFilter();
                    fgSetTextOrder('task');
                }''')
                self.assertIn(self.argument, page.locator('#L3').inner_text())
                self.assertEqual(self.facts(page)['x'], 0)
                page.locator('#textViewport').evaluate('node => node.scrollLeft = node.scrollWidth')
                after = self.facts(page)
                self.assertAlmostEqual(after['left'] + after['viewport'], after['extent'], delta=1)
                self.assertLessEqual(after['rootWidth'], after['width'])
