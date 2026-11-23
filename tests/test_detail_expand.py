"""Short Timeline panes can borrow chart space without changing saved splits."""
from pathlib import Path
import threading
import unittest

import helpers
from fglib.viewer import create_server
from test_uftrace_import import call, lifecycle_event, stream


class DetailExpandTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise unittest.SkipTest(str(error))
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.kernel = helpers.FakeKernel()
        self.addCleanup(self.kernel.close)
        self.serial = 0
        self.errors = []
        self.context = self.browser.new_context(viewport={'width': 1500, 'height': 900})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open_report(self, mode='single', with_calls=False, count=205, no_text=True, function_name='work'):
        self.serial += 1
        records = []
        for index in range(count):
            event = lifecycle_event('exec', index * 1000)
            event.update(tid=index + 1, text=f'filename=/bin/task pid={index + 1}')
            records.append(event)
            if with_calls:
                records.extend([call('entry', 'user', index * 1000 + 20, 0, function_name, 1, tid=index + 1),
                                call('exit', 'user', index * 1000 + 80, 0, function_name, 1, tid=index + 1)])
        trace = Path(self.kernel.dir) / f'capture-{self.serial}.jsonl'
        trace.write_text(stream(records), encoding='utf-8')
        destination = f'report-{self.serial}' + ('.html' if mode == 'single' else '')
        options = ['--no-config', '--timeline', '--report-mode', mode]
        if no_text:
            options.append('--no-text-view')
        self.kernel.run(str(trace), *options, out=destination)
        report = Path(self.kernel.dir) / destination
        if mode == 'sidecar':
            server = create_server(report)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 5)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            url = server.url
        else:
            url = report.as_uri()
        self.page.goto(url)
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.action('an-tasks')

    def action(self, name):
        button = self.page.locator(f'[data-act="{name}"]')
        if not button.is_visible():
            self.page.locator('[data-act=options]').click()
        button.click()

    def test_text_jump_reveals_call_in_the_final_chart_height(self):
        self.open_report(with_calls=True, count=36, no_text=False)
        target = self.page.evaluate('''async () => {
            const scroll = document.querySelector('.tl-scroll');
            const model = fgView('timeline').probe();
            const heightWithDetails = scroll.clientHeight;
            model.select(-1);
            scroll.scrollTop = 0;
            const {top} = scroll.getBoundingClientRect();
            const index = Array.from({length: model.N}, (unused, i) => i).find(index => {
                const y = model.xyOf(index)[1] - top;
                return y > heightWithDetails + 17 && y < scroll.clientHeight - 17;
            });
            if (index === undefined) throw new Error('Fixture needs a call below the shortened chart');
            const line = fgData.read('tlData').cols.line[index];
            await fgSwitchView('text');
            await fgRequestTextLine(line, row => fgSelectMountedTextLine(row, false));
            return {index, line};
        }''')
        self.page.get_by_role('button', name=f'In Timeline · L{target["line"]}', exact=True).click()
        self.page.wait_for_function('index => fgView("timeline").probe().sel() === index', arg=target['index'])
        self.assertTrue(self.page.evaluate('''index => {
            const {top, bottom} = document.querySelector('.tl-scroll').getBoundingClientRect();
            const y = fgView('timeline').probe().xyOf(index)[1];
            return y > top && y < bottom;
        }''', target['index']))

    def test_opening_details_updates_the_split_without_measuring_the_page_again(self):
        for mode, width in (('single', 1440), ('sidecar', 480)):
            with self.subTest(mode=mode, width=width):
                self.page.set_viewport_size({'width': width, 'height': 800})
                self.open_report(mode=mode, with_calls=True, count=8)
                facts = self.page.evaluate('''async () => {
                    const model = fgView('timeline').probe();
                    const root = document.getElementById('timelineView');
                    const pane = document.getElementById('tlDetail');
                    const canvas = root.querySelector('.tl-canvas');
                    const scroll = root.querySelector('.tl-scroll');
                    const settle = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    model.select(-1);
                    localStorage.setItem('fgTl.detailFrac', '0.2');
                    await settle();
                    const before = {root: root.clientHeight, tracks: scroll.clientHeight};
                    let reads = 0;
                    root.getBoundingClientRect = () => {
                        reads++;
                        return Element.prototype.getBoundingClientRect.call(root);
                    };
                    try { model.select(0); } finally { delete root.getBoundingClientRect; }
                    await settle();
                    return {reads, before, root: root.clientHeight, tracks: scroll.clientHeight,
                            selected: model.sel(), pane: pane.offsetHeight, painted: canvas.height,
                            separator: +root.querySelector('.tl-resizer').getAttribute('aria-valuenow')};
                }''')
                self.assertEqual(facts['reads'], 0)
                self.assertEqual(facts['root'], facts['before']['root'])
                self.assertLess(facts['tracks'], facts['before']['tracks'])
                self.assertEqual(facts['painted'], facts['tracks'])
                self.assertEqual(facts['selected'], 0)
                self.assertEqual(facts['pane'], 160)
                self.assertEqual(facts['separator'], facts['pane'])
                self.page.set_viewport_size({'width': width, 'height': 840})
                self.page.wait_for_function('height => document.getElementById("timelineView").clientHeight > height',
                                            arg=facts['root'])
                self.assertEqual(self.page.locator('#tlDetail').evaluate('pane => pane.offsetHeight'), 168)

    def test_replacing_calls_reuses_the_split_without_sync_height_reads(self):
        for mode, observer in (('single', True), ('sidecar', True), ('single', False)):
            with self.subTest(mode=mode, observer=observer):
                if not observer:
                    self.context.add_init_script('window.ResizeObserver = undefined;')
                self.open_report(mode=mode, with_calls=True, count=8)
                facts = self.page.evaluate('''async observer => {
                    const model = fgView('timeline').probe();
                    const pane = document.getElementById('tlDetail');
                    const nativeHeight = Object.getOwnPropertyDescriptor(Element.prototype, 'clientHeight').get;
                    const selection = () => +pane.firstElementChild.dataset.recordedCall;
                    const isShort = () => pane.classList.contains('tl-detail-short');
                    const settle = async () => {
                        await new Promise(requestAnimationFrame);
                        await new Promise(requestAnimationFrame);
                    };
                    model.select(0);
                    await settle();
                    let reads = 0;
                    Object.defineProperty(pane, 'clientHeight', {configurable: true, get() {
                        reads++;
                        return nativeHeight.call(this);
                    }});
                    try {
                        const calls = [1, 2, 3].map(index => { model.select(index); return selection(); });
                        const syncReads = reads, initial = isShort();
                        if (observer) {
                            pane.style.height = '120px';
                            await settle();
                            const observedShort = isShort();
                            pane.style.height = '190px';
                            await settle();
                            return {calls, syncReads, initial, observedShort, restored: !isShort()};
                        }
                        return {calls, syncReads, initial};
                    } finally { delete pane.clientHeight; }
                }''', observer)
                self.assertEqual(facts['calls'], [1, 2, 3])
                self.assertFalse(facts['initial'])
                if observer:
                    self.assertEqual(facts['syncReads'], 0)
                    self.assertTrue(facts['observedShort'])
                    self.assertTrue(facts['restored'])
                else:
                    self.assertGreaterEqual(facts['syncReads'], 3)

    def shorten(self):
        self.page.evaluate("() => { localStorage.setItem('fgTl.detailFrac', '0.1'); fgView('timeline').resize(); }")

    def button(self):
        return self.page.locator('[data-detail-expand]')

    def facts(self):
        return self.page.evaluate("""() => {
            const model = fgView('timeline').probe();
            return {range: model.view(), selection: model.sel(), split: localStorage.getItem('fgTl.detailFrac'),
                    tasks: model.TRACKS.length, calls: model.N};
        }""")

    def assert_cell_readable(self, cell):
        self.assertTrue(cell.evaluate('''node => {
            const bounds = node.getBoundingClientRect();
            return node.contains(document.elementFromPoint(bounds.left + 4, bounds.top + 4));
        }'''))

    def test_keyboard_expansion_preserves_manual_split_and_selection(self):
        self.open_report(with_calls=True)
        self.assertFalse(self.button().is_visible())
        self.page.evaluate("fgView('timeline').probe().select(0)")
        self.shorten()
        before = self.facts()
        height = self.page.locator('.tl-detail').bounding_box()['height']
        self.button().press('Enter')
        self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
        self.assertFalse(self.page.locator('.tl-scroll').is_visible())
        self.assertGreater(self.page.locator('.tl-detail').bounding_box()['height'], height)
        self.assertEqual(self.facts(), before)
        self.button().press('Space')
        self.assertEqual(self.button().get_attribute('aria-pressed'), 'false')
        self.assertTrue(self.page.locator('.tl-scroll').is_visible())
        self.assertAlmostEqual(self.page.locator('.tl-detail').bounding_box()['height'], height, delta=1)
        self.assertEqual(self.facts(), before)
        self.assertTrue(self.button().evaluate('(element) => element === document.activeElement'))

    def test_repeated_call_selection_observes_sticky_inset_without_sync_style_reads(self):
        self.open_report(with_calls=True, count=8, no_text=False)
        facts = self.page.evaluate('''async () => {
            const pane = document.getElementById('tlDetail'), model = fgView('timeline').probe();
            const computedStyle = window.getComputedStyle;
            let reads = 0;
            window.getComputedStyle = (node, pseudo) => {
                if (node.classList.contains('tl-detail-head')) reads++;
                return computedStyle(node, pseudo);
            };
            const afterPaint = async () => {
                await new Promise(requestAnimationFrame);
                await new Promise(requestAnimationFrame);
            };
            try {
                const selections = [];
                for (const index of [0, 1, 3, 2, 5]) {
                    model.select(index);
                    await afterPaint();
                    const head = pane.firstElementChild;
                    selections.push([+head.dataset.recordedCall, head.offsetHeight > 0,
                        computedStyle(pane).scrollPaddingBlockStart === head.offsetHeight + 'px']);
                }
                return {reads, selections};
            } finally { window.getComputedStyle = computedStyle; }
        }''')
        self.assertEqual(facts, dict(reads=0, selections=[[number, True, True] for number in [0, 1, 3, 2, 5]]))
        for width, sticky in ((640, False), (1500, True)):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 900})
                self.page.wait_for_function('''sticky => {
                    const pane = document.getElementById('tlDetail'), head = pane.firstElementChild;
                    return head.offsetHeight > 0 && getComputedStyle(pane).scrollPaddingBlockStart ===
                        (sticky ? head.offsetHeight + 'px' : '0px');
                }''', arg=sticky)

    def test_reselecting_the_same_canvas_call_keeps_its_detail_until_a_real_change(self):
        self.open_report(with_calls=True, count=8, no_text=False)
        point = self.page.evaluate('() => fgView("timeline").probe().xyOf(0)')
        self.page.mouse.click(*point)
        self.page.evaluate('''() => {
            window.preservedCallHead = document.querySelector('[data-recorded-call="0"]');
            if (!preservedCallHead) throw new Error('First canvas call was not selected');
        }''')
        self.page.mouse.click(*point)
        self.assertTrue(self.page.evaluate('() => preservedCallHead.isConnected'))
        self.action('an-tasks')
        self.assertFalse(self.page.evaluate('() => preservedCallHead.isConnected'))
        self.page.mouse.click(*point)
        self.page.locator('[data-recorded-call="0"]').wait_for()
        other_point = self.page.evaluate('() => fgView("timeline").probe().xyOf(1)')
        self.page.mouse.click(*other_point)
        self.page.locator('[data-recorded-call="1"]').wait_for()
        before = self.page.evaluate('() => fgView("timeline").probe().view()')
        self.page.mouse.dblclick(*other_point)
        self.assertNotEqual(self.page.evaluate('() => fgView("timeline").probe().view()'), before)
        self.page.locator('[data-recorded-call="1"]').wait_for()

    def test_text_jump_back_to_selected_slice_keeps_the_open_stack(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.page.set_viewport_size({'width': 600, 'height': 850})
                self.open_report(mode=mode, with_calls=True, count=8, no_text=False)
                line = self.page.evaluate('''() => {
                    fgView('timeline').probe().select(0);
                    const stack = document.querySelector('[data-stack-toggle]');
                    stack.click();
                    window.selectedCallPane = document.querySelector('#tlDetail').firstElementChild;
                    window.openStack = document.getElementById('tlStackBody');
                    return fgData.read('tlData').cols.line[0];
                }''')
                self.page.locator('.tl-canvas').press('Enter')
                self.page.get_by_role('button', name=f'In Timeline · L{line}', exact=True).click()
                self.page.get_by_role('button', name='Return to Text', exact=True).wait_for()
                self.page.locator('[data-recorded-call="0"]').wait_for()
                self.assertEqual(self.page.evaluate('''() => [
                    selectedCallPane === document.querySelector('#tlDetail').firstElementChild,
                    openStack === document.getElementById('tlStackBody'),
                    document.querySelector('[data-stack-toggle]').getAttribute('aria-expanded')
                ]'''), [True, True, 'true'])
                self.page.get_by_role('button', name='Return to Text', exact=True).click()
                self.assertEqual(self.page.evaluate('fgCurrentView()'), 'text')

    def test_detail_scroll_resets_only_when_opening_a_different_call_view(self):
        for mode in ('single', 'sidecar'):
            for width, height, touch in ((844, 390, True), (1500, 900, False)):
                with self.subTest(mode=mode, width=width):
                    context = self.browser.new_context(viewport={'width': width, 'height': height},
                                                       is_mobile=touch, has_touch=touch)
                    self.addCleanup(context.close)
                    self.page = context.new_page()
                    self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                    self.open_report(mode=mode, with_calls=True, count=8, no_text=False)
                    self.page.evaluate('''() => {
                        fgView('timeline').probe().select(-1);
                        localStorage.setItem('fgTl.detailFrac', '0.19');
                        fgView('timeline').resize();
                    }''')
                    pane = self.page.locator('#tlDetail')
                    canvas = self.page.locator('.tl-canvas')

                    def scroll():
                        return pane.evaluate('pane => pane.scrollTop')

                    def wait_at_top():
                        self.page.wait_for_function('() => document.getElementById("tlDetail").scrollTop === 0', timeout=1500)

                    def click_call(index):
                        x, y = self.page.evaluate('index => fgView("timeline").probe().xyOf(index)', index)
                        if touch:
                            self.page.touchscreen.tap(x, y)
                        else:
                            self.page.mouse.click(x, y)
                        pane.locator(f'[data-recorded-call="{index}"]').wait_for()

                    def scroll_down():
                        pane.evaluate('pane => { pane.scrollTop = 200; }')
                        position = scroll()
                        self.assertGreater(position, 40)
                        return position

                    settle = '() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))'
                    pane.evaluate('''pane => {
                        const scrollTo = pane.scrollTo.bind(pane);
                        pane.originResets = 0;
                        pane.scrollTo = options => {
                            if (options.top === 0 && options.left === 0) pane.originResets++;
                            scrollTo(options);
                        };
                    }''')
                    click_call(0)
                    self.page.evaluate(settle)
                    wait_at_top()
                    self.assertEqual(pane.evaluate('pane => pane.originResets'), 0)
                    click_call(1)
                    self.page.evaluate(settle)
                    click_call(0)
                    self.page.evaluate(settle)
                    self.assertEqual(pane.evaluate('pane => pane.originResets'), 0)

                    reading_position = scroll_down()
                    click_call(0)
                    self.assertEqual(scroll(), reading_position)
                    canvas.evaluate('canvas => canvas.focus({preventScroll: true})')
                    self.page.keyboard.press('Enter')
                    back = self.page.get_by_role('button', name='Return to Timeline', exact=True)
                    if touch:
                        back.tap()
                    else:
                        back.click()
                    self.assertEqual(scroll(), reading_position)

                    view_state = '''() => {
                        const tracks = document.querySelector('.tl-scroll');
                        return {page: scrollY, tracks: tracks.scrollTop, time: fgView('timeline').probe().view(),
                            split: localStorage.getItem('fgTl.detailFrac')};
                    }'''
                    before = self.page.evaluate(view_state)
                    click_call(1)
                    wait_at_top()
                    self.assertEqual(self.page.evaluate(view_state), before)
                    self.assertTrue(pane.evaluate('''pane => {
                        const title = pane.querySelector('.tl-detail-title').getBoundingClientRect();
                        const bounds = pane.getBoundingClientRect();
                        return title.top >= bounds.top && title.bottom <= Math.min(bounds.bottom, innerHeight);
                    }'''))

                    self.action('an-tasks')
                    scroll_down()
                    click_call(1)
                    wait_at_top()

                    scroll_down()
                    canvas.evaluate('canvas => canvas.focus({preventScroll: true})')
                    self.page.keyboard.press('Escape')
                    self.assertFalse(pane.is_visible())
                    resets_before = pane.evaluate('pane => pane.originResets')
                    click_call(0)
                    self.page.evaluate(settle)
                    wait_at_top()
                    self.assertEqual(pane.evaluate('pane => pane.originResets'), resets_before + 1)
                    scroll_down()
                    self.page.evaluate('''() => {
                        const model = fgView('timeline').probe();
                        model.select(-1);
                        model.select(1);
                    }''')
                    self.page.evaluate(settle)
                    wait_at_top()
                    self.assertEqual(pane.evaluate('pane => pane.originResets'), resets_before + 2)
                    self.page.evaluate('''() => {
                        const model = fgView('timeline').probe();
                        model.select(-1);
                        model.select(0);
                    }''')
                    self.page.evaluate(settle)
                    wait_at_top()
                    self.assertEqual(pane.evaluate('pane => pane.originResets'), resets_before + 2)
                    context.close()

    def test_landscape_touch_expansion_exposes_rows_both_themes_and_delivery_modes(self):
        self.context.close()
        for theme in ('light', 'dark'):
            for mode in ('single', 'sidecar'):
                with self.subTest(theme=theme, mode=mode):
                    context = self.browser.new_context(viewport={'width': 844, 'height': 390}, is_mobile=True, has_touch=True, device_scale_factor=2)
                    self.addCleanup(context.close)
                    self.page = context.new_page()
                    self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                    self.page.add_init_script(f"localStorage.setItem('theme', '{theme}')")
                    self.open_report(mode=mode)
                    self.assertLess(self.page.locator('.tl-detail').bounding_box()['height'], 140)
                    self.assertGreaterEqual(self.button().bounding_box()['width'], 44)
                    self.assertGreaterEqual(self.button().bounding_box()['height'], 44)
                    before = self.facts()
                    self.button().tap()
                    pane = self.page.locator('.tl-detail').bounding_box()
                    row = self.page.locator('[data-task-row]').first.bounding_box()
                    footer = self.page.locator('.an-task-footer').bounding_box()
                    self.assertGreater(pane['height'], 180)
                    self.assertLessEqual(pane['y'] + pane['height'], 390)
                    self.assertGreaterEqual(row['y'], pane['y'])
                    self.assertLessEqual(row['y'] + row['height'], footer['y'])
                    self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 844)
                    self.assertFalse(self.page.locator('.tl-event-only').is_visible())
                    self.assertEqual(self.facts(), before)
                    self.page.locator('[data-task-page="1"]').tap()
                    self.assertEqual(self.page.locator('.an-task-footer [role=status]').inner_text(), 'Tasks 101–200 of 205')
                    self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
                    self.button().tap()
                    self.assertTrue(self.page.locator('.tl-scroll').is_visible())
                    self.assertTrue(self.page.locator('.tl-event-only').is_visible())
                    self.assertEqual(self.facts(), before)
                    context.close()

    def test_landscape_touch_keeps_recorded_facts_and_chart_visible(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                context = self.browser.new_context(viewport={'width': 844, 'height': 390}, is_mobile=True, has_touch=True)
                self.addCleanup(context.close)
                self.page = context.new_page()
                self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                self.page.add_init_script("localStorage.setItem('fgTl.detailFrac', '0.27')")
                self.open_report(mode=mode, with_calls=True, count=8, no_text=False)
                chart = self.page.locator('.tl-stage')
                pane = self.page.locator('.tl-detail')
                resize = self.page.locator('.tl-resizer')
                self.assertTrue(resize.is_visible())
                self.page.evaluate("fgView('timeline').probe().select(0)")
                original = self.facts()
                self.assertTrue(resize.is_visible())
                self.assertEqual(resize.get_attribute('aria-orientation'), 'vertical')
                default_width = int(resize.get_attribute('aria-valuenow'))
                self.assertIn('wide', resize.get_attribute('aria-valuetext'))
                chart_box, pane_box = chart.bounding_box(), pane.bounding_box()
                self.assertGreaterEqual(pane_box['height'], 180)
                self.assertLessEqual(chart_box['x'] + chart_box['width'], pane_box['x'])
                self.assertAlmostEqual(chart_box['y'], pane_box['y'], delta=1)
                duration = pane.locator('.tl-call-duration').bounding_box()
                self.assertLessEqual(duration['y'] + duration['height'], pane_box['y'] + pane_box['height'])
                self.assertLessEqual(pane_box['y'] + pane_box['height'], 390)
                self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 844)
                width_pref = "localStorage.getItem('fgTl.detailWidthFrac')"
                touch = context.new_cdp_session(self.page)

                def drag_left(distance):
                    box = resize.bounding_box()
                    touch_x = box['x'] + box['width'] / 2
                    touch_y = box['y'] + box['height'] / 2
                    touch.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': touch_x, 'y': touch_y}]})
                    touch.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': touch_x - distance, 'y': touch_y}]})

                drag_left(60)
                self.page.locator(f'.tl-resizer[aria-valuenow="{default_width + 60}"]').wait_for()
                self.assertIsNone(self.page.evaluate(width_pref))
                touch.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
                self.assertFalse(resize.evaluate('handle => handle.classList.contains("drag")'))
                self.assertGreater(float(self.page.evaluate(width_pref)), .5)
                self.assertAlmostEqual(pane.bounding_box()['width'], default_width + 60, delta=1)
                self.assertEqual(self.facts(), original)
                self.assertTrue(self.page.evaluate('''() => {
                    const tracks = document.querySelector('.tl-canvas');
                    const ruler = document.querySelector('.tl-head-canvas');
                    return tracks.clientWidth === ruler.clientWidth && tracks.width === ruler.width;
                }'''))
                resize.press('Home')
                self.assertEqual(resize.get_attribute('aria-valuenow'), resize.get_attribute('aria-valuemin'))
                self.assertGreaterEqual(pane.bounding_box()['width'], 280)
                resize.press('End')
                self.assertEqual(resize.get_attribute('aria-valuenow'), resize.get_attribute('aria-valuemax'))
                self.assertGreaterEqual(chart.bounding_box()['width'], 320)
                resize.press('Enter')
                self.assertIsNone(self.page.evaluate(width_pref))
                self.assertAlmostEqual(pane.bounding_box()['width'], default_width, delta=1)
                resize.press('ArrowRight')
                self.assertAlmostEqual(pane.bounding_box()['width'], default_width - 16, delta=1)
                resize.press('ArrowLeft')
                self.assertAlmostEqual(pane.bounding_box()['width'], default_width, delta=1)
                resize.press('ArrowLeft')
                saved_width = self.page.evaluate(width_pref)
                self.assertAlmostEqual(pane.bounding_box()['width'], default_width + 16, delta=1)
                point = self.page.evaluate('''async () => {
                    await new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)));
                    return fgView('timeline').probe().xyOf(1);
                }''')
                self.assertTrue(self.page.evaluate('point => document.elementFromPoint(...point)?.matches(".tl-canvas")', point))
                self.page.touchscreen.tap(*point)
                self.page.wait_for_function("fgView('timeline').probe().sel() === 1")
                selected = self.facts()
                self.assertEqual(selected, dict(original, selection=1))
                self.button().tap()
                self.assertFalse(chart.is_visible())
                self.assertFalse(resize.is_visible())
                self.button().tap()
                self.assertTrue(chart.is_visible())
                self.assertAlmostEqual(pane.bounding_box()['width'], default_width + 16, delta=1)
                self.page.locator('.tl-canvas').press('Enter')
                self.page.get_by_role('button', name='Return to Timeline', exact=True).tap()
                self.assertAlmostEqual(pane.bounding_box()['width'], default_width + 16, delta=1)
                self.assertEqual(self.facts(), selected)
                drag_left(28)
                self.page.locator(f'.tl-resizer[aria-valuenow="{default_width + 44}"]').wait_for()
                self.page.set_viewport_size({'width': 390, 'height': 844})
                self.page.locator('.tl-resizer[aria-orientation="horizontal"]').wait_for()
                touch.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
                self.assertFalse(self.page.locator('body').evaluate("body => body.classList.contains('tl-resizing')"))
                self.assertGreaterEqual(pane.bounding_box()['y'], chart.bounding_box()['y'] + chart.bounding_box()['height'])
                self.assertAlmostEqual(pane.bounding_box()['height'], round(.27 * 844), delta=1)
                self.assertEqual(self.facts(), selected)
                self.page.set_viewport_size({'width': 844, 'height': 390})
                self.page.locator('.tl-resizer[aria-orientation="vertical"]').wait_for()
                self.assertAlmostEqual(pane.bounding_box()['width'], default_width + 16, delta=1)
                self.assertEqual(self.page.evaluate(width_pref), saved_width)
                self.action('an-tasks')
                self.assertEqual(resize.get_attribute('aria-orientation'), 'horizontal')
                self.page.reload()
                self.page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().select(1); }")
                self.assertAlmostEqual(pane.bounding_box()['width'], default_width + 16, delta=1)
                resize.dblclick()
                self.assertAlmostEqual(pane.bounding_box()['width'], default_width, delta=1)
                self.assertIsNone(self.page.evaluate(width_pref))
                context.close()

    def test_help_and_keyboard_focus_remain_available_without_the_chart(self):
        self.open_report(mode='sidecar')
        self.shorten()
        self.button().click()
        self.page.keyboard.press('?')
        self.assertTrue(self.page.locator('.tl-help').is_visible())
        self.page.locator('.tl-help-close').click()
        self.assertTrue(self.button().evaluate('(element) => element === document.activeElement'))
        self.page.keyboard.press('?')
        self.page.keyboard.press('Escape')
        self.assertFalse(self.page.locator('.tl-help').is_visible())
        self.assertTrue(self.button().evaluate('(element) => element === document.activeElement'))
        self.page.locator('.header-actions button').filter(has_text='?').click()
        self.assertTrue(self.page.locator('.tl-help').is_visible())

    def test_switching_text_and_back_preserves_expansion_and_view_specific_help(self):
        self.open_report(mode='sidecar', with_calls=True, no_text=False)
        self.shorten()
        before = self.facts()
        self.button().click()
        self.page.evaluate("async () => { await fgSwitchView('text'); }")
        self.page.locator('.header-actions button').filter(has_text='?').click()
        self.assertTrue(self.page.locator('#keyboardHint').is_visible())
        self.assertFalse(self.page.locator('.tl-help').is_visible())
        self.page.keyboard.press('Escape')
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
        self.assertEqual(self.facts(), before)
        self.page.locator('.header-actions button').filter(has_text='?').click()
        self.assertTrue(self.page.locator('.tl-help').is_visible())
        self.page.keyboard.press('Escape')
        self.button().click()
        self.assertTrue(self.page.locator('.tl-scroll').is_visible())

    def test_close_and_escape_restore_chart_without_persisting_expansion(self):
        self.open_report()
        self.shorten()
        before = self.facts()
        for close in ('click', 'Enter', 'Space', 'Escape'):
            with self.subTest(close=close):
                self.button().click()
                button = self.page.get_by_role('button', name='Close details', exact=True)
                if close == 'click':
                    button.click()
                else:
                    button.press(close)
                self.assertFalse(self.page.locator('.tl-detail').is_visible())
                self.assertEqual(self.page.locator('.tl-detail > *').count(), 0)
                self.assertTrue(self.page.locator('.tl-scroll').is_visible())
                focused_chart = self.page.locator('.tl-canvas').evaluate('canvas => canvas === document.activeElement')
                self.assertFalse(self.page.locator('#timelineView').evaluate('(element) => element.classList.contains("tl-detail-expanded")'))
                self.action('an-tasks')
                self.assertTrue(focused_chart)
                self.assertEqual(self.button().get_attribute('aria-pressed'), 'false')
                self.assertEqual(self.facts(), before)
        self.button().click()
        self.page.reload()
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.action('an-tasks')
        self.assertEqual(self.button().get_attribute('aria-pressed'), 'false')
        self.assertEqual(self.page.evaluate("localStorage.getItem('fgTl.detailFrac')"), before['split'])

    def test_replacing_analysis_panels_and_selection_keeps_one_control(self):
        self.open_report(with_calls=True)
        self.shorten()
        self.button().click()
        for action in ('an-errors', 'an-sec', 'an-lat', 'process-events', 'an-tasks'):
            with self.subTest(action=action):
                self.action(action)
                self.assertEqual(self.button().count(), 1)
                self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
                self.assertFalse(self.page.locator('.tl-scroll').is_visible())
        self.page.evaluate("fgView('timeline').probe().select(0)")
        self.assertEqual(self.button().count(), 1)
        self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
        self.button().click()
        self.assertTrue(self.page.locator('.tl-scroll').is_visible())
        self.assertEqual(self.facts()['selection'], 0)

    def test_resize_and_timeline_maximize_preserve_expanded_state_and_saved_fraction(self):
        self.open_report(mode='sidecar')
        self.shorten()
        before = self.facts()
        self.button().click()
        for width, height in ((844, 390), (390, 844), (1500, 900)):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.page.wait_for_function("() => document.querySelector('.tl-detail').getBoundingClientRect().bottom <= innerHeight")
                self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
                self.assertEqual(self.facts(), before)
        self.action('max')
        self.assertFalse(self.page.locator('.view-tabs').is_visible())
        self.assertFalse(self.page.locator('.tl-scroll').is_visible())
        self.action('max')
        self.assertTrue(self.page.locator('.view-tabs').is_visible())
        self.assertEqual(self.facts(), before)
        self.button().click()
        self.assertTrue(self.page.locator('.tl-scroll').is_visible())

    def test_restoring_to_a_tall_panel_keeps_focus_on_a_visible_control(self):
        self.open_report()
        self.shorten()
        self.button().press('Enter')
        self.page.evaluate("() => { localStorage.removeItem('fgTl.detailFrac'); fgView('timeline').resize(); }")
        self.button().press('Enter')
        self.assertFalse(self.button().is_visible())
        self.assertTrue(self.page.locator('.an-task-head .tl-close').evaluate('(element) => element === document.activeElement'))

    def test_duration_histogram_resizes_while_the_main_canvases_are_hidden(self):
        self.open_report(with_calls=True)
        self.shorten()
        self.button().click()
        self.page.evaluate("() => { const model = fgView('timeline').probe(); model.dist(model.sName[0]); }")
        for width, height in ((1500, 900), (800, 420), (390, 844)):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.page.wait_for_function("""() => {
                    const canvas = document.querySelector('.an-hist');
                    return canvas.width > 0 && canvas.width === Math.round(canvas.parentElement.clientWidth * devicePixelRatio);
                }""")
                self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
                self.assertFalse(self.page.locator('.tl-scroll').is_visible())
        self.button().click()
        self.assertTrue(self.page.locator('.tl-scroll').is_visible())

    def test_expanded_details_do_not_paint_hidden_charts(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open_report(mode=mode, with_calls=True, count=8)
                self.shorten()
                facts = self.page.evaluate('''async () => {
                    const timeline = fgView('timeline'), model = timeline.probe();
                    const root = document.getElementById('timelineView');
                    const canvases = ['.tl-canvas', '.tl-head-canvas'].map(selector => root.querySelector(selector));
                    const [tracks, ruler] = canvases.map(canvas => canvas.getContext('2d'));
                    const frames = [0, 0];
                    const settle = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    const fill = tracks.fillRect, copy = ruler.drawImage;
                    const expand = () => root.querySelector('[data-detail-expand]').click();
                    await settle();
                    const originalPixels = canvases.map(canvas => canvas.toDataURL());
                    tracks.fillRect = function(x, y, width, height) {
                        if (x === 0 && y === 0 && width === this.canvas.width / devicePixelRatio &&
                            height === this.canvas.height / devicePixelRatio) frames[0]++;
                        return fill.call(this, x, y, width, height);
                    };
                    ruler.drawImage = function(...args) {
                        frames[1]++;
                        return copy.apply(this, args);
                    };
                    try {
                        model.select(0);
                        expand();
                        await settle();
                        const queued = frames.slice();
                        model.select(2);
                        await settle();
                        model.zoomTo(model.sStart[2], model.sEnd[2], -1);
                        await settle();
                        const hidden = frames.slice();
                        const hiddenPixelsUnchanged = canvases.every((canvas, index) => canvas.toDataURL() === originalPixels[index]);
                        const rulerCanvas = canvases[1];
                        rulerCanvas.width = rulerCanvas.width;
                        rulerCanvas.dispatchEvent(new Event('contextrestored'));
                        await settle();
                        const resetWhileHidden = frames.every((count, index) => count === hidden[index]) &&
                            ruler.getImageData(0, 0, 1, 1).data[3] === 0;
                        const selected = root.querySelector('[data-recorded-call]').dataset.recordedCall;
                        const view = model.view();
                        expand();
                        await settle();
                        const restored = frames.map((value, index) => value > hidden[index]);
                        const pixels = canvases.map(canvas => canvas.toDataURL());
                        timeline.draw();
                        await settle();
                        return {queued, hidden, hiddenPixelsUnchanged, resetWhileHidden, selected, restored, state: model.sel() === 2 &&
                            JSON.stringify(model.view()) === JSON.stringify(view),
                            pixels: pixels.every((image, index) => image !== originalPixels[index] &&
                                image === canvases[index].toDataURL())};
                    } finally {
                        delete tracks.fillRect;
                        delete ruler.drawImage;
                    }
                }''')
                self.assertEqual(facts, dict(queued=[0, 0], hidden=[0, 0], hiddenPixelsUnchanged=True,
                                             resetWhileHidden=True, selected='2', restored=[True, True], state=True, pixels=True))

    def test_touch_slice_controls_do_not_overlap_long_function_names(self):
        self.context.close()
        self.context = self.browser.new_context(viewport={'width': 844, 'height': 390}, is_mobile=True, has_touch=True)
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.open_report(with_calls=True, function_name='namespace::' + 'long_function_name_' * 20, count=1)
        self.page.evaluate("fgView('timeline').probe().select(0)")
        head = self.page.locator('.tl-slice-head').bounding_box()
        expand = self.button().bounding_box()
        close = self.page.locator('.tl-slice-head .tl-close').bounding_box()
        title = self.page.locator('.tl-slice-head .tl-detail-title').bounding_box()
        self.assertAlmostEqual(expand['y'], head['y'], delta=1)
        self.assertLessEqual(expand['x'] + expand['width'], close['x'])
        self.assertLessEqual(title['x'] + title['width'], expand['x'])
        self.button().tap()
        self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
        self.assertLessEqual(self.page.locator('.tl-detail').bounding_box()['y'] + self.page.locator('.tl-detail').bounding_box()['height'], 390)

    def test_portrait_touch_details_can_expand_without_a_short_split(self):
        self.context.close()
        for mode, width, height in (('single', 320, 740), ('sidecar', 390, 844)):
            with self.subTest(mode=mode, width=width):
                self.context = self.browser.new_context(viewport={'width': width, 'height': height},
                                                        is_mobile=True, has_touch=True)
                self.addCleanup(self.context.close)
                self.page = self.context.new_page()
                self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                self.open_report(mode=mode, with_calls=True, function_name='namespace::' + 'long_function_name_' * 20, count=1)
                self.page.evaluate("fgView('timeline').probe().select(0)")
                pane = self.page.locator('.tl-detail')
                initial_height = pane.bounding_box()['height']
                self.assertGreaterEqual(initial_height, 140)
                self.assertFalse(self.page.locator('.tl-detail-short').count())
                before = self.facts()
                self.assertTrue(self.button().is_visible())
                self.assertTrue(self.button().evaluate('''button => {
                    const bounds = button.getBoundingClientRect(), heading = button.closest('.tl-slice-head');
                    const title = heading.querySelector('.tl-detail-title').getBoundingClientRect();
                    const controls = heading.querySelector('.tl-call-controls').getBoundingClientRect();
                    return bounds.width >= 44 && bounds.height >= 44 && bounds.right <= innerWidth &&
                        title.right <= controls.left && heading.scrollWidth <= heading.clientWidth &&
                        button.contains(document.elementFromPoint(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2));
                }'''))
                self.button().tap()
                self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
                self.assertFalse(self.page.locator('.tl-scroll').is_visible())
                self.assertGreater(pane.bounding_box()['height'], initial_height)
                self.button().tap()
                self.assertEqual(self.button().get_attribute('aria-pressed'), 'false')
                self.assertTrue(self.page.locator('.tl-scroll').is_visible())
                self.assertAlmostEqual(pane.bounding_box()['height'], initial_height, delta=1)
                self.assertEqual(self.facts(), before)

    def test_short_call_pane_scrolls_its_heading_out_of_the_way(self):
        self.context.close()
        for mode, width, height, touch in (('single', 1500, 900, False), ('sidecar', 320, 700, True)):
            with self.subTest(mode=mode, width=width):
                self.context = self.browser.new_context(viewport={'width': width, 'height': height},
                                                        is_mobile=touch, has_touch=touch)
                self.addCleanup(self.context.close)
                self.page = self.context.new_page()
                self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                self.open_report(mode=mode, with_calls=True, function_name='namespace::' + 'long_function_name_' * 20, count=1)
                self.page.evaluate("fgView('timeline').probe().select(0)")
                self.shorten()
                before = self.facts()
                pane = self.page.locator('.tl-detail.tl-detail-short')
                pane.scroll_into_view_if_needed()
                bounds = pane.bounding_box()
                self.page.mouse.move(bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2)
                self.page.mouse.wheel(0, 80)
                self.page.wait_for_function("() => document.querySelector('.tl-detail').scrollTop > 0")
                duration = pane.locator('.tl-call-facts .k').get_by_text('duration', exact=True)
                facts = duration.evaluate('''element => {
                    const pane = element.closest('.tl-detail');
                    const panel = pane.getBoundingClientRect(), heading = pane.querySelector('.tl-slice-head');
                    pane.scrollTop += element.getBoundingClientRect().top - panel.top - 6;
                    const label = element.getBoundingClientRect();
                    const hit = document.elementFromPoint(label.left + 3, label.top + 3);
                    return {visible: element.contains(hit), headerGone: heading.getBoundingClientRect().bottom <= label.top};
                }''')
                self.assertEqual(facts, dict(visible=True, headerGone=True))
                self.assertEqual(self.facts(), before)
                pane.evaluate('node => { node.scrollTop = 0; }')
                self.button().press('Enter')
                self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
                self.assertEqual(self.facts(), before)
                self.assertEqual(self.page.locator('.tl-slice-head').evaluate('node => getComputedStyle(node).top'), '0px')
                self.context.close()

    def test_short_duration_pane_does_not_pin_heading_or_table_over_calls(self):
        self.open_report(mode='sidecar', with_calls=True, count=60)
        self.page.evaluate("fgView('timeline').probe().select(0)")
        self.shorten()
        self.page.evaluate("() => { const model = fgView('timeline').probe(); model.dist(model.sName[0]); }")
        pane = self.page.locator('.tl-detail.tl-detail-short')
        pane.scroll_into_view_if_needed()
        original = self.facts()
        self.assertEqual(original['selection'], 0)
        table = pane.locator('.an-dist-call-browser table')
        self.assertEqual(table.locator('tbody tr').count(), 50)
        facts = table.locator('tbody tr').first.locator('td').first.evaluate('''cell => {
            const pane = cell.closest('.tl-detail');
            const row = cell.parentElement, heading = pane.querySelector('.an-dist-head');
            const tableHead = row.closest('table').querySelector('th');
            pane.scrollTop += row.getBoundingClientRect().top - pane.getBoundingClientRect().top - 6;
            const rect = cell.getBoundingClientRect();
            return {readable:cell.contains(document.elementFromPoint(rect.left + 3, rect.top + 3)),
                headingTop:getComputedStyle(heading).top, tableHeadPosition:getComputedStyle(tableHead).position};
        }''')
        self.assertEqual(facts, dict(readable=True, headingTop='auto', tableHeadPosition='static'))
        pane.evaluate('node => { node.scrollTop = 0; }')
        self.button().press('Enter')
        self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
        self.assertEqual(self.facts(), original)
        heading = self.page.locator('.an-dist-head')
        header = self.page.locator('.an-dist-call-browser th').first

        def assert_pinned():
            height = heading.evaluate('node => node.offsetHeight')
            self.assertEqual(header.evaluate('node => [getComputedStyle(node).position, getComputedStyle(node).top, node.style.top]'),
                             ['sticky', f'{height}px', ''])
            gap = header.evaluate('''node => {
                const pane = node.closest('.tl-detail'), heading = pane.querySelector('.an-dist-head');
                const previous = pane.scrollTop;
                pane.scrollTop = 0;
                pane.scrollTop = node.closest('table').getBoundingClientRect().top -
                    pane.getBoundingClientRect().top - heading.offsetHeight + 30;
                const gap = node.getBoundingClientRect().top - heading.getBoundingClientRect().bottom;
                pane.scrollTop = previous;
                return gap;
            }''')
            self.assertAlmostEqual(gap, 0, delta=1)

        assert_pinned()
        self.button().press('Space')
        self.assertEqual(self.facts(), original)
        self.assertEqual(header.evaluate('node => getComputedStyle(node).position'), 'static')
        resizer = self.page.locator('.tl-resizer')
        resizer.press('End')
        assert_pinned()
        resizer.press('Home')
        self.assertEqual(header.evaluate('node => getComputedStyle(node).position'), 'static')

    def test_csv_stays_byte_identical_while_page_and_expansion_are_preserved(self):
        self.open_report(mode='sidecar')
        self.shorten()
        self.button().click()
        self.page.locator('[data-task-page="1"]').click()
        def export():
            with self.page.expect_download() as download:
                self.page.locator('[data-act=tasks-csv]').click()
            return Path(download.value.path()).read_bytes()
        expanded = export()
        self.assertEqual(self.page.locator('.an-task-footer [role=status]').inner_text(), 'Tasks 101–200 of 205')
        self.assertEqual(self.button().get_attribute('aria-pressed'), 'true')
        self.button().click()
        self.assertEqual(export(), expanded)
        self.assertEqual(self.page.locator('.an-task-footer [role=status]').inner_text(), 'Tasks 101–200 of 205')

    def test_short_duration_paging_reveals_new_calls_below_the_table_header(self):
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.open_report(mode='sidecar', with_calls=True, count=105)
        self.page.evaluate("() => { const model = fgView('timeline').probe(); model.dist(model.sName[0]); }")
        self.page.locator('.tl-resizer').press('Home')
        pane = self.page.locator('.tl-detail.tl-detail-short')
        rows = pane.locator('.an-list tr')
        first = rows.first
        first_cell = first.locator('td').first
        original = first.get_attribute('data-slice')
        previous = pane.locator('[data-act=dist-prev]')
        next_page = pane.locator('[data-act=dist-next]')

        next_page.click()
        self.assertNotEqual(first.get_attribute('data-slice'), original)
        self.assert_cell_readable(first_cell)
        self.page.keyboard.press('Enter')
        self.assertEqual(rows.count(), 5)
        self.assert_cell_readable(first_cell)
        self.assertTrue(previous.evaluate('node => node === document.activeElement'))
        self.page.keyboard.press('Enter')
        self.assertEqual(rows.count(), 50)
        self.assert_cell_readable(first_cell)
        self.page.keyboard.press('Enter')
        self.assertEqual(first.get_attribute('data-slice'), original)
        self.assert_cell_readable(first_cell)

    def test_short_task_paging_reveals_the_first_task_instead_of_the_filters(self):
        self.open_report(with_calls=True)
        self.page.locator('.tl-resizer').press('Home')
        pane = self.page.locator('.tl-detail.tl-detail-short')
        first = pane.locator('[data-task-row]').first
        original = first.get_attribute('data-task-row')
        pane.locator('[data-task-page="1"]').click()
        self.assertNotEqual(first.get_attribute('data-task-row'), original)
        self.assertEqual(pane.locator('.an-task-footer [role=status]').inner_text(), 'Tasks 101–200 of 205')
        self.assert_cell_readable(first.locator('td').first)
