"""Timeline hover follows the latest pointer once per paint without losing evidence."""

from itertools import product
from pathlib import Path
import threading
import unittest
from urllib.parse import urljoin

import helpers
from fglib.viewer import create_server


class TimelineHoverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise unittest.SkipTest(str(error))
        cls.kernel = helpers.FakeKernel(inline_source=True)
        cls.addClassCleanup(cls.kernel.close)
        for mode in ('single', 'sidecar'):
            cls.kernel.run(helpers.fixture('pi4_sched'), '--timeline', '--enable-fold', '--filter',
                           '--func-links', '--report-mode', mode, out='index.html' if mode == 'single' else 'sidecar')
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

    def open_report(self, mode='single', theme='light', ratio=1):
        context = self.browser.new_context(viewport={'width': 1500, 'height': 900}, device_scale_factor=ratio)
        self.addCleanup(context.close)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(lambda: self.assertEqual(errors, []))
        page.goto(urljoin(self.server.url, 'index.html' if mode == 'single' else 'sidecar/index.html'))
        page.evaluate('''theme => {
            lsSet('view', 'timeline');
            localStorage.setItem('theme', theme);
        }''', theme)
        page.reload(wait_until='load')
        page.evaluate('async () => { await fgSwitchView("timeline"); }')
        if mode == 'sidecar':
            self.assertTrue(page.evaluate('fgTextDeferred()'))
            self.assertEqual(page.locator('#content .line-container').count(), 0)
        page.evaluate('''async () => {
            const model = fgView('timeline').probe();
            model.select(0);
            model.zoomTo(model.sStart[0], model.sEnd[0], 0);
            window.hoverFrame = () => new Promise(resolve => requestAnimationFrame(resolve));
            const canvas = document.querySelector('.tl-canvas');
            window.hoverMove = point => canvas.dispatchEvent(
                new MouseEvent('mousemove', {bubbles:true, clientX:point[0], clientY:point[1]}));
            await hoverFrame();
            await hoverFrame();
        }''')
        return page

    def test_pointer_bursts_do_not_format_or_measure_stale_hover_targets(self):
        for mode, theme, ratio in product(('single', 'sidecar'), ('light', 'dark'), (1, 2)):
            with self.subTest(mode=mode, theme=theme, ratio=ratio):
                page = self.open_report(mode, theme, ratio)
                result = page.evaluate('''async () => {
                    const model = fgView('timeline').probe();
                    const canvas = document.querySelector('.tl-canvas');
                    const tip = document.querySelector('.tl-tip');
                    const point = model.xyOf(1);
                    const bounds = canvas.getBoundingClientRect();
                    const empty = [bounds.left + 50, bounds.bottom - 2];
                    hoverMove(point);
                    await hoverFrame();
                    const reference = {pixels:canvas.toDataURL(), tip:tip.textContent};
                    hoverMove(empty);
                    await hoverFrame();
                    const descriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
                    const read = Element.prototype.getBoundingClientRect;
                    const work = {writes:0, reads:0};
                    Object.defineProperty(tip, 'innerHTML', {configurable:true,
                        get() { return descriptor.get.call(this); },
                        set(value) { work.writes++; descriptor.set.call(this, value); }});
                    Element.prototype.getBoundingClientRect = function(...args) {
                        if (this === canvas || this === tip) work.reads++;
                        return read.apply(this, args);
                    };
                    for (let index = 0; index < 80; index++) hoverMove(index % 2 ? point : empty);
                    const immediate = {...work};
                    await hoverFrame();
                    const after = {...work};
                    Element.prototype.getBoundingClientRect = read;
                    delete tip.innerHTML;
                    return {immediate, after, samePixels:canvas.toDataURL() === reference.pixels,
                        sameTip:tip.textContent === reference.tip};
                }''')
                self.assertEqual(result['immediate'], {'writes': 0, 'reads': 0})
                self.assertEqual(result['after']['writes'], 1)
                self.assertLessEqual(result['after']['reads'], 2)
                self.assertTrue(result['samePixels'])
                self.assertTrue(result['sameTip'])
                page.context.close()

    def test_first_text_initialization_and_real_scroll_preserve_latest_hover_and_data(self):
        for mode, theme in product(('single', 'sidecar'), ('light', 'dark')):
            with self.subTest(mode=mode, theme=theme):
                page = self.open_report(mode, theme)
                original = page.evaluate('''() => {
                    const model = fgView('timeline').probe();
                    return {data:JSON.stringify(fgData.read('tlData')), selected:model.sel(), view:model.view()};
                }''')
                for iteration in range(3):
                    page.locator('.view-tab[data-view=text]').click()
                    page.wait_for_function('() => fgCurrentView() === "text" && !fgTextDeferred()')
                    page.mouse.move(1100, 600)
                    previous_scroll = page.evaluate('scrollY')
                    page.mouse.wheel(0, 4000)
                    page.wait_for_function('previous => scrollY >= previous + 4000', arg=previous_scroll)
                    text = page.evaluate('''() => ({rows:fgTextNavigation.order.slice(),
                        content:document.getElementById('content').textContent, scroll:scrollY})''')
                    page.locator('.view-tab[data-view=timeline]').click()
                    result = page.evaluate('''async () => {
                        const model = fgView('timeline').probe();
                        const canvas = document.querySelector('.tl-canvas');
                        const point = model.xyOf(1);
                        const box = canvas.getBoundingClientRect();
                        for (let index = 0; index < 81; index++)
                            hoverMove(index % 2 ? [box.left + 50, box.bottom - 2] : point);
                        await hoverFrame();
                        const pixels = canvas.toDataURL();
                        const tip = document.querySelector('.tl-tip').textContent;
                        fgView('timeline').draw();
                        await hoverFrame();
                        return {data:JSON.stringify(fgData.read('tlData')), selected:model.sel(), view:model.view(),
                            samePixels:pixels === canvas.toDataURL(), tip, expected:model.nameOf(1),
                            rows:fgTextNavigation.order.slice(), content:document.getElementById('content').textContent};
                    }''')
                    self.assertEqual({key: result[key] for key in original}, original)
                    self.assertEqual(result['rows'], text['rows'])
                    self.assertEqual(result['content'], text['content'])
                    self.assertTrue(result['samePixels'])
                    self.assertIn(result['expected'], result['tip'])
                    page.locator('.view-tab[data-view=text]').click()
                    page.wait_for_function('() => fgCurrentView() === "text"')
                    self.assertEqual(page.evaluate('scrollY'), text['scroll'])
                    page.locator('.view-tab[data-view=timeline]').click()
                page.context.close()

    def test_leaving_scrolling_dragging_or_switching_views_cancels_queued_hover(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                page = self.open_report(mode)
                for action in ('leave', 'scroll', 'drag', 'touch', 'view'):
                    with self.subTest(action=action):
                        result = page.evaluate('''async action => {
                            const model = fgView('timeline').probe();
                            const canvas = document.querySelector('.tl-canvas');
                            const tip = document.querySelector('.tl-tip');
                            const point = model.xyOf(0);
                            canvas.dispatchEvent(new MouseEvent('mouseleave'));
                            await hoverFrame();
                            hoverMove(point);
                            if (action === 'leave') canvas.dispatchEvent(new MouseEvent('mouseleave'));
                            if (action === 'scroll') document.querySelector('.tl-scroll').dispatchEvent(new Event('scroll'));
                            if (action === 'drag') canvas.dispatchEvent(new MouseEvent('mousedown',
                                {button:0, bubbles:true, clientX:point[0], clientY:point[1]}));
                            if (action === 'touch') {
                                canvas.setPointerCapture = () => {};
                                canvas.dispatchEvent(new PointerEvent('pointerdown', {pointerType:'touch', pointerId:42,
                                    bubbles:true, clientX:point[0], clientY:point[1]}));
                            }
                            if (action === 'view') await fgSwitchView('text');
                            await hoverFrame();
                            await hoverFrame();
                            const hidden = tip.style.display === 'none';
                            if (action === 'view') await fgSwitchView('timeline');
                            if (action === 'drag') window.dispatchEvent(new MouseEvent('mouseup', {button:0}));
                            if (action === 'touch') canvas.dispatchEvent(new PointerEvent('pointercancel',
                                {pointerType:'touch', pointerId:42, bubbles:true}));
                            await hoverFrame();
                            return {hidden, stillHidden:tip.style.display === 'none'};
                        }''', action)
                        self.assertTrue(result['hidden'])
                        self.assertTrue(result['stillHidden'])
                page.context.close()


if __name__ == '__main__':
    unittest.main()
