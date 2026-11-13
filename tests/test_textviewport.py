"""Text scrolling and delegated interactions preserve report navigation and layout."""
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

    def test_view_tabs_stay_above_text_controls_and_switch_without_losing_position(self):
        for mode in ('single', 'sidecar'):
            for width in (390, 1500):
                for theme in ('light', 'dark'):
                    with self.subTest(mode=mode, width=width, theme=theme):
                        page = self.open_report(mode, width, theme)
                        page.evaluate('''() => {
                            document.getElementById('textViewport').scrollLeft = 550;
                            scrollTo(0, 12000);
                        }''')
                        page.wait_for_function('() => scrollY >= 12000')
                        position = self.facts(page)
                        facts = page.evaluate('''() => {
                            const tabs = document.querySelector('.view-tabs');
                            const controls = document.querySelector('.controls');
                            const bounds = tabs.getBoundingClientRect();
                            const button = tabs.querySelector('[data-view=timeline]').getBoundingClientRect();
                            return {top:bounds.top, bottom:bounds.bottom,
                                controlTop:controls.getBoundingClientRect().top,
                                reachable:document.elementFromPoint(button.x + button.width / 2,
                                    button.y + button.height / 2)?.closest('.view-tab')?.dataset.view};
                        }''')
                        self.assertGreaterEqual(facts['top'], 0)
                        self.assertAlmostEqual(facts['controlTop'], facts['bottom'], delta=1)
                        self.assertEqual(facts['reachable'], 'timeline')
                        page.locator('.view-tab[data-view=timeline]').click()
                        page.wait_for_function("() => fgCurrentView() === 'timeline'")
                        self.assertEqual(page.evaluate('scrollY'), 0)
                        page.locator('.view-tab[data-view=text]').click()
                        page.wait_for_function("() => fgCurrentView() === 'text'")
                        restored = self.facts(page)
                        self.assertEqual(restored['y'], position['y'])
                        self.assertEqual(restored['left'], position['left'])

    def test_tab_height_changes_keep_controls_and_anchor_targets_clear(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                page = self.open_report(mode, width=1500)
                page.locator('.view-tab').first.evaluate('node => node.style.paddingBlock = "20px"')
                page.evaluate('''() => {
                    history.replaceState(null, '', '#L4101');
                    handleAnchorNavigation();
                }''')
                page.wait_for_function('''() => {
                    const tabs = document.querySelector('.view-tabs').getBoundingClientRect();
                    const controls = document.querySelector('.controls').getBoundingClientRect();
                    const row = document.getElementById('L4101').getBoundingClientRect();
                    return tabs.top >= 0 && Math.abs(controls.top - tabs.bottom) <= 1 &&
                        row.top >= controls.bottom && row.bottom <= innerHeight;
                }''')
                page.set_viewport_size({'width': 390, 'height': 844})
                page.wait_for_function('''() => {
                    const tabs = document.querySelector('.view-tabs').getBoundingClientRect();
                    const controls = document.querySelector('.controls').getBoundingClientRect();
                    return tabs.top >= 0 && Math.abs(controls.top - tabs.bottom) <= 1;
                }''')
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)

    def test_trace_line_roundtrips_keep_hover_work_bounded_and_all_data_intact(self):
        for mode in ('single', 'sidecar'):
            for theme in ('light', 'dark'):
                with self.subTest(mode=mode, theme=theme):
                    page = self.open_report(mode, width=1500, theme=theme)
                    page.set_default_timeout(10000)
                    page.evaluate('''async () => {
                        await fgSwitchView('timeline');
                        const model = fgView('timeline').probe();
                        model.select(3500);
                        model.zoomTo(model.sStart[0], model.sEnd[0], 3500);
                        window.roundtripSnapshot = () => ({data:JSON.stringify(fgData.read('tlData')),
                            selected:model.sel(), view:model.view(), rows:fgTextNavigation.order.slice(),
                            original:document.getElementById('content').textContent});
                        window.originalRoundtrip = roundtripSnapshot();
                        window.hiddenTextMeasurements = 0;
                        const bounds = Element.prototype.getBoundingClientRect;
                        Element.prototype.getBoundingClientRect = function(...args) {
                            if (fgCurrentView() !== 'text' && document.getElementById('content').contains(this)) {
                                hiddenTextMeasurements++;
                            }
                            return bounds.apply(this, args);
                        };
                        window.hoverRoundtrip = () => {
                            const canvas = document.querySelector('.tl-canvas');
                            const [center, vertical] = model.xyOf(0);
                            const move = offset => canvas.dispatchEvent(new MouseEvent('mousemove',
                                {bubbles:true, clientX:center + offset, clientY:vertical}));
                            move(0);
                            const tip = document.querySelector('.tl-tip');
                            const node = tip.firstElementChild;
                            const text = tip.textContent;
                            const first = tip.getBoundingClientRect().toJSON();
                            for (let offset = 1; offset <= 12; offset++) move(offset);
                            return {sameNode:node === tip.firstElementChild, sameText:text === tip.textContent,
                                first, last:tip.getBoundingClientRect().toJSON(),
                                hiddenMeasurements:hiddenTextMeasurements};
                        };
                        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    }''')
                    session = page.context.new_cdp_session(page)
                    session.send('Performance.enable')
                    for iteration in range(4):
                        before = {metric['name']: metric['value'] for metric in
                                  session.send('Performance.getMetrics')['metrics']}
                        result = page.evaluate('hoverRoundtrip()')
                        after = {metric['name']: metric['value'] for metric in
                                 session.send('Performance.getMetrics')['metrics']}
                        self.assertTrue(result['sameNode'])
                        self.assertTrue(result['sameText'])
                        self.assertEqual(result['hiddenMeasurements'], 0)
                        self.assertAlmostEqual(abs(result['last']['x'] - result['first']['x']), 12, delta=1)
                        self.assertLessEqual(after['LayoutCount'] - before['LayoutCount'], 2)
                        self.assertEqual(page.locator('.tl-tip .t-name').inner_text(), 'root')
                        self.assertTrue(page.evaluate('JSON.stringify(roundtripSnapshot()) === JSON.stringify(originalRoundtrip)'))
                        if iteration == 3:
                            break
                        page.locator('.tl-detail a[data-line]').last.click()
                        page.wait_for_function("() => fgCurrentView() === 'text' && scrollY > 5000")
                        tab = page.locator('.view-tab[data-view=timeline]').bounding_box()
                        self.assertGreaterEqual(tab['y'], 0)
                        self.assertLess(tab['y'] + tab['height'], 844)
                        page.locator('.view-tab[data-view=timeline]').click()
                        page.wait_for_function("() => fgCurrentView() === 'timeline'")
                        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')

    def test_tooltips_fit_the_viewport_and_update_on_different_hover_targets(self):
        for mode in ('single', 'sidecar'):
            for width in (320, 1500):
                with self.subTest(mode=mode, width=width):
                    page = self.open_report(mode, width=width, touch=False)
                    page.evaluate('''async () => {
                        await fgSwitchView('timeline');
                        const model = fgView('timeline').probe();
                        model.select(0); model.zoomTo(model.sStart[0], model.sEnd[0], 0);
                    }''')
                    for index in (0, 1):
                        result = page.evaluate('''index => {
                            const model = fgView('timeline').probe();
                            model.select(index); model.zoomTo(model.sStart[index], model.sEnd[index], index);
                            const canvas = document.querySelector('.tl-canvas');
                            const [center, vertical] = model.xyOf(index);
                            canvas.dispatchEvent(new MouseEvent('mousemove',
                                {bubbles:true, clientX:center, clientY:vertical}));
                            return {expected:model.nameOf(index), bounds:document.querySelector('.tl-tip').getBoundingClientRect().toJSON()};
                        }''', index)
                        self.assertEqual(page.locator('.tl-tip .t-name').inner_text(), result['expected'])
                        self.assertGreaterEqual(result['bounds']['left'], 0)
                        self.assertLessEqual(result['bounds']['right'], width)
                        self.assertGreaterEqual(result['bounds']['top'], 0)

    def test_compact_symbols_preserve_pixels_character_styles_and_native_selection(self):
        for mode in ['single', 'sidecar']:
            for width in [390, 1500]:
                for theme in ['light', 'dark']:
                    with self.subTest(mode=mode, width=width, theme=theme):
                        page = self.open_report(mode, width, theme)
                        page.evaluate('''async () => {
                            await document.fonts.ready;
                            const content = document.querySelector('#L4 .line-content');
                            const run = Array.from(content.querySelectorAll('.hl-symbol')).find(node => node.textContent === '();');
                            if (!run) throw new Error('Expected one compact leaf terminator');
                            run.scrollIntoView({block:'nearest', inline:'center'});
                            window.symbolSnapshot = () => {
                                const walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT);
                                const characters = [];
                                while (walker.nextNode()) {
                                    const node = walker.currentNode, style = getComputedStyle(node.parentElement);
                                    for (let offset = 0; offset < node.length; offset++) {
                                        const range = document.createRange();
                                        range.setStart(node, offset); range.setEnd(node, offset + 1);
                                        const bounds = range.getBoundingClientRect();
                                        characters.push([node.data[offset], style.color, style.fontFamily,
                                            style.fontSize, style.fontWeight, style.letterSpacing,
                                            bounds.x, bounds.y, bounds.width, bounds.height]);
                                    }
                                }
                                return {text:content.textContent, characters};
                            };
                            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                        }''')
                        compact = page.evaluate('symbolSnapshot()')
                        bounds = page.locator('#L4').bounding_box()
                        clip = dict(x=max(0, bounds['x']), y=bounds['y'],
                                    width=min(width, bounds['x'] + bounds['width']) - max(0, bounds['x']),
                                    height=bounds['height'])
                        compact_pixels = page.screenshot(clip=clip, animations='disabled')
                        selection = '''() => {
                            const content = document.querySelector('#L4 .line-content');
                            const range = document.createRange(); range.selectNodeContents(content);
                            const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
                            const selected = selection.toString(); selection.removeAllRanges(); return selected;
                        }'''
                        self.assertEqual(page.evaluate(selection), compact['text'])
                        expanded = page.evaluate('''async () => {
                            const content = document.querySelector('#L4 .line-content');
                            let removed = 0;
                            for (const run of Array.from(content.querySelectorAll('.hl-symbol'))) {
                                if (run.textContent.length < 2) continue;
                                const fragment = document.createDocumentFragment();
                                for (const character of run.textContent) {
                                    const symbol = run.cloneNode(false); symbol.textContent = character;
                                    fragment.appendChild(symbol);
                                }
                                removed += run.textContent.length - 1; run.replaceWith(fragment);
                            }
                            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                            return {removed, snapshot:symbolSnapshot()};
                        }''')
                        self.assertEqual(expanded['removed'], 2)
                        self.assertEqual(page.screenshot(clip=clip, animations='disabled'), compact_pixels)
                        self.assertEqual(expanded['snapshot']['text'], compact['text'])
                        self.assertEqual(len(expanded['snapshot']['characters']), len(compact['characters']))
                        for before, after in zip(compact['characters'], expanded['snapshot']['characters']):
                            self.assertEqual(before[:6], after[:6])
                            for compact_position, expanded_position in zip(before[6:], after[6:]):
                                self.assertAlmostEqual(compact_position, expanded_position, delta=1 / 32)
                        self.assertEqual(page.evaluate(selection), compact['text'])

    def test_text_interactions_use_two_delegates_instead_of_per_row_handlers(self):
        for mode in ['single', 'sidecar']:
            with self.subTest(mode=mode):
                page = self.open_report(mode, width=1500)
                self.assertEqual(page.locator('#content .line-container').count(), 4204)
                self.assertEqual(page.locator('#content .line-container[onclick], #content .line-container[ondblclick], '
                                              '#content .line-container > .line-number[onclick]').count(), 0)
                self.assertTrue(page.evaluate('''() => Array.from(document.querySelectorAll(
                    '#content .line-container, #content .line-container > .line-number')).every(
                    node => node.onclick === null && node.ondblclick === null)'''))
                session = page.context.new_cdp_session(page)
                target = session.send('Runtime.evaluate', {'expression': 'document.getElementById("content")',
                                                           'objectGroup': 'text-interactions'})['result']['objectId']
                for iteration in range(2):
                    listeners = session.send('DOMDebugger.getEventListeners', {'objectId': target})['listeners']
                    self.assertEqual(sorted(listener['type'] for listener in listeners if
                                            'function fgHandleTextInteraction(' in listener.get('handler', {}).get('description', '')),
                                     ['click', 'dblclick'])
                    page.evaluate('async () => { await fgSwitchView("timeline"); await fgSwitchView("text"); fgToggleTextOrder(); }')
                session.detach()

    def test_delegated_line_number_copies_one_anchor_without_expanding_source(self):
        for mode in ['single', 'sidecar']:
            with self.subTest(mode=mode):
                page = self.open_report(mode, width=1500)
                page.evaluate('''() => {
                    window.copiedAnchors = [];
                    Object.defineProperty(navigator.clipboard, 'writeText', {value: text => {
                        copiedAnchors.push(text); return Promise.resolve();
                    }});
                    const number = document.querySelector('#L3 > .line-number');
                    const label = document.createElement('b'); label.textContent = number.textContent;
                    number.replaceChildren(label);
                }''')
                page.locator('#L3 > .line-number b').click()
                page.wait_for_function('() => location.hash === "#L3" && copiedAnchors.length === 1')
                page.wait_for_timeout(250)
                self.assertEqual(page.evaluate('copiedAnchors.length'), 1)
                self.assertTrue(page.evaluate('copiedAnchors[0].endsWith("#L3")'))
                self.assertEqual(page.locator('#line_2_content').evaluate('node => node.style.display'), 'none')

    def test_delegated_source_button_opens_and_closes_exactly_once(self):
        for mode in ['single', 'sidecar']:
            with self.subTest(mode=mode):
                page = self.open_report(mode, width=1500)
                button = page.locator('#L3 .expand-btn')
                button.click()
                page.wait_for_function('() => document.getElementById("line_2_content").style.display === "block"')
                self.assertEqual(button.text_content(), '-')
                self.assertTrue(page.locator('#L3').evaluate('node => node.classList.contains("selected")'))
                button.click()
                page.wait_for_function('() => document.getElementById("line_2_content").style.display === "none"')
                self.assertEqual(button.text_content(), '+')

    def test_delegated_double_click_keeps_native_selection_and_cancels_source_toggle(self):
        for mode in ['single', 'sidecar']:
            with self.subTest(mode=mode):
                page = self.open_report(mode, width=1500)
                page.evaluate('''() => {
                    window.doubleCalls = 0; const original = handleDoubleClick;
                    handleDoubleClick = (...args) => { doubleCalls++; return original(...args); };
                }''')
                page.locator('#L3 .hl-time').first.dblclick()
                page.wait_for_timeout(250)
                self.assertEqual(page.evaluate('doubleCalls'), 1)
                self.assertTrue(page.evaluate('getSelection().toString().trim().length > 0'))
                self.assertEqual(page.locator('#line_2_content').evaluate('node => node.style.display'), 'none')

    def test_delegated_row_events_do_not_toggle_source_when_folding(self):
        for mode in ['single', 'sidecar']:
            with self.subTest(mode=mode):
                page = self.open_report(mode, width=1500)
                control = page.locator('#L2 .fold-icon')
                control.click()
                self.assertEqual(page.locator('#L3').evaluate('node => node.style.display'), 'none')
                self.assertTrue(page.locator('#L2').evaluate('node => node.classList.contains("folded")'))
                control.click()
                page.wait_for_timeout(250)
                self.assertNotEqual(page.locator('#L3').evaluate('node => node.style.display'), 'none')
                self.assertFalse(page.locator('#L2').evaluate('node => node.classList.contains("folded")'))
                self.assertEqual(page.locator('#line_1_content').evaluate('node => node.style.display'), 'none')

    def test_source_descendants_are_not_mistaken_for_trace_rows_after_reordering(self):
        for mode in ['single', 'sidecar']:
            with self.subTest(mode=mode):
                page = self.open_report(mode, width=1500)
                page.evaluate('''() => {
                    fgToggleTextOrder(); window.rowCalls = 0; const original = handleLineClick;
                    handleLineClick = (...args) => { rowCalls++; return original(...args); };
                }''')
                page.locator('#L3 .expand-btn').click()
                page.wait_for_function('() => document.getElementById("line_2_content").style.display === "block"')
                page.locator('#line_2_content .src-frame[open] .source-line').first.click()
                page.wait_for_timeout(250)
                self.assertEqual(page.evaluate('rowCalls'), 1)
                self.assertEqual(page.locator('#line_2_content').evaluate('node => node.style.display'), 'block')

    def test_legacy_inline_handlers_are_not_called_again_by_delegates(self):
        for mode in ['single', 'sidecar']:
            with self.subTest(mode=mode):
                page = self.open_report(mode, width=1500)
                page.evaluate('''() => {
                    const row = document.getElementById('L3');
                    row.setAttribute('onclick', "handleLineClick(event, 'line_2')");
                    row.setAttribute('ondblclick', "handleDoubleClick(event, 'line_2')");
                    row.querySelector('.line-number').setAttribute('onclick', "updateAnchor('L3', event)");
                    window.clickCalls = 0; window.doubleCalls = 0; window.anchorCalls = 0;
                    const click = handleLineClick, double = handleDoubleClick, anchor = updateAnchor;
                    handleLineClick = (...args) => { clickCalls++; return click(...args); };
                    handleDoubleClick = (...args) => { doubleCalls++; return double(...args); };
                    updateAnchor = (...args) => { anchorCalls++; return anchor(...args); };
                }''')
                page.locator('#L3 .expand-btn').click()
                page.wait_for_function('() => document.getElementById("line_2_content").style.display === "block"')
                self.assertEqual(page.evaluate('clickCalls'), 1)
                page.locator('#L3 > .line-number').click()
                self.assertEqual(page.evaluate('anchorCalls'), 1)
                page.locator('#L3 .hl-time').first.dblclick()
                self.assertEqual(page.evaluate('doubleCalls'), 1)

    def test_keyboard_source_toggle_remains_available_without_inline_handlers(self):
        for mode in ['single', 'sidecar']:
            with self.subTest(mode=mode):
                page = self.open_report(mode, width=1500)
                page.locator('#L3').focus()
                page.keyboard.press('Enter')
                page.wait_for_function('() => document.getElementById("line_2_content").style.display === "block"')
                page.keyboard.press('Enter')
                page.wait_for_function('() => document.getElementById("line_2_content").style.display === "none"')

    def test_separate_touch_source_taps_use_the_same_delegated_route_in_both_themes(self):
        for mode in ['single', 'sidecar']:
            for theme in ['light', 'dark']:
                with self.subTest(mode=mode, theme=theme):
                    page = self.open_report(mode, theme=theme)
                    target = page.locator('#L3 .hl-time').first
                    target.tap()
                    page.wait_for_function('() => document.getElementById("line_2_content").style.display === "block"')
                    page.wait_for_timeout(400)
                    target.tap()
                    page.wait_for_function('() => document.getElementById("line_2_content").style.display === "none"')

    def test_native_source_links_keep_their_target_without_toggling_rows(self):
        for mode in ['single', 'sidecar']:
            with self.subTest(mode=mode):
                page = self.open_report(mode, width=1500)
                page.context.route('https://**/*', lambda route: route.fulfill(status=200, body='source fixture'))
                link = page.locator('#L3 .func-name-link').first
                expected = link.get_attribute('href')
                with page.expect_popup() as popup:
                    link.click()
                popup.value.wait_for_load_state()
                self.assertEqual(popup.value.url, expected)
                popup.value.close()
                page.wait_for_timeout(250)
                self.assertEqual(page.locator('#line_2_content').evaluate('node => node.style.display'), 'none')

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
