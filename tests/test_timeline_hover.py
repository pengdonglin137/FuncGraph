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

    def test_opening_text_source_does_not_leave_a_native_caret_in_other_views(self):
        for mode, theme, ratio in product(('single', 'sidecar'), ('light', 'dark'), (1, 2)):
            with self.subTest(mode=mode, theme=theme, ratio=ratio):
                page = self.open_report(mode, theme, ratio)
                original = page.evaluate('''() => {
                    const model = fgView('timeline').probe();
                    return {data:JSON.stringify(fgData.read('tlData')), selected:model.sel(), view:model.view()};
                }''')
                page.locator('.view-tab[data-view=text]').click()
                row = page.locator('#content .line-container.expandable').first
                identifier = row.get_attribute('data-line-id')
                row.click(position={'x': row.bounding_box()['width'] - 10, 'y': 10})
                source = page.locator('#' + identifier + '_content')
                source.wait_for(state='visible')
                page.wait_for_function('identifier => !!document.getElementById(identifier + "_content").querySelector(".source-line")',
                                       arg=identifier)
                text = page.evaluate('''identifier => {
                    window.savedSourcePanel = document.getElementById(identifier + '_content');
                    return {html:savedSourcePanel.innerHTML, rows:fgTextNavigation.order.slice(),
                        selected:keyboardSelectedLine.id, scroll:scrollY};
                }''', identifier)
                for iteration in range(2):
                    page.locator('.view-tab[data-view=timeline]').click()
                    self.assertEqual(page.evaluate('getSelection().rangeCount'), 0)
                    result = page.evaluate('''async () => {
                        const model = fgView('timeline').probe();
                        hoverMove(model.xyOf(1));
                        await hoverFrame();
                        const canvas = document.querySelector('.tl-canvas');
                        const pixels = canvas.toDataURL();
                        const tip = document.querySelector('.tl-tip').textContent;
                        fgView('timeline').draw();
                        await hoverFrame();
                        return {data:JSON.stringify(fgData.read('tlData')), selected:model.sel(), view:model.view(),
                            samePixels:pixels === canvas.toDataURL(), tip, expected:model.nameOf(1)};
                    }''')
                    self.assertEqual({key: result[key] for key in original}, original)
                    self.assertTrue(result['samePixels'])
                    self.assertIn(result['expected'], result['tip'])
                    page.locator('.view-tab[data-view=timeline]').click()
                    self.assertEqual(page.evaluate('getSelection().rangeCount'), 0)
                    page.evaluate('''() => {
                        const tab = document.querySelector('.view-tab[data-view=timeline]');
                        getSelection().collapse(tab.firstChild, 0);
                        fgSwitchView('timeline');
                    }''')
                    self.assertEqual(page.evaluate('getSelection().rangeCount'), 0)
                    page.locator('.view-tab[data-view=text]').click()
                    restored = page.evaluate('''identifier => ({html:savedSourcePanel.innerHTML,
                        sameNode:savedSourcePanel === document.getElementById(identifier + '_content'),
                        rows:fgTextNavigation.order.slice(), selected:keyboardSelectedLine.id, scroll:scrollY,
                        display:getComputedStyle(savedSourcePanel).display})''', identifier)
                    self.assertEqual({key: restored[key] for key in text}, text)
                    self.assertTrue(restored['sameNode'])
                    self.assertEqual(restored['display'], 'block')
                page.context.close()

    def test_text_selection_is_suspended_and_restored_with_its_direction(self):
        for mode, backward in product(('single', 'sidecar'), (False, True)):
            with self.subTest(mode=mode, backward=backward):
                page = self.open_report(mode)
                page.locator('.view-tab[data-view=text]').click()
                row = page.locator('#content .line-container.expandable').first
                identifier = row.get_attribute('data-line-id')
                row.locator('.expand-btn').click()
                page.locator('#' + identifier + '_content').wait_for(state='visible')
                page.wait_for_function('identifier => !!document.getElementById(identifier + "_content").querySelector(".source-line")',
                                       arg=identifier)
                result = page.evaluate('''async ({identifier, backward}) => {
                    const panel = document.getElementById(identifier + '_content');
                    const walker = document.createTreeWalker(panel, NodeFilter.SHOW_TEXT);
                    const start = walker.nextNode();
                    let finish = start;
                    while (walker.nextNode()) finish = walker.currentNode;
                    const selection = getSelection();
                    selection.setBaseAndExtent(backward ? finish : start, backward ? finish.length : 0,
                        backward ? start : finish, backward ? 0 : finish.length);
                    await hoverFrame();
                    await hoverFrame();
                    const original = {anchor:selection.anchorNode, offset:selection.anchorOffset,
                        focus:selection.focusNode, end:selection.focusOffset, text:selection.toString(), scroll:scrollY, html:panel.innerHTML};
                    const counts = [];
                    const stages = [];
                    for (let iteration = 0; iteration < 2; iteration++) {
                        await fgSwitchView('timeline');
                        counts.push(selection.rangeCount);
                        await fgSwitchView('text');
                        await hoverFrame();
                        stages.push({text:selection.toString(), anchor:selection.anchorNode?.textContent,
                            focus:selection.focusNode?.textContent, offset:selection.anchorOffset, end:selection.focusOffset});
                    }
                    return {counts, stages, text:original.text, sameText:selection.toString() === original.text,
                        sameEndpoints:selection.anchorNode === original.anchor && selection.anchorOffset === original.offset &&
                            selection.focusNode === original.focus && selection.focusOffset === original.end,
                        sameScroll:scrollY === original.scroll, sameHtml:panel.innerHTML === original.html,
                        samePanel:panel === document.getElementById(identifier + '_content')};
                }''', {'identifier': identifier, 'backward': backward})
                self.assertEqual(result['counts'], [0, 0])
                self.assertTrue(result['text'])
                for key in ('sameText', 'sameEndpoints', 'sameScroll', 'sameHtml', 'samePanel'):
                    self.assertTrue(result[key], (key, result))
                page.context.close()

    def test_selection_cleanup_preserves_search_editing_and_discards_removed_text_ranges(self):
        page = self.open_report()
        result = page.evaluate('''async () => {
            const input = document.querySelector('.tl-search');
            input.value = 'read';
            input.focus(); input.setSelectionRange(1, 3);
            await fgSwitchView('timeline');
            const editing = document.activeElement === input && input.value === 'read' &&
                input.selectionStart === 1 && input.selectionEnd === 3;
            await fgSwitchView('text');
            const panel = document.createElement('span');
            panel.textContent = 'temporary source';
            document.getElementById('content').appendChild(panel);
            const range = document.createRange(); range.selectNodeContents(panel);
            getSelection().removeAllRanges(); getSelection().addRange(range);
            await fgSwitchView('timeline');
            const suspended = getSelection().rangeCount === 0;
            panel.remove();
            await fgSwitchView('text');
            return {editing, suspended, empty:getSelection().rangeCount === 0};
        }''')
        self.assertEqual(result, {'editing': True, 'suspended': True, 'empty': True})
        page.context.close()


if __name__ == '__main__':
    unittest.main()
