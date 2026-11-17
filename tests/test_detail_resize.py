"""Keyboard resizing shares the pointer split without changing recorded data."""

from itertools import product
from pathlib import Path
import threading
import unittest

import helpers
from fglib.viewer import create_server
from test_uftrace_import import capture, lifecycle_event, stream

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class DetailResizeBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel()
        cls.addClassCleanup(cls.kernel.close)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)
        mixed = Path(cls.kernel.dir, 'mixed.jsonl')
        mixed.write_text(stream(capture()))
        events = Path(cls.kernel.dir, 'events.jsonl')
        events.write_text(stream([lifecycle_event('exec', 0), lifecycle_event('exit', 1000)]))
        cls.urls = {}
        for kind, mode in product(('kernel', 'mixed', 'events'), ('single', 'sidecar')):
            trace = {'kernel': helpers.fixture('pi4_sched'), 'mixed': mixed, 'events': events}[kind]
            destination = kind + '-' + mode + ('.html' if mode == 'single' else '')
            cls.kernel.run(str(trace), '--no-config', '--timeline', '--enable-fold', '--filter',
                           '--report-mode', mode, out=destination)
            report = Path(cls.kernel.dir, destination)
            if mode == 'sidecar':
                server = create_server(report)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                cls.addClassCleanup(thread.join, 5)
                cls.addClassCleanup(server.server_close)
                cls.addClassCleanup(server.shutdown)
                cls.urls[kind, mode] = server.url
            else:
                cls.urls[kind, mode] = report.as_uri()

    def setUp(self):
        self.context = None
        self.errors = []

    def tearDown(self):
        if self.context:
            self.context.close()
        self.assertEqual(self.errors, [])

    def open(self, kind='mixed', mode='single', theme='light', width=1440, height=960):
        if self.context:
            self.context.close()
        self.context = self.browser.new_context(viewport={'width': width, 'height': height},
                                                has_touch=width < 600, is_mobile=width < 600)
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.urls[kind, mode])
        self.page.evaluate("async () => await fgSwitchView('timeline')")
        self.page.evaluate('theme => document.documentElement.dataset.theme = theme', theme)
        if kind == 'events':
            helpers.click_timeline_tool(self.page, '[data-act=an-tasks]')
        else:
            self.page.evaluate("fgView('timeline').probe().select(1)")
        self.separator = self.page.get_by_role('separator', name='Resize details')

    def snapshot(self):
        return self.page.evaluate('''() => {
            const model = fgView('timeline').probe();
            return {data:JSON.stringify(fgData.read('tlData')), range:model.view(),
                selection:model.sel(), starts:Array.from(model.sStart), ends:Array.from(model.sEnd),
                filter:fgFilterState.text, view:fgCurrentView()};
        }''')

    def height(self):
        return self.page.locator('.tl-detail').bounding_box()['height']

    def assert_readout(self):
        self.assertEqual(self.separator.get_attribute('aria-orientation'), 'horizontal')
        self.assertEqual(self.separator.get_attribute('aria-controls'), 'tlDetail')
        minimum = int(self.separator.get_attribute('aria-valuemin'))
        maximum = int(self.separator.get_attribute('aria-valuemax'))
        current = int(self.separator.get_attribute('aria-valuenow'))
        self.assertEqual(minimum, 60)
        self.assertLessEqual(minimum, current)
        self.assertLessEqual(current, maximum)
        self.assertAlmostEqual(current, self.height(), delta=1)
        self.assertEqual(self.separator.get_attribute('aria-valuetext'), f'Details {current} pixels')
        self.assertGreaterEqual(self.page.locator('.tl-stage').bounding_box()['height'], 160)

    def test_separator_is_a_named_tab_stop_only_with_visible_details(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open(mode=mode)
                self.assertEqual(self.separator.count(), 1)
                before = self.snapshot()
                self.assert_readout()
                self.page.locator('.tl-canvas').focus()
                self.page.keyboard.press('Tab')
                self.assertTrue(self.separator.evaluate('element => element === document.activeElement'))
                self.assertEqual(self.separator.evaluate("element => getComputedStyle(element).outlineWidth"), '2px')
                self.separator.press('?')
                self.assertTrue(self.page.locator('.tl-help').is_visible())
                self.assertIn('16px', self.page.locator('.tl-help').inner_text())
                self.page.keyboard.press('Escape')
                self.assertTrue(self.separator.evaluate('element => element === document.activeElement'))
                self.assertEqual(self.snapshot(), before)
                self.page.keyboard.press('Escape')
                self.assertEqual(self.page.get_by_role('separator').count(), 0)

    def test_keys_resize_only_details_and_reset_the_saved_split(self):
        for kind, mode, theme in product(('kernel', 'mixed', 'events'), ('single', 'sidecar'), ('light', 'dark')):
            with self.subTest(kind=kind, mode=mode, theme=theme):
                self.open(kind=kind, mode=mode, theme=theme)
                before = self.snapshot()
                initial = self.height()
                self.separator.focus()
                scroll = self.page.evaluate('[scrollX, scrollY]')
                for key, expected in [('ArrowUp', initial + 16), ('ArrowDown', initial), ('Home', 60)]:
                    self.separator.press(key)
                    self.assertAlmostEqual(self.height(), expected, delta=1)
                    self.assert_readout()
                self.separator.press('End')
                self.assertAlmostEqual(self.height(), int(self.separator.get_attribute('aria-valuemax')), delta=1)
                for key in ('ArrowLeft', 'ArrowRight'):
                    self.separator.press(key)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.page.evaluate('[scrollX, scrollY]'), scroll)
                self.assertTrue(self.separator.evaluate('element => element === document.activeElement'))
                self.assertIsNotNone(self.page.evaluate("localStorage.getItem('fgTl.detailFrac')"))
                self.separator.press('Enter')
                self.assertAlmostEqual(self.height(), initial, delta=1)
                self.assertIsNone(self.page.evaluate("localStorage.getItem('fgTl.detailFrac')"))
                self.assertEqual(self.snapshot(), before)
                self.assert_readout()

    def test_boundary_keys_do_not_replace_a_clamped_preference(self):
        self.open()
        self.page.evaluate("localStorage.setItem('fgTl.detailFrac', '0.7')")
        self.page.set_viewport_size({'width': 568, 'height': 320})
        self.page.evaluate("fgView('timeline').resize()")
        self.assert_readout()
        self.assertEqual(self.height(), 60)
        before = self.snapshot()
        for key in ('ArrowUp', 'ArrowDown', 'Home', 'End'):
            self.separator.press(key)
            self.assertEqual(self.page.evaluate("localStorage.getItem('fgTl.detailFrac')"), '0.7')
        self.assertEqual(self.snapshot(), before)
        self.page.set_viewport_size({'width': 1440, 'height': 960})
        self.page.evaluate("fgView('timeline').resize()")
        self.assert_readout()
        self.assertGreater(self.height(), 60)

    def test_modified_composing_and_consumed_keys_keep_their_native_owner(self):
        self.open()
        before = self.snapshot()
        initial = self.height()
        results = self.separator.evaluate('''element => {
            const result = [];
            for (const key of ['ArrowUp','ArrowDown','Home','End','Enter']) {
                for (const settings of [{ctrlKey:true},{altKey:true},{metaKey:true},{isComposing:true},{keyCode:229},{consumed:true}]) {
                    const event = new KeyboardEvent('keydown', {key,bubbles:true,cancelable:true,...settings});
                    if (settings.consumed) event.preventDefault();
                    element.dispatchEvent(event);
                    result.push(event.defaultPrevented === !!settings.consumed);
                }
            }
            return result;
        }''')
        self.assertTrue(all(results))
        self.assertEqual(self.height(), initial)
        self.assertIsNone(self.page.evaluate("localStorage.getItem('fgTl.detailFrac')"))
        self.assertEqual(self.snapshot(), before)

    def test_resizing_still_works_when_preference_storage_is_unavailable(self):
        self.open()
        self.page.evaluate('''() => {
            localStorage.setItem('fgTl.detailFrac', '0.4');
            fgView('timeline').resize();
            Storage.prototype.setItem = Storage.prototype.removeItem = () => { throw new Error('Storage unavailable'); };
        }''')
        before = self.snapshot()
        initial = self.height()
        self.separator.press('ArrowUp')
        self.assertAlmostEqual(self.height(), initial + 16, delta=1)
        bounds = self.separator.bounding_box()
        center = bounds['x'] + bounds['width'] / 2
        self.page.mouse.move(center, bounds['y'] + 4)
        self.page.mouse.down()
        self.page.mouse.move(center, bounds['y'] - 20, steps=3)
        self.page.mouse.up()
        self.assertAlmostEqual(self.height(), initial + 40, delta=1)
        self.separator.press('Enter')
        self.assertAlmostEqual(self.height(), round(960 * .32), delta=1)
        self.assertEqual(self.page.evaluate("localStorage.getItem('fgTl.detailFrac')"), '0.4')
        self.assertEqual(self.snapshot(), before)
        self.assert_readout()

    def test_pointer_keyboard_and_reload_share_the_split_without_loading_text(self):
        for mode, width in product(('single', 'sidecar'), (390, 1440)):
            with self.subTest(mode=mode, width=width):
                self.open(mode=mode, width=width, height=960, theme='dark' if width == 390 else 'light')
                before = self.snapshot()
                requests = []
                self.page.on('request', lambda request: requests.append(request.url))
                text_ready = self.page.evaluate('fgData.ready("fgText")')
                self.separator.press('ArrowUp')
                current = self.height()
                bounds = self.separator.bounding_box()
                center = bounds['x'] + bounds['width'] / 2
                self.page.mouse.move(center, bounds['y'] + 4)
                self.page.mouse.down()
                self.page.mouse.move(center, bounds['y'] - 36, steps=5)
                self.page.mouse.up()
                self.assertAlmostEqual(self.height(), current + 40, delta=1)
                self.assert_readout()
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.page.evaluate('fgData.ready("fgText")'), text_ready)
                self.assertFalse(any(url.endswith(('text.html', 'text-index.json', 'compare.json')) for url in requests))
                self.assertTrue(self.page.evaluate('''() => {
                    const canvas = document.querySelector('.tl-canvas');
                    return canvas.width > 0 && canvas.height > 0 && canvas.getContext('2d')
                        .getImageData(0,0,canvas.width,canvas.height).data.some((value,index) => index % 4 === 3 && value > 0);
                }'''))
                stored = self.page.evaluate("localStorage.getItem('fgTl.detailFrac')")
                height = self.height()
                self.page.reload()
                self.page.evaluate("async () => await fgSwitchView('timeline')")
                self.page.evaluate("fgView('timeline').probe().select(1)")
                self.assertEqual(self.page.evaluate("localStorage.getItem('fgTl.detailFrac')"), stored)
                self.assertAlmostEqual(self.height(), height, delta=1)
                self.assert_readout()
                self.assertEqual(self.snapshot(), before)
                self.separator.dblclick()
                self.assertIsNone(self.page.evaluate("localStorage.getItem('fgTl.detailFrac')"))
                self.assert_readout()

    def test_view_orientation_fullscreen_and_expansion_keep_readouts_current(self):
        for mode, native in product(('single', 'sidecar'), (True, False)):
            with self.subTest(mode=mode, native=native):
                self.open(mode=mode)
                before = self.snapshot()
                if not native:
                    self.page.evaluate('Element.prototype.requestFullscreen = undefined')
                self.page.locator('[data-act=max]').click()
                self.page.wait_for_function("document.getElementById('timelineView').classList.contains('tl-fullscreen')")
                self.assert_readout()
                self.separator.press('Home')
                self.assertEqual(self.height(), 60)
                self.page.locator('[data-detail-expand]').click()
                self.assertEqual(self.page.get_by_role('separator').count(), 0)
                self.page.locator('[data-detail-expand]').click()
                self.assert_readout()
                self.assertEqual(self.height(), 60)
                self.page.locator('[data-act=max]').click()
                self.page.wait_for_function("!document.getElementById('timelineView').classList.contains('tl-fullscreen')")
                for width, height in ((844, 390), (1440, 960)):
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.page.evaluate("fgView('timeline').resize()")
                    self.assert_readout()
                self.page.evaluate("async () => { await fgSwitchView('text'); await fgSwitchView('timeline'); }")
                self.assert_readout()
                self.assertEqual(self.snapshot(), before)

    def test_all_storage_access_can_fail_without_disabling_resizing(self):
        self.open()
        initial = self.height()
        self.page.evaluate('''() => {
            Storage.prototype.getItem = Storage.prototype.setItem = Storage.prototype.removeItem =
                () => { throw new Error('Storage unavailable'); };
        }''')
        before = self.snapshot()
        self.separator.press('ArrowUp')
        self.assertAlmostEqual(self.height(), initial + 16, delta=1)
        self.separator.press('ArrowDown')
        self.assertAlmostEqual(self.height(), initial, delta=1)
        self.separator.press('Home')
        self.assertEqual(self.height(), 60)
        self.separator.press('Enter')
        self.assertAlmostEqual(self.height(), initial, delta=1)
        self.assertEqual(self.snapshot(), before)
        self.assert_readout()


if __name__ == '__main__':
    unittest.main()
