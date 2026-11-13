"""Timeline resizing preserves painted frames, canvas geometry and trace evidence."""

from itertools import product
from pathlib import Path
import threading
import unittest

import helpers
from fglib.viewer import create_server


class TimelineResizeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise unittest.SkipTest(str(error))
        cls.kernel = helpers.FakeKernel()
        cls.addClassCleanup(cls.kernel.close)
        for mode in ['single', 'sidecar']:
            cls.kernel.run(helpers.fixture('pi4_sched'), '--timeline', '--enable-fold', '--filter',
                           '--report-mode', mode, out='single.html' if mode == 'single' else 'sidecar')
        cls.server = create_server(Path(cls.kernel.dir, 'sidecar'))
        cls.addClassCleanup(cls.server.server_close)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.thread.join, 5)
        cls.addClassCleanup(cls.server.shutdown)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)

    def open_report(self, mode='single', ratio=1, theme='light'):
        context = self.browser.new_context(viewport={'width': 1500, 'height': 900}, device_scale_factor=ratio)
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(10000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(lambda: self.assertEqual(errors, []))
        page.goto(Path(self.kernel.dir, 'single.html').as_uri() if mode == 'single' else self.server.url)
        page.evaluate('''async theme => {
            document.documentElement.setAttribute('data-theme', theme);
            await fgSwitchView('timeline');
            fgView('timeline').probe().select(0);
            window.resizeState = () => {
                const model = fgView('timeline').probe();
                const columns = [model.sName,model.sStart,model.sEnd,model.sDepth,model.sParent,model.sTask];
                return JSON.stringify([fgData.read('tlData'),model.N,model.NA,model.S,model.SYN,
                    columns.map(column => Array.from(column)),model.sel(),model.view()]);
            };
            window.resizePixels = () => ['.tl-canvas','.tl-head-canvas'].map(selector => {
                const canvas = document.querySelector(selector), box = canvas.getBoundingClientRect();
                return {width:canvas.width,height:canvas.height,cssWidth:box.width,cssHeight:box.height,
                    alpha:canvas.getContext('2d').getImageData(0,0,1,1).data[3]};
            });
            window.settleResize = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            await settleResize();
        }''', theme)
        return page

    def drag_frame(self, page, target):
        return page.evaluate('''async target => {
            const handle = document.querySelector('.tl-resizer');
            const during = await new Promise(resolve => requestAnimationFrame(() => {
                handle.dispatchEvent(new PointerEvent('pointermove',{pointerId:1,clientY:target,bubbles:true}));
                requestAnimationFrame(() => resolve(resizePixels()));
            }));
            await settleResize();
            return {during,after:resizePixels()};
        }''', target)

    def assert_painted(self, rows, ratio):
        for canvas in rows:
            self.assertEqual(canvas['alpha'], 255, 'A resize frame cleared the canvas before its redraw')
            self.assertGreater(canvas['height'], 0)
            self.assertAlmostEqual(canvas['width'], canvas['cssWidth'] * ratio, delta=1)

    def test_drag_frames_stay_painted_in_both_delivery_modes_themes_and_pixel_ratios(self):
        for mode, ratio, theme in product(['single', 'sidecar'], [1, 1.25, 2], ['light', 'dark']):
            with self.subTest(mode=mode, ratio=ratio, theme=theme):
                page = self.open_report(mode, ratio, theme)
                original = page.evaluate('resizeState()')
                handle = page.locator('.tl-resizer').bounding_box()
                page.mouse.move(handle['x'] + handle['width'] / 2, handle['y'] + handle['height'] / 2)
                page.mouse.down()
                try:
                    for offset in [-100, 80, -40]:
                        facts = self.drag_frame(page, handle['y'] + offset)
                        self.assert_painted(facts['during'], ratio)
                        self.assert_painted(facts['after'], ratio)
                        for canvas in facts['after']:
                            self.assertAlmostEqual(canvas['height'], canvas['cssHeight'] * ratio, delta=1)
                        self.assertEqual(page.evaluate('resizeState()'), original)
                finally:
                    page.mouse.up()
                page.evaluate('settleResize()')
                self.assert_painted(page.evaluate('resizePixels()'), ratio)

    def test_repeated_identical_resizes_do_not_reset_either_backing_store(self):
        page = self.open_report()
        facts = page.evaluate('''async () => {
            const counts = {width:0,height:0};
            for (const canvas of document.querySelectorAll('.tl-canvas,.tl-head-canvas')) {
                for (const key of ['width','height']) {
                    const native = Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype,key);
                    Object.defineProperty(canvas,key,{configurable:true,get() { return native.get.call(this); },
                        set(value) { counts[key]++; native.set.call(this,value); }});
                }
            }
            for (let index = 0; index < 12; index++) fgView('timeline').resize();
            await settleResize();
            return counts;
        }''')
        self.assertEqual(facts, dict(width=0, height=0))

    def test_initial_matching_backing_store_still_initializes_font_metrics(self):
        source = Path(helpers.ROOT, 'assets/timeline/render.js').read_text()
        setup = source[:source.index('const CTX_HUE')]
        sizing = source[source.index('function syncCanvasSizes()'):source.index('const visibleTracks')]
        context = self.browser.new_context()
        self.addCleanup(context.close)
        page = context.new_page()
        facts = page.evaluate('''source => {
            const main = document.createElement('canvas'), head = document.createElement('canvas');
            return new Function('elCv','elHead',source + `
                W = elCv.width; H = elCv.height;
                const HEAD_H = elHead.height;
                let measurements = 0, writes = 0;
                const measure = ctx.measureText.bind(ctx);
                ctx.measureText = text => { measurements++; return measure(text); };
                for (const canvas of [elCv,elHead]) for (const key of ['width','height']) {
                    const native = Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype,key);
                    Object.defineProperty(canvas,key,{get() { return native.get.call(this); },
                        set(value) { writes++; native.set.call(this,value); }});
                }
                syncCanvasSizes(); syncCanvasSizes();
                return {measurements,writes,labelWidth:charW,
                    expected:measure('abcdefghijklmnopqrstuvwxyz_0123').width / 31};
            `)(main,head);
        }''', setup + sizing)
        self.assertEqual(facts['writes'], 0)
        self.assertEqual(facts['measurements'], 1)
        self.assertGreater(facts['labelWidth'], 0)
        self.assertEqual(facts['labelWidth'], facts['expected'])

    def test_repeated_drag_completion_cancellation_and_lost_capture_remove_all_listeners(self):
        page = self.open_report()
        page.evaluate('''() => {
            const handle = document.querySelector('.tl-resizer');
            const add = handle.addEventListener.bind(handle), remove = handle.removeEventListener.bind(handle);
            window.resizeListeners = new Map(['pointermove','pointerup','pointercancel','lostpointercapture'].map(kind => [kind,new Map()]));
            handle.addEventListener = (kind,callback,options) => {
                const listeners = resizeListeners.get(kind);
                if (!listeners) { add(kind,callback,options); return; }
                if (listeners.has(callback)) return;
                const wrapped = event => {
                    if (options?.once) listeners.delete(callback);
                    callback.call(handle,event);
                };
                listeners.set(callback,wrapped);
                add(kind,wrapped,options);
            };
            handle.removeEventListener = (kind,callback,options) => {
                const listeners = resizeListeners.get(kind), wrapped = listeners?.get(callback);
                remove(kind,wrapped || callback,options);
                listeners?.delete(callback);
            };
        }''')
        for ending in ['up', 'up', 'cancel', 'lost', 'up', 'cancel']:
            with self.subTest(ending=ending):
                handle = page.locator('.tl-resizer').bounding_box()
                page.mouse.move(handle['x'] + handle['width'] / 2, handle['y'] + handle['height'] / 2)
                page.mouse.down()
                self.drag_frame(page, handle['y'] - 15)
                if ending == 'cancel':
                    page.evaluate('document.querySelector(".tl-resizer").dispatchEvent(new PointerEvent("pointercancel",{pointerId:1}))')
                elif ending == 'lost':
                    page.evaluate('document.querySelector(".tl-resizer").releasePointerCapture(1)')
                page.mouse.up()
                self.assertEqual(page.evaluate('Array.from(resizeListeners.values(),listeners => listeners.size)'), [0, 0, 0, 0])
                self.assertFalse(page.evaluate('document.body.classList.contains("tl-resizing")'))

    def test_pending_drag_cleanup_double_click_and_fullscreen_keep_painted_data(self):
        page = self.open_report('sidecar', 2)
        original = page.evaluate('resizeState()')
        page.get_by_role('button', name='Full screen', exact=True).click()
        page.wait_for_function('document.querySelector("#timelineView").classList.contains("tl-fullscreen")')
        page.evaluate('settleResize()')
        handle = page.locator('.tl-resizer').bounding_box()
        page.mouse.move(handle['x'] + handle['width'] / 2, handle['y'] + handle['height'] / 2)
        page.mouse.down()
        page.evaluate('''() => {
            const handle = document.querySelector('.tl-resizer'), target = handle.getBoundingClientRect().top - 100;
            handle.dispatchEvent(new PointerEvent('pointermove',{pointerId:1,clientY:target}));
            handle.dispatchEvent(new PointerEvent('pointercancel',{pointerId:1}));
        }''')
        page.mouse.up()
        page.evaluate('settleResize()')
        self.assert_painted(page.evaluate('resizePixels()'), 2)
        page.locator('.tl-resizer').dblclick()
        page.evaluate('settleResize()')
        self.assertIsNone(page.evaluate('localStorage.getItem("fgTl.detailFrac")'))
        page.get_by_role('button', name='Exit full screen', exact=True).click()
        page.evaluate('settleResize()')
        self.assert_painted(page.evaluate('resizePixels()'), 2)
        self.assertEqual(page.evaluate('resizeState()'), original)
