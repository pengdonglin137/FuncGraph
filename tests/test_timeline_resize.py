"""Timeline resizing preserves painted frames, canvas geometry and trace evidence."""

from itertools import product
from pathlib import Path
import threading
import unittest

import helpers
from fglib.viewer import create_server
from test_uftrace_import import call, stream


class TimelineResizeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise unittest.SkipTest(str(error))
        cls.kernel = helpers.FakeKernel()
        cls.addClassCleanup(cls.kernel.close)
        deep_trace = Path(cls.kernel.dir, 'deep.jsonl')
        deep_trace.write_text(stream([
            call('entry', 'user', 100 * depth, depth, f'frame_{depth}', depth + 1)
            for depth in range(64)
        ] + [
            call('exit', 'user', 100_000 - 100 * depth, depth, f'frame_{depth}', depth + 1)
            for depth in reversed(range(64))
        ]))
        cpu_trace = Path(cls.kernel.dir, 'cpus.jsonl')
        cpu_trace.write_text(stream([
            call(kind, 'kernel', cpu * 1000 + offset, 0, 'work', 1, tid=400 + cpu, cpu=cpu, comm=f'worker_{cpu}')
            for cpu in range(8) for kind, offset in (('entry', 100), ('exit', 900))
        ]))
        events_trace = Path(cls.kernel.dir, 'events.jsonl')
        events_trace.write_text(stream([
            call('event', 'kernel', 50, 0, 'sched:sched_switch', 0, tid=10,
                 text='prev_comm=a prev_pid=10 prev_prio=120 prev_state=S ==> next_comm=b next_pid=20 next_prio=120'),
            call('event', 'kernel', 8000, 0, 'sched:sched_switch', 0, tid=20,
                 text='prev_comm=b prev_pid=20 prev_prio=120 prev_state=S ==> next_comm=a next_pid=10 next_prio=120'),
        ]))
        cls.reports = {}
        for trace_name, trace in [('pi4', helpers.fixture('pi4_sched')), ('deep', str(deep_trace)),
                                  ('cpus', str(cpu_trace)), ('events', str(events_trace))]:
            for mode in ('single', 'sidecar'):
                name = f'{trace_name}-{mode}' + ('.html' if mode == 'single' else '')
                path = Path(cls.kernel.dir, name)
                cls.kernel.run(trace, '--timeline', '--enable-fold', '--filter', '--report-mode', mode, out=name)
                if mode == 'single':
                    cls.reports[trace_name, mode] = path.as_uri()
                else:
                    server = create_server(path)
                    cls.addClassCleanup(server.server_close)
                    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
                    thread.start()
                    cls.addClassCleanup(thread.join, 5)
                    cls.addClassCleanup(server.shutdown)
                    cls.reports[trace_name, mode] = server.url
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)

    def open_report(self, mode='single', ratio=1, theme='light', select_initial=True, trace='pi4', touch=False, size=None):
        width, height = size or ((480, 800) if touch else (1500, 900))
        context = self.browser.new_context(viewport={'width': width, 'height': height}, device_scale_factor=ratio,
                                           has_touch=touch, is_mobile=touch)
        self.addCleanup(context.close)
        page = context.new_page()
        page.set_default_timeout(10000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(lambda: self.assertEqual(errors, []))
        page.goto(self.reports[trace, mode])
        page.evaluate('''async ([theme, selectInitial]) => {
            document.documentElement.setAttribute('data-theme', theme);
            await fgSwitchView('timeline');
            if (selectInitial) fgView('timeline').probe().select(0);
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
        }''', [theme, select_initial])
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

    def test_deep_zoom_ruler_labels_are_distinct_inside_the_canvas(self):
        for mode, width, theme in (('single', 1500, 'light'), ('sidecar', 360, 'dark')):
            with self.subTest(mode=mode, width=width):
                page = self.open_report(mode, ratio=1.25, theme=theme)
                page.set_viewport_size({'width': width, 'height': 620})
                facts = page.evaluate('''async () => {
                    await settleResize();
                    const model = fgView('timeline').probe();
                    const canvas = document.querySelector('.tl-head-canvas');
                    const prototype = CanvasRenderingContext2D.prototype, fillText = prototype.fillText;
                    const trackCanvas = document.querySelector('.tl-canvas');
                    const hoverContext = document.querySelector('.tl-hover-time').getContext('2d');
                    const hoverFill = hoverContext.fillText;
                    const ticks = [], hover = [];
                    hoverContext.fillText = function(text, ...args) {
                        hover.push(text);
                        return hoverFill.call(this, text, ...args);
                    };
                    prototype.fillText = function(text, x, y, ...args) {
                        if (y === 36 && (this.canvas === canvas || (!this.canvas.isConnected &&
                            this.canvas.width === canvas.width && this.canvas.height === canvas.height)))
                            ticks.push({text, x, right: x + this.measureText(text).width});
                        return fillText.call(this, text, x, y, ...args);
                    };
                    model.select(-1);
                    const center = Math.floor(Math.max(...model.sEnd) * .73 / 500) * 500;
                    model.zoomTo(center - 750, center + 750, -1);
                    await new Promise(requestAnimationFrame);
                    const rect = trackCanvas.getBoundingClientRect();
                    for (const fraction of [.30, .40]) {
                        trackCanvas.dispatchEvent(new MouseEvent('mousemove', {bubbles:true,
                            clientX:rect.left + rect.width * fraction, clientY:rect.top + 24}));
                        await new Promise(requestAnimationFrame);
                    }
                    prototype.fillText = fillText;
                    return {ticks, hover, width:canvas.clientWidth};
                }''')
                ticks = facts['ticks']
                self.assertGreaterEqual(len(ticks), 2)
                values = [float(label['text'].split()[0]) for label in ticks]
                self.assertEqual(values, sorted(set(values)))
                self.assertEqual(len({label['text'].split()[-1] for label in ticks}), 1)
                self.assertTrue(all(0 <= label['x'] < label['right'] <= facts['width'] for label in ticks), ticks)
                self.assertTrue(all(first['right'] + 5 <= second['x'] for first, second in zip(ticks, ticks[1:])), ticks)
                self.assertEqual(len(facts['hover']), 2)
                self.assertLess(*(float(label.split()[0]) for label in facts['hover']))
                page.context.close()

    def test_slice_selection_reuses_axis_pixels_until_the_view_changes(self):
        for mode, ratio in (('single', 1.25), ('sidecar', 2)):
            with self.subTest(mode=mode, ratio=ratio):
                page = self.open_report(mode, ratio)
                facts = page.evaluate('''async () => {
                    const canvas = document.querySelector('.tl-head-canvas');
                    const prototype = CanvasRenderingContext2D.prototype, fillText = prototype.fillText;
                    const model = fgView('timeline').probe(), initial = canvas.toDataURL();
                    const initialView = model.view();
                    let labels = 0;
                    prototype.fillText = function(text, horizontal, vertical, ...args) {
                        if (vertical === 36 && (this.canvas === canvas || (!this.canvas.isConnected &&
                            this.canvas.width === canvas.width && this.canvas.height === canvas.height))) labels++;
                        return fillText.call(this, text, horizontal, vertical, ...args);
                    };
                    try {
                        model.select(1);
                        await settleResize();
                        const selectionChanged = canvas.toDataURL() !== initial;
                        model.select(0);
                        await settleResize();
                        const selectionLabels = labels, restored = canvas.toDataURL() === initial;
                        const sameView = JSON.stringify(model.view()) === JSON.stringify(initialView);
                        model.zoomTo(model.sStart[1], model.sEnd[1], -1);
                        await settleResize();
                        const zoomLabels = labels - selectionLabels;
                        labels = 0;
                        const light = canvas.toDataURL();
                        document.documentElement.dataset.theme = 'dark';
                        await settleResize();
                        return {selectionLabels, selectionChanged, sameView, restored, zoomLabels,
                            themeLabels: labels, themeChanged: canvas.toDataURL() !== light};
                    } finally { prototype.fillText = fillText; }
                }''')
                self.assertEqual(facts['selectionLabels'], 0, facts)
                self.assertTrue(facts['selectionChanged'] and facts['sameView'] and facts['restored'], facts)
                self.assertGreater(facts['zoomLabels'], 0, facts)
                self.assertGreater(facts['themeLabels'], 0, facts)
                self.assertTrue(facts['themeChanged'], facts)
                page.context.close()

    def test_ruler_status_does_not_cover_the_selected_duration(self):
        page = self.open_report()
        page.set_viewport_size({'width': 360, 'height': 620})
        facts = page.evaluate('''async () => {
            await settleResize();
            const model = fgView('timeline').probe();
            const canvas = document.querySelector('.tl-head-canvas');
            const context = canvas.getContext('2d'), fillText = context.fillText;
            const status = [];
            context.fillText = function(text, x, y, ...args) {
                if (y > 46) {
                    const width = this.measureText(text).width;
                    const left = this.textAlign === 'right' ? x - width : x;
                    status.push({text, left, right: left + width});
                }
                return fillText.call(this, text, x, y, ...args);
            };
            const index = model.sStart.findIndex((start, index) => start > 100000 && model.sEnd[index] - start > 500);
            const start = model.sStart[index], end = model.sEnd[index];
            model.select(index);
            model.zoomTo(start - 10000, end + 2000, -1);
            await new Promise(requestAnimationFrame);
            return {status, width:canvas.clientWidth};
        }''')
        self.assertEqual(len(facts['status']), 2, facts)
        self.assertTrue(any(label['text'].startswith('span ') for label in facts['status']), facts)
        ordered = sorted(facts['status'], key=lambda label: label['left'])
        self.assertGreaterEqual(ordered[0]['left'], 0)
        self.assertLessEqual(ordered[-1]['right'], facts['width'])
        self.assertLess(ordered[0]['right'] + 5, ordered[1]['left'], facts)

    def test_viewport_row_start_preserves_partial_rows_and_exact_edges(self):
        source = Path(helpers.ROOT, 'assets/timeline/render.js').read_text()
        helper = source[source.index('function firstViewportRow('):source.index('function drawTrack(')]
        context = self.browser.new_context()
        self.addCleanup(context.close)
        page = context.new_page()
        differences = page.evaluate('''helper => new Function(helper + `
            const ROW = 17, rows = 64, differences = [];
            for (let ordinal = -5 * ROW; ordinal <= 70 * ROW; ordinal++) {
                for (const offset of [-0.25, 0, 0.25]) {
                    const top = -ordinal + offset;
                    for (const height of [0, 16, 17, 240.5, 533]) {
                        const previous = [], current = [];
                        for (let index = 0; index < rows; index++) {
                            const position = top + index * ROW;
                            if (position <= height && position + ROW >= 0) previous.push(index);
                        }
                        for (let index = firstViewportRow(top); index < rows; index++) {
                            if (top + index * ROW > height) break;
                            current.push(index);
                        }
                        if (previous.join(',') !== current.join(',')) differences.push({top, height});
                    }
                }
            }
            return differences.slice(0, 10);
        `)()''', helper)
        self.assertEqual(differences, [])

    def test_opening_details_keeps_the_clicked_row_visible_without_changing_time(self):
        for mode, touch in product(('single', 'sidecar'), (False, True)):
            with self.subTest(mode=mode, touch=touch):
                page = self.open_report(mode, select_initial=False, trace='deep', touch=touch)
                for position in ('bottom', 'top'):
                    with self.subTest(position=position):
                        point = page.evaluate('''position => {
                            const model = fgView('timeline').probe();
                            const canvas = document.querySelector('.tl-canvas');
                            const scroller = document.querySelector('.tl-scroll');
                            scroller.scrollTop = 0;
                            const bounds = canvas.getBoundingClientRect();
                            const [start, end] = model.view();
                            const first = position === 'bottom' ? bounds.bottom - 140 : bounds.top + 8;
                            const last = position === 'bottom' ? bounds.bottom - 12 : bounds.top + 140;
                            for (let index = 0; index < model.N; index++) {
                                if (model.TRACKS[model.sTask[index]].collapsed ||
                                        (model.sEnd[index] - model.sStart[index]) * bounds.width / (end - start) < 4) continue;
                                const [x, y] = model.xyOf(index);
                                if (y > first && y < last && document.elementFromPoint(x, y) === canvas) {
                                    return {index, x, y, view: [start, end]};
                                }
                            }
                            return null;
                        }''', position)
                        self.assertIsNotNone(point)
                        click_or_tap = page.touchscreen.tap if touch else page.mouse.click
                        click_or_tap(point['x'], point['y'])
                        result = page.evaluate('''async () => {
                            await settleResize();
                            const model = fgView('timeline').probe();
                            const canvas = document.querySelector('.tl-canvas');
                            const scroller = document.querySelector('.tl-scroll');
                            const [x, y] = model.xyOf(model.sel());
                            return {index: model.sel(), view: model.view(), scroll: scroller.scrollTop,
                                visible: document.elementFromPoint(x, y) === canvas, bottom: canvas.getBoundingClientRect().bottom};
                        }''')
                        self.assertEqual(result['index'], point['index'])
                        self.assertEqual(result['view'], point['view'])
                        self.assertTrue(result['visible'], result)
                        if position == 'bottom':
                            self.assertGreater(point['y'], result['bottom'])
                            self.assertGreater(result['scroll'], 0)
                            self.assertLess(result['scroll'], point['y'] - result['bottom'] + 20)
                        else:
                            self.assertEqual(result['scroll'], 0)
                        page.locator('.tl-detail .tl-close').click()
                        page.evaluate('settleResize()')

    def test_tapping_visible_edge_rows_with_details_open_keeps_the_track_position(self):
        for mode, size in product(('single', 'sidecar'), ((480, 800), (844, 390))):
            with self.subTest(mode=mode, size=size):
                page = self.open_report(mode, trace='deep', touch=True, size=size)
                for edge, index in (('top', 24), ('bottom', 40)):
                    with self.subTest(edge=edge):
                        before = page.evaluate('''async ([edge, index]) => {
                            const scroller = document.querySelector('.tl-scroll'), canvas = scroller.querySelector('canvas');
                            const model = fgView('timeline').probe(), bounds = canvas.getBoundingClientRect();
                            const desired = edge === 'top' ? bounds.top + 5 : bounds.bottom - 5;
                            scroller.scrollTop += model.xyOf(index)[1] - desired;
                            await settleResize();
                            const point = model.xyOf(index);
                            return {point, hit:document.elementFromPoint(...point) === canvas,
                                tracks: scroller.scrollTop, page:scrollY, view:model.view()};
                        }''', [edge, index])
                        self.assertTrue(before['hit'], before)
                        self.assertGreater(before['tracks'], 0)
                        page.touchscreen.tap(*before['point'])
                        result = page.evaluate('''async () => {
                            await settleResize();
                            const model = fgView('timeline').probe();
                            return {selected:model.sel(), tracks:document.querySelector('.tl-scroll').scrollTop,
                                page:scrollY, view:model.view()};
                        }''')
                        self.assertEqual(result, dict(selected=index, tracks=before['tracks'],
                                                      page=before['page'], view=before['view']))
                        self.assertTrue(page.locator(f'[data-recorded-call="{index}"]').is_visible())

    def test_scroll_extent_matches_tracks_when_the_detail_split_changes(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                page = self.open_report(mode, 1.25, select_initial=False, trace='deep', size=(1000, 900))
                measure = '''async () => {
                    const scroll = document.querySelector('.tl-scroll');
                    const canvas = scroll.querySelector('canvas');
                    const model = fgView('timeline').probe();
                    const trackHeight = Math.max(...model.TRACKS.map(track => track.y + track.h));
                    scroll.scrollTop = scroll.scrollHeight;
                    await settleResize();
                    return {extent: scroll.scrollTop, expected: Math.max(0, trackHeight - scroll.clientHeight),
                        height: scroll.clientHeight, canvasHeight: canvas.clientHeight,
                        rulerAligned: canvas.clientWidth === document.querySelector('.tl-head-canvas').clientWidth,
                        sticky: canvas.getBoundingClientRect().top === scroll.getBoundingClientRect().top + scroll.clientTop};
                }'''
                before = page.evaluate(measure)
                page.evaluate('fgView("timeline").probe().select(0)')
                opened = page.evaluate(measure)
                page.locator('.tl-close').click()
                closed = page.evaluate(measure)
                page.set_viewport_size({'width': 1000, 'height': 1700})
                tall = page.evaluate(measure)
                self.assertGreater(before['extent'], 0)
                self.assertGreater(opened['extent'], before['extent'])
                self.assertEqual(closed, before)
                self.assertEqual(tall['extent'], 0)
                for state in (before, opened, closed, tall):
                    self.assertEqual(state['canvasHeight'], state['height'], state)
                    self.assertEqual(state['extent'], state['expected'], state)
                    self.assertTrue(state['sticky'] and state['rulerAligned'], state)

    def test_short_touch_landscape_first_call_is_reachable_without_scrolling(self):
        for mode, size in product(('single', 'sidecar'), ((568, 320), (659, 375), (660, 375), (667, 375), (760, 390), (844, 390))):
            with self.subTest(mode=mode, size=size):
                page = self.open_report(mode, select_initial=False, trace='cpus', touch=True, size=size)
                point = page.evaluate('''() => {
                    const model = fgView('timeline').probe(), canvas = document.querySelector('.tl-canvas');
                    const [left, top] = model.xyOf(0);
                    return {left, top, start: scrollY, trackOffset: model.TRACKS[model.sTask[0]].y,
                        toolbar: document.querySelector('.tl-toolbar').offsetHeight,
                        hit: document.elementFromPoint(left, top) === canvas};
                }''')
                self.assertEqual((point['start'], point['trackOffset']), (0, 35), point)
                self.assertLessEqual(point['toolbar'], 60, point)
                self.assertTrue(point['hit'], point)
                page.touchscreen.tap(point['left'], point['top'])
                self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), 0)
                divider = page.get_by_role('separator', name='Resize details')
                self.assertEqual(divider.get_attribute('aria-orientation'), 'vertical' if size[0] >= 660 else 'horizontal')
                page.get_by_role('button', name='Selected call actions').tap()
                page.get_by_role('menuitem', name='Open in Text').wait_for()
                page.context.close()

    def test_narrow_portrait_toolbar_fits_zoom_inline_notes_and_tools_in_two_rows(self):
        for mode, size in product(('single', 'sidecar'), ((320, 640), (390, 844))):
            with self.subTest(mode=mode, size=size):
                page = self.open_report(mode, select_initial=False, trace='cpus', touch=True, size=size)
                page.evaluate('''() => {
                    const inline = document.querySelector('[data-act=inl]');
                    inline.style.removeProperty('display');
                    const notes = document.querySelector('[data-act=notes]');
                    notes.hidden = false;
                    notes.setAttribute('aria-label', 'Notes 2');
                    notes.querySelector('.tl-toolbar-long').textContent = 'Notes 2';
                }''')
                geometry = '''async () => {
                    await settleResize();
                    const primary = document.querySelector('.tl-primary');
                    const buttons = [...primary.querySelectorAll('.tl-quick button, [data-act=max]')];
                    return {height:primary.offsetHeight, overflow:document.documentElement.scrollWidth > innerWidth,
                        allReachable:buttons.every(button => {
                            const box = button.getBoundingClientRect();
                            const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
                            return box.width >= 44 && box.height >= 44 && hit?.closest('button') === button;
                        })};
                }'''
                self.assertEqual(page.evaluate(geometry), dict(height=92, overflow=False, allReachable=True))

                page.get_by_role('button', name='Tools', exact=True).tap()
                page.get_by_role('textbox', name='Filter trace lines', exact=True).fill('func:work')
                page.get_by_role('button', name='Apply', exact=True).tap()
                filtered = page.get_by_role('button', name='Tools · Filter on', exact=True)
                filtered.wait_for()
                page.get_by_role('dialog', name='Timeline tools').press('Escape')
                self.assertEqual(page.evaluate(geometry), dict(height=92, overflow=False, allReachable=True))
                self.assertEqual(filtered.evaluate('button => getComputedStyle(button, "::after").content'), '"●"')

                span = page.evaluate('fgView("timeline").probe().view()')
                page.get_by_role('button', name='Zoom timeline in', exact=True).tap()
                zoomed = page.evaluate('fgView("timeline").probe().view()')
                self.assertLess(zoomed[1] - zoomed[0], span[1] - span[0])
                page.get_by_role('button', name='Fit trace', exact=True).tap()
                self.assertEqual(page.evaluate('fgView("timeline").probe().view()'), span)
                page.context.close()

    def test_initial_landscape_split_uses_final_width_without_measuring_the_mounted_split(self):
        for mode, (size, fraction) in product(('single', 'sidecar'), (
                ((660, 375), 0.5), ((667, 375), 0.95), ((760, 390), 0.05), ((844, 390), 0.5), ((1024, 480), 0.95))):
            with self.subTest(mode=mode, size=size, fraction=fraction):
                page = self.open_report(mode, select_initial=False, trace='deep', touch=True, size=size)
                point = page.evaluate('''fraction => {
                    localStorage.setItem('fgTl.detailWidthFrac', fraction);
                    const host = document.querySelector('.tl-chart-detail-split');
                    const detail = host.querySelector('.tl-detail');
                    const divider = host.querySelector('.tl-resizer');
                    const html = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
                    const width = Object.getOwnPropertyDescriptor(Element.prototype, 'clientWidth').get;
                    window.mountedSplitReads = 0;
                    Object.defineProperty(host, 'clientWidth', {configurable: true, get() {
                        if (host.querySelector('.tl-detail.show')) mountedSplitReads++;
                        return width.call(host);
                    }});
                    Object.defineProperty(detail, 'innerHTML', {configurable: true, get: html.get, set(markup) {
                        if (markup.includes('tl-recorded-call-head')) {
                            window.splitAtMount = [this.offsetWidth, this.childElementCount,
                                divider.getAttribute('aria-orientation')];
                            delete detail.innerHTML;
                        }
                        html.set.call(this, markup);
                    }});
                    return fgView('timeline').probe().xyOf(0);
                }''', fraction)
                page.touchscreen.tap(*point)
                self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), 0)
                self.assertEqual(page.evaluate('mountedSplitReads'), 0)
                state = page.evaluate('''() => {
                    const divider = document.querySelector('.tl-resizer'), host = divider.parentElement;
                    delete host.clientWidth;
                    const actual = host.querySelector('.tl-detail').offsetWidth;
                    return {available:host.clientWidth - divider.offsetWidth, actual,
                        minimum:+divider.getAttribute('aria-valuemin'), maximum:+divider.getAttribute('aria-valuemax'),
                        current:+divider.getAttribute('aria-valuenow'), orientation:divider.getAttribute('aria-orientation')};
                }''')
                available = state['available']
                minimum = min(280, available // 2)
                maximum = max(minimum, available - 320)
                expected = int(max(minimum, min(fraction * available, maximum)) + 0.5)
                self.assertEqual(state, dict(available=available, actual=expected, minimum=minimum,
                                             maximum=maximum, current=expected, orientation='vertical'))
                self.assertEqual(page.evaluate('splitAtMount'), [expected, 0, 'vertical'])
                page.context.close()

    def test_initial_cpu_fit_preserves_manual_expansion_and_other_layouts(self):
        offset = '''() => Math.min(...fgView('timeline').probe().TRACKS.map(track => track.y))'''
        page = self.open_report(select_initial=False, trace='cpus', touch=True, size=(667, 375))
        self.assertEqual(page.evaluate(offset), 35)
        canvas = page.locator('.tl-canvas').bounding_box()
        page.touchscreen.tap(canvas['x'] + 10, canvas['y'] + 10)
        self.assertEqual(page.evaluate(offset), 162)
        for size in ((900, 610), (667, 375)):
            page.set_viewport_size(dict(width=size[0], height=size[1]))
            page.evaluate('settleResize()')
            self.assertEqual(page.evaluate(offset), 162)
        page.get_by_role('button', name='Text', exact=True).tap()
        page.get_by_role('button', name='Timeline', exact=True).tap()
        self.assertEqual(page.evaluate(offset), 162)
        page.context.close()

        for trace, size, touch, expected in (('cpus', (844, 480), True, 162),
                                            ('cpus', (667, 375), False, 162),
                                            ('cpus', (390, 844), True, 162),
                                            ('events', (568, 320), True, 43)):
            with self.subTest(trace=trace, size=size, touch=touch):
                page = self.open_report(select_initial=False, trace=trace, touch=touch, size=size)
                self.assertEqual(page.evaluate(offset), expected)
                if trace == 'events':
                    self.assertEqual(page.evaluate("fgView('timeline').probe().N"), 0)
                page.context.close()

    def test_track_header_metrics_reuse_widths_and_refresh_filter_and_collapse(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                page = self.open_report(mode, trace='cpus', select_initial=False)
                page.evaluate('''() => {
                    const context = document.querySelector('.tl-canvas').getContext('2d');
                    const measure = context.measureText, fill = context.fillText;
                    let measurements = [], labels = [];
                    context.measureText = function(text) {
                        if (/^[▾▸] |^CPU [0-9]+$/.test(text)) measurements.push(text);
                        return measure.call(this, text);
                    };
                    context.fillText = function(text, x, y, ...rest) {
                        labels.push(text);
                        return fill.call(this, text, x, y, ...rest);
                    };
                    window.headerPaint = async () => {
                        measurements = []; labels = [];
                        fgView('timeline').draw();
                        await settleResize();
                        return {measurements, labels};
                    };
                }''')
                unfiltered = page.evaluate('headerPaint()')
                self.assertEqual(unfiltered['measurements'], [])
                self.assertIn('CPU 0', unfiltered['labels'])
                self.assertFalse(any('matched' in label for label in unfiltered['labels']))
                for indexes, expected in [([0], (1, 0)), ([1], (0, 1)), ([0, 1], (1, 1)), ([], (0, 0))]:
                    filtered = page.evaluate('''async indexes => {
                        const lines = fgData.read('tlData').cols.line;
                        fgNotifyFilter('trace selection', new Set(indexes.map(index => lines[index])));
                        return headerPaint();
                    }''', indexes)
                    self.assertEqual(filtered['measurements'], [])
                    for cpu, count in enumerate(expected):
                        self.assertTrue(any(label.startswith(f'CPU {cpu}  ·') and label.endswith(f'{count} matched')
                                            for label in filtered['labels']), filtered)
                cleared = page.evaluate('''async () => { fgNotifyFilter('', null); return headerPaint(); }''')
                self.assertFalse(any('matched' in label for label in cleared['labels']))
                page.locator('.tl-canvas').click(position={'x': 8, 'y': 8})
                collapsed = page.evaluate('headerPaint()')
                self.assertIn('▸ CPUs', collapsed['labels'])
                self.assertTrue(any('expand for CPU lanes' in label for label in collapsed['labels']))
                self.assertNotIn('CPU 0', collapsed['labels'])
                page.context.close()

    def test_track_header_is_skipped_only_when_entirely_above_chart(self):
        page = self.open_report(trace='deep', select_initial=False)
        counts = page.evaluate('''async () => {
            const scroller = document.querySelector('.tl-scroll');
            const context = scroller.querySelector('canvas').getContext('2d'), fill = context.fillText;
            const counts = [];
            let headings;
            context.fillText = function(text, ...args) {
                if (/^[▾▸] /.test(text)) headings.push(text);
                return fill.call(this, text, ...args);
            };
            for (const offset of [0, 19, 20, 200, 0]) {
                headings = [];
                scroller.scrollTop = offset;
                fgView('timeline').draw();
                await settleResize();
                counts.push(headings.length);
            }
            return counts;
        }''')
        self.assertEqual(counts, [1, 1, 0, 0, 1])

    def test_short_touch_landscape_search_and_return_stay_reachable_in_fullscreen(self):
        for size in ((568, 320), (844, 390)):
            with self.subTest(size=size):
                page = self.open_report(select_initial=False, trace='cpus', touch=True, size=size)
                page.get_by_role('button', name='Text', exact=True).tap()
                line = page.evaluate("fgData.read('tlData').cols.line[0]")
                page.locator(f'#L{line} .line-content').tap()
                page.get_by_role('button', name=f'In Timeline · L{line}', exact=True).tap()
                back = page.get_by_role('button', name='Return to Text', exact=True)
                back.wait_for()
                page.evaluate('document.getElementById("timelineView").requestFullscreen = undefined')
                full = page.get_by_role('button', name='Full screen', exact=True)
                self.assertEqual(full.inner_text(), 'Full')
                full.tap()
                page.locator('#timelineView.tl-fullscreen').wait_for()
                search = page.locator('.tl-search')
                search.fill('^work$')
                next_match = page.get_by_role('button', name='Next search match', exact=True)
                page.wait_for_function('!document.querySelector("[data-act=searchnext]").disabled')
                exit_full = page.get_by_role('button', name='Exit full screen', exact=True)
                self.assertEqual(exit_full.inner_text(), 'Exit')
                self.assertEqual(back.inner_text(), '← Text')
                for button in (back, exit_full, next_match):
                    box = button.bounding_box()
                    self.assertGreaterEqual(box['width'], 44, box)
                    self.assertGreaterEqual(box['height'], 44, box)
                    self.assertTrue(button.evaluate('''node => {
                        const {left, top, width, height} = node.getBoundingClientRect();
                        return node.contains(document.elementFromPoint(left + width / 2, top + height / 2));
                    }'''))
                next_match.tap()
                back.tap()
                self.assertEqual(page.evaluate('fgCurrentView()'), 'text')
                self.assertFalse(back.is_visible())
                page.context.close()

    def test_fullscreen_preserves_scrolled_call_after_a_text_jump(self):
        for mode, native in (('single', False), ('sidecar', True)):
            with self.subTest(mode=mode, native=native):
                page = self.open_report(mode, trace='deep', select_initial=False, touch=True, size=(844, 390))
                if not native:
                    page.evaluate('document.getElementById("timelineView").requestFullscreen = undefined')
                page.get_by_role('button', name='Text', exact=True).tap()
                number = page.evaluate('''async () => {
                    const line = fgData.read('tlData').cols.line[40];
                    await fgRequestTextLine(line, row => fgSelectMountedTextLine(row, false));
                    return line;
                }''')
                page.get_by_role('button', name=f'In Timeline · L{number}', exact=True).tap()
                await_state = '''async () => {
                    await settleResize();
                    const model = fgView('timeline').probe(), chart = document.querySelector('.tl-scroll');
                    return {id:model.sel(), scroll:chart.scrollTop, height:chart.clientHeight,
                        hit:document.elementFromPoint(...model.xyOf(model.sel())) === chart.firstElementChild};
                }'''
                before = page.evaluate(await_state)
                self.assertEqual(before['id'], 40)
                self.assertGreater(before['scroll'], 0)
                self.assertTrue(before['hit'], before)
                page.get_by_role('button', name='Full screen', exact=True).tap()
                page.wait_for_function('''native => !!document.fullscreenElement === native &&
                    document.getElementById('timelineView').classList.contains('tl-fullscreen')''', arg=native)
                after = page.evaluate(await_state)
                self.assertEqual(after['id'], before['id'])
                self.assertGreater(after['height'], before['height'])
                self.assertEqual(after['scroll'], before['scroll'])
                self.assertTrue(after['hit'], after)
                page.get_by_role('button', name='Return to Text', exact=True).tap()
                page.wait_for_function('() => fgCurrentView() === "text" && !document.fullscreenElement')
                page.context.close()

    def test_short_touch_landscape_search_keeps_chart_and_zoom_available(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                page = self.open_report(mode, trace='cpus', select_initial=False, touch=True, size=(568, 320))
                toolbar = page.locator('.tl-toolbar')
                canvas = page.locator('.tl-canvas')
                self.assertLessEqual(toolbar.bounding_box()['height'], 60)
                search = page.locator('.tl-search')
                search.fill('^work$')
                page.locator('[data-act=searchnext]:enabled').wait_for()
                self.assertLessEqual(toolbar.bounding_box()['height'], 60)
                next_match = page.get_by_role('button', name='Next search match', exact=True)
                next_match.tap()
                self.assertEqual(page.locator('.tl-count').inner_text().split(), ['1', '/', '8', 'calls'])
                self.assertTrue(canvas.evaluate('''node => {
                    const model = fgView('timeline').probe();
                    return model.sel() === 0 && document.elementFromPoint(...model.xyOf(0)) === node;
                }'''))
                actions = page.get_by_role('button', name='Selected call actions')
                for control in (search, next_match, actions):
                    self.assertTrue(control.evaluate('''node => {
                        const rect = node.getBoundingClientRect();
                        return rect.top >= 0 && rect.bottom <= innerHeight &&
                            node.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
                    }'''))
                self.assertTrue(next_match.evaluate('node => node === document.activeElement'))
                zoom = page.get_by_role('button', name='Zoom timeline in', exact=True, include_hidden=True)
                fit = page.get_by_role('button', name='Fit trace', exact=True, include_hidden=True)
                tools = page.get_by_role('button', name='Tools', exact=True)
                self.assertFalse(zoom.is_visible())
                tools.tap()
                box = zoom.bounding_box()
                self.assertGreaterEqual(min(box['width'], box['height']), 44, box)
                view = "fgView('timeline').probe().view()"
                initial = page.evaluate(view)
                zoom.tap()
                zoomed = page.evaluate(view)
                self.assertLess(zoomed[1] - zoomed[0], initial[1] - initial[0])
                fit.tap()
                self.assertEqual(page.evaluate(view), initial)
                zoom.focus()
                page.set_viewport_size(dict(width=667, height=375))
                page.evaluate('settleResize()')
                self.assertEqual(tools.get_attribute('aria-expanded'), 'false')
                self.assertTrue(zoom.evaluate('node => node.closest(".tl-primary") && node === document.activeElement'))
                page.set_viewport_size(dict(width=568, height=320))
                page.evaluate('settleResize()')
                self.assertFalse(zoom.is_visible())
                self.assertTrue(tools.evaluate('node => node === document.activeElement'))
                self.assertEqual(search.input_value(), '^work$')
                self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), 0)
                page.context.close()

    def test_search_keeps_controls_reachable_when_call_actions_cannot_fit(self):
        page = self.open_report(trace='cpus', select_initial=False, touch=True, size=(480, 240))
        page.locator('.tl-search').fill('^work$')
        next_match = page.get_by_role('button', name='Next search match', exact=True)
        next_match.tap()
        self.assertEqual(page.evaluate('[scrollY, fgView("timeline").probe().sel()]'), [0, 0])
        self.assertTrue(next_match.evaluate('''node => {
            const rect = node.getBoundingClientRect();
            return node === document.activeElement && node.contains(document.elementFromPoint(
                rect.left + rect.width / 2, rect.top + rect.height / 2));
        }'''))

    def test_short_slice_selection_exposes_detail_controls_and_preserves_timeline_return(self):
        for mode, fullscreen in product(('single', 'sidecar'), (False, True)):
            with self.subTest(mode=mode, fullscreen=fullscreen):
                page = self.open_report(mode, select_initial=False, trace='deep', touch=True,
                                        size=(480, 240) if fullscreen else (667, 350))
                if fullscreen:
                    page.evaluate('() => { document.getElementById("timelineView").requestFullscreen = undefined; }')
                    page.get_by_role('button', name='Full screen', exact=True).tap()
                    page.locator('#timelineView.tl-fullscreen').wait_for()
                point = page.evaluate('''async () => {
                    await settleResize();
                    const model = fgView('timeline').probe();
                    const canvas = document.querySelector('.tl-canvas');
                    const [x,y] = model.xyOf(0);
                    return {x,y, hit:document.elementFromPoint(x,y) === canvas, view:model.view(), history:history.length};
                }''')
                self.assertTrue(point['hit'], point)
                page.touchscreen.tap(point['x'], point['y'])
                observed = '''async () => {
                    await settleResize();
                    const model = fgView('timeline').probe();
                    const canvas = document.querySelector('.tl-canvas');
                    const root = document.getElementById('timelineView');
                    const [x,y] = model.xyOf(0);
                    const actions = [...root.querySelectorAll('.tl-call-controls button')].filter(node => node.offsetWidth);
                    const reachable = actions.every(node => {
                        const box = node.getBoundingClientRect();
                        return box.height >= 44 && document.elementFromPoint(box.x + box.width / 2, box.bottom - 2) === node;
                    });
                    return {selected:model.sel(), view:model.view(), hit:document.elementFromPoint(x,y) === canvas,
                        reachable, controls:actions.length, scroll:scrollY, rootScroll:root.scrollTop,
                        stage:root.querySelector('.tl-stage').getBoundingClientRect().height, history:history.length};
                }'''
                before = page.evaluate(observed)
                self.assertEqual(before['selected'], 0, before)
                self.assertEqual(before['view'], point['view'], before)
                self.assertGreaterEqual(before['stage'], 160)
                self.assertEqual(before['controls'], 3)
                self.assertTrue(before['hit'] and before['reachable'], before)
                if fullscreen:
                    self.assertGreater(before['rootScroll'], 0)
                else:
                    self.assertEqual(before['scroll'], 0)
                if not fullscreen:
                    page.get_by_role('button', name='Selected call actions').tap()
                    page.get_by_role('menuitem', name='Open in Text').tap()
                    page.get_by_role('button', name='Return to Timeline', exact=True).tap()
                    self.assertEqual(page.evaluate(observed), before)
                page.context.close()

    def test_text_jump_keeps_the_slice_return_and_detail_controls_in_view(self):
        layouts = (((667, 350), True, True), ((667, 350), False, True),
                   ((480, 240), True, True), ((480, 130), True, False))
        for mode, (size, touch, controls_fit) in product(('single', 'sidecar'), layouts):
            with self.subTest(mode=mode, size=size, touch=touch):
                page = self.open_report(mode, select_initial=False, trace='deep', touch=touch, size=size)
                target = page.evaluate('''async () => {
                    const index = 31;
                    const line = fgData.read('tlData').cols.line[index];
                    await fgSwitchView('text');
                    await fgRequestTextLine(line, row => {
                        fgSelectMountedTextLine(row, false);
                        fgScrollTextLine(row, {block: 'center', behavior: 'instant'});
                    }, {revealFolds: true});
                    return {index, line, scroll: scrollY, history: history.length};
                }''')
                jump = page.get_by_role('button', name=f'In Timeline · L{target["line"]}', exact=True)
                (jump.tap if touch else jump.click)()
                page.wait_for_function('index => fgView("timeline").probe().sel() === index', arg=target['index'])
                observed = page.evaluate('''async index => {
                    await settleResize();
                    const [left, top] = fgView('timeline').probe().xyOf(index);
                    const reached = node => {
                        const rect = node.getBoundingClientRect();
                        return node.contains(document.elementFromPoint(rect.left + rect.width / 2, rect.bottom - 2));
                    };
                    const controls = [...document.querySelectorAll('.tl-call-controls button')].filter(node => node.offsetWidth);
                    return {slice: document.elementFromPoint(left, top)?.matches('.tl-canvas'),
                        back: reached(document.getElementById('fgJumpReturn')),
                        controls: controls.map(reached)};
                }''', target['index'])
                self.assertTrue(observed['slice'] and observed['back'], observed)
                self.assertGreaterEqual(len(observed['controls']), 2)
                self.assertEqual(all(observed['controls']), controls_fit, observed)
                back = page.get_by_role('button', name='Return to Text', exact=True)
                (back.tap if touch else back.click)()
                self.assertEqual(page.evaluate('[fgCurrentView(), scrollY, history.length]'),
                                 ['text', target['scroll'], target['history']])
                self.assertTrue(jump.is_visible())
                page.context.close()

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
                        for frame in ('during', 'after'):
                            self.assert_painted(facts[frame], ratio)
                            for canvas in facts[frame]:
                                self.assertAlmostEqual(canvas['height'], canvas['cssHeight'] * ratio, delta=1, msg=facts)
                        self.assertEqual(facts['during'], facts['after'])
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

    def test_sticky_call_header_reserves_scroll_space_without_affecting_analysis(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                page = self.open_report(mode, trace='deep', select_initial=False)
                page.evaluate('''async () => {
                    fgView('timeline').probe().select(63);
                    await settleResize(); await settleResize();
                }''')
                pane = page.locator('.tl-detail')
                row = pane.locator('[data-context-list=stack] [data-slice="30"]')
                metrics = row.evaluate('''node => {
                    const pane = node.closest('.tl-detail'), head = pane.querySelector('.tl-recorded-call-head');
                    node.scrollIntoView({block: 'start', behavior: 'instant'});
                    return {padding: parseFloat(getComputedStyle(pane).scrollPaddingBlockStart), height: head.offsetHeight,
                        clearance: node.getBoundingClientRect().top - head.getBoundingClientRect().bottom};
                }''')
                self.assertGreater(metrics['height'], 0)
                self.assertEqual(metrics['padding'], metrics['height'])
                self.assertGreaterEqual(metrics['clearance'], -1, metrics)
                divider = page.get_by_role('separator', name='Resize details')
                divider.press('Home')
                self.assertEqual(pane.evaluate('node => getComputedStyle(node).scrollPaddingBlockStart'), 'auto')
                divider.press('End')
                page.evaluate('''() => {
                    const probe = fgView('timeline').probe();
                    probe.range(probe.sStart[0], probe.sEnd[0]);
                }''')
                header = pane.locator('.tl-range-table th').first
                header.wait_for()
                self.assertTrue(header.evaluate('''header => {
                    const pane = header.closest('.tl-detail'), head = pane.querySelector('.tl-detail-head');
                    return getComputedStyle(pane).scrollPaddingBlockStart === 'auto' &&
                        parseFloat(getComputedStyle(header).top) === head.offsetHeight && head.offsetHeight > 0;
                }'''))
                page.context.close()

    def test_timeline_return_keeps_unchanged_pixels_and_repaints_a_hidden_theme_change(self):
        for mode, ratio in (('single', 1), ('sidecar', 2)):
            with self.subTest(mode=mode, ratio=ratio):
                page = self.open_report(mode, ratio)
                result = page.evaluate('''async () => {
                    const canvas = document.querySelector('.tl-canvas'), ruler = document.querySelector('.tl-head-canvas');
                    const context = canvas.getContext('2d'), fill = context.fillRect;
                    const pixels = () => [canvas.toDataURL(), ruler.toDataURL()];
                    const settle = async () => { await settleResize(); await settleResize(); };
                    await settle();
                    let paints = 0;
                    context.fillRect = function(left, top, width, height) {
                        if (!left && !top) {
                            const {a: scaleX, d: scaleY} = this.getTransform();
                            if (Math.abs(width * scaleX - canvas.width) < 1e-6 &&
                                Math.abs(height * scaleY - canvas.height) < 1e-6) paints++;
                        }
                        return fill.apply(this, arguments);
                    };
                    const original = pixels();
                    const expectedSelection = resizeState();
                    const roundTrips = [];
                    for (let attempt = 0; attempt < 3; attempt++) {
                        await fgSwitchView('text'); await settle();
                        const before = paints;
                        await fgSwitchView('timeline'); await settle();
                        roundTrips.push([paints - before, JSON.stringify(pixels()) === JSON.stringify(original)]);
                    }
                    await fgSwitchView('text'); await settle();
                    document.documentElement.setAttribute('data-theme', 'dark');
                    await settle();
                    const beforeTheme = paints;
                    await fgSwitchView('timeline'); await settle();
                    const themePaints = paints - beforeTheme, dark = pixels();
                    const changed = dark.every((image, index) => image !== original[index]);
                    fgView('timeline').draw(); await settle();
                    return {roundTrips, themePaints, changed, selection:resizeState() === expectedSelection,
                        matchesFreshPaint:JSON.stringify(dark) === JSON.stringify(pixels())};
                }''')
                self.assertEqual(result['roundTrips'], [[0, True]] * 3)
                self.assertGreater(result['themePaints'], 0)
                self.assertTrue(result['changed'] and result['matchesFreshPaint'] and result['selection'], result)
                page.context.close()

    def test_toolbar_observer_ignores_unchanged_returns_but_resizes_for_visible_growth(self):
        for mode, touch, size in (('single', False, (1500, 900)), ('sidecar', True, (844, 390))):
            with self.subTest(mode=mode, size=size):
                page = self.open_report(mode, touch=touch, size=size)
                state = page.evaluate('''async () => {
                    const root = document.getElementById('timelineView');
                    const bounds = root.getBoundingClientRect;
                    const settle = async () => { await settleResize(); await settleResize(); };
                    await settle();
                    let reads = 0;
                    root.getBoundingClientRect = function() { reads++; return bounds.apply(this, arguments); };
                    const returns = [];
                    for (let attempt = 0; attempt < 3; attempt++) {
                        await fgSwitchView('text'); await settle();
                        const previous = reads;
                        await fgSwitchView('timeline'); await settle();
                        returns.push(reads - previous);
                    }
                    const toolbar = root.querySelector('.tl-toolbar');
                    const chart = root.querySelector('.tl-scroll');
                    const canvas = root.querySelector('.tl-canvas');
                    const addition = document.createElement('div');
                    addition.style.cssText = 'flex: 0 0 100%; height: 48px';
                    const original = {toolbar: toolbar.offsetHeight, chart: chart.clientHeight};
                    const previous = reads;
                    toolbar.append(addition);
                    await settle();
                    const grown = {toolbar: toolbar.offsetHeight, chart: chart.clientHeight, canvas: canvas.height,
                        reads: reads - previous};
                    addition.remove();
                    await settle();
                    const restored = chart.clientHeight === original.chart;
                    await fgSwitchView('text'); await settle();
                    toolbar.append(addition);
                    await settle();
                    await fgSwitchView('timeline'); await settle();
                    const grownWhileHidden = {toolbar: toolbar.offsetHeight, chart: chart.clientHeight,
                        canvas: canvas.height};
                    return {returns, original, grown, restored, grownWhileHidden};
                }''')
                self.assertEqual(state['returns'], [1, 1, 1])
                self.assertGreater(state['grown']['reads'], 0)
                self.assertGreater(state['grown']['toolbar'], state['original']['toolbar'])
                self.assertLess(state['grown']['chart'], state['original']['chart'])
                self.assertEqual(state['grown']['canvas'], state['grown']['chart'])
                self.assertTrue(state['restored'])
                self.assertEqual(state['grownWhileHidden'], {
                    key: state['grown'][key] for key in ('toolbar', 'chart', 'canvas')})
                page.context.close()

    def test_detail_height_changes_skip_ruler_paints_but_width_changes_repaint(self):
        for mode, ratio in [('single', 1), ('sidecar', 1.25), ('single', 2)]:
            with self.subTest(mode=mode, ratio=ratio):
                page = self.open_report(mode, ratio)
                facts = page.evaluate('''async () => {
                    const tracks = document.querySelector('.tl-canvas'), ruler = document.querySelector('.tl-head-canvas');
                    const context = ruler.getContext('2d'), copy = context.drawImage;
                    window.rulerPaints = 0;
                    context.drawImage = function(...args) { rulerPaints++; return copy.apply(this, args); };
                    window.rulerBeforeResize = ruler.toDataURL();
                    const heights = [tracks.height], state = resizeState();
                    const handle = document.querySelector('.tl-resizer');
                    for (let step = 0; step < 3; step++) {
                        handle.dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowUp', bubbles:true}));
                        await settleResize();
                        heights.push(tracks.height);
                    }
                    return {heights, paints:rulerPaints, sameRuler:ruler.toDataURL() === rulerBeforeResize,
                        sameData:resizeState() === state};
                }''')
                self.assertEqual(facts['heights'], sorted(set(facts['heights']), reverse=True))
                self.assertEqual(facts['paints'], 0, facts)
                self.assertTrue(facts['sameRuler'] and facts['sameData'], facts)
                page.set_viewport_size({'width': 1281, 'height': 900})
                result = page.evaluate('''async () => {
                    await settleResize();
                    return {paints:rulerPaints, changed:document.querySelector('.tl-head-canvas').toDataURL() !== rulerBeforeResize};
                }''')
                self.assertGreater(result['paints'], 0)
                self.assertTrue(result['changed'])
                self.assert_painted(page.evaluate('resizePixels()'), ratio)
                page.context.close()

    def test_track_font_metrics_initialize_and_survive_backing_store_resizes(self):
        source = Path(helpers.ROOT, 'assets/timeline/render.js').read_text()
        setup = source[:source.index('const CTX_HUE')]
        sizing = source[source.index('function syncCanvasSize('):source.index('const visibleTracks')]
        context = self.browser.new_context()
        self.addCleanup(context.close)
        page = context.new_page()
        facts = page.evaluate('''source => {
            const main = document.createElement('canvas'), head = document.createElement('canvas');
            const hoverTime = document.createElement('canvas');
            return new Function('elCv','elHead','elHoverTime',source + `
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
                const rulerChanged = [syncCanvasSizes(), syncCanvasSizes()];
                const initial = {measurements,writes,rulerChanged,labelWidth:charW,
                    expected:measure('abcdefghijklmnopqrstuvwxyz_0123').width / 31};
                H += 20;
                rulerChanged.push(syncCanvasSizes());
                W -= 10;
                rulerChanged.push(syncCanvasSizes());
                const resized = {measurements,writes,keptWidth:charW === initial.labelWidth};
                DPR = 2;
                rulerChanged.push(syncCanvasSizes());
                return {initial,resized,measurements,writes,labelWidth:charW,
                    expected:measure('abcdefghijklmnopqrstuvwxyz_0123').width / 31};
            `)(main,head,hoverTime);
        }''', setup + sizing)
        self.assertEqual(facts['initial']['writes'], 0)
        self.assertEqual(facts['initial']['measurements'], 1)
        self.assertGreater(facts['initial']['labelWidth'], 0)
        self.assertEqual(facts['initial']['labelWidth'], facts['initial']['expected'])
        self.assertEqual(facts['initial']['rulerChanged'], [False, False, False, True, True])
        self.assertEqual(facts['resized'], {'measurements': 1, 'writes': 3, 'keptWidth': True})
        self.assertEqual(facts['measurements'], 2)
        self.assertEqual(facts['writes'], 7)
        self.assertGreater(facts['labelWidth'], 0)
        self.assertEqual(facts['labelWidth'], facts['expected'])

    def test_track_transform_reuses_scale_and_recovers_after_context_reset(self):
        for ratio, theme in ((1, 'light'), (1.25, 'dark'), (2, 'light')):
            with self.subTest(ratio=ratio, theme=theme):
                page = self.open_report(ratio=ratio, theme=theme)
                result = page.evaluate('''async () => {
                    const canvas = document.querySelector('.tl-canvas'), context = canvas.getContext('2d');
                    const state = resizeState(), pixels = canvas.toDataURL();
                    const setTransform = context.setTransform.bind(context);
                    const calls = [];
                    context.setTransform = (...args) => { calls.push(args); setTransform(...args); };
                    fgView('timeline').draw();
                    await settleResize();
                    const warm = {transforms: calls.length, samePixels: canvas.toDataURL() === pixels};
                    canvas.width = canvas.width;
                    const resetAlpha = context.getImageData(0, 0, 1, 1).data[3];
                    const dimensions = [canvas.width, canvas.height];
                    canvas.dispatchEvent(new Event('contextrestored'));
                    await settleResize();
                    return {warm, calls, resetAlpha, restoredPixels: canvas.toDataURL() === pixels,
                        sameDimensions: dimensions[0] === canvas.width && dimensions[1] === canvas.height,
                        sameSelection: resizeState() === state};
                }''')
                self.assertEqual(result, dict(warm=dict(transforms=0, samePixels=True),
                    calls=[[ratio, 0, 0, ratio, 0, 0]], resetAlpha=0, restoredPixels=True,
                    sameDimensions=True, sameSelection=True))
                page.context.close()

    def test_ruler_recovers_pixels_after_visible_or_backdrop_context_reset(self):
        for mode, ratio, theme in (('single', 1, 'light'), ('sidecar', 1.25, 'dark'), ('single', 2, 'light')):
            with self.subTest(mode=mode, ratio=ratio, theme=theme):
                page = self.open_report(mode=mode, ratio=ratio, theme=theme)
                facts = page.evaluate('''async () => {
                    const timeline = fgView('timeline');
                    const ruler = document.querySelector('.tl-head-canvas');
                    const tracks = document.querySelector('.tl-canvas').getContext('2d');
                    const context = ruler.getContext('2d');
                    const drawImage = context.drawImage, fillRect = tracks.fillRect;
                    let backdrop, copies = 0, trackPaints = 0;
                    context.drawImage = function(layer, ...args) {
                        backdrop = layer;
                        copies++;
                        return drawImage.call(this, layer, ...args);
                    };
                    tracks.fillRect = function(...args) {
                        trackPaints++;
                        return fillRect.apply(this, args);
                    };
                    timeline.draw();
                    await settleResize();
                    const initial = ruler.toDataURL(), state = resizeState();
                    const results = [];
                    for (const canvas of [ruler, backdrop]) {
                        copies = trackPaints = 0;
                        const width = canvas.width, height = canvas.height;
                        canvas.width = width;
                        const blank = canvas.getContext('2d').getImageData(0, 0, 1, 1).data[3] === 0;
                        canvas.dispatchEvent(new Event('contextrestored'));
                        await settleResize();
                        results.push({blank, copies, trackPaints, recovered: ruler.toDataURL() === initial,
                            sameDimensions: canvas.width === width && canvas.height === height});
                    }
                    return {results, sameState: resizeState() === state};
                }''')
                expected = dict(blank=True, copies=1, trackPaints=0, recovered=True, sameDimensions=True)
                self.assertEqual(facts, dict(results=[expected, expected], sameState=True))
                page.context.close()

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

    def test_burst_previews_latest_split_and_only_persists_on_release(self):
        page = self.open_report()
        handle = page.locator('.tl-resizer')
        box = handle.bounding_box()
        start = box['y'] + box['height'] / 2
        page.mouse.move(box['x'] + box['width'] / 2, start)
        page.mouse.down()
        preview = handle.evaluate('''async (handle, start) => {
            const toolbar = document.querySelector('.tl-toolbar');
            const before = +handle.getAttribute('aria-valuenow');
            const offsetHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight');
            const setItem = Storage.prototype.setItem;
            window.detailWrites = [];
            Storage.prototype.setItem = function(key, value) {
                if (key === 'fgTl.detailFrac') detailWrites.push(value);
                return setItem.call(this, key, value);
            };
            let measured = 0;
            Object.defineProperty(toolbar, 'offsetHeight', {configurable:true, get() {
                measured++;
                return offsetHeight.get.call(this);
            }});
            try {
                for (let offset = 1; offset <= 120; offset++) {
                    handle.dispatchEvent(new PointerEvent('pointermove', {pointerId:1, clientY:start - offset/3}));
                }
                handle.dispatchEvent(new PointerEvent('pointerup', {pointerId:2}));
                handle.dispatchEvent(new PointerEvent('pointermove', {pointerId:2, clientY:start + 200}));
                const burst = [measured, detailWrites.length, +handle.getAttribute('aria-valuenow')];
                await settleResize();
                return {before, burst, preview:[measured, detailWrites.length, +handle.getAttribute('aria-valuenow')],
                    active:handle.classList.contains('drag'), pixels:resizePixels()};
            } finally { delete toolbar.offsetHeight; }
        }''', start)
        before = preview['before']
        self.assertEqual(preview['burst'], [0, 0, before])
        self.assertEqual(preview['preview'], [1, 0, before + 40])
        self.assertTrue(preview['active'])
        self.assert_painted(preview['pixels'], 1)
        page.mouse.up()
        self.assertEqual(page.evaluate('detailWrites.length'), 1)
        self.assertEqual(page.evaluate('detailWrites[0]'), page.evaluate('localStorage.getItem("fgTl.detailFrac")'))
        page.reload()
        page.evaluate('''async () => {
            await fgSwitchView('timeline');
            fgView('timeline').probe().select(0);
        }''')
        self.assertEqual(int(handle.get_attribute('aria-valuenow')), before + 40)

    def test_vertical_split_limits_are_stable_when_notes_shrink(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                page = self.open_report(mode, select_initial=False, size=(1000, 650))
                page.evaluate('''async () => {
                    const notes = document.querySelector('.tl-notes');
                    for (let index = 0; index < 12; index++) {
                        const note = document.createElement('div');
                        note.className = 'tl-note';
                        note.style.flexBasis = '100%';
                        note.textContent = 'Trace capability note ' + index;
                        notes.append(note);
                    }
                    if (notes.classList.contains('hide')) document.querySelector('[data-act=notes]').click();
                    localStorage.setItem('fgTl.detailFrac', '0.85');
                    await settleResize();
                    fgView('timeline').probe().select(0);
                    await settleResize();
                }''')
                measure = '''async () => {
                    await settleResize();
                    const handle = document.querySelector('.tl-resizer');
                    const notes = document.querySelector('.tl-notes');
                    const detail = document.querySelector('.tl-detail');
                    return {max: +handle.getAttribute('aria-valuemax'), now: +handle.getAttribute('aria-valuenow'),
                        height: detail.offsetHeight, notes: notes.offsetHeight, noteContent: notes.scrollHeight,
                        stage: document.querySelector('.tl-stage').offsetHeight, bottom: detail.getBoundingClientRect().bottom};
                }'''
                handle = page.locator('.tl-resizer')
                initial = page.evaluate(measure)
                self.assertEqual(initial['now'], initial['max'])
                handle.press('Home')
                minimum = page.evaluate(measure)
                self.assertEqual(minimum['height'], 60)
                self.assertGreater(minimum['notes'], initial['notes'])
                self.assertEqual(minimum['max'], initial['max'])
                handle.press('End')
                maximum = page.evaluate(measure)
                self.assertEqual(maximum, initial)
                self.assertEqual(maximum['now'], maximum['height'])
                self.assertGreater(maximum['noteContent'], maximum['notes'])
                self.assertGreaterEqual(maximum['stage'], 160)
                self.assertLessEqual(maximum['bottom'], page.viewport_size['height'])
                page.locator('[data-act=notes]').click()
                handle.press('End')
                without_notes = page.evaluate(measure)
                self.assertEqual(without_notes['max'], maximum['max'] + maximum['notes'])
                self.assertEqual(without_notes['height'], without_notes['max'])
                self.assertLessEqual(without_notes['bottom'], page.viewport_size['height'])

    def test_vertical_drag_does_not_paint_when_clamped_to_either_limit(self):
        page = self.open_report()
        handle = page.get_by_role('separator', name='Resize details')
        start_height = int(handle.get_attribute('aria-valuenow'))
        minimum, maximum = (int(handle.get_attribute('aria-value' + name)) for name in ('min', 'max'))
        original = page.evaluate('resizeState()')
        page.evaluate('''() => {
            const chart = document.querySelector('.tl-canvas'), context = chart.getContext('2d');
            const fill = context.fillRect;
            window.splitPaints = 0;
            context.fillRect = function(left, top, width, height) {
                if (left === 0 && top === 0 && width === chart.width && height === chart.height) splitPaints++;
                return fill.apply(this, arguments);
            };
        }''')
        bounds = handle.bounding_box()
        pointer_x, pointer_y = bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2
        page.mouse.move(pointer_x, pointer_y)
        page.mouse.down()

        def move(offset):
            page.mouse.move(pointer_x, pointer_y + offset)
            page.evaluate('settleResize()')

        for limit, direction in ((minimum, 1), (maximum, -1)):
            with self.subTest(limit=limit):
                move(start_height - limit + direction * 8)
                self.assertEqual(int(handle.get_attribute('aria-valuenow')), limit)
                paints = page.evaluate('splitPaints')
                for excess in (10, 14, 18):
                    move(start_height - limit + direction * excess)
                self.assertEqual(page.evaluate('splitPaints'), paints)
                self.assertIsNone(page.evaluate("localStorage.getItem('fgTl.detailFrac')"))
                self.assert_painted(page.evaluate('resizePixels()'), 1)

        paints = page.evaluate('splitPaints')
        move(40)
        self.assertEqual(int(handle.get_attribute('aria-valuenow')), start_height - 40)
        self.assertGreater(page.evaluate('splitPaints'), paints)
        page.mouse.up()
        self.assertAlmostEqual(float(page.evaluate("localStorage.getItem('fgTl.detailFrac')")), (start_height - 40) / 900, places=4)
        self.assertEqual(page.evaluate('resizeState()'), original)
        self.assert_painted(page.evaluate('resizePixels()'), 1)

    def test_landscape_drag_does_not_paint_when_clamped_to_either_limit(self):
        page = self.open_report('sidecar', touch=True, size=(844, 390))
        handle = page.get_by_role('separator', name='Resize details')
        self.assertEqual(handle.get_attribute('aria-orientation'), 'vertical')
        start_width = int(handle.get_attribute('aria-valuenow'))
        minimum, maximum = (int(handle.get_attribute('aria-value' + name)) for name in ('min', 'max'))
        original = page.evaluate('resizeState()')
        page.evaluate('''() => {
            const chart = document.querySelector('.tl-canvas'), context = chart.getContext('2d');
            const fill = context.fillRect;
            window.splitPaints = 0;
            context.fillRect = function(left, top, width, height) {
                if (left === 0 && top === 0 && width === chart.width && height === chart.height) splitPaints++;
                return fill.apply(this, arguments);
            };
        }''')
        bounds = handle.bounding_box()
        touch_x, touch_y = bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2
        touch = page.context.new_cdp_session(page)

        def move(offset):
            touch.send('Input.dispatchTouchEvent', {'type': 'touchMove',
                                                   'touchPoints': [{'x': touch_x + offset, 'y': touch_y}]})
            page.evaluate('settleResize()')

        touch.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': touch_x, 'y': touch_y}]})
        for limit, direction in ((minimum, 1), (maximum, -1)):
            with self.subTest(limit=limit):
                move(start_width - limit + direction * 20)
                self.assertEqual(int(handle.get_attribute('aria-valuenow')), limit)
                paints = page.evaluate('splitPaints')
                for excess in (25, 35, 45):
                    move(start_width - limit + direction * excess)
                self.assertEqual(page.evaluate('splitPaints'), paints)
                self.assertIsNone(page.evaluate("localStorage.getItem('fgTl.detailWidthFrac')"))
                self.assert_painted(page.evaluate('resizePixels()'), 1)
        paints = page.evaluate('splitPaints')
        move(40)
        self.assertEqual(int(handle.get_attribute('aria-valuenow')), start_width - 40)
        self.assertGreater(page.evaluate('splitPaints'), paints)
        touch.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
        self.assertEqual(page.evaluate('resizeState()'), original)
        self.assert_painted(page.evaluate('resizePixels()'), 1)

    def test_switching_views_discards_pending_split_preview(self):
        page = self.open_report('sidecar')
        handle = page.locator('.tl-resizer')
        before = handle.get_attribute('aria-valuenow')
        box = handle.bounding_box()
        start = box['y'] + box['height'] / 2
        page.mouse.move(box['x'] + box['width'] / 2, start)
        page.mouse.down()
        handle.evaluate('''async (handle, start) => {
            handle.dispatchEvent(new PointerEvent('pointermove', {pointerId:1, clientY:start - 80}));
            await fgSwitchView('text');
        }''', start)
        page.mouse.up()
        page.evaluate('''async () => { await fgSwitchView('timeline'); await settleResize(); }''')
        self.assertEqual(handle.get_attribute('aria-valuenow'), before)
        self.assertIsNone(page.evaluate('localStorage.getItem("fgTl.detailFrac")'))
        self.assertFalse(page.evaluate('document.body.classList.contains("tl-resizing")'))
        self.assert_painted(page.evaluate('resizePixels()'), 1)

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
