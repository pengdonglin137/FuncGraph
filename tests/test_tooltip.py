"""Timeline hover previews fit the viewport and reuse only valid geometry."""
from pathlib import Path
import unittest

import helpers
import test_browser as browser_tests


@unittest.skipIf(browser_tests.sync_playwright is None, 'playwright not installed')
class TimelineTooltipBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.k = helpers.FakeKernel()
        cls.k.run(helpers.fixture('pi4_sched'), '--no-config', '--timeline', '--enable-fold',
                  '--filter', '--func-links', '--base-url', 'https://example.org/src', out='report.html')
        cls.url = (Path(cls.k.dir) / 'report.html').as_uri()
        cls.pw = browser_tests.sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch(ignore_default_args=['--hide-scrollbars'])
        except Exception as error:
            cls.pw.stop()
            cls.k.close()
            raise unittest.SkipTest(str(error))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.k.close()

    setUp = browser_tests.BrowserSmokeTest.setUp
    tearDown = browser_tests.BrowserSmokeTest.tearDown
    ev = browser_tests.BrowserSmokeTest.ev
    open_range_data = browser_tests.BrowserSmokeTest.open_range_data
    open_timeline = browser_tests.BrowserSmokeTest.open_timeline

    def settle(self):
        self.ev('async () => { for (let frame = 0; frame < 3; frame++) await new Promise(requestAnimationFrame); }')

    def move_to(self, index=0):
        point = self.ev('index => fgView("timeline").probe().xyOf(index)', index)
        self.page.mouse.move(*point)
        self.page.locator('.tl-tip').wait_for(state='visible')
        self.settle()
        return point

    def assert_fits(self):
        bounds = self.ev('''() => {
            const box = document.querySelector('.tl-tip').getBoundingClientRect(), viewport = visualViewport;
            return {left:box.left, top:box.top, right:box.right, bottom:box.bottom,
                viewportLeft:viewport?.offsetLeft || 0, viewportTop:viewport?.offsetTop || 0,
                width:viewport?.width || innerWidth, height:viewport?.height || innerHeight};
        }''')
        self.assertGreaterEqual(bounds['left'], bounds['viewportLeft'] + 3.5)
        self.assertGreaterEqual(bounds['top'], bounds['viewportTop'] + 3.5)
        self.assertLessEqual(bounds['right'], bounds['viewportLeft'] + bounds['width'] - 3.5)
        self.assertLessEqual(bounds['bottom'], bounds['viewportTop'] + bounds['height'] - 3.5)

    def instrument_geometry(self):
        self.ev('''() => {
            const tooltip = document.querySelector('.tl-tip');
            const original = tooltip.getBoundingClientRect.bind(tooltip);
            window.tooltipReads = 0;
            tooltip.getBoundingClientRect = () => { tooltipReads++; return original(); };
        }''')

    def long_call(self):
        name = 'long_wrapper_' + 'nested_namespace_' * 30 + 'operation'
        caller = 'outer_' + 'caller_namespace_' * 30 + 'invoke+0x12/0x40'
        arguments = 'path="' + 'segment/' * 200 + 'payload", count=17'
        flags = (1 << 15) | 4 | 8 | (7 << 4) | (1 << 7) | (2 << 11)
        self.open_range_data([name], callers=[caller], durations=[1000000],
                             columns={'args': [2], 'flags': [flags]},
                             extra={'strings': [name, caller, arguments], 'preempt_base': 1})
        return name, caller, arguments

    def test_long_previews_fit_without_silently_losing_full_details(self):
        for width, height in ((1500, 900), (390, 400), (844, 390), (320, 480)):
            with self.subTest(viewport=(width, height)):
                self.ev('fgSwitchView("text")')
                self.page.reload()
                self.page.set_viewport_size(dict(width=width, height=height))
                name, caller, arguments = self.long_call()
                original = self.ev('JSON.stringify(fgData.read("tlData"))')
                point = self.move_to()
                self.assert_fits()
                self.assertEqual(self.page.locator('.tl-tip .t-name').text_content(), name)
                clipped = self.page.locator('.tl-tip-more').is_visible()
                self.assertEqual(clipped, height < 900)
                if clipped:
                    self.assertEqual(self.page.locator('.tl-tip-more').inner_text(), 'Select for full details')
                self.page.mouse.click(*point)
                self.assertEqual(self.ev('fgView("timeline").probe().sel()'), 0)
                self.assertEqual(self.page.locator('.tl-detail-title').text_content(), name)
                self.assertIn(caller, self.page.locator('.tl-call-facts').inner_text())
                self.assertIn(arguments, self.page.locator('.tl-call-facts').inner_text())
                self.assertEqual(self.ev('JSON.stringify(fgData.read("tlData"))'), original)

    def test_repeated_hover_after_expanding_text_reuses_size_and_updates_position(self):
        self.open_range_data(['worker'], durations=[1000000])
        self.ev('fgSwitchView("text")')
        self.page.locator('.line-container.expandable').first.locator('.line-content').click()
        self.page.locator('.expanded-content:visible').first.wait_for(state='visible')
        self.open_timeline()
        point = self.move_to()
        original = self.page.locator('.tl-tip').inner_text()
        first_transform = self.page.locator('.tl-tip').evaluate('node => node.style.transform')
        self.instrument_geometry()
        for index in range(12):
            self.page.mouse.move(point[0] + index + 1, point[1])
            self.settle()
        self.assertEqual(self.ev('tooltipReads'), 0)
        self.assertEqual(self.page.locator('.tl-tip').inner_text(), original)
        self.assertNotEqual(self.page.locator('.tl-tip').evaluate('node => node.style.transform'), first_transform)
        self.assertFalse(self.page.locator('.tl-tip-more').is_visible())
        self.ev('document.querySelector(".tl-tip").style.fontSize = "17px"')
        self.settle()
        self.assertGreater(self.ev('tooltipReads'), 0)
        self.assert_fits()
        self.ev('tooltipReads = 0')
        self.page.mouse.move(point[0] + 16, point[1])
        self.settle()
        self.assertEqual(self.ev('tooltipReads'), 0)

    def test_target_and_overhead_changes_cannot_reuse_stale_content(self):
        self.open_range_data(['first', 'second_with_a_longer_name'], durations=[1000, 2000], starts=[0, 3000],
                             extra={'overhead': {'child': 10, 'self': 5, 'source': 'option'}})
        self.move_to(0)
        self.instrument_geometry()
        self.move_to(1)
        self.assertGreater(self.ev('tooltipReads'), 0)
        self.assertEqual(self.page.locator('.tl-tip .t-name').inner_text(), 'second_with_a_longer_name')
        before = self.page.locator('.tl-tip').inner_text()
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        self.move_to(1)
        self.assertNotEqual(self.page.locator('.tl-tip').inner_text(), before)
        self.assert_fits()

    def test_viewport_resize_and_visual_viewport_offsets_invalidate_placement(self):
        self.long_call()
        self.move_to()
        self.page.set_viewport_size(dict(width=390, height=600))
        self.page.locator('.tl-tip').wait_for(state='hidden')
        self.move_to()
        self.assert_fits()
        self.ev('''() => {
            for (const [key, value] of Object.entries({offsetLeft:40, offsetTop:20, width:300, height:360}))
                Object.defineProperty(visualViewport, key, {configurable:true, value});
            visualViewport.dispatchEvent(new Event('resize'));
        }''')
        self.page.locator('.tl-tip').wait_for(state='hidden')
        self.move_to()
        self.assert_fits()
        self.ev('visualViewport.dispatchEvent(new Event("scroll"))')
        self.page.locator('.tl-tip').wait_for(state='hidden')

    def test_no_resize_observer_still_bounds_and_invalidates_on_reentry(self):
        self.ev('window.ResizeObserver = undefined')
        self.page.set_viewport_size(dict(width=390, height=480))
        self.long_call()
        point = self.move_to()
        self.assert_fits()
        self.instrument_geometry()
        self.page.mouse.move(point[0] + 2, point[1])
        self.settle()
        self.assertEqual(self.ev('tooltipReads'), 0)
        self.page.mouse.move(0, 0)
        self.page.locator('.tl-tip').wait_for(state='hidden')
        self.move_to()
        self.assertGreater(self.ev('tooltipReads'), 0)
        self.assert_fits()

    def test_fullscreen_and_its_fallback_keep_long_previews_bounded(self):
        for native in (False, True):
            with self.subTest(native=native):
                self.ev('fgSwitchView("text")')
                self.page.reload()
                self.page.set_viewport_size(dict(width=390, height=480))
                name = self.long_call()[0]
                self.assertEqual(self.ev('fgView("timeline").probe().nameOf(0)'), name)
                if not native:
                    self.ev('Element.prototype.requestFullscreen = undefined')
                self.page.locator('[data-act=max]').click()
                self.page.wait_for_function('''() =>
                    document.getElementById('timelineView').classList.contains('tl-fullscreen') &&
                    !document.querySelector('[data-act=max]').disabled''')
                self.settle()
                self.move_to()
                self.assert_fits()
                self.assertTrue(self.page.locator('.tl-tip-more').is_visible())
                self.page.locator('[data-act=max]').click()
                self.page.wait_for_function('''() =>
                    !document.getElementById('timelineView').classList.contains('tl-fullscreen') &&
                    !document.fullscreenElement''')

    def prepare_async_source(self):
        target = self.ev('''() => {
            const data = fgData.read('tlData');
            const index = data.cols.caller.findIndex(caller => data.sources.callers[caller]?.length);
            const frame = data.sources.callers[data.cols.caller[index]].slice(-1)[0];
            delete data.sources;
            window.tipSourceReady = false;
            fgData.sourceReady = () => tipSourceReady;
            fgData.loadSource = (source, options) => new Promise((resolve, reject) => {
                window.tipSourceSignal = options.signal;
                window.resolveTipSource = () => { tipSourceReady = true; resolve(); };
                window.rejectTipSource = reject;
            });
            return {index, source:frame[1] + ':' + frame[2]};
        }''')
        self.open_timeline()
        point = self.ev('index => { const probe = fgView("timeline").probe(); probe.zoomTo(probe.sStart[index], probe.sEnd[index], index); return probe.xyOf(index); }', target['index'])
        self.page.mouse.move(*point)
        self.page.wait_for_function('typeof resolveTipSource === "function"')
        self.settle()
        return target

    def test_async_source_position_refreshes_size_and_preserves_exact_location(self):
        target = self.prepare_async_source()
        self.assertEqual(self.page.locator('.tl-tip .t-k', has_text='source').count(), 0)
        self.instrument_geometry()
        self.ev('resolveTipSource()')
        self.page.wait_for_function('source => document.querySelector(".tl-tip").textContent.includes(source)', arg=target['source'])
        self.settle()
        self.assertGreater(self.ev('tooltipReads'), 0)
        self.assertIn(target['source'], self.page.locator('.tl-tip').inner_text())
        self.assert_fits()

    def test_late_source_completion_does_not_revive_a_hidden_tooltip(self):
        self.prepare_async_source()
        self.page.mouse.move(0, 0)
        self.page.locator('.tl-tip').wait_for(state='hidden')
        self.assertTrue(self.ev('tipSourceSignal.aborted'))
        self.ev('resolveTipSource()')
        self.settle()
        self.assertFalse(self.page.locator('.tl-tip').is_visible())

    def test_async_source_failure_stays_bounded_and_escapes_its_message(self):
        self.page.set_viewport_size(dict(width=390, height=480))
        self.prepare_async_source()
        message = 'missing/' * 150 + '<img src=x onerror="window.tooltipExecuted=1">'
        self.ev('message => rejectTipSource(new Error(message))', message)
        self.page.wait_for_function('document.querySelector(".tl-tip").textContent.includes("Source not loaded:")')
        self.settle()
        self.assertIn(message, self.page.locator('.tl-tip-body').text_content())
        self.assertEqual(self.page.locator('.tl-tip img').count(), 0)
        self.assertIsNone(self.ev('window.tooltipExecuted'))
        self.assert_fits()
        self.assertTrue(self.page.locator('.tl-tip-more').is_visible())
