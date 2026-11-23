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
        from benchmark_offcpu_viewport import fixture
        root = Path(cls.kernel.dir)
        fixture(root / 'index.html', root / 'dense-waits.html', 16000, call_depth=0)
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

    def open_report(self, mode='single', theme='light', ratio=1, touch=False):
        context = self.browser.new_context(viewport={'width': 1500, 'height': 900}, device_scale_factor=ratio,
                                           has_touch=touch)
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

    def test_dense_wait_pixel_keeps_exact_hit_and_opens_first_wait_in_gap(self):
        context = self.browser.new_context(viewport={'width': 1500, 'height': 900}, device_scale_factor=2)
        self.addCleanup(context.close)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(lambda: self.assertEqual(errors, []))
        page.goto(urljoin(self.server.url, 'dense-waits.html'))
        target = page.evaluate('''async () => {
            const timeline = await fgSwitchView('timeline'), model = timeline.probe();
            const canvas = document.querySelector('.tl-canvas');
            window.waitFrame = () => new Promise(requestAnimationFrame);
            window.waitEvent = (type, [clientX, clientY]) =>
                canvas.dispatchEvent(new MouseEvent(type, {bubbles: true, clientX, clientY}));
            await waitFrame(); await waitFrame();
            const [start, end] = model.view(), bounds = canvas.getBoundingClientRect();
            const scale = bounds.width / (end - start), groups = new Map(), records = model.TRACKS[0].off;
            for (const [index, record] of records.entries()) {
                if (record[3] !== 1) continue;
                const pixel = Math.floor((record[1] - start) * scale);
                if (!groups.has(pixel)) groups.set(pixel, []);
                groups.get(pixel).push(index);
            }
            const clientY = Math.round(model.offXY(records[128])[1]);
            for (const [pixel, group] of groups) {
                const clientX = Math.ceil(bounds.left + pixel), time = start + (clientX - bounds.left) / scale;
                if (group.length < 4 || pixel < 80 || pixel > 300 ||
                        records.some(record => record[3] === 1 && record[1] <= time && record[2] >= time)) continue;
                return {point: [clientX, clientY], selected: group[0], count: group.length};
            }
            throw Error('No dense wait pixel containing a real scheduling gap');
        }''')
        page.evaluate('''async point => { waitEvent('mousemove', point); await waitFrame(); await waitFrame(); }''', target['point'])
        tip = page.locator('.tl-tip')
        self.assertIn(f"{target['count']} waits start in this pixel", tip.text_content())
        self.assertTrue(page.locator('.tl-canvas').evaluate("canvas => canvas.classList.contains('ptr')"))
        repeated_reads = page.evaluate('''async point => {
            const track = fgView('timeline').probe().TRACKS[0], records = track.off;
            const counts = [];
            let reads = 0;
            track.off = new Proxy(records, {get(target, key, receiver) {
                if (typeof key === 'string' && /^\\d+$/.test(key)) reads++;
                return Reflect.get(target, key, receiver);
            }});
            try {
                for (const offset of [1, 2, 3]) {
                    reads = 0;
                    waitEvent('mousemove', [point[0], point[1] + offset]);
                    await waitFrame(); await waitFrame();
                    counts.push(reads);
                }
                return {counts, bound: Math.ceil(Math.log2(records.length)) + 4};
            } finally { track.off = records; }
        }''', target['point'])
        self.assertLessEqual(max(repeated_reads['counts'][1:]), repeated_reads['bound'], repeated_reads)
        self.assertIn(f"{target['count']} waits start in this pixel", tip.text_content())
        page.evaluate('''point => { waitEvent('mousedown', point); waitEvent('mouseup', point); }''', target['point'])
        self.assertIn('Showing the first', page.locator('.an-wait-dense').text_content())
        self.assertEqual(page.locator('.an-wait-table tr[data-wait]').first.get_attribute('data-wait'), str(target['selected']))
        with page.expect_download() as download:
            page.locator('[data-act=wait-csv]').click()
        self.assertIn(f"1,{target['selected']},0,1,worker,", Path(download.value.path()).read_text(encoding='utf-8-sig'))

        adjacent = target['selected'] + 1
        point = page.evaluate('''async index => {
            const model = fgView('timeline').probe(), record = model.TRACKS[0].off[index];
            model.zoomTo(record[1] - 3000, record[2] + 3000);
            await waitFrame(); await waitFrame();
            return model.offXY(record).map(Math.round);
        }''', adjacent)
        page.evaluate('''async point => { waitEvent('mousemove', point); await waitFrame(); await waitFrame(); }''', point)
        self.assertNotIn('waits start in this pixel', tip.text_content())
        page.evaluate('''point => { waitEvent('mousedown', point); waitEvent('mouseup', point); }''', point)
        self.assertEqual(page.locator('.an-wait-table tr[data-wait]').first.get_attribute('data-wait'), str(adjacent))
        self.assertEqual(page.locator('.an-wait-dense').count(), 0)

    def test_pointer_bursts_do_not_format_or_measure_stale_hover_targets(self):
        for mode, theme, ratio in product(('single', 'sidecar'), ('light', 'dark'), (1, 2)):
            with self.subTest(mode=mode, theme=theme, ratio=ratio):
                page = self.open_report(mode, theme, ratio)
                result = page.evaluate('''async () => {
                    const model = fgView('timeline').probe();
                    const canvas = document.querySelector('.tl-canvas');
                    const tip = document.querySelector('.tl-tip');
                    const body = tip.querySelector('.tl-tip-body');
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
                    Object.defineProperty(body, 'innerHTML', {configurable:true,
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
                    delete body.innerHTML;
                    return {immediate, after, samePixels:canvas.toDataURL() === reference.pixels,
                        sameTip:tip.textContent === reference.tip};
                }''')
                self.assertEqual(result['immediate'], {'writes': 0, 'reads': 0})
                self.assertEqual(result['after']['writes'], 1)
                self.assertLessEqual(result['after']['reads'], 2)
                self.assertTrue(result['samePixels'])
                self.assertTrue(result['sameTip'])
                page.context.close()

    def test_hover_and_track_scroll_leave_ruler_alone_but_zoom_repaints_it(self):
        for mode, theme, ratio in product(('single', 'sidecar'), ('light', 'dark'), (1, 2)):
            with self.subTest(mode=mode, theme=theme, ratio=ratio):
                page = self.open_report(mode, theme, ratio)
                result = page.evaluate('''async () => {
                    const model = fgView('timeline').probe();
                    const tracks = document.querySelector('.tl-canvas');
                    const ruler = document.querySelector('.tl-head-canvas');
                    const scroll = document.querySelector('.tl-scroll');
                    const settle = async () => { await hoverFrame(); await hoverFrame(); };
                    const state = () => JSON.stringify([fgData.read('tlData'),model.sel(),model.view()]);
                    const initial = {tracks:tracks.toDataURL(), ruler:ruler.toDataURL(), state:state()};
                    const rulerContext = ruler.getContext('2d');
                    const fill = rulerContext.fillRect;
                    let rulerFills = 0;
                    rulerContext.fillRect = function(...args) { rulerFills++; return fill.apply(this, args); };
                    hoverMove(model.xyOf(1));
                    await settle();
                    const hoverChangedTracks = tracks.toDataURL() !== initial.tracks;
                    tracks.dispatchEvent(new MouseEvent('mouseleave'));
                    await settle();
                    const leaveRestoredTracks = tracks.toDataURL() === initial.tracks;
                    const startScroll = scroll.scrollTop;
                    const bottom = scroll.scrollHeight - scroll.clientHeight;
                    scroll.scrollTop = startScroll < bottom ? Math.min(bottom, startScroll + 80) : Math.max(0, startScroll - 80);
                    await settle();
                    const scrollMoved = scroll.scrollTop !== startScroll;
                    const scrollChangedTracks = tracks.toDataURL() !== initial.tracks;
                    const sameRuler = ruler.toDataURL() === initial.ruler;
                    const sameState = state() === initial.state;
                    const localFills = rulerFills;
                    scroll.scrollTop = startScroll;
                    model.zoomTo(model.sStart[1], model.sEnd[1], 1);
                    await settle();
                    const zoomChangedRuler = ruler.toDataURL() !== initial.ruler;
                    delete rulerContext.fillRect;
                    return {hoverChangedTracks, leaveRestoredTracks, scrollMoved, scrollChangedTracks,
                        sameRuler, sameState, localFills, zoomChangedRuler, zoomFills:rulerFills - localFills};
                }''')
                for fact in ('hoverChangedTracks', 'leaveRestoredTracks', 'scrollMoved', 'scrollChangedTracks',
                             'sameRuler', 'sameState', 'zoomChangedRuler'):
                    self.assertTrue(result[fact], result)
                self.assertEqual(result['localFills'], 0)
                self.assertGreater(result['zoomFills'], 0)
                page.context.close()

    def test_wheel_only_reads_canvas_geometry_for_zoom_and_does_not_repaint_at_pan_limit(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                page = self.open_report(mode)
                result = page.evaluate('''async () => {
                    const model = fgView('timeline').probe();
                    const canvas = document.querySelector('.tl-canvas');
                    const ruler = document.querySelector('.tl-head-canvas');
                    const bounds = canvas.getBoundingClientRect.bind(canvas);
                    const width = bounds().width;
                    const context = ruler.getContext('2d'), fill = context.fillRect;
                    const start = model.sStart[0], end = model.sEnd[0];
                    const settle = async () => { await hoverFrame(); await hoverFrame(); };
                    const reset = async () => { model.zoomTo(start, end, -1); await settle(); };
                    const view = () => model.view();
                    const same = (first, second) => first.every((value, index) => Math.abs(value - second[index]) < 1e-6);
                    const send = options => !canvas.dispatchEvent(new WheelEvent('wheel', {
                        clientX: 220, bubbles: true, cancelable: true, ...options,
                    }));
                    let geometryReads = 0, rulerPaints = 0;
                    canvas.getBoundingClientRect = () => { geometryReads++; return bounds(); };
                    context.fillRect = function(...args) { rulerPaints++; return fill.apply(this, args); };
                    const before = view();
                    const verticalHandled = send({deltaY: 120});
                    const vertical = {moved: !same(view(), before), handled: verticalHandled, geometryReads};
                    const horizontalHandled = send({deltaX: 24});
                    const pixelView = view();
                    const horizontal = {moved: !same(pixelView, before), handled: horizontalHandled, geometryReads};
                    await reset();
                    send({deltaY: 1, deltaMode: 1, shiftKey: true});
                    const lineMatches = same(view(), pixelView);
                    await reset();
                    send({deltaX: width * .2});
                    const wideView = view();
                    await reset();
                    send({deltaX: .2, deltaMode: 2});
                    const pageMatches = same(view(), wideView);
                    send({deltaX: 1e8});
                    await settle();
                    const edgeView = view();
                    rulerPaints = 0;
                    for (let step = 0; step < 6; step++) {
                        send({deltaX: 120});
                        await hoverFrame();
                    }
                    const edge = {moved: !same(view(), edgeView), rulerPaints, geometryReads};
                    await reset();
                    const previous = view(), readsBeforeZoom = geometryReads;
                    const zoomHandled = send({deltaY: -120, ctrlKey: true});
                    const zoomed = view();
                    const zoom = {narrower: zoomed[1] - zoomed[0] < previous[1] - previous[0],
                        handled:zoomHandled, geometryReads:geometryReads - readsBeforeZoom};
                    delete canvas.getBoundingClientRect;
                    delete context.fillRect;
                    return {vertical, horizontal, lineMatches, pageMatches, edge, zoom};
                }''')
                self.assertEqual(result, dict(vertical=dict(moved=False, handled=False, geometryReads=0),
                    horizontal=dict(moved=True, handled=True, geometryReads=0), lineMatches=True, pageMatches=True,
                    edge=dict(moved=False, rulerPaints=0, geometryReads=0),
                    zoom=dict(narrower=True, handled=True, geometryReads=1)))

    def test_zoom_limits_skip_geometry_and_paint_for_both_wheel_surfaces(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                page = self.open_report(mode)
                result = page.evaluate('''async () => {
                    const model = fgView('timeline').probe();
                    const canvas = document.querySelector('.tl-canvas');
                    const ruler = document.querySelector('.tl-head-canvas');
                    const origin = canvas.getBoundingClientRect();
                    const context = ruler.getContext('2d'), drawImage = context.drawImage;
                    const settle = async () => { await hoverFrame(); await hoverFrame(); };
                    const span = () => { const [start, end] = model.view(); return end - start; };
                    const send = (target, deltaY) => !target.dispatchEvent(new WheelEvent('wheel', {
                        deltaY, ctrlKey: true, cancelable: true, bubbles: true,
                        clientX: origin.left + origin.width * .4,
                    }));
                    let reads = 0, paints = 0;
                    for (const surface of [canvas, ruler]) {
                        const bounds = surface.getBoundingClientRect.bind(surface);
                        surface.getBoundingClientRect = () => { reads++; return bounds(); };
                    }
                    context.drawImage = function(...args) { paints++; return drawImage.apply(this, args); };
                    const results = [];
                    for (const direction of [-1, 1]) {
                        send(canvas, direction * 10000);
                        await settle();
                        const edge = JSON.stringify(model.view()), edgeSpan = span();
                        reads = paints = 0;
                        let consumed = true;
                        for (let step = 0; step < 6; step++) {
                            consumed = send(step % 2 ? ruler : canvas, direction * 120) && consumed;
                            await settle();
                        }
                        const blocked = {reads, paints, sameView:edge === JSON.stringify(model.view()), consumed};
                        reads = paints = 0;
                        send(ruler, -direction * 120);
                        await settle();
                        results.push({blocked, resumed: (edgeSpan - span()) * direction > 0, reads, paints});
                    }
                    return results;
                }''')
                self.assertEqual(result, [dict(blocked=dict(reads=0, paints=0, sameView=True, consumed=True),
                                               resumed=True, reads=1, paints=1)] * 2)

    def observe_zoom_paints(self, page):
        page.evaluate('''() => {
            const ruler = document.querySelector('.tl-head-canvas').getContext('2d');
            const drawImage = ruler.drawImage;
            let paints = 0;
            ruler.drawImage = function(...args) { paints++; return drawImage.apply(this, args); };
            window.zoomState = () => [fgView('timeline').probe().view(), paints];
            window.zoomSettle = () => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)));
            window.zoomLimit = async direction => {
                const canvas = document.querySelector('.tl-canvas'), bounds = canvas.getBoundingClientRect();
                canvas.dispatchEvent(new WheelEvent('wheel', {deltaY: direction * 10000, ctrlKey: true,
                    cancelable: true, clientX: bounds.left + bounds.width / 2}));
                await zoomSettle();
            };
        }''')

    def test_zoom_buttons_and_shortcuts_skip_paint_at_both_limits(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                page = self.open_report(mode, touch=True)
                self.observe_zoom_paints(page)
                for direction, key, action, reverse in [(-1, 'w', 'zoomin', 's'), (1, 's', 'zoomout', 'w')]:
                    page.evaluate('zoomLimit', direction)
                    button = page.locator(f'[data-act={action}]')
                    canvas = page.locator('.tl-canvas')
                    before = page.evaluate('zoomState()')
                    button.click(timeout=5000)
                    page.evaluate('zoomSettle()')
                    self.assertTrue(button.evaluate('element => element === document.activeElement'))
                    after_button = page.evaluate('zoomState()')
                    canvas.press(key)
                    page.evaluate('zoomSettle()')
                    self.assertEqual(after_button, before)
                    self.assertEqual(page.evaluate('zoomState()'), before)
                    canvas.press(reverse)
                    page.evaluate('zoomSettle()')
                    moved, paints = page.evaluate('zoomState()')
                    self.assertNotEqual(moved, before[0])
                    self.assertEqual(paints, before[1] + 1)

    def test_pinch_skips_paint_at_maximum_but_can_pan_at_minimum(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                page = self.open_report(mode, touch=True)
                self.observe_zoom_paints(page)
                session = page.context.new_cdp_session(page)
                bounds = page.locator('.tl-canvas').bounding_box()
                center = round(bounds['x'] + bounds['width'] / 2)
                vertical = round(bounds['y'] + bounds['height'] / 2)

                def gesture(kind, offsets):
                    session.send('Input.dispatchTouchEvent', {'type': kind, 'touchPoints': [
                        {'id': index, 'x': center + offset, 'y': vertical} for index, offset in enumerate(offsets)]})
                    page.evaluate('zoomSettle()')

                page.evaluate('zoomLimit', 1)
                gesture('touchStart', [-100, 100])
                widest = page.evaluate('zoomState()')
                for distance in (90, 80, 70):
                    gesture('touchMove', [-distance, distance])
                blocked = page.evaluate('zoomState()')
                gesture('touchMove', [-110, 110])
                narrower = page.evaluate('zoomState()')
                gesture('touchEnd', [])
                self.assertEqual(blocked, widest)
                self.assertNotEqual(narrower[0], widest[0])
                self.assertGreater(narrower[1], widest[1])

                page.evaluate('zoomLimit', -1)
                gesture('touchStart', [-100, 100])
                [start, end], paints = page.evaluate('zoomState()')
                gesture('touchMove', [-80, 120])
                [shifted_start, shifted_end], shifted_paints = page.evaluate('zoomState()')
                gesture('touchEnd', [])
                self.assertAlmostEqual(shifted_end - shifted_start, end - start, delta=1e-6)
                self.assertLess(shifted_start, start)
                self.assertGreater(shifted_paints, paints)

    def test_drag_limits_skip_paint_until_the_view_moves(self):
        for mode, gesture in product(('single', 'sidecar'), ('mouse', 'touch', 'overview')):
            with self.subTest(mode=mode, gesture=gesture):
                page = self.open_report(mode, touch=gesture == 'touch')
                page.evaluate('''() => {
                    const ruler = document.querySelector('.tl-head-canvas').getContext('2d');
                    const drawImage = ruler.drawImage;
                    window.dragPaints = 0;
                    ruler.drawImage = function(...args) { dragPaints++; return drawImage.apply(this, args); };
                }''')
                surface = page.locator('.tl-head-canvas' if gesture == 'overview' else '.tl-canvas')
                session = page.context.new_cdp_session(page) if gesture == 'touch' else None
                state = '[fgView("timeline").probe().view(), dragPaints]'
                settle = 'async () => { await hoverFrame(); await hoverFrame(); }'
                for direction in (-1, 1):
                    page.locator('[data-act=fit]').click()
                    page.evaluate(settle)
                    bounds = surface.bounding_box()
                    vertical = round(bounds['y'] + (10 if gesture == 'overview' else bounds['height'] / 2))

                    def move(fraction, step=None):
                        horizontal = round(bounds['x'] + fraction * bounds['width'])
                        if session:
                            session.send('Input.dispatchTouchEvent', {'type': step or 'touchMove', 'touchPoints':
                                         [] if step == 'touchEnd' else [{'x': horizontal, 'y': vertical}]})
                        else:
                            page.mouse.move(horizontal, vertical)
                            if step == 'touchStart':
                                page.mouse.down()
                            elif step == 'touchEnd':
                                page.mouse.up()
                        page.evaluate(settle)

                    edge = .5 + direction * (.46 if gesture == 'overview' else .25)
                    move(.5, 'touchStart')
                    move(edge)
                    at_limit = page.evaluate(state)
                    for step in range(1, 5):
                        move(edge + direction * step * .007)
                    stopped = page.evaluate(state)
                    move(.5)
                    reversed_state = page.evaluate(state)
                    move(.5, 'touchEnd')
                    self.assertEqual(stopped, at_limit, (gesture, direction))
                    self.assertNotEqual(reversed_state[0], at_limit[0])
                    self.assertEqual(reversed_state[1], at_limit[1] + 1)

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
                    page.evaluate('async () => { await hoverFrame(); await hoverFrame(); }')
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
                for action in ('leave', 'scroll', 'page-scroll', 'viewport-scroll', 'report-scroll', 'drag', 'touch', 'view'):
                    with self.subTest(action=action):
                        result = page.evaluate('''async action => {
                            const model = fgView('timeline').probe();
                            const canvas = document.querySelector('.tl-canvas');
                            const tip = document.querySelector('.tl-tip');
                            const cursor = document.querySelector('.tl-hover-cursor');
                            const point = model.xyOf(0);
                            canvas.dispatchEvent(new MouseEvent('mouseleave'));
                            hoverMove(point);
                            await hoverFrame();
                            const wasVisible = !cursor.hidden;
                            hoverMove(point);
                            if (action === 'leave') canvas.dispatchEvent(new MouseEvent('mouseleave'));
                            if (action === 'scroll') document.querySelector('.tl-scroll').dispatchEvent(new Event('scroll'));
                            if (action === 'page-scroll') window.dispatchEvent(new Event('scroll'));
                            if (action === 'viewport-scroll') visualViewport.dispatchEvent(new Event('scroll'));
                            if (action === 'report-scroll') document.getElementById('timelineView').dispatchEvent(new Event('scroll'));
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
                            const hidden = tip.style.display === 'none' && cursor.hidden;
                            if (action === 'view') await fgSwitchView('timeline');
                            if (action === 'drag') window.dispatchEvent(new MouseEvent('mouseup', {button:0}));
                            if (action === 'touch') canvas.dispatchEvent(new PointerEvent('pointercancel',
                                {pointerType:'touch', pointerId:42, bubbles:true}));
                            await hoverFrame();
                            return {wasVisible, hidden, stillHidden:tip.style.display === 'none' && cursor.hidden};
                        }''', action)
                        self.assertTrue(result['wasVisible'])
                        self.assertTrue(result['hidden'])
                        self.assertTrue(result['stillHidden'])
                page.context.close()

    def test_viewport_resize_keeps_only_fresh_hover_over_the_canvas(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                page = self.open_report(mode)
                result = page.evaluate('''async () => {
                    const model = fgView('timeline').probe();
                    const canvas = document.querySelector('.tl-canvas');
                    const tip = document.querySelector('.tl-tip');
                    const cursor = document.querySelector('.tl-hover-cursor');
                    const point = model.xyOf(0).map(Math.round);
                    const isOverCanvas = document.elementFromPoint(...point) === canvas;
                    const current = () => ({tip:tip.style.display === 'flex' ? tip.querySelector('.t-name')?.textContent : null,
                        cursor:!cursor.hidden});
                    const frames = async () => { await hoverFrame(); await hoverFrame(); };
                    hoverMove(point);
                    visualViewport.dispatchEvent(new Event('resize'));
                    await frames();
                    const fresh = current();
                    window.dispatchEvent(new Event('resize'));
                    await frames();
                    const stale = current();
                    hoverMove(point);
                    visualViewport.dispatchEvent(new Event('resize'));
                    const cover = document.createElement('div');
                    cover.style.cssText = 'position:fixed;inset:0;z-index:4000';
                    document.body.append(cover);
                    await frames();
                    const covered = current();
                    cover.remove();
                    return {isOverCanvas, fresh, stale, covered, expected:model.nameOf(0)};
                }''')
                self.assertTrue(result['isOverCanvas'], result)
                self.assertEqual(result['fresh'], {'tip': result['expected'], 'cursor': True})
                for key in ('stale', 'covered'):
                    self.assertEqual(result[key], {'tip': None, 'cursor': False})
                page.context.close()

    def test_canvas_press_avoids_slice_lookup_and_ignores_subthreshold_motion(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                page = self.open_report(mode=mode)
                facts = page.evaluate('''async () => {
                    const canvas = document.querySelector('.tl-canvas');
                    const scroll = document.querySelector('.tl-scroll');
                    const model = fgView('timeline').probe();
                    const frame = requestAnimationFrame;
                    await new Promise(resolve => frame(() => frame(resolve)));
                    const bounds = canvas.getBoundingClientRect();
                    const clientX = bounds.left + 100, clientY = bounds.top + 42;
                    let reads = 0, frames = 0;
                    const position = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollTop');
                    Object.defineProperty(scroll, 'scrollTop', {
                        configurable: true,
                        get() { reads++; return position.get.call(this); },
                        set(value) { position.set.call(this, value); },
                    });
                    window.requestAnimationFrame = callback => { frames++; return frame(callback); };
                    const send = (target, type, options = {}) => target.dispatchEvent(new MouseEvent(type, {
                        button: 0, buttons: type === 'mouseup' ? 0 : 1,
                        bubbles: true, cancelable: true, clientX, clientY, ...options,
                    }));
                    try {
                        send(canvas, 'mousedown');
                        const panReads = reads;
                        const before = model.view();
                        send(window, 'mousemove', {clientX: clientX + 2});
                        const jitterFrames = frames;
                        const still = JSON.stringify(before) === JSON.stringify(model.view());
                        send(window, 'mousemove', {clientX: clientX + 20});
                        const panned = JSON.stringify(before) !== JSON.stringify(model.view());
                        send(window, 'mouseup');
                        reads = 0;
                        send(canvas, 'mousedown', {shiftKey: true});
                        const trackRangeReads = reads;
                        send(window, 'mouseup');
                        reads = 0;
                        send(canvas, 'mousedown', {shiftKey: true, altKey: true});
                        const globalRangeReads = reads;
                        send(window, 'mouseup');
                        return {panReads, jitterFrames, still, panned, trackRangeReads, globalRangeReads};
                    } finally {
                        delete scroll.scrollTop;
                        window.requestAnimationFrame = frame;
                    }
                }''')
                self.assertEqual(facts, dict(panReads=0, jitterFrames=0, still=True, panned=True,
                                             trackRangeReads=1, globalRangeReads=0))

    def test_interrupted_canvas_drag_does_not_move_the_view_or_leave_a_grab_cursor(self):
        for interruption in ('view', 'blur', 'release'):
            with self.subTest(interruption=interruption):
                page = self.open_report()
                bounds = page.locator('.tl-canvas').bounding_box()
                horizontal = bounds['x'] + bounds['width'] / 2
                vertical = bounds['y'] + 32
                page.mouse.move(horizontal, vertical)
                page.mouse.down()
                page.mouse.move(horizontal - 50, vertical)
                state = 'fgView("timeline").probe().view()'
                before = page.evaluate(state)
                if interruption == 'view':
                    page.evaluate('async () => { await fgSwitchView("text"); await fgSwitchView("timeline"); }')
                elif interruption == 'blur':
                    page.evaluate('window.dispatchEvent(new Event("blur"))')
                if interruption == 'release':
                    page.evaluate('''([clientX, clientY]) => document.querySelector('.tl-canvas').dispatchEvent(
                        new MouseEvent('mousemove', {bubbles:true, clientX, clientY, buttons:0}))''',
                                  [horizontal - 100, vertical])
                else:
                    page.mouse.move(horizontal - 100, vertical)
                self.assertEqual(page.locator('.tl-canvas.grab').count(), 0)
                self.assertEqual(page.evaluate(state), before)
                page.mouse.up()
                self.assertEqual(page.evaluate('fgView("timeline").probe().sel()'), 0)

    def test_interrupted_range_drag_preserves_committed_overlay_and_summary(self):
        page = self.open_report()
        page.evaluate('''async () => {
            const model = fgView('timeline').probe();
            const [start, end] = model.view();
            model.range(start + (end - start) * .15, start + (end - start) * .3);
            await hoverFrame(); await hoverFrame();
            window.committedRangeHead = document.querySelector('.tl-range-head');
        }''')
        band = page.locator('.tl-range-band')
        position = '(node) => [node.style.left, node.style.width]'
        original = band.evaluate(position)
        bounds = page.locator('.tl-canvas').bounding_box()
        horizontal = bounds['x'] + bounds['width'] / 2
        vertical = bounds['y'] + 32
        page.keyboard.down('Shift')
        page.keyboard.down('Alt')
        page.mouse.move(horizontal, vertical)
        page.mouse.down()
        page.mouse.move(horizontal + 90, vertical)
        page.wait_for_function('''previous => {
            const style = document.querySelector('.tl-range-band').style;
            return style.left !== previous[0] && style.width !== previous[1];
        }''', arg=original)
        page.evaluate('async () => { await fgSwitchView("text"); await fgSwitchView("timeline"); await hoverFrame(); }')
        page.mouse.up()
        page.keyboard.up('Alt')
        page.keyboard.up('Shift')
        self.assertEqual(band.evaluate(position), original)
        self.assertTrue(page.evaluate('document.querySelector(".tl-range-head") === committedRangeHead'))

    def test_blank_canvas_dismisses_details_once_without_repainting_again(self):
        for touch in (False, True):
            with self.subTest(touch=touch):
                page = self.open_report(touch=touch)
                click = page.touchscreen.tap if touch else page.mouse.click
                blank = '''async () => {
                    const model = fgView('timeline').probe();
                    const canvas = document.querySelector('.tl-canvas');
                    const scroll = document.querySelector('.tl-scroll');
                    const track = model.TRACKS[model.sTask[0]];
                    const gap = track.y + track.h - 3;
                    scroll.scrollTop = Math.max(0, gap - scroll.clientHeight / 2);
                    await hoverFrame(); await hoverFrame();
                    const bounds = canvas.getBoundingClientRect();
                    const point = [bounds.left + bounds.width / 2, bounds.top + gap - scroll.scrollTop];
                    if (document.elementFromPoint(...point) !== canvas) throw Error('Track gap is not reachable');
                    return point;
                }'''
                click(*page.evaluate(blank))
                self.assertEqual(page.evaluate('fgView("timeline").probe().sel()'), -1)
                self.assertEqual(page.locator('.tl-detail.show').count(), 0)
                point = page.evaluate(blank)
                if not touch:
                    page.mouse.move(*point)
                before = page.evaluate('''async () => {
                    await hoverFrame(); await hoverFrame();
                    window.blankPaints = 0;
                    const context = document.querySelector('.tl-canvas').getContext('2d');
                    const fill = context.fillRect.bind(context);
                    context.fillRect = (...args) => { blankPaints++; return fill(...args); };
                    return fgView('timeline').probe().view();
                }''')
                for _ in range(3):
                    click(*point)
                    page.evaluate('async () => { await hoverFrame(); await hoverFrame(); }')
                self.assertEqual(page.evaluate('blankPaints'), 0)
                self.assertEqual(page.evaluate('fgView("timeline").probe().view()'), before)
                self.assertEqual(page.evaluate('fgView("timeline").probe().sel()'), -1)
                self.assertEqual(page.locator('.tl-detail.show').count(), 0)

    def test_returning_to_timeline_does_not_accept_a_stale_captured_tap(self):
        page = self.open_report()
        facts = page.evaluate('''async () => {
            const model = fgView('timeline').probe();
            model.select(-1);
            await hoverFrame(); await hoverFrame();
            const canvas = document.querySelector('.tl-canvas');
            const [clientX, clientY] = model.xyOf(0);
            const pointerId = 42;
            const captured = new Set(), released = [];
            canvas.setPointerCapture = id => captured.add(id);
            canvas.hasPointerCapture = id => captured.has(id);
            canvas.releasePointerCapture = id => { captured.delete(id); released.push(id); };
            const send = type => canvas.dispatchEvent(new PointerEvent(type, {
                pointerType:'touch', pointerId, bubbles:true, cancelable:true, clientX, clientY,
            }));
            send('pointerdown');
            await fgSwitchView('text');
            await fgSwitchView('timeline');
            send('pointerup');
            send('click');
            const selectedAfterReturn = model.sel();
            send('pointerdown');
            send('pointerup');
            send('click');
            return {released, selectedAfterReturn, freshSelection:model.sel()};
        }''')
        self.assertEqual(facts, dict(released=[42], selectedAfterReturn=-1, freshSelection=0))

    def test_range_drag_updates_overlay_and_ruler_without_repainting_tracks(self):
        for mode, theme, global_range in product(('single', 'sidecar'), ('light', 'dark'), (False, True)):
            with self.subTest(mode=mode, theme=theme, global_range=global_range):
                page = self.open_report(mode, theme, ratio=2)
                facts = page.evaluate('''async globalRange => {
                    const canvas = document.querySelector('.tl-canvas');
                    const model = fgView('timeline').probe();
                    const bounds = canvas.getBoundingClientRect();
                    const clientX = bounds.left + bounds.width * .25, clientY = model.xyOf(0)[1];
                    const context = canvas.getContext('2d');
                    const fillRect = context.fillRect;
                    let trackPaints = 0;
                    context.fillRect = function(...args) { trackPaints++; return fillRect.apply(this, args); };
                    const ruler = document.querySelector('.tl-head-canvas');
                    const overlay = document.querySelector('.tl-range-overlay');
                    const band = overlay?.firstElementChild;
                    const send = (target, type, delta = 0) => target.dispatchEvent(new MouseEvent(type, {
                        button: 0, buttons: type === 'mouseup' ? 0 : 1,
                        bubbles: true, cancelable: true, shiftKey: true, altKey: globalRange,
                        clientX: clientX + delta, clientY,
                    }));
                    try {
                        send(canvas, 'mousedown');
                        send(window, 'mousemove', 12);
                        await hoverFrame();
                        const before = {width:band?.getBoundingClientRect().width, ruler:ruler.toDataURL()};
                        trackPaints = 0;
                        send(window, 'mousemove', 62);
                        await hoverFrame();
                        const boundsAfter = band?.getBoundingClientRect();
                        const facts = {trackPaints, rulerChanged:ruler.toDataURL() !== before.ruler,
                            bandGrew:boundsAfter?.width > before.width,
                            coverage:!!boundsAfter && (globalRange ? boundsAfter.height === bounds.height :
                                boundsAfter.height > 0 && boundsAfter.height < bounds.height &&
                                boundsAfter.top <= clientY && clientY < boundsAfter.bottom),
                            hitCanvas:document.elementFromPoint(clientX + 20, clientY) === canvas};
                        send(window, 'mouseup', 62);
                        const summary = document.querySelector('.tl-range-head');
                        facts.committed = !!summary && summary.textContent.includes('all tracks') === globalRange;
                        await hoverFrame();
                        facts.overlayKept = overlay?.hidden === false;
                        summary?.querySelector('.tl-close').click();
                        await hoverFrame();
                        facts.overlayCleared = overlay?.hidden === true;
                        return facts;
                    } finally {
                        delete context.fillRect;
                    }
                }''', global_range)
                self.assertEqual(facts, dict(trackPaints=0, rulerChanged=True, bandGrew=True, coverage=True,
                                             hitCanvas=True, committed=True, overlayKept=True, overlayCleared=True))

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
                    page.evaluate('async () => { await hoverFrame(); await hoverFrame(); }')
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
