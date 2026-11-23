"""Text event visibility is a reversible presentation choice, never a data filter."""
import io
import json
from pathlib import Path
import threading
import unittest

import helpers
from fglib.textindex import is_trace_event, TextIndexWriter
from fglib.viewer import create_server
from test_kvm import event
from test_uftrace_import import call, stream


class TextEventModelTest(unittest.TestCase):
    def test_classification_keeps_calls_loss_markers_and_headers(self):
        for raw in ('1.0 | 0) task-42 | /* kvm:kvm_entry: vcpu 0 */',
                    '0) | /* sched_wakeup: comm=task pid=42 */',
                    '0) | task-42 => other-43', ' 20) swapper/20-0 => Chrome_ChildIOT-1903485',
                    '0) | /* raw: switch-in */'):
            self.assertTrue(is_trace_event({'raw_line': raw}), raw)
        for raw in ('# tracer: function_graph', 'CPU:0 [LOST 4 EVENTS]',
                    '0) | /* Lost 4 records */', '0) | } /* func ret=0 */',
                    '0) | func();', '0) | /* context is unavailable */',
                    'unrecognized task-42 => other-43'):
            self.assertFalse(is_trace_event({'raw_line': raw}), raw)
        self.assertFalse(is_trace_event({'raw_line': '0) | /* kvm_entry: text */', 'raw_func_name': 'func'}))
        self.assertTrue(is_trace_event({'record_type': 'event', 'display_func_name': 'event_name'}))
        self.assertFalse(is_trace_event({'record_type': 'lost'}))

    def test_index_retains_exact_event_row_numbers_without_changing_legacy_columns(self):
        output = io.BytesIO()
        writer = TextIndexWriter(output, include_raw_functions=True)
        writer.append(2, {}, 'root() {')
        writer.append(5, {'trace-event': '1'}, '/* event: payload */')
        writer.append(9, {}, 'CPU:0 [LOST 2 EVENTS]')
        writer.finish('index.json')
        data = json.loads(output.getvalue())
        self.assertEqual(data['version'], 5)
        self.assertEqual(data['events'], [5])
        self.assertEqual([row[0] for row in data['rows']], [2, 5, 9])


class TextEventBrowserTest(unittest.TestCase):
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
        self.page = self.browser.new_page(viewport={'width': 1400, 'height': 950})
        self.addCleanup(self.page.close)
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open_report(self, mode='single', windowed=False, records=None, compare=False):
        path = Path(self.kernel.dir, 'events.jsonl')
        if records is None:
            records = [call('entry', 'kernel', 0, 0, 'root', 1), event('kvm_entry', 10),
                       call('entry', 'kernel', 11, 1, 'leaf', 2),
                       call('exit', 'kernel', 12, 1, 'leaf', 2), event('kvm_exit', 20),
                       dict(type='lost', space='kernel', time_ns=event('kvm_entry', 21)['time_ns'],
                            tid=42, cpu=4, count=2), call('exit', 'kernel', 30, 0, 'root', 1)]
        path.write_text(stream(records))
        destination = 'report' if mode == 'sidecar' else 'report.html'
        options = ['--compare', helpers.fixture('pi4_default')] if compare else []
        self.kernel.run(str(path), '--no-config', '--timeline', '--enable-fold', '--filter',
                        '--report-mode', mode, *options, out=destination)
        report = Path(self.kernel.dir, destination)
        if mode == 'sidecar':
            server = create_server(report)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 5)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            url = server.url + ('?text=windowed' if windowed else '')
        else:
            url = report.as_uri()
        self.page.goto(url)
        self.page.evaluate("async () => { await fgSwitchView('text'); }")
        self.page.locator('#textEventsBtn').wait_for(state='attached')

    def test_short_touch_fullscreen_keeps_prior_call_and_return_reachable(self):
        from playwright.sync_api import expect

        records = [call('entry', 'kernel', 10, 0, 'root', 1), event('kvm_entry', 20),
                   call('exit', 'kernel', 30, 0, 'root', 1)]
        for mode, width in (('single', 240), ('sidecar', 320)):
            with self.subTest(mode=mode, width=width):
                page = self.browser.new_page(viewport={'width': width, 'height': 240}, is_mobile=True, has_touch=True)
                self.addCleanup(page.context.close)
                page.on('pageerror', lambda error: self.errors.append(str(error)))
                self.page = page
                self.open_report(mode, windowed=mode == 'sidecar', records=records)
                row = page.locator('[data-trace-event="1"]').first
                number = int(row.get_attribute('data-line-number'))
                row.locator('.line-content').tap()
                position = '[scrollY, document.getElementById("textViewport").scrollLeft]'
                before = page.evaluate(position)
                page.get_by_role('button', name=f'In Timeline · L{number}', exact=True).tap()
                back = page.get_by_role('button', name='Return to Text', exact=True)
                status = page.locator('#fgJumpStatus')
                expect(status).to_contain_text('Prior call')
                if mode == 'single':
                    page.evaluate("document.getElementById('timelineView').requestFullscreen = undefined")
                page.get_by_role('button', name='Full screen', exact=True).tap()
                page.wait_for_function('''native => Boolean(document.fullscreenElement) === native &&
                    document.querySelector('.tl-fullscreen') && !document.querySelector('[data-act=max]').disabled''',
                    arg=mode == 'sidecar')
                exit_full = page.get_by_role('button', name='Exit full screen', exact=True)
                tools = page.get_by_role('button', name='Tools', exact=True)
                for control in (back, status, exit_full, tools):
                    self.assertTrue(control.evaluate('''node => {
                        const rect = node.getBoundingClientRect();
                        return node.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
                    }'''), control)
                self.assertTrue(page.evaluate('''() => {
                    const timeline = fgView('timeline').probe();
                    return timeline.sel() === 0 && document.elementFromPoint(...timeline.xyOf(0))?.matches('.tl-canvas');
                }'''))
                search = page.get_by_role('textbox', name='Search traced function names (regular expression)')
                expect(search).to_be_hidden()
                page.keyboard.press('/')
                expect(page.get_by_role('dialog', name='Timeline tools')).to_be_visible()
                expect(search).to_be_focused()
                expect(page.get_by_role('button', name='Fit trace')).to_be_visible()
                expect(page.get_by_role('button', name='Timeline help')).to_be_visible()
                search.fill('^root$')
                page.keyboard.press('Escape')
                expect(tools).to_be_focused()
                expect(search).to_be_hidden()
                self.assertEqual(page.locator('.tl-search').input_value(), '^root$')
                if mode == 'single':
                    tools.tap()
                    search.focus()
                    page.set_viewport_size({'width': 568, 'height': 240})
                    expect(search).to_be_focused()
                    expect(page.get_by_role('dialog', name='Timeline tools')).to_be_hidden()
                    page.set_viewport_size({'width': width, 'height': 240})
                    expect(search).to_be_hidden()
                    expect(tools).to_be_focused()
                back.tap()
                expect(status).to_be_empty()
                self.assertEqual(page.evaluate(position), before)

    def test_fullscreen_jump_keeps_nested_call_visible_across_toolbar_reflow(self):
        from playwright.sync_api import expect

        depth = 18
        records = [call('entry', 'kernel', index * 10, index, f'call_{index}', index + 1)
                   for index in range(depth)]
        records += [event('kvm_entry', depth * 10)]
        records += [call('exit', 'kernel', (2 * depth - index) * 10, index, f'call_{index}', index + 1)
                    for index in reversed(range(depth))]
        for width, height, fallback, native in ((240, 301, False, False), (280, 301, True, False), (390, 240, True, True)):
            with self.subTest(width=width, height=height, fallback=fallback, native=native):
                page = self.browser.new_page(viewport={'width': width, 'height': height}, is_mobile=True, has_touch=True)
                self.addCleanup(page.context.close)
                page.on('pageerror', lambda error: self.errors.append(str(error)))
                self.page = page
                self.open_report(records=records)
                if fallback:
                    row = page.locator('[data-trace-event="1"]').first
                    number = int(row.get_attribute('data-line-number'))
                else:
                    number = page.evaluate('fgData.read("tlData").cols.line.at(-1)')
                    row = page.locator(f'#L{number}')
                row.locator('.line-content').tap()
                page.get_by_role('button', name=f'In Timeline · L{number}', exact=True).tap()
                if fallback:
                    expect(page.locator('#fgJumpStatus')).to_contain_text('Prior call')
                if not native:
                    page.evaluate("document.getElementById('timelineView').requestFullscreen = undefined")
                timeline_state = '() => { const tl = fgView("timeline").probe(); return [tl.sel(), tl.view()]; }'
                before = page.evaluate(timeline_state)
                self.assertEqual(before[0], depth - 1)
                page.get_by_role('button', name='Full screen', exact=True).tap()
                page.wait_for_function('''native => Boolean(document.fullscreenElement) === native &&
                    document.querySelector('.tl-fullscreen') && !document.querySelector('[data-act=max]').disabled''', arg=native)
                all_reachable = '''() => {
                    const tl = fgView('timeline').probe();
                    const back = document.getElementById('fgJumpReturn');
                    const {x, y, width, height} = back.getBoundingClientRect();
                    return document.getElementById('timelineView').scrollTop === 0 &&
                        document.elementFromPoint(...tl.xyOf(tl.sel()))?.matches('.tl-canvas') &&
                        back.contains(document.elementFromPoint(x + width / 2, y + height / 2));
                }'''
                page.wait_for_function(all_reachable)
                search = page.get_by_role('textbox', name='Search traced function names (regular expression)')
                expect(search).to_be_hidden()
                if not native:
                    page.set_viewport_size({'width': 568, 'height': 240})
                    expect(search).to_be_visible()
                    page.wait_for_function(all_reachable)
                    page.set_viewport_size({'width': width, 'height': height})
                    expect(search).to_be_hidden()
                    page.wait_for_function(all_reachable)
                self.assertEqual(page.evaluate(timeline_state), before)
                page.get_by_role('button', name='Return to Text', exact=True).tap()
                page.wait_for_function('() => fgCurrentView() === "text" && !document.fullscreenElement')

    def test_scrolling_fullscreen_details_keeps_return_and_exit_reachable(self):
        from playwright.sync_api import expect

        records = [call('entry', 'kernel', 10, 0, 'root', 1), event('kvm_entry', 20),
                   call('exit', 'kernel', 30, 0, 'root', 1)]
        for width, native in ((240, False), (390, True), (568, False)):
            with self.subTest(width=width, native=native):
                page = self.browser.new_page(viewport={'width': width, 'height': 240}, is_mobile=True, has_touch=True)
                self.addCleanup(page.context.close)
                page.on('pageerror', lambda error: self.errors.append(str(error)))
                self.page = page
                self.open_report(records=records)
                row = page.locator('[data-trace-event="1"]').first
                number = int(row.get_attribute('data-line-number'))
                row.locator('.line-content').tap()
                read_position = '[scrollY, document.getElementById("textViewport").scrollLeft]'
                origin = page.evaluate(read_position)
                page.get_by_role('button', name=f'In Timeline · L{number}', exact=True).tap()
                if not native:
                    page.evaluate("document.getElementById('timelineView').requestFullscreen = undefined")
                page.get_by_role('button', name='Full screen', exact=True).tap()
                page.wait_for_function('''native => Boolean(document.fullscreenElement) === native &&
                    document.querySelector('.tl-fullscreen') && !document.querySelector('[data-act=max]').disabled''', arg=native)
                root = page.locator('#timelineView')
                root.evaluate('root => { root.scrollTop = root.scrollHeight; }')
                self.assertGreater(root.evaluate('root => root.scrollTop'), 44)
                back = page.get_by_role('button', name='Return to Text', exact=True)
                exit_full = page.get_by_role('button', name='Exit full screen', exact=True)
                tools = page.get_by_role('button', name='Tools', exact=True)
                for control in (back, exit_full, tools, page.locator('.tl-call-controls')):
                    self.assertTrue(control.evaluate('''node => {
                        const {left, top, width, height, bottom} = node.getBoundingClientRect();
                        return top >= 0 && bottom <= innerHeight &&
                            node.contains(document.elementFromPoint(left + width / 2, top + height / 2));
                    }'''), control)
                outer_scroll = root.evaluate('root => root.scrollTop')
                bounds = tools.bounding_box()
                page.touchscreen.tap(bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2)
                expect(page.get_by_role('dialog', name='Timeline tools')).to_be_visible()
                page.keyboard.press('Escape')
                expect(tools).to_be_focused()
                self.assertEqual(root.evaluate('root => root.scrollTop'), outer_scroll)
                bounds = back.bounding_box()
                page.touchscreen.tap(bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2)
                page.wait_for_function('() => fgCurrentView() === "text" && !document.fullscreenElement')
                self.assertEqual(page.evaluate(read_position), origin)

    def test_timeline_jump_keeps_unmatched_line_notice_with_return_control(self):
        from playwright.sync_api import expect

        records = [event('kvm_entry', 10), call('entry', 'kernel', 20, 0, 'root', 1),
                   event('kvm_exit', 22), call('exit', 'kernel', 30, 0, 'root', 1)]
        for mode, width in (('single', 1440), ('sidecar', 320)):
            with self.subTest(mode=mode, width=width):
                self.page.set_viewport_size({'width': width, 'height': 350 if width == 320 else 950})
                self.open_report(mode, windowed=mode == 'sidecar', records=records)
                page = self.page
                status = page.locator('#fgJumpStatus')
                back = page.get_by_role('button', name='Return to Text', exact=True)
                position = '[scrollY, document.getElementById("textViewport").scrollLeft, history.length]'

                def jump(row):
                    number = int(row.get_attribute('data-line-number'))
                    row.locator('.line-content').click()
                    button = page.get_by_role('button', name=f'In Timeline · L{number}', exact=True)
                    button.scroll_into_view_if_needed()
                    before = page.evaluate(position)
                    button.press('Enter')
                    return number, before

                early, before = jump(page.locator('[data-trace-event="1"]').first)
                expect(status).to_have_text(f'No Timeline slice for L{early}.')
                expect(page.locator('.view-tab[data-view=timeline]')).to_be_focused()
                self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), -1)
                back.click()
                expect(status).to_be_empty()
                self.assertEqual(page.evaluate(position), before)

                fallback, before = jump(page.locator('[data-trace-event="1"]').last)
                entry, exit_line = page.evaluate("[fgData.read('tlData').cols.line[0], fgData.read('tlData').cols.eline[0]]")
                expect(status).to_have_text(f'L{fallback}: no slice. Prior call L{entry}.')
                expect(page.locator('.view-tab[data-view=timeline]')).to_be_focused()
                self.assertTrue(status.evaluate('''node => {
                    const label = document.createRange();
                    label.selectNodeContents(node);
                    const rects = Array.from(label.getClientRects());
                    const bounds = node.getBoundingClientRect();
                    return rects.length === 1 && rects[0].right <= bounds.right;
                }'''))
                self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), 0)
                self.assertFalse(page.locator('.tl-toast.show').count())
                page.get_by_role('button', name='Full screen', exact=True).click()
                expect(page.locator('.tl-fullscreen .tl-toolbar #fgJumpStatus')).to_be_visible()
                back.press('Enter')
                expect(status).to_be_empty()
                self.assertEqual(page.evaluate(position), before)

                jump(page.locator(f'#L{exit_line}'))
                back.wait_for()
                page.wait_for_function("fgView('timeline').probe().sel() === 0")
                expect(status).to_be_empty()
                page.get_by_role('button', name='Text', exact=True).click()
                expect(back).to_be_hidden()

    def test_coarse_narrow_toolbar_keeps_jumps_visible_and_events_in_more(self):
        self.open_report(compare=True)
        page = self.browser.new_page(viewport={'width': 320, 'height': 560}, has_touch=True, is_mobile=True)
        self.addCleanup(page.close)
        page.on('pageerror', lambda error: self.errors.append(str(error)))
        page.goto(self.page.url)
        page.locator('.view-tab[data-view=text]').tap()
        events = page.get_by_role('button', name='Events: shown', exact=True)
        self.assertFalse(events.is_visible())
        page.get_by_role('button', name='More', exact=True).tap()
        self.assertGreaterEqual(events.bounding_box()['height'], 44)
        events.tap()
        self.assertEqual(page.locator('[data-trace-event="1"]:visible').count(), 0)
        page.get_by_role('button', name='More', exact=True).tap()

        line = page.evaluate("fgData.read('tlData').cols.line[0]")
        row = page.locator(f'#L{line}')
        row.locator('.line-content').tap()
        jump = page.get_by_role('button', name=f'In Timeline · L{line}', exact=True)
        self.assertEqual(jump.bounding_box()['y'], page.locator('#textMoreToggle').bounding_box()['y'])
        jump.tap()
        back = page.get_by_role('button', name='Return to Text', exact=True)
        self.assertEqual(back.inner_text(), '← Text')
        self.assertEqual(page.locator('.view-tab').count(), 3)
        self.assertTrue(page.locator('.view-tabs').evaluate('''tabs => {
            const bounds = tabs.getBoundingClientRect();
            const buttons = Array.from(tabs.querySelectorAll(':scope > button'));
            return buttons.length === 4 && buttons.every(button => {
                const rect = button.getBoundingClientRect();
                return rect.top + .1 >= bounds.top && rect.bottom <= bounds.bottom + .1 &&
                    rect.left >= bounds.left && rect.right <= bounds.right && rect.height >= 44 &&
                    button.scrollWidth <= button.clientWidth;
            }) && bounds.height <= 46;
        }'''))
        back.tap()
        self.assertEqual(page.evaluate('fgCurrentView()'), 'text')
        self.assertTrue(jump.is_visible())

    def test_compact_compare_tabs_keep_both_returns_on_the_same_row(self):
        self.open_report(compare=True)
        for width in (256, 280, 300, 320):
            with self.subTest(width=width):
                page = self.browser.new_page(viewport={'width': width, 'height': 560}, has_touch=True, is_mobile=True)
                try:
                    page.goto(self.page.url)
                    line = page.evaluate('''async () => {
                        const timeline = await fgSwitchView('timeline');
                        timeline.probe().select(0);
                        return fgData.read('tlData').cols.line[0];
                    }''')
                    opener = page.locator(f'.tl-detail button[data-line="{line}"]').first
                    opener.tap()

                    def verify_return(view):
                        label = f'Return to {view}'
                        button = page.get_by_role('button', name=label, exact=True)
                        self.assertTrue(button.evaluate('''button => {
                            const tabs = button.closest('.view-tabs');
                            const container = tabs.getBoundingClientRect();
                            const controls = [...tabs.querySelectorAll(':scope > button')];
                            const baseline = controls[0].getBoundingClientRect().top;
                            return controls.length === 4 && container.height <= 46 && controls.every(control => {
                                const box = control.getBoundingClientRect();
                                const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
                                return box.top === baseline && box.left >= container.left && box.right <= container.right &&
                                    box.width >= 44 && box.height >= 44 && control.scrollWidth <= control.clientWidth &&
                                    control.contains(hit);
                            });
                        }'''))
                        self.assertEqual(button.get_attribute('title'), label)
                        if width < 299:
                            self.assertEqual(button.evaluate('button => getComputedStyle(button, "::before").content'), '"←"')
                        else:
                            self.assertEqual(button.inner_text(), f'← {view}')
                        button.tap()
                        self.assertEqual(page.evaluate('fgCurrentView()'), view.lower())
                        self.assertFalse(button.is_visible())

                    verify_return('Timeline')
                    self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), 0)
                    page.locator('.view-tab[data-view=text]').tap()
                    page.locator(f'#L{line} .line-content').tap()
                    page.get_by_role('button', name=f'In Timeline · L{line}', exact=True).tap()
                    verify_return('Text')
                finally:
                    page.close()

    def test_toggle_keeps_functions_and_loss_rows_and_does_not_mutate_timeline(self):
        self.open_report()
        self.assertEqual(self.page.locator('[data-trace-event="1"]:visible').count(), 2)
        before = self.page.evaluate("JSON.stringify(fgData.read('tlData'))")
        self.page.locator('#textEventsBtn').click()
        self.assertEqual(self.page.locator('[data-trace-event="1"]:visible').count(), 0)
        self.assertIn('Lost 2 records', self.page.locator('#content').inner_text())
        self.assertIn('leaf', self.page.locator('#content').inner_text())
        self.assertEqual(self.page.locator('#textEventsBtn').get_attribute('aria-pressed'), 'false')
        self.page.locator('#textEventsBtn').click()
        self.assertEqual(self.page.locator('[data-trace-event="1"]:visible').count(), 2)
        self.assertEqual(self.page.evaluate("JSON.stringify(fgData.read('tlData'))"), before)

    def test_toggle_projects_only_event_rows(self):
        self.open_report()
        self.page.evaluate('''() => {
            window.eventProjections = [];
            const project = fgProjectFoldLine;
            fgProjectFoldLine = (line, visible) => {
                eventProjections.push(line.getAttribute('data-trace-event'));
                return project(line, visible);
            };
        }''')
        for expected in ('Events: hidden', 'Events: shown'):
            self.page.evaluate('eventProjections.length = 0')
            self.page.locator('#textEventsBtn').click()
            self.assertEqual(self.page.locator('#textEventsBtn').inner_text(), expected)
            self.assertEqual(self.page.evaluate('eventProjections'), ['1', '1'])

    def test_first_toggle_keeps_the_complete_text_model_lazy_and_event_navigation_works(self):
        self.open_report()
        root = "document.getElementById('content')"
        self.assertFalse(self.page.evaluate('fgDomTextModels.has(' + root + ')'))
        number = int(self.page.locator('[data-trace-event="1"]').first.get_attribute('data-line-number'))
        before = self.page.locator('#content .line-container').count()
        self.page.locator('#textEventsBtn').click()
        self.assertFalse(self.page.evaluate('fgDomTextModels.has(' + root + ')'))
        self.assertEqual(self.page.locator('[data-trace-event="1"]:visible').count(), 0)
        self.page.evaluate('async number => { await fgEnsureTextLine(number); }', number)
        self.assertEqual(self.page.locator('#textEventsBtn').inner_text(), 'Events: shown')
        self.assertTrue(self.page.locator('#L' + str(number)).is_visible())
        self.assertFalse(self.page.evaluate('fgDomTextModels.has(' + root + ')'))
        self.assertEqual(self.page.locator('#content .line-container').count(), before)
        self.page.locator('#textEventsBtn').click()
        self.page.reload()
        self.page.evaluate("async () => { await fgSwitchView('text'); }")
        self.assertEqual(self.page.locator('#textEventsBtn').inner_text(), 'Events: hidden')
        self.assertEqual(self.page.locator('[data-trace-event="1"]:visible').count(), 0)
        self.assertFalse(self.page.evaluate('fgDomTextModels.has(' + root + ')'))

    def test_event_button_uses_native_keyboard_activation_without_row_navigation(self):
        self.open_report()
        self.page.evaluate('''() => {
            keyboardSelectedLine = document.querySelector('#content .line-container[data-func]');
            keyboardSelectedLine.classList.add('keyboard-selected');
            window.eventKeyboardSelection = keyboardSelectedLine;
            window.eventKeyboardBlocked = [];
            document.addEventListener('keydown', event => eventKeyboardBlocked.push(event.defaultPrevented));
        }''')
        button = self.page.locator('#textEventsBtn')
        button.focus()
        button.press('Space')
        self.assertEqual(button.inner_text(), 'Events: hidden')
        button.press('Enter')
        self.assertEqual(button.inner_text(), 'Events: shown')
        button.press('ArrowDown')
        self.assertTrue(self.page.evaluate('keyboardSelectedLine === eventKeyboardSelection'))
        self.assertTrue(button.evaluate('button => button === document.activeElement'))
        self.assertEqual(self.page.evaluate('eventKeyboardBlocked'), [False, False, False])

    def test_toggle_keeps_open_source_nodes_and_pending_source_work_untouched(self):
        trace = Path(self.kernel.dir, 'source-events.txt')
        trace.write_text('# tracer: function_graph\n'
                         '0) task-42 | root() { /* <-main+0x4/0x80 */\n'
                         '0) task-42 | /* sched_wakeup: comm=other pid=43 */\n'
                         '0) task-42 | leaf(); /* <-root+0x4/0x80 */\n'
                         '0) task-42 | } /* root */\n')
        self.kernel.run(str(trace), '--no-config', '--enable-fold', '--filter', '--func-links', out='source-events.html')
        self.page.goto(Path(self.kernel.dir, 'source-events.html').as_uri())
        self.page.locator('.expand-btn').first.click()
        self.page.locator('.expanded-content:visible').wait_for()
        self.page.evaluate('''() => {
            window.eventSourceNode = document.querySelector('.expanded-content[style*="block"]');
            window.eventSourceChild = eventSourceNode.firstElementChild;
            window.eventExpandedState = JSON.stringify(expandedLines);
            window.eventSourceWork = [];
            const fill = fgFill, cancel = fgCancelSource;
            fgFill = (...args) => { eventSourceWork.push('fill'); return fill(...args); };
            fgCancelSource = (...args) => { eventSourceWork.push('cancel'); return cancel(...args); };
        }''')
        self.page.locator('#textEventsBtn').click()
        self.page.locator('#textEventsBtn').click()
        self.assertTrue(self.page.evaluate('''() => eventSourceNode.isConnected && eventSourceNode.firstElementChild === eventSourceChild &&
            eventSourceNode.style.display === 'block' && JSON.stringify(expandedLines) === eventExpandedState'''))
        self.assertEqual(self.page.evaluate('eventSourceWork'), [])

    def test_reading_shortcuts_do_not_override_controls_or_editable_content(self):
        self.open_report()
        self.page.evaluate('''() => {
            window.controlNavigations = [];
            navigateToExpandableLine = direction => controlNavigations.push(['step',direction]);
            navigateToTextBoundary = last => controlNavigations.push(['edge',last]);
            const widgets = document.createElement('div');
            widgets.innerHTML = '<a href="#L1">link</a><span role="button" tabindex="0">action</span>' +
                '<details><summary>summary</summary>details</details>' +
                '<div contenteditable="true"><span>editable</span></div>';
            document.querySelector('.controls').append(widgets);
            widgets.id = 'eventControlWidgets';
        }''')
        for selector in ('#textEventsBtn', '#eventControlWidgets a', '#eventControlWidgets [role="button"]',
                         '#eventControlWidgets summary', '#eventControlWidgets [contenteditable]'):
            with self.subTest(selector=selector):
                self.page.locator(selector).focus()
                self.page.keyboard.press('j')
                self.page.keyboard.press('Home')
        self.assertEqual(self.page.evaluate('controlNavigations'), [])
        self.page.evaluate('document.activeElement.blur()')
        self.page.keyboard.press('j')
        self.page.keyboard.press('Home')
        self.assertEqual(self.page.evaluate('controlNavigations'), [['step', 1], ['edge', False]])

    def test_reading_shortcuts_preserve_modifiers_composition_and_consumed_keys(self):
        self.open_report()
        facts = self.page.evaluate('''() => {
            window.browserKeyActions = [];
            navigateToExpandableLine = () => browserKeyActions.push('step');
            navigateToTextBoundary = () => browserKeyActions.push('boundary');
            window.scrollBy = () => browserKeyActions.push('page');
            document.getElementById('textViewport').scrollBy = () => browserKeyActions.push('horizontal');
            foldToDepth = () => browserKeyActions.push('depth');
            unfoldAllBlocks = () => browserKeyActions.push('unfold');
            showKeyboardHint = () => browserKeyActions.push('help');
            closeKeyboardHint = () => browserKeyActions.push('close-help');
            document.getElementById('keyboardHint').style.display = 'block';
            const keys = ['j','k','h','l','ArrowDown','ArrowUp','Home','End','PageDown','PageUp',' ','?','0','1','Escape'];
            const variants = [{ctrlKey:true},{metaKey:true},{altKey:true},{ctrlKey:true,altKey:true},
                {isComposing:true},{keyCode:229},{consumed:true}];
            const facts = [];
            for (const selector of ['body','#content .fold-icon','#textEventsBtn']) {
                const target = document.querySelector(selector);
                if (!target) throw new Error('Missing keyboard target: '+selector);
                for (const key of keys) for (const variant of variants) {
                    const event = new KeyboardEvent('keydown',{key,bubbles:true,cancelable:true,...variant});
                    if (variant.consumed) event.preventDefault();
                    target.dispatchEvent(event);
                    facts.push({selector,key,variant,prevented:event.defaultPrevented});
                }
            }
            return facts;
        }''')
        self.assertEqual(len(facts), 315)
        for fact in facts:
            self.assertEqual(fact['prevented'], bool(fact['variant'].get('consumed')), fact)
        self.assertEqual(self.page.evaluate('browserKeyActions'), [])

    def test_text_select_all_stays_scoped_without_overriding_native_controls(self):
        self.open_report()
        for modifier in ['ctrlKey', 'metaKey']:
            with self.subTest(modifier=modifier):
                facts = self.page.evaluate('''modifier => {
                    const root = document.getElementById('content');
                    const selection = window.getSelection();
                    const events = [];
                    for (const selector of ['body','#content .fold-icon']) {
                        selection.removeAllRanges();
                        const event = new KeyboardEvent('keydown',{key:'a',[modifier]:true,bubbles:true,cancelable:true});
                        document.querySelector(selector).dispatchEvent(event);
                        events.push(event.defaultPrevented && selection.rangeCount === 1 &&
                            root.contains(selection.anchorNode) && root.contains(selection.focusNode));
                    }
                    for (const selector of ['#textEventsBtn','#filterInput']) {
                        selection.removeAllRanges();
                        const event = new KeyboardEvent('keydown',{key:'a',[modifier]:true,bubbles:true,cancelable:true});
                        document.querySelector(selector).dispatchEvent(event);
                        events.push(!event.defaultPrevented && selection.rangeCount === 0);
                    }
                    for (const variant of [{altKey:true},{shiftKey:true}]) {
                        const event = new KeyboardEvent('keydown',{key:'a',[modifier]:true,...variant,bubbles:true,cancelable:true});
                        document.body.dispatchEvent(event);
                        events.push(!event.defaultPrevented && selection.rangeCount === 0);
                    }
                    return events;
                }''', modifier)
                self.assertEqual(facts, [True] * 6)

    def test_hidden_events_stay_hidden_after_filter_reset_folding_and_reload(self):
        self.open_report()
        self.page.locator('#textEventsBtn').click()
        self.page.evaluate("document.getElementById('filterInput').value='func:leaf'; applyFilter()")
        self.page.evaluate('clearFilter()')
        self.page.get_by_role('button', name='Collapse All', exact=True).click()
        self.page.get_by_role('button', name='Expand All', exact=True).click()
        self.assertEqual(self.page.locator('[data-trace-event="1"]:visible').count(), 0)
        self.page.reload()
        self.page.evaluate("async () => { await fgSwitchView('text'); }")
        self.assertEqual(self.page.locator('#textEventsBtn').inner_text(), 'Events: hidden')
        self.assertEqual(self.page.locator('[data-trace-event="1"]:visible').count(), 0)

    def test_sidecar_full_and_windowed_event_navigation_reveals_original_evidence(self):
        for windowed in (False, True):
            with self.subTest(windowed=windowed):
                if self.page.url != 'about:blank':
                    self.page.evaluate('localStorage.clear()')
                self.open_report('sidecar', windowed)
                number = self.page.locator('[data-trace-event="1"]').first.get_attribute('data-line-number')
                self.page.locator('#textEventsBtn').click()
                self.page.wait_for_function("() => Array.from(document.querySelectorAll('[data-trace-event]')).every(row => !row.getClientRects().length)")
                self.page.evaluate('async number => { await fgEnsureTextLine(number); }', int(number))
                self.assertEqual(self.page.locator('#textEventsBtn').inner_text(), 'Events: shown')
                self.assertTrue(self.page.locator('#L' + number).is_visible())

    def test_mobile_event_only_report_can_restore_events_in_both_themes(self):
        self.page.close()
        self.page = self.browser.new_page(viewport={'width': 320, 'height': 844}, has_touch=True, is_mobile=True)
        self.addCleanup(self.page.close)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.open_report(records=[event('kvm_entry', 10), event('kvm_exit', 20)])
        self.assertTrue(self.page.evaluate('fgTextCompactMedia.matches'))
        self.page.evaluate("document.getElementById('filterInput').value='cpu:4'; applyFilter()")
        self.page.get_by_role('button', name='More', exact=True).tap()
        for theme in ('light', 'dark'):
            self.page.evaluate("theme => document.documentElement.setAttribute('data-theme',theme)", theme)
            self.page.locator('#textEventsBtn').click()
            self.assertTrue(self.page.locator('#textEventsBtn').is_visible())
            self.assertEqual(self.page.locator('[data-trace-event="1"]:visible').count(), 0)
            self.page.locator('#textEventsBtn').click()
            self.assertEqual(self.page.locator('[data-trace-event="1"]:visible').count(), 2)
            self.assertTrue(self.page.evaluate('''() => Array.from(document.querySelector('.toolbar > .btn-group').children)
                .filter(button => button.getClientRects().length).every(button => {
                    const rect = button.getBoundingClientRect();
                    return rect.left >= 0 && rect.right <= innerWidth && rect.width >= 44 && rect.height >= 44;
                }) && document.documentElement.scrollWidth === document.documentElement.clientWidth'''))

    def test_plain_function_graph_text_hides_only_event_and_switch_rows(self):
        trace = Path(self.kernel.dir, 'plain.txt')
        trace.write_text('# tracer: function_graph\n'
                         '0) task-42 | root() {\n'
                         '0) task-42 | /* sched_wakeup: comm=other pid=43 */\n'
                         '0) task-42 | /* kvm:kvm_entry: vcpu 0 */\n'
                         ' 0) task-42 => other-43\n'
                         '0) other-43 | /* raw: switch-in */\n'
                         '0) task-42 | leaf();\n'
                         'CPU:0 [LOST 2 EVENTS]\n'
                         '0) task-42 | } /* root */\n')
        self.kernel.run(str(trace), '--no-config', out='plain.html')
        self.page.goto(Path(self.kernel.dir, 'plain.html').as_uri())
        self.page.locator('#textEventsBtn').click()
        self.assertEqual(self.page.locator('[data-trace-event="1"]').count(), 4)
        self.assertEqual(self.page.locator('[data-trace-event="1"]:visible').count(), 0)
        self.assertNotIn('task-42 => other-43', self.page.locator('#content').inner_text())
        self.assertIn('leaf', self.page.locator('#content').inner_text())
        self.assertIn('LOST 2 EVENTS', self.page.locator('#content').inner_text())

    def test_report_without_events_does_not_add_a_toolbar_button(self):
        trace = Path(self.kernel.dir, 'calls.txt')
        trace.write_text('0) task-42 | work();\n')
        self.kernel.run(str(trace), '--no-config', out='calls.html')
        self.page.goto(Path(self.kernel.dir, 'calls.html').as_uri())
        self.assertEqual(self.page.locator('#textEventsBtn').count(), 0)
        self.assertIn('work', self.page.locator('#content').inner_text())
