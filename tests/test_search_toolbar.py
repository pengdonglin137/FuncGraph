"""Timeline search controls stay separate from adjacent toolbar actions."""

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
class SearchToolbarBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel(inline_source=True)
        cls.addClassCleanup(cls.kernel.close)
        trace = str(Path(helpers.ROOT, 'samples/sample_mixed.jsonl'))
        options = ['--no-config', '--timeline', '--filter', '--enable-fold', '--func-links']
        cls.kernel.run(trace, *options, '--report-mode', 'single', out='single.html')
        cls.kernel.run(trace, *options, '--report-mode', 'sidecar', out='sidecar')
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

    def open_report(self, width=568, height=320, touch=True, mode='single', theme='light'):
        context = self.browser.new_context(viewport={'width': width, 'height': height},
                                           is_mobile=touch, has_touch=touch)
        self.addCleanup(context.close)
        self.page = context.new_page()
        self.page.set_default_timeout(15000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        url = Path(self.kernel.dir, 'single.html').as_uri() if mode == 'single' else self.server.url
        self.page.goto(url)
        self.page.evaluate("async theme => { document.documentElement.dataset.theme = theme; await fgSwitchView('timeline'); }", theme)
        self.page.locator('.tl-canvas').wait_for()
        self.assertTrue(self.page.locator('[data-act=inl]').is_visible())
        self.assertTrue(self.page.locator('[data-act=notes]').is_visible())
        self.settle()

    def settle(self):
        self.page.evaluate('''() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))''')

    def start(self, query):
        self.page.locator('.tl-search').fill(query)
        self.page.locator('.tl-search').press('Enter')
        self.page.wait_for_function("() => document.querySelector('.tl-count').getAttribute('aria-busy') === 'false'")
        self.settle()

    def assert_controls(self):
        facts = self.page.evaluate('''() => {
            const toolbar = document.querySelector('.tl-toolbar');
            const group = document.querySelector('.tl-search-group').getBoundingClientRect();
            const controls = [...document.querySelectorAll('.tl-primary button,.tl-primary input')]
                .filter(element => element.getClientRects().length);
            const targets = controls.map(element => {
                const box = element.getBoundingClientRect();
                const points = [[.5,.5],[.1,.1],[.9,.1],[.1,.9],[.9,.9]];
                return {name:element.dataset.act || element.className,left:box.left,right:box.right,
                    top:box.top,bottom:box.bottom,width:box.width,height:box.height,
                    hit:points.every(([horizontal,vertical]) => element.contains(document.elementFromPoint(
                        box.x+box.width*horizontal,box.y+box.height*vertical)))};
            });
            const overlapping = [];
            for (let first = 0; first < targets.length; first++) {
                for (let second = first + 1; second < targets.length; second++) {
                    const left = targets[first], right = targets[second];
                    if (Math.min(left.right,right.right)-Math.max(left.left,right.left) > .5 &&
                        Math.min(left.bottom,right.bottom)-Math.max(left.top,right.top) > .5)
                        overlapping.push([left.name,right.name]);
                }
            }
            const escaped = [...document.querySelectorAll('.tl-search-group > input,.tl-search-nav,.tl-count')]
                .filter(element => element.getClientRects().length).flatMap(element => {
                    const box = element.getBoundingClientRect();
                    return box.left < group.left - 1 || box.right > group.right + 1 ? [element.className] : [];
                });
            return {targets,overlapping,escaped,overflow:toolbar.scrollWidth-toolbar.clientWidth,
                inputWidth:document.querySelector('.tl-search').getBoundingClientRect().width,
                compact:document.querySelector('#timelineView').classList.contains('tl-compact')};
        }''')
        self.assertEqual(facts['overlapping'], [])
        self.assertEqual(facts['escaped'], [])
        self.assertLessEqual(facts['overflow'], 1)
        if facts['compact']:
            self.assertGreaterEqual(facts['inputWidth'], 100)
        for target in facts['targets']:
            self.assertTrue(target['hit'], target)
            if facts['compact'] and target['name'] in ('searchprev', 'searchnext', 'max'):
                self.assertGreaterEqual(target['height'], 44)
        return facts

    def test_initial_and_active_search_controls_are_not_occluded_in_both_delivery_modes(self):
        for mode, theme in product(('single', 'sidecar'), ('light', 'dark')):
            for width, height, touch in ((320, 568, True), (390, 740, True), (568, 320, True),
                                         (640, 360, True), (844, 390, True), (1500, 900, False)):
                with self.subTest(mode=mode, theme=theme, size=(width, height), touch=touch):
                    self.open_report(width, height, touch, mode, theme)
                    self.assert_controls()
                    self.start('.*')
                    self.assert_controls()
                    self.page.context.close()

    def test_refresh_keeps_chart_and_toolbar_in_place_on_short_touch_screens(self):
        for width, mode in ((320, 'single'), (350, 'single'), (720, 'sidecar'), (760, 'sidecar')):
            with self.subTest(width=width, mode=mode):
                self.open_report(width, 420, mode=mode)
                self.start('.*')
                geometry = self.page.locator('.tl-search').evaluate('''async input => {
                    const selectors = ['.tl-primary', '.tl-search', '.tl-search-nav', '.tl-more', '.tl-canvas'];
                    const read = () => selectors.map(selector => document.querySelector(selector).getBoundingClientRect().toJSON());
                    const idle = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    input.focus({preventScroll:true});
                    await idle();
                    const before = read();
                    input.dispatchEvent(new CompositionEvent('compositionstart', {bubbles:true}));
                    await idle();
                    const during = read(), status = document.querySelector('.tl-count');
                    const result = status.querySelector('.tl-count-value');
                    const details = {busy:status.getAttribute('aria-busy'), message:status.innerText,
                        oldCountHidden:!!result && getComputedStyle(result).visibility === 'hidden',
                        focused:document.activeElement === input};
                    input.dispatchEvent(new CompositionEvent('compositionend', {bubbles:true}));
                    return {before, during, details};
                }''')
                self.assertEqual(geometry['during'], geometry['before'])
                self.assertEqual(geometry['details'], dict(busy='true', message='Composing…', oldCountHidden=True, focused=True))
                self.page.locator('.tl-count[aria-busy=false]').wait_for(state='attached')
                self.assert_controls()

    def test_short_landscape_filter_badge_preserves_the_search_toolbar(self):
        for width, height, mode in ((660, 360, 'single'), (720, 400, 'sidecar'), (844, 390, 'single')):
            with self.subTest(width=width, height=height, mode=mode):
                self.open_report(width, height, mode=mode)
                self.start('.*')
                toolbar = self.page.locator('.tl-toolbar')
                idle_height = toolbar.evaluate('toolbar => toolbar.offsetHeight')
                self.page.evaluate('''() => fgNotifyFilter('test scope', new Set(fgData.read('tlData').cols.line))''')
                self.page.locator('.tl-count[aria-busy=false]').wait_for(state='attached')
                self.settle()
                tools = self.page.get_by_role('button', name='Tools · Filter on', exact=True)
                self.assertTrue(tools.is_visible())
                self.assertEqual(tools.locator('.tl-more-filter').is_visible(), width > 800)
                if width <= 800:
                    self.assertEqual(toolbar.evaluate('toolbar => toolbar.offsetHeight'), idle_height)
                    self.assertEqual(tools.evaluate("node => getComputedStyle(node, '::after').content"), '"●"')
                self.assert_controls()
                tools.tap()
                self.page.get_by_role('dialog', name='Timeline tools', exact=True).wait_for()
                self.page.context.close()

    def test_focused_navigation_waits_without_activating_and_has_a_fallback(self):
        self.open_report(width=390, height=740)
        self.start('.*')
        initial = self.page.evaluate('fgView("timeline").probe().sel()')
        next_button = self.page.get_by_role('button', name='Next search match')
        next_button.focus()
        for query in ('.*?', '['):
            with self.subTest(query=query):
                state = next_button.evaluate('''(button, query) => {
                    const input = document.querySelector('.tl-search');
                    input.value = query;
                    input.dispatchEvent(new Event('input', {bubbles:true}));
                    button.click();
                    return {focused:button === document.activeElement, tabIndex:button.tabIndex,
                        disabled:button.getAttribute('aria-disabled'),
                        busy:document.querySelector('.tl-count').getAttribute('aria-busy')};
                }''', query)
                self.assertEqual(state, dict(focused=True, tabIndex=-1, disabled='true', busy='true'))
                self.page.locator('.tl-count[aria-busy=false]').wait_for(state='attached')
                self.assertEqual(self.page.evaluate('fgView("timeline").probe().sel()'), initial)
                if query == '.*?':
                    self.assertTrue(next_button.evaluate('button => button === document.activeElement && button.tabIndex === 0'))
                    self.assertTrue(next_button.is_enabled())
                else:
                    self.assertEqual(self.page.locator('.tl-count').inner_text(), 'Invalid regex')
                    self.assertTrue(self.page.locator('.tl-search').evaluate('input => input === document.activeElement'))

    def test_touch_next_and_previous_do_not_accidentally_activate_fullscreen(self):
        for mode, theme in product(('single', 'sidecar'), ('light', 'dark')):
            with self.subTest(mode=mode, theme=theme):
                self.open_report(mode=mode, theme=theme)
                self.start('.*')
                before = self.page.evaluate("JSON.stringify(fgData.read('tlData'))")
                selected = self.page.evaluate("fgView('timeline').probe().sel()")
                self.tap('searchnext')
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), selected + 1)
                self.assertEqual(self.page.locator('[data-act=max]').get_attribute('aria-pressed'), 'false')
                self.tap('searchprev')
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), selected)
                self.assertEqual(self.page.evaluate("JSON.stringify(fgData.read('tlData'))"), before)

    def tap(self, action):
        box = self.page.locator(f'[data-act={action}]').bounding_box()
        self.page.touchscreen.tap(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)

    def test_long_counter_fixture_preserves_exact_digits_and_every_touch_target(self):
        self.open_report()
        self.start('.*')
        before = self.page.evaluate("JSON.stringify(fgData.read('tlData'))")
        for width, height in ((320, 568), (390, 740), (568, 320), (640, 360), (844, 390)):
            self.page.set_viewport_size({'width': width, 'height': height})
            self.settle()
            for value in ('1 / 20000', '20000 / 20000', '1000000 / 1000000', '4294967295 / 4294967295'):
                with self.subTest(size=(width, height), value=value):
                    self.page.locator('.tl-count-value').evaluate('(element,value) => element.textContent = value', value)
                    self.settle()
                    self.assert_controls()
                    self.assertEqual(self.page.locator('.tl-count').text_content(), value + ' calls')
                    self.assertEqual(self.page.locator('.tl-count-value').evaluate('''element => {
                        const range = document.createRange();
                        range.selectNodeContents(element);
                        return range.getClientRects().length;
                    }'''), 1)
        self.assertEqual(self.page.evaluate("JSON.stringify(fgData.read('tlData'))"), before)

    def test_empty_invalid_and_cleared_search_keep_input_and_fullscreen_separate(self):
        self.open_report()
        for query in ('not_a_recorded_function', '[', '.*'):
            with self.subTest(query=query):
                self.start(query)
                self.assert_controls()
        self.page.locator('.tl-search').press('Escape')
        self.settle()
        self.assertEqual(self.page.locator('.tl-search').input_value(), '')
        self.assertFalse(self.page.locator('.tl-search-nav').is_visible())
        self.assert_controls()

    def test_fullscreen_keyboard_follows_the_compact_toolbar_order(self):
        for mode, native, height in product(('single', 'sidecar'), (True, False), (240, 640)):
            with self.subTest(mode=mode, native=native, height=height):
                self.open_report(width=320, height=height, mode=mode)
                if not native:
                    self.page.evaluate('Element.prototype.requestFullscreen = undefined')
                self.page.locator('[data-act=max]').tap()
                self.page.wait_for_function('native => !!document.fullscreenElement === native && !document.querySelector("[data-act=max]").disabled', arg=native)
                self.start('.*')
                search = self.page.locator('.tl-search')
                search.focus()
                zoom = ['zoomout', 'zoomin', 'fit']
                zoom_in_tools = height == 240
                order = ['searchprev', 'searchnext', 'max', 'help']
                if not zoom_in_tools:
                    order += zoom
                order += ['inl', 'notes', 'options']
                for action in order:
                    self.page.keyboard.press('Tab')
                    self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), action)
                if zoom_in_tools:
                    self.page.keyboard.press('Enter')
                    for position, action in enumerate(zoom):
                        if position:
                            self.page.keyboard.press('Tab')
                        self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), action)
                    self.page.keyboard.press('Escape')
                    self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), 'options')
                for action in reversed(order[:-1]):
                    self.page.keyboard.press('Shift+Tab')
                    self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), action)
                self.page.keyboard.press('Shift+Tab')
                self.assertTrue(search.evaluate('input => input === document.activeElement'))

    def test_toolbar_breakpoint_preserves_focus_and_restores_desktop_order(self):
        self.open_report(width=390, height=740)
        self.start('.*')
        select_state = '() => [fgView("timeline").probe().sel(), document.querySelector(".tl-search").value]'
        before = self.page.evaluate(select_state)
        fit = self.page.locator('[data-act=fit]')
        fit.focus()
        for width, height, compact, next_after_search in ((1200, 800, False, 'zoomout'),
                                                          (390, 740, True, 'max')):
            with self.subTest(compact=compact):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.page.wait_for_function('compact => document.querySelector("#timelineView").classList.contains("tl-compact") === compact', arg=compact)
                self.assertTrue(fit.evaluate('button => button === document.activeElement'))
                self.assertEqual(self.page.evaluate(select_state), before)
                self.page.locator('[data-act=searchnext]').focus()
                self.page.keyboard.press('Tab')
                self.assertEqual(self.page.evaluate('document.activeElement.dataset.act'), next_after_search)
                fit.focus()
        tools = self.page.locator('[data-act=options]')
        tools.press('Enter')
        self.assertTrue(self.page.locator('.tl-options').evaluate('options => options.contains(document.activeElement)'))
        self.page.set_viewport_size({'width': 1200, 'height': 800})
        self.page.wait_for_function('() => !document.querySelector("#timelineView").classList.contains("tl-compact")')
        self.assertEqual(tools.get_attribute('aria-expanded'), 'false')
        self.assertTrue(tools.evaluate('button => button === document.activeElement'))
        self.assertEqual(self.page.evaluate(select_state), before)

    def test_orientation_changes_keep_query_selection_and_open_tools_usable(self):
        self.open_report(width=390, height=740)
        self.start('.*')
        before = self.page.evaluate("JSON.stringify(fgData.read('tlData'))")
        selected = self.page.evaluate("fgView('timeline').probe().sel()")
        for width, height in ((568, 320), (640, 360), (390, 740)):
            self.page.set_viewport_size({'width': width, 'height': height})
            self.settle()
            self.assert_controls()
            self.assertEqual(self.page.locator('.tl-search').input_value(), '.*')
            self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), selected)
            self.page.locator('[data-act=options]').tap()
            self.settle()
            box = self.page.locator('.tl-options').bounding_box()
            self.assertGreaterEqual(box['y'], 0)
            self.assertLessEqual(box['y'] + box['height'], height + 1)
            self.page.keyboard.press('Escape')
        self.assertEqual(self.page.evaluate("JSON.stringify(fgData.read('tlData'))"), before)
