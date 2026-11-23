"""Selected-call actions stay compact, accessible and lazy in every report mode."""

from itertools import product
from pathlib import Path
import threading
import unittest

import helpers
from fglib.viewer import create_server
from test_uftrace_import import call, capture, stream

CLIPPED_SYMBOL = 'dispatch<std::array<' + 'VeryLongRecordedTypeName' * 13 + ', 32>>'
CLONE_SYMBOL = 'inner.isra.17'

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class DetailActionsBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel(inline_source=True)
        cls.addClassCleanup(cls.kernel.close)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)
        cls.urls = {}
        for kind in ('mixed', 'user'):
            records = capture()
            if kind == 'user':
                records = [record for record in records if record['space'] == 'user']
            Path(cls.kernel.dir, kind + '.jsonl').write_text(stream(records))
        symbols = capture()
        for record in symbols:
            if record['name'] == 'work<int>' and record['space'] == 'user':
                record['name'] = CLIPPED_SYMBOL
            elif record['name'] == 'inner':
                record['name'] = CLONE_SYMBOL
        Path(cls.kernel.dir, 'symbols.jsonl').write_text(stream(symbols))
        focus = [call(phase, 'user', timestamp, depth, name, address)
                 for phase, timestamp, depth, name, address in (
                     ('entry', 100, 0, 'outer', 1),
                     ('entry', 110, 1, 'brief', 2), ('exit', 118, 1, 'brief', 2),
                     ('entry', 200, 1, 'point', 3), ('exit', 200, 1, 'point', 3),
                     ('exit', 500000, 0, 'outer', 1))]
        focus[0]['source'] = {'file': 'focus.c', 'line': 1}
        Path(cls.kernel.dir, 'focus.c').write_text('void outer(void) {}\n')
        Path(cls.kernel.dir, 'focus.jsonl').write_text(stream(focus))
        fixtures = list(product(('mixed', 'user', 'kernel', 'timeline-only', 'symbols', 'focus'), ('single', 'sidecar')))
        fixtures.append(('focus', 'single-windowed'))
        for kind, mode in fixtures:
            trace = helpers.fixture('pi4_nodur') if kind == 'kernel' else str(
                Path(cls.kernel.dir, ('mixed' if kind == 'timeline-only' else kind) + '.jsonl'))
            destination = kind + '-' + mode + ('' if mode == 'sidecar' else '.html')
            options = ['--no-config', '--timeline', '--enable-fold', '--filter', '--report-mode', mode]
            if kind == 'timeline-only':
                options.append('--no-text-view')
            if kind == 'focus':
                options.extend(['--user-src', cls.kernel.dir])
            cls.kernel.run(trace, *options, out=destination)
            report = Path(cls.kernel.dir, destination)
            if mode == 'sidecar':
                server = create_server(report)
                thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
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
        self.requests = []

    def tearDown(self):
        if self.context:
            self.context.close()
        self.assertEqual(self.errors, [])

    def open(self, kind='mixed', mode='single', width=1440, height=960, theme='light', native=True, touch=None):
        if self.context:
            self.context.close()
        has_touch = width < 600 if touch is None else touch
        self.context = self.browser.new_context(viewport={'width': width, 'height': height},
                                                has_touch=has_touch, is_mobile=has_touch)
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('request', lambda request: self.requests.append(request.url))
        if not native:
            self.page.add_init_script('Element.prototype.requestFullscreen = undefined;')
        self.page.goto(self.urls[kind, mode])
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.page.evaluate('theme => document.documentElement.dataset.theme = theme', theme)
        self.page.evaluate('''() => {
            const model = fgView('timeline').probe();
            model.select(1);
            window.actionOriginal = {data: JSON.stringify(fgData.read('tlData')),
                starts: model.sStart, tracks: model.TRACKS, view: model.view(), selected: model.sel()};
        }''')
        self.trigger = self.page.locator('[data-call-actions]')
        self.menu = self.page.locator('#tlCallActions')

    def assert_unchanged(self):
        self.assertTrue(self.page.evaluate('''() => {
            const model = fgView('timeline').probe();
            return JSON.stringify(fgData.read('tlData')) === actionOriginal.data &&
                model.sStart === actionOriginal.starts && model.TRACKS === actionOriginal.tracks &&
                JSON.stringify(model.view()) === JSON.stringify(actionOriginal.view) &&
                model.sel() === actionOriginal.selected;
        }'''))

    def assert_return_target(self, button, touch):
        facts = button.evaluate('''button => {
            const {left, right, top, bottom, width, height} = button.getBoundingClientRect();
            const hit = document.elementFromPoint((left + right) / 2, (top + bottom) / 2);
            return {clickable: button.contains(hit), fits: left >= 0 && right <= innerWidth &&
                top >= 0 && bottom <= innerHeight, width, height};
        }''')
        self.assertTrue(facts['clickable'] and facts['fits'], facts)
        if touch:
            self.assertGreaterEqual(facts['width'], 44, facts)
            self.assertGreaterEqual(facts['height'], 44, facts)
        else:
            self.assertLess(facts['height'], 44, facts)

    def open_menu(self):
        self.trigger.click()
        self.assertTrue(self.menu.is_visible())
        self.assertEqual(self.trigger.get_attribute('aria-expanded'), 'true')

    def focused_action(self):
        return self.page.evaluate('document.activeElement.dataset.callAction')

    def assert_menu_focus_visible(self):
        facts = self.menu.evaluate('''menu => {
            const item = document.activeElement.getBoundingClientRect();
            const bounds = menu.getBoundingClientRect(), top = bounds.top + menu.clientTop;
            return {itemTop:item.top, itemBottom:item.bottom, top,
                bottom:top + menu.clientHeight, scrollTop:menu.scrollTop,
                outer:[scrollX, scrollY, document.querySelector('.tl-detail').scrollTop,
                    document.querySelector('.tl-scroll').scrollTop]};
        }''')
        self.assertGreaterEqual(facts['itemTop'], facts['top'] - 0.5, facts)
        self.assertLessEqual(facts['itemBottom'], facts['bottom'] + 0.5, facts)
        return facts

    def gate_fullscreen(self):
        self.page.evaluate('''() => {
            const state=window.fullscreenTest={root:null};
            Object.defineProperty(document,'fullscreenElement',{configurable:true,get:()=>state.root});
            Element.prototype.requestFullscreen=function() {
                return new Promise((resolve,reject)=>{
                    state.reject=reject;
                    state.enter=()=>{
                        state.root=this;
                        document.activeElement.blur();
                        document.dispatchEvent(new Event('fullscreenchange'));
                        resolve();
                    };
                });
            };
            document.exitFullscreen=()=>state.exiting ||= new Promise(resolve=>{
                state.exit=(blur=true)=>{
                    state.root=null;
                    if (blur) document.activeElement.blur();
                    document.dispatchEvent(new Event('fullscreenchange'));
                    resolve();
                };
            });
        }''')

    def open_pending_text_action(self, mode, route='menu'):
        self.open(mode=mode)
        self.gate_fullscreen()
        self.page.locator('[data-act=max]').click()
        if route == 'menu':
            self.open_menu()
            self.menu.locator('[data-call-action=text]').press('Enter')
        elif route == 'key':
            self.page.locator('.tl-canvas').focus()
            self.page.keyboard.press('Enter')
        else:
            self.page.locator('.tl-detail button[data-line]').first.click()
        self.page.wait_for_function("() => fgCurrentView()==='text' && !fgTextDeferred()")

    def test_only_one_secondary_entry_in_every_space_theme_width_and_delivery_mode(self):
        for kind, mode, theme, width in product(('kernel', 'user', 'mixed'), ('single', 'sidecar'),
                                               ('light', 'dark'), (320, 390, 1440)):
            with self.subTest(kind=kind, mode=mode, theme=theme, width=width):
                self.open(kind, mode, width, theme=theme)
                self.assertEqual(self.trigger.inner_text(), 'Actions ▾')
                self.assertEqual(self.trigger.get_attribute('aria-controls'), 'tlCallActions')
                self.assertEqual(self.trigger.get_attribute('aria-haspopup'), 'menu')
                self.assertFalse(self.menu.is_visible())
                self.assertEqual(self.page.locator('.tl-detail-head [data-dist], .tl-detail-head [data-line]').count(), 0)
                self.assertEqual(self.page.locator('.tl-detail-head [data-call-actions]').count(), 1)
                self.assertLessEqual(self.page.locator('.tl-detail-head').bounding_box()['height'], 80)
                self.open_menu()
                group = self.menu.locator('[data-call-detail-actions]')
                self.assertEqual(group.is_visible(), width < 600)
                self.assertEqual(self.menu.locator('[role=menuitem]').count() - group.locator('[role=menuitem]').count(), 4)
                self.assertEqual(self.menu.evaluate('element => element.parentElement.id'), 'timelineView')
                self.assertEqual(self.focused_action(), 'distribution')
                if width < 600:
                    self.assertGreaterEqual(self.trigger.bounding_box()['height'], 44)
                    for item in self.menu.locator('[role=menuitem]').all():
                        self.assertGreaterEqual(item.bounding_box()['height'], 44)
                self.page.keyboard.press('Escape')
                self.assertFalse(self.menu.is_visible())
                self.assertTrue(self.trigger.evaluate('element => element === document.activeElement'))
                self.assert_unchanged()

    def test_ultranarrow_minimum_split_keeps_call_header_targets_visible(self):
        for mode, width, theme in (('single', 240, 'light'), ('sidecar', 248, 'dark')):
            with self.subTest(mode=mode, width=width, theme=theme):
                self.open(kind='symbols', mode=mode, width=width, height=568, theme=theme, touch=True)
                divider = self.page.get_by_role('separator', name='Resize details')
                divider.press('Home')
                self.assertEqual(divider.get_attribute('aria-valuenow'), divider.get_attribute('aria-valuemin'))
                pane = self.page.locator('.tl-detail')
                pane.evaluate('''pane => {
                    scrollBy({top: pane.getBoundingClientRect().bottom - innerHeight + 12, behavior: 'instant'});
                }''')
                title = pane.locator('[data-call-symbol]')
                actions = pane.get_by_role('button', name='Selected call actions', exact=True)
                expand = pane.get_by_role('button', name='Expand details into the chart area')
                close = pane.get_by_role('button', name='Close details')
                self.assertEqual(actions.inner_text(), '⋯')
                self.assertEqual(pane.evaluate('pane => pane.scrollTop'), 0)
                title_y = title.bounding_box()['y']
                for target in (title, actions, expand, close):
                    self.assert_return_target(target, touch=True)
                    self.assertAlmostEqual(target.bounding_box()['y'], title_y, delta=1)

                def tap_in_place(target):
                    self.assert_return_target(target, touch=True)
                    bounds = target.bounding_box()
                    self.page.touchscreen.tap(bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2)

                tap_in_place(actions)
                self.assertTrue(self.menu.is_visible())
                self.page.keyboard.press('Escape')
                self.assertTrue(actions.evaluate('button => button === document.activeElement'))
                split_scroll = self.page.evaluate('scrollY')
                self.assertGreater(split_scroll, 0)
                tap_in_place(expand)
                self.assertFalse(self.page.locator('.tl-canvas').is_visible())
                self.assertLess(self.page.evaluate('scrollY'), split_scroll)
                restore = pane.get_by_role('button', name='Restore chart and detail split')
                tap_in_place(restore)
                self.assertTrue(self.page.locator('.tl-canvas').is_visible())
                self.assertEqual(divider.get_attribute('aria-valuenow'), divider.get_attribute('aria-valuemin'))
                self.assertEqual(self.page.evaluate('scrollY'), split_scroll)
                tap_in_place(close)
                self.assertFalse(pane.is_visible())

    def test_short_call_header_moves_context_below_navigation_without_losing_badges(self):
        for mode, touch in product(('single', 'sidecar'), (False, True)):
            with self.subTest(mode=mode, touch=touch):
                self.open(kind='mixed', mode=mode, width=390, height=844, touch=touch)
                pane = self.page.locator('#tlDetail')
                divider = self.page.get_by_role('separator', name='Resize details')
                badge = pane.locator('.tl-call-context')
                self.assertEqual(badge.count(), 1)
                badge.evaluate('node => window.originalCallContext = node')
                text = badge.inner_text()
                self.assertTrue(text.strip())
                divider.press('Home')
                facts = pane.evaluate('''pane => {
                    const bounds = pane.getBoundingClientRect();
                    const head = pane.querySelector('.tl-recorded-call-head');
                    const context = pane.querySelector('.tl-call-context');
                    const controls = [...head.querySelectorAll('button')].filter(button => button.getClientRects().length);
                    const fits = controls.every(button => {
                        const rect = button.getBoundingClientRect();
                        return rect.top >= bounds.top + pane.clientTop &&
                            rect.bottom <= bounds.top + pane.clientTop + pane.clientHeight;
                    });
                    return {fits, controls:controls.length, same:context === originalCallContext,
                        inBody:context.parentElement.matches('.tl-recorded-call-body > .tl-left'),
                        afterNav:context.getBoundingClientRect().top >= pane.querySelector('.tl-recorded-call-nav')
                            .getBoundingClientRect().bottom,
                        scrollTop:pane.scrollTop};
                }''')
                self.assertEqual(facts, dict(fits=True, controls=4, same=True, inBody=True, afterNav=True, scrollTop=0))
                self.assertEqual(badge.inner_text(), text)
                self.open_menu()
                self.page.keyboard.press('Escape')
                self.page.set_viewport_size(dict(width=900, height=960))
                divider.press('End')
                self.assertTrue(badge.evaluate('node => node === originalCallContext && node.parentElement.matches(".tl-recorded-call-head")'))
                self.assertEqual(badge.inner_text(), text)
                self.assert_unchanged()

    def test_call_actions_follow_the_header_width_instead_of_the_viewport(self):
        for mode, theme in (('single', 'light'), ('sidecar', 'dark')):
            with self.subTest(mode=mode, theme=theme):
                self.open(kind='symbols', mode=mode, width=844, height=390, theme=theme, touch=True)
                divider = self.page.get_by_role('separator', name='Resize details')
                self.assertEqual(self.trigger.inner_text(), 'Actions ▾')
                divider.press('Home')
                self.assertEqual(self.trigger.inner_text(), '⋯')
                self.assert_return_target(self.trigger, touch=True)
                self.trigger.tap()
                self.assertTrue(self.menu.is_visible())
                self.page.keyboard.press('Escape')
                self.assertTrue(self.trigger.evaluate('button => button === document.activeElement'))
                self.assert_unchanged()

                divider.press('Enter')
                self.assertEqual(self.trigger.inner_text(), 'Actions ▾')
                for width, text in ((280, '⋯'), (300, '⋯'), (320, 'Actions ▾')):
                    self.page.set_viewport_size({'width': width, 'height': 640})
                    self.page.wait_for_function('''text => document.querySelector('[data-call-actions]').innerText === text''', arg=text)
                    self.assertEqual(self.trigger.get_attribute('aria-label'), 'Selected call actions')
                    box = self.trigger.bounding_box()
                    self.assertGreaterEqual(box['width'], 44)
                    self.assertGreaterEqual(box['height'], 44)
                    self.assert_unchanged()

    def test_touch_targets_stay_44px_in_landscape_and_on_wide_tablets(self):
        for mode, viewport in product(('single', 'sidecar'), ((844, 390), (1024, 768), (1280, 800))):
            with self.subTest(mode=mode, viewport=viewport):
                self.open(mode=mode, width=viewport[0], height=viewport[1], touch=True)
                self.trigger.tap()
                self.assertTrue(self.menu.is_visible())
                self.assertGreaterEqual(self.trigger.bounding_box()['height'], 44)
                if viewport == (844, 390):
                    opener, popup = self.trigger.bounding_box(), self.menu.bounding_box()
                    self.assertTrue(popup['x'] + popup['width'] <= opener['x'] or
                                    opener['x'] + opener['width'] <= popup['x'] or
                                    popup['y'] + popup['height'] <= opener['y'] or
                                    opener['y'] + opener['height'] <= popup['y'])
                for item in self.menu.locator('[role=menuitem]').all():
                    self.assertGreaterEqual(item.bounding_box()['height'], 44)
                self.trigger.tap()
                self.assertFalse(self.menu.is_visible())
                self.assert_unchanged()

    def test_wide_touch_tablet_can_expand_restore_and_close_from_the_detail_header(self):
        for mode, viewport in product(('single', 'sidecar'), ((1024, 768), (1280, 800))):
            with self.subTest(mode=mode, viewport=viewport):
                self.open(mode=mode, width=viewport[0], height=viewport[1], touch=True)
                pane = self.page.locator('.tl-detail')
                canvas = self.page.locator('.tl-canvas')
                close = pane.get_by_role('button', name='Close details')
                expand = pane.get_by_role('button', name='Expand details into the chart area')
                restore = pane.get_by_role('button', name='Restore chart and detail split')
                divider = self.page.get_by_role('separator', name='Resize details')
                divider.press('Home')
                divider.press('ArrowDown')
                short_height = pane.bounding_box()['height']
                for button in (expand, close):
                    box = button.bounding_box()
                    self.assertGreaterEqual(box['width'], 44)
                    self.assertGreaterEqual(box['height'], 44)
                    self.assertTrue(button.evaluate('''button => {
                        const {left, right, top, bottom} = button.getBoundingClientRect();
                        return [left + 2, right - 2].every(x => [top + 2, bottom - 2].every(y =>
                            button.contains(document.elementFromPoint(x, y))));
                    }'''))
                expand.tap()
                self.assertFalse(canvas.is_visible())
                self.assertGreaterEqual(restore.bounding_box()['width'], 44)
                restore.tap()
                self.assertTrue(canvas.is_visible())
                self.assertEqual(pane.bounding_box()['height'], short_height)
                self.assert_unchanged()
                close.tap()
                self.assertFalse(pane.is_visible())
                self.assertTrue(canvas.evaluate('element => document.activeElement === element'))

    def test_landscape_call_navigation_fits_at_both_split_limits(self):
        for mode, viewport in product(('single', 'sidecar'), ((660, 375), (760, 360), (844, 390))):
            with self.subTest(mode=mode, viewport=viewport):
                self.open(kind='user', mode=mode, width=viewport[0], height=viewport[1], touch=True)
                divider = self.page.get_by_role('separator', name='Resize details')
                self.assertEqual(divider.get_attribute('aria-orientation'), 'vertical')
                nav = self.page.get_by_role('group', name='Recorded call navigation')
                parent = nav.get_by_role('button', name='Recorded parent (↑): main')
                for key in ('Home', 'Enter', 'End', 'Home'):
                    divider.press(key)
                    bounds = nav.bounding_box()
                    buttons = nav.get_by_role('button').all()
                    self.assertEqual(len(buttons), 4)
                    first = buttons[0].bounding_box()
                    for button in buttons:
                        box = button.bounding_box()
                        self.assertAlmostEqual(box['y'], first['y'], delta=1)
                        self.assertGreaterEqual(box['height'], 44)
                        self.assertGreaterEqual(box['width'], 44)
                        self.assertGreaterEqual(box['x'], bounds['x'])
                        self.assertLessEqual(box['x'] + box['width'], bounds['x'] + bounds['width'])
                    self.assertTrue(parent.is_enabled())
                    self.assert_unchanged()
                parent.tap()
                child = nav.get_by_role('button', name='First recorded child (↓): work<int>')
                child.press('Enter')
                self.assert_unchanged()
                self.assertEqual(divider.get_attribute('aria-valuenow'), divider.get_attribute('aria-valuemin'))

    def test_narrow_portrait_call_navigation_uses_one_row_and_wraps_larger_text(self):
        nav_geometry = '''nav => {
            const bounds = nav.getBoundingClientRect();
            const buttons = Array.from(nav.querySelectorAll('button'), button => button.getBoundingClientRect());
            return {rows: new Set(buttons.map(button => Math.round(button.top))).size,
                targets: buttons.every(button => button.width >= 44 && button.height >= 44),
                fits: buttons.every(button => button.left >= bounds.left && button.right <= bounds.right) &&
                    nav.scrollWidth <= nav.clientWidth + 1};
        }'''
        for (mode, theme), (width, height) in product(
                (('single', 'light'), ('sidecar', 'dark')), ((240, 390), (280, 480))):
            with self.subTest(mode=mode, theme=theme, width=width, height=height):
                self.open(kind='user', mode=mode, width=width, height=height, theme=theme, touch=True)
                nav = self.page.get_by_role('group', name='Recorded call navigation')
                self.assertEqual(nav.get_by_role('button').count(), 4)
                self.assertEqual(nav.evaluate(nav_geometry), dict(rows=1, targets=True, fits=True))
                self.page.get_by_role('button', name='Expand details into the chart area').tap()
                self.assertEqual(nav.evaluate(nav_geometry), dict(rows=1, targets=True, fits=True))
                nav.scroll_into_view_if_needed()
                for button in nav.get_by_role('button').all():
                    self.assert_return_target(button, touch=True)
                nav.get_by_role('button', name='Recorded parent (↑): main').tap()
                self.assertEqual(self.page.evaluate('fgView("timeline").probe().sel()'), 0)
                nav.get_by_role('button', name='First recorded child (↓): work<int>').press('Enter')
                self.assert_unchanged()
                nav.evaluate("node => node.querySelectorAll('button').forEach(button => { button.style.fontSize = '16px'; })")
                self.assertEqual(nav.evaluate(nav_geometry), dict(rows=2, targets=True, fits=True))

    def test_expanded_call_navigation_at_an_endpoint_keeps_keyboard_focus(self):
        cases = (('user', 'parent', 'child', 0), ('focus', 'next', 'previous', 2))
        for (kind, forward, reverse, target), mode, viewport, touch in product(
                cases, ('single', 'sidecar'), ((390, 844), (1024, 600)), (False, True)):
            with self.subTest(kind=kind, mode=mode, viewport=viewport, touch=touch):
                self.open(kind=kind, mode=mode, width=viewport[0], height=viewport[1], touch=touch)
                self.page.get_by_role('separator', name='Resize details').press('Home')
                self.page.get_by_role('button', name='Expand details into the chart area').press('Enter')
                canvas = self.page.locator('.tl-canvas')
                self.assertFalse(canvas.is_visible())
                advance = self.page.locator(f'[data-call-nav={forward}]')
                back = self.page.locator(f'[data-call-nav={reverse}]')
                advance.focus()
                for index, disabled, focused in ((target, advance, back), (1, back, advance)):
                    self.page.keyboard.press('Enter')
                    self.assertEqual(self.page.evaluate('fgView("timeline").probe().sel()'), index)
                    self.assertTrue(disabled.is_disabled())
                    self.assertTrue(focused.evaluate('''button => {
                        const rect = button.getBoundingClientRect();
                        return button === document.activeElement && button.contains(document.elementFromPoint(
                            rect.left + rect.width / 2, rect.top + rect.height / 2));
                    }'''))
                self.assertFalse(canvas.is_visible())

    def test_short_split_call_navigation_keeps_the_focus_visible_after_paint(self):
        for mode, viewport, touch in product(('single', 'sidecar'), ((390, 844), (1024, 600)), (False, True)):
            with self.subTest(mode=mode, viewport=viewport, touch=touch):
                self.open(kind='user', mode=mode, width=viewport[0], height=viewport[1], touch=touch)
                divider = self.page.get_by_role('separator', name='Resize details')
                divider.press('Home')
                height = divider.get_attribute('aria-valuenow')
                self.assertEqual(height, divider.get_attribute('aria-valuemin'))
                parent = self.page.locator('[data-call-nav=parent]')
                child = self.page.locator('[data-call-nav=child]')
                parent.focus()
                position = self.page.evaluate('[scrollX, scrollY]')
                for index, focus in ((0, child), (1, parent)):
                    self.page.keyboard.press('Enter')
                    self.assertEqual(self.page.evaluate('fgView("timeline").probe().sel()'), index)
                    self.assertTrue(focus.evaluate('''async button => {
                        await new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)));
                        const rect = button.getBoundingClientRect();
                        const pane = button.closest('.tl-detail'), top = pane.getBoundingClientRect().top + pane.clientTop;
                        return button === document.activeElement && rect.top >= top && rect.bottom <= top + pane.clientHeight &&
                            button.contains(document.elementFromPoint(
                                rect.left + rect.width / 2, rect.top + rect.height / 2));
                    }'''))
                    self.assertEqual(self.page.evaluate('[scrollX, scrollY]'), position)
                    self.assertEqual(divider.get_attribute('aria-valuenow'), height)
                self.assertTrue(self.page.locator('.tl-canvas').is_visible())

    def test_short_split_actions_navigate_without_leaving_the_visible_header(self):
        for mode, touch in product(('single', 'sidecar'), (False, True)):
            with self.subTest(mode=mode, touch=touch):
                self.open(kind='focus', mode=mode, width=390, height=295, touch=touch)
                short = self.page.locator('.tl-detail')
                self.page.get_by_role('separator', name='Resize details').press('Home')
                self.page.wait_for_function('() => document.getElementById("tlDetail").classList.contains("tl-detail-short")')
                self.trigger.scroll_into_view_if_needed()
                position = self.page.evaluate('[scrollX, scrollY]')
                group = self.menu.get_by_role('group', name='Navigate recorded calls')
                for action, index in (('parent', 0), ('child', 1), ('next', 2), ('previous', 1)):
                    self.open_menu()
                    item = group.locator(f'[data-call-action="navigate-{action}"]')
                    self.assertEqual(item.count(), 1)
                    self.assertTrue(item.is_enabled())
                    self.assertEqual(item.get_attribute('aria-label'), short.locator(f'[data-call-nav={action}]').get_attribute('aria-label'))
                    if touch:
                        self.assertGreaterEqual(item.bounding_box()['height'], 44)
                        item.tap()
                    else:
                        item.press('Enter')
                    self.assertEqual(self.page.evaluate('fgView("timeline").probe().sel()'), index)
                    self.assertFalse(self.menu.is_visible())
                    self.assertTrue(self.trigger.evaluate('''async button => {
                        await new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)));
                        const box = button.getBoundingClientRect();
                        return document.activeElement === button && button.contains(document.elementFromPoint(
                            box.left + box.width / 2, box.top + box.height / 2));
                    }'''))
                    self.assertEqual(self.page.evaluate('[scrollX, scrollY]'), position)
                self.open_menu()
                self.assertTrue(group.locator('[data-call-action="navigate-previous"]').is_disabled())
                self.page.keyboard.press('Escape')
                self.page.set_viewport_size(dict(width=1440, height=960))
                self.page.get_by_role('separator', name='Resize details').press('End')
                self.open_menu()
                self.assertFalse(group.is_visible())

    def test_short_split_call_actions_fit_navigation_above_the_detail_shortcuts(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open(kind='focus', mode=mode, width=390, height=295, touch=True)
                self.page.get_by_role('separator', name='Resize details').press('Home')
                self.trigger.tap()
                group = self.menu.get_by_role('group', name='Navigate recorded calls')
                measure = '''group => {
                    const bounds = group.getBoundingClientRect(), menu = group.closest('#tlCallActions');
                    const boxes = [...group.querySelectorAll('button')].map(button => button.getBoundingClientRect());
                    return {rows:new Set(boxes.map(box => box.top)).size, buttons:boxes.length,
                        height:bounds.height, targets:boxes.every(box => Math.min(box.width, box.height) >= 44),
                        bounded:menu.scrollWidth === menu.clientWidth && boxes.every(box =>
                            box.left >= bounds.left && box.right <= bounds.right),
                        shortcut:menu.querySelector('[data-call-detail-actions] button')?.getBoundingClientRect().bottom,
                        menuBottom:menu.getBoundingClientRect().bottom - menu.clientTop};
                }'''
                desktop_width = group.evaluate(measure)
                self.assertEqual((desktop_width['rows'], desktop_width['buttons']), (1, 4), desktop_width)
                self.assertTrue(desktop_width['targets'] and desktop_width['bounded'], desktop_width)
                self.assertLessEqual(desktop_width['height'], 68)
                self.assertLessEqual(desktop_width['shortcut'], desktop_width['menuBottom'])
                self.page.keyboard.press('Escape')
                self.page.evaluate('''() => Object.defineProperty(visualViewport, 'width', {configurable:true, value:200})''')
                self.trigger.press('ArrowDown')
                zoomed = group.evaluate(measure)
                self.assertGreater(zoomed['rows'], 1, zoomed)
                self.assertEqual(zoomed['buttons'], 4, zoomed)
                self.assertTrue(zoomed['targets'] and zoomed['bounded'], zoomed)

    def test_call_navigation_shortcuts_keep_visible_focus_in_details(self):
        layouts = (('single', 390, 844, True, False), ('sidecar', 844, 390, True, False),
                   ('sidecar', 1024, 600, False, True))
        for mode, width, height, touch, expanded in layouts:
            with self.subTest(mode=mode, viewport=(width, height), expanded=expanded):
                self.open(kind='focus', mode=mode, width=width, height=height, touch=touch)
                divider = self.page.get_by_role('separator', name='Resize details')
                divider.press('Home')
                if expanded:
                    self.page.get_by_role('button', name='Expand details into the chart area').press('Enter')
                self.page.locator('[data-call-nav=parent]').focus()
                position = self.page.evaluate('[scrollX, scrollY]')
                for key, index, action in ((']', 2, 'previous'), (']', 2, 'previous'),
                                           ('[', 1, 'next'), ('ArrowUp', 0, 'child'),
                                           ('ArrowDown', 1, 'parent'), ('ArrowDown', 1, 'parent'),
                                           ('Enter', 0, 'child')):
                    self.page.keyboard.press(key)
                    self.assertEqual(self.page.evaluate('fgView("timeline").probe().sel()'), index, key)
                    self.assertTrue(self.page.locator(f'[data-call-nav={action}]').evaluate('''async button => {
                        await new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)));
                        const box = button.getBoundingClientRect();
                        return button === document.activeElement && button.contains(document.elementFromPoint(
                            box.left + box.width / 2, box.top + box.height / 2));
                    }'''), key)
                    self.assertEqual(self.page.evaluate('[scrollX, scrollY]'), position, key)
                self.assertEqual(self.page.locator('.tl-canvas').is_visible(), not expanded)

    def test_compact_actions_reveal_stack_and_move_only_the_detail_pane(self):
        for mode, viewport, touch in product(('single', 'sidecar'), ((320, 740), (568, 320), (844, 390)), (True, False)):
            with self.subTest(mode=mode, viewport=viewport, touch=touch):
                self.open(mode=mode, width=viewport[0], height=viewport[1], touch=touch)
                stack = self.page.locator('[data-stack-toggle]')
                rows = self.page.locator('[data-context-list=stack] [data-context-kind]')
                if touch:
                    self.trigger.tap()
                else:
                    self.trigger.click()
                action = self.menu.get_by_role('menuitem', name='Show call stack')
                back = self.page.get_by_role('button', name='Return to call details')
                self.assertTrue(action.is_visible())
                self.assertFalse(back.is_visible())
                self.assertEqual(rows.count(), 0)
                self.assertFalse(self.menu.get_by_role('menuitem', name='Show function prototype').is_visible())
                self.assertFalse(self.menu.get_by_role('menuitem', name='Show source excerpt').is_visible())
                scroll = '''() => [scrollX, scrollY, document.querySelector('.tl-scroll').scrollTop]'''
                before = self.page.evaluate(scroll)
                pane = self.page.locator('.tl-detail')
                detail_before = pane.evaluate('pane => [pane.scrollTop, pane.scrollLeft]')
                if viewport[0] < 640 or not touch:
                    action.press('Enter')
                else:
                    action.tap()
                self.assertFalse(self.menu.is_visible())
                self.assertEqual(stack.get_attribute('aria-expanded'), 'true')
                self.assertGreater(rows.count(), 0)
                self.assertTrue(stack.evaluate('''button => {
                    const rect = button.getBoundingClientRect();
                    return document.activeElement === button && button.contains(document.elementFromPoint(
                        (rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2));
                }'''))
                self.assertEqual(self.page.evaluate(scroll), before)
                self.assert_unchanged()
                self.assertGreaterEqual(back.bounding_box()['height'], 44)
                if viewport[0] < 640 or not touch:
                    self.page.keyboard.press('Shift+Tab')
                    self.assertTrue(back.evaluate('button => button === document.activeElement'))
                    self.page.keyboard.press('Tab')
                    self.assertTrue(stack.evaluate('button => button === document.activeElement'))
                    self.assertEqual(self.page.evaluate(scroll), before)
                    self.page.keyboard.press('Shift+Tab')
                    self.assertTrue(back.evaluate('button => button === document.activeElement'))
                    self.page.keyboard.press('Enter')
                else:
                    back.tap()
                self.assertFalse(back.is_visible())
                self.assertTrue(self.trigger.evaluate('button => button === document.activeElement'))
                self.assertEqual(pane.evaluate('pane => [pane.scrollTop, pane.scrollLeft]'), detail_before)
                self.assertEqual(self.page.evaluate(scroll), before)
                self.assertEqual(stack.get_attribute('aria-expanded'), 'true')
                self.assert_unchanged()

    def test_detail_section_jump_uses_the_clipped_visual_viewport(self):
        self.open(width=844, height=390, touch=True)
        self.page.evaluate('''() => {
            const pane = document.getElementById('tlDetail');
            const top = pane.getBoundingClientRect().top + 48;
            Object.defineProperty(visualViewport, 'offsetTop', {configurable: true, value: top});
            Object.defineProperty(visualViewport, 'height', {configurable: true, value: innerHeight - top - 8});
        }''')
        origin = self.page.evaluate('''() => [scrollX, scrollY, document.querySelector('.tl-scroll').scrollTop,
            document.getElementById('tlDetail').scrollTop]''')
        self.trigger.press('ArrowDown')
        self.menu.get_by_role('menuitem', name='Show call stack').press('Enter')
        heading = self.page.locator('[data-stack-toggle]')
        back = self.page.get_by_role('button', name='Return to call details', exact=True)
        visible_focus = '''button => {
            const rect = button.getBoundingClientRect();
            return button === document.activeElement && rect.top >= visualViewport.offsetTop &&
                rect.bottom <= visualViewport.offsetTop + visualViewport.height &&
                button.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
        }'''
        self.assertTrue(heading.evaluate(visible_focus))
        self.page.keyboard.press('Shift+Tab')
        self.assertTrue(back.evaluate(visible_focus))
        self.page.keyboard.press('Tab')
        self.assertTrue(heading.evaluate(visible_focus))
        self.page.evaluate('''() => { delete visualViewport.offsetTop; delete visualViewport.height; }''')
        self.page.keyboard.press('Escape')
        self.assertEqual(self.page.evaluate('''() => [scrollX, scrollY, document.querySelector('.tl-scroll').scrollTop,
            document.getElementById('tlDetail').scrollTop]'''), origin)
        self.assertTrue(self.trigger.evaluate('button => button === document.activeElement'))
        self.assert_unchanged()

    def test_short_touch_pane_keeps_section_back_and_heading_tappable_together(self):
        for mode, viewport in product(('single', 'sidecar'), ((320, 740), (568, 320))):
            with self.subTest(mode=mode, viewport=viewport):
                self.open(kind='focus', mode=mode, width=viewport[0], height=viewport[1], touch=True)
                self.page.evaluate('''() => {
                    const model = fgView('timeline').probe();
                    model.select(-1);
                    localStorage.setItem('fgTl.detailFrac', '0.09');
                    model.select(0);
                }''')
                pane = self.page.locator('.tl-detail')
                self.assertLess(pane.evaluate('node => node.clientHeight'), 80)
                scroll = '''() => [scrollY, document.querySelector('.tl-scroll').scrollTop,
                    document.querySelector('.tl-detail').scrollTop]'''
                for action, selector in (('Show call stack', '[data-stack-toggle]'),
                                          ('Show source excerpt', '.tl-cs > summary')):
                    self.trigger.tap()
                    before = self.page.evaluate(scroll)
                    self.menu.get_by_role('menuitem', name=action).tap()
                    back = self.page.get_by_role('button', name='Return to call details', exact=True)
                    point = back.evaluate('''async (button, selector) => {
                        for (let frame = 0; frame < 4; frame++) await new Promise(requestAnimationFrame);
                        const heading = document.querySelector(selector);
                        const center = rect => [rect.left + rect.width / 2, rect.top + rect.height / 2];
                        const bounds = button.getBoundingClientRect();
                        if (bounds.width < 44 || bounds.height < 44 || document.activeElement !== heading ||
                                !heading.contains(document.elementFromPoint(...center(heading.getBoundingClientRect()))) ||
                                !button.contains(document.elementFromPoint(...center(bounds)))) return null;
                        return center(bounds);
                    }''', selector)
                    self.assertIsNotNone(point, action)
                    self.page.touchscreen.tap(*point)
                    self.assertFalse(back.is_visible())
                    self.assertTrue(self.trigger.evaluate('button => document.activeElement === button'))
                    self.assertEqual(self.page.evaluate(scroll), before)
                    self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 0)
                    if action == 'Show call stack':
                        self.assertEqual(pane.locator(selector).get_attribute('aria-expanded'), 'true')
                    else:
                        self.assertTrue(pane.locator('.tl-cs').evaluate('section => section.open'))

    def test_short_stack_jump_keeps_csv_touch_target_beside_back_and_stack(self):
        for mode, width, font_size in product(('single', 'sidecar'), (280, 320), (12, 16)):
            with self.subTest(mode=mode, width=width, font_size=font_size):
                self.open(kind='focus', mode=mode, width=width, height=740, touch=True)
                self.page.add_style_tag(content=f'.tl-stack-head .tl-btn {{font-size:{font_size}px}}')
                self.page.evaluate('''() => {
                    const model = fgView('timeline').probe();
                    model.select(-1);
                    localStorage.setItem('fgTl.detailFrac', '0.09');
                    model.select(1);
                }''')
                export = self.page.get_by_role('button', name='Export stack CSV', exact=True)
                self.assertEqual(export.inner_text(), 'Export stack CSV')
                with self.page.expect_download() as expected:
                    export.press('Enter')
                original = Path(expected.value.path()).read_bytes()
                self.trigger.tap()
                self.menu.get_by_role('menuitem', name='Show call stack', exact=True).tap()
                self.assertEqual(export.inner_text(), 'CSV')
                stack = self.page.get_by_role('button', name='Call stack (2 recorded frames)', exact=True)
                self.assertEqual(stack.inner_text(), 'Call stack (2)')
                points = self.page.evaluate('''() => {
                    const pane = document.querySelector('.tl-detail');
                    const bounds = pane.getBoundingClientRect();
                    return ['[data-detail-return]', '[data-stack-toggle]', '[data-context-export=stack]'].map(selector => {
                        const control = pane.querySelector(selector), box = control.getBoundingClientRect();
                        const point = [box.left + box.width / 2, box.top + box.height / 2];
                        return box.height >= 44 && box.top >= bounds.top && box.bottom <= bounds.bottom &&
                            control.contains(document.elementFromPoint(...point)) ? point : null;
                    });
                }''')
                self.assertTrue(all(points), points)
                scroll = '''() => [scrollY, document.querySelector('.tl-scroll').scrollTop,
                    document.querySelector('.tl-detail').scrollTop]'''
                before = self.page.evaluate(scroll)
                with self.page.expect_download() as actual:
                    self.page.touchscreen.tap(*points[2])
                self.assertEqual(Path(actual.value.path()).read_bytes(), original)
                self.assertEqual(self.page.evaluate(scroll), before)
                self.page.touchscreen.tap(*points[0])
                self.assertEqual(export.inner_text(), 'Export stack CSV')
                self.assertEqual(stack.get_attribute('aria-expanded'), 'true')

    def test_short_desktop_split_defers_stack_and_keeps_section_shortcuts(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open(kind='focus', mode=mode, width=1440, height=900)
                self.page.evaluate('''() => {
                    const model = fgView('timeline').probe();
                    model.select(-1);
                    localStorage.setItem('fgTl.detailFrac', '0.12');
                    model.select(0);
                }''')
                pane = self.page.locator('.tl-detail')
                self.assertTrue(pane.evaluate("node => node.classList.contains('tl-detail-short')"))
                stack = pane.locator('[data-stack-toggle]')
                rows = pane.locator('[data-context-list=stack] [data-context-kind]')
                self.assertEqual(rows.count(), 0)
                self.assertEqual(stack.get_attribute('aria-expanded'), 'false')
                self.assertTrue(pane.get_by_role('button', name='Export stack CSV').is_enabled())
                before = self.page.evaluate('''() => [scrollY, document.querySelector('.tl-scroll').scrollTop,
                    fgView('timeline').probe().sel(), fgView('timeline').probe().view()]''')
                self.open_menu()
                self.assertTrue(self.menu.get_by_role('menuitem', name='Show source excerpt').is_visible())
                self.menu.get_by_role('menuitem', name='Show call stack').press('Enter')
                self.assertEqual(stack.get_attribute('aria-expanded'), 'true')
                self.assertEqual(rows.count(), 1)
                self.page.keyboard.press('Shift+Tab')
                self.page.get_by_role('button', name='Return to call details').press('Enter')
                self.assertTrue(self.trigger.evaluate('node => node === document.activeElement'))
                self.assertEqual(self.page.evaluate('''() => [scrollY, document.querySelector('.tl-scroll').scrollTop,
                    fgView('timeline').probe().sel(), fgView('timeline').probe().view()]'''), before)

                self.page.evaluate("() => fgView('timeline').probe().select(1)")
                self.assertEqual(rows.count(), 0)
                self.assertEqual(stack.get_attribute('aria-expanded'), 'false')
                self.page.evaluate('''() => {
                    const model = fgView('timeline').probe();
                    model.select(-1);
                    localStorage.setItem('fgTl.detailFrac', '0.32');
                    model.select(1);
                }''')
                self.assertEqual(stack.count(), 0)
                self.assertEqual(rows.count(), 2)

    def test_clamped_split_uses_actual_height_for_first_stack(self):
        for mode, touch in (('single', False), ('sidecar', True)):
            with self.subTest(mode=mode, touch=touch):
                self.open(kind='focus', mode=mode, width=1024, height=520, touch=touch)
                self.page.add_style_tag(content='.tl-toolbar { min-height: 150px; }')
                select = '''fraction => {
                    const model = fgView('timeline').probe();
                    model.select(-1);
                    localStorage.setItem('fgTl.detailFrac', fraction);
                    const observer = new MutationObserver(() => {});
                    observer.observe(document.getElementById('tlDetail'), {subtree: true, childList: true});
                    model.select(1);
                    const lateMount = observer.takeRecords().some(record =>
                        [...record.addedNodes].some(node => node.matches?.('[data-context-list=stack]')));
                    observer.disconnect();
                    return lateMount;
                }'''
                self.assertFalse(self.page.evaluate(select, '0.45'))
                pane = self.page.locator('.tl-detail')
                self.assertTrue(pane.evaluate("node => node.clientHeight < 140 && node.classList.contains('tl-detail-short')"))
                stack = pane.locator('[data-stack-toggle]')
                rows = pane.locator('[data-context-list=stack] [data-context-kind]')
                self.assertEqual(rows.count(), 0)
                self.assertEqual(stack.get_attribute('aria-expanded'), 'false')
                self.assertTrue(pane.get_by_role('button', name='Export stack CSV').is_enabled())
                stack.press('Enter')
                self.assertEqual(rows.count(), 2)
                self.page.set_viewport_size({'width': 1024, 'height': 850})
                self.page.wait_for_function("() => !document.querySelector('.tl-detail').classList.contains('tl-detail-short')")
                self.assertEqual(stack.get_attribute('aria-expanded'), 'true')
                self.assertEqual(rows.count(), 2)
                self.assertFalse(self.page.evaluate(select, '0.45'))
                self.assertEqual(stack.count(), 0)
                self.assertEqual(rows.count(), 2)
                self.page.add_style_tag(content='.tl-detail.show { border-block-width: 8px; }')
                self.assertTrue(self.page.evaluate(select, '0.17'))
                self.assertTrue(pane.evaluate("node => node.clientHeight < 140 && node.classList.contains('tl-detail-short')"))
                self.assertEqual(stack.get_attribute('aria-expanded'), 'false')
                self.assertEqual(rows.count(), 0)
                self.assert_unchanged()

    def test_first_desktop_canvas_selection_sizes_the_split_before_mounting_details(self):
        self.open(kind='focus')
        point = self.page.evaluate('''async () => {
            const model = fgView('timeline').probe();
            model.select(-1);
            model.zoomTo(model.sStart[1], model.sEnd[1], -1);
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const pane = document.getElementById('tlDetail');
            const height = Object.getOwnPropertyDescriptor(Element.prototype, 'clientHeight').get;
            window.initialSplitReads = [];
            Object.defineProperty(pane, 'clientHeight', {configurable: true, get() {
                initialSplitReads.push({head: !!this.querySelector('[data-recorded-call]'),
                    body: !!this.querySelector('.tl-detail-body')});
                return height.call(this);
            }});
            return model.xyOf(1);
        }''')
        self.page.mouse.click(*point)
        facts = self.page.evaluate('''() => {
            const pane = document.getElementById('tlDetail');
            delete pane.clientHeight;
            const model = fgView('timeline').probe();
            return {first: initialSplitReads[0], selected: model.sel(),
                stack: pane.querySelectorAll('[data-context-list=stack] [data-context-kind]').length,
                visible: document.elementFromPoint(...model.xyOf(1))?.matches('.tl-canvas'),
                focused: document.activeElement.matches('.tl-canvas')};
        }''')
        self.assertEqual(facts, dict(first=dict(head=False, body=False), selected=1, stack=2, visible=True, focused=True))
        self.trigger.click()
        self.menu.get_by_role('menuitem', name='Open in Text').wait_for()

    def test_compact_action_reveals_source_and_restores_the_split(self):
        layouts = ((390, 844, True, False), (280, 390, True, False), (568, 320, True, False),
                   (844, 390, True, True), (1024, 480, False, False))
        for mode, (width, height, touch, narrow) in product(('single', 'sidecar'), layouts):
            with self.subTest(mode=mode, width=width, height=height):
                self.open(kind='focus', mode=mode, width=width, height=height, touch=touch)
                toolbar = self.page.locator('.tl-toolbar')
                if width == 280:
                    self.page.add_style_tag(content='.tl-toolbar { min-height: 150px; }')
                if narrow:
                    self.page.get_by_role('separator', name='Resize details').press('Home')
                self.page.evaluate("fgView('timeline').probe().select(0)")
                source = self.page.locator('.tl-cs')
                source.evaluate('box => { box.open = false; }')
                notes = self.page.locator('.tl-notes')
                if width == 280:
                    toolbar.locator('[data-act=notes]').tap()
                    self.assertTrue(notes.is_visible())
                (self.trigger.tap if touch else self.trigger.click)()
                self.assertFalse(source.evaluate('box => box.open'))
                snapshot = '''() => {
                    const pane = document.getElementById('tlDetail'), model = fgView('timeline').probe();
                    return [scrollY, pane.scrollTop, pane.scrollLeft, pane.clientHeight, pane.clientWidth,
                        document.querySelector('.tl-scroll').scrollTop, model.sel(), model.view()];
                }'''
                before = self.page.evaluate(snapshot)
                action = self.menu.get_by_role('menuitem', name='Show source excerpt')
                (action.tap if touch else action.click)()
                self.assertEqual(toolbar.is_visible(), width != 280)
                if width == 280:
                    self.assertFalse(notes.is_visible())
                line = source.locator('.source-line').first
                line.wait_for(state='attached')
                self.assertTrue(source.evaluate('box => box.open'))
                self.assertTrue(source.locator('summary').evaluate('''summary => {
                    const rect = summary.getBoundingClientRect();
                    return document.activeElement === summary && summary.contains(
                        document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2));
                }'''))
                self.assertIn('outer', line.inner_text())
                self.assertTrue(line.evaluate('''line => {
                    const box = line.getBoundingClientRect();
                    return [box.top + 1, box.bottom - 1].every(y =>
                        line.contains(document.elementFromPoint(box.left + 8, y)));
                }'''), f'source line is mounted but clipped: {line.bounding_box()}')
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 0)
                back = self.page.get_by_role('button', name='Return to call details', exact=True)
                self.assert_return_target(back, touch=True)
                (back.tap if touch else back.click)()
                self.assertFalse(back.is_visible())
                self.assertTrue(toolbar.is_visible())
                if width == 280:
                    self.assertTrue(notes.is_visible())
                self.assertTrue(self.trigger.evaluate('button => button === document.activeElement'))
                self.assertTrue(source.evaluate('box => box.open'))
                self.assertEqual(self.page.evaluate(snapshot), before)
                (self.trigger.tap if touch else self.trigger.click)()
                (action.tap if touch else action.click)()
                self.assert_return_target(back, touch=True)
                self.page.evaluate("fgView('timeline').probe().select(1)")
                self.assertFalse(back.is_visible())
                self.assertEqual(self.page.evaluate(snapshot)[3:5], before[3:5])

    def test_delayed_source_shortcut_does_not_reenter_reading_space_after_return(self):
        for return_early in (False, True):
            with self.subTest(return_early=return_early):
                self.open(kind='focus', mode='sidecar', width=280, height=390, touch=True)
                self.page.evaluate('''() => {
                    const load = fgData.loadSource.bind(fgData);
                    const ready = new Promise(resolve => { window.releaseSource = resolve; });
                    fgData.loadSource = (key, options) => Promise.all([load(key, options), ready]).then(([source]) => source);
                    fgView('timeline').probe().select(0);
                    window.sourceReadingPosition = () => {
                        const root = document.getElementById('timelineView'), pane = document.getElementById('tlDetail');
                        const heading = pane.querySelector('.tl-cs > summary');
                        return [scrollY, pane.scrollTop, pane.scrollLeft, root.clientHeight, heading.getBoundingClientRect().top];
                    };
                    document.addEventListener('click', function onSourceAction(event) {
                        if (!event.target.closest('[data-call-action=detail-source]')) return;
                        document.removeEventListener('click', onSourceAction);
                        window.sourceOpeningPosition = sourceReadingPosition();
                    });
                }''')
                self.trigger.tap()
                self.menu.get_by_role('menuitem', name='Show source excerpt').tap()
                pane = self.page.locator('.tl-detail')
                source = pane.locator('.tl-cs-src')
                source.get_by_role('status').wait_for()
                self.assertEqual(source.get_attribute('aria-busy'), 'true')
                expanded = self.page.locator('#timelineView.tl-detail-expanded')
                expanded.wait_for()
                self.assertTrue(self.page.evaluate('''async () => {
                    for (let frame = 0; frame < 3; frame++) await new Promise(requestAnimationFrame);
                    return JSON.stringify(sourceReadingPosition()) === JSON.stringify(sourceOpeningPosition);
                }'''), 'hiding the toolbar moved the reader while the excerpt was still loading')
                scroll = 'pane => [scrollY, pane.scrollTop, pane.scrollLeft]'
                if return_early:
                    back = pane.get_by_role('button', name='Return to call details', exact=True)
                    self.assert_return_target(back, True)
                    back.tap()
                    self.assertTrue(self.trigger.evaluate('button => button === document.activeElement'))
                before = pane.evaluate(scroll)
                self.page.evaluate('releaseSource()')
                first = source.locator('.source-line').first
                first.wait_for(state='attached')
                self.assertIsNone(source.get_attribute('aria-busy'))
                self.assertEqual(pane.evaluate(scroll), before)
                self.assertEqual(expanded.is_visible(), not return_early)
                if return_early:
                    self.assertTrue(self.trigger.evaluate('button => button === document.activeElement'))
                    self.assertFalse(pane.locator('[data-detail-return]').count())
                else:
                    self.assertTrue(first.evaluate('''line => {
                        const bounds = line.getBoundingClientRect();
                        return line.contains(document.elementFromPoint(bounds.left + 8, bounds.bottom - 1));
                    }'''))
                    self.page.keyboard.press('Escape')
                    self.assertTrue(self.trigger.evaluate('button => button === document.activeElement'))
                    self.assertFalse(expanded.is_visible())

    def test_short_source_heading_keeps_mode_and_return_on_the_same_row(self):
        for width, height in ((240, 390), (280, 240), (320, 330)):
            with self.subTest(width=width, height=height):
                self.open(kind='focus', mode='sidecar', width=width, height=height, touch=True)
                self.page.evaluate('''() => {
                    const load = fgData.loadSource.bind(fgData);
                    const ready = new Promise(resolve => { window.releaseSource = resolve; });
                    fgData.loadSource = (key, options) => Promise.all([load(key, options), ready]).then(([source]) => source);
                    fgView('timeline').probe().select(0);
                }''')
                self.trigger.tap()
                self.menu.get_by_role('menuitem', name='Show source excerpt').tap()
                section = self.page.locator('.tl-detail-source-jump')
                heading = section.locator('.tl-cs > summary')
                source = section.locator('.tl-cs-src')
                source.get_by_role('status').wait_for()
                loading = heading.bounding_box()
                self.page.evaluate('releaseSource()')
                source.locator('.source-line').first.wait_for(state='attached')
                mode = section.get_by_role('button', name='Source: chain', exact=True)
                back = section.get_by_role('button', name='Return to call details', exact=True)
                self.assertEqual(heading.bounding_box(), loading)
                self.assertEqual(mode.bounding_box()['y'], back.bounding_box()['y'])
                self.assert_return_target(mode, touch=True)
                self.assert_return_target(back, touch=True)
                mode.tap()
                section.get_by_role('button', name='Source: flat', exact=True).wait_for()
                self.assertEqual(heading.bounding_box(), loading)
                self.assertTrue(heading.evaluate('summary => summary.parentElement.open'))
                back.tap()
                self.assertTrue(self.trigger.evaluate('button => button === document.activeElement'))

    def test_recorded_calls_without_source_do_not_mount_source_controls(self):
        self.open(kind='timeline-only')
        mounted = self.page.evaluate('''() => {
            const detail = document.querySelector('.tl-detail');
            const model = fgView('timeline').probe();
            const descriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
            const inserted = [];
            Object.defineProperty(detail, 'innerHTML', {
                configurable: true, get: descriptor.get,
                set(markup) {
                    descriptor.set.call(this, markup);
                    inserted.push(!!this.querySelector('.tl-cs'));
                }
            });
            try {
                model.select(0);
                model.select(1);
                model.select(0);
            } finally { delete detail.innerHTML; }
            return inserted;
        }''')
        self.assertEqual(mounted, [False, False, False])
        self.assertTrue(self.trigger.is_visible())
        self.assertEqual(self.page.locator('.tl-cs').count(), 0)

    def test_repeated_call_site_reuses_source_without_copying_inline_frame_choices(self):
        self.open(kind='focus', width=844, height=390, touch=True)
        first, second = self.page.evaluate('''() => {
            const lines = fgData.read('tlData').cols.line;
            const first = lines.findIndex(line => fgSourceForLine(line) !== null);
            const key = fgSourceForLine(lines[first]);
            const second = lines.findIndex((line, index) => index !== first && line !== lines[first]);
            const lookup = fgSourceForLine;
            fgSourceForLine = line => line === lines[second] ? key : lookup(line);
            const read = fgSourceHtml;
            fgSourceHtml = source => source === key ?
                '<div class="location-link">helper at focus.c:2</div><div class="source-line">int helper;</div>' +
                '<div class="location-link"> (inlined by) outer at focus.c:1</div><div class="source-line">int outer;</div>' : read(source);
            return [first, second];
        }''')
        self.assertGreaterEqual(first, 0)
        self.assertGreaterEqual(second, 0)
        source = self.page.locator('.tl-cs-src')
        heading = self.page.locator('.tl-cs > summary')
        self.page.evaluate("index => fgView('timeline').probe().select(index)", first)
        heading.scroll_into_view_if_needed()
        source.locator('.tl-source-code').first.wait_for(state='attached')
        source.evaluate('''source => {
            window.readSourceState = node => {
                const clone = node.cloneNode(true), frames = [...clone.querySelectorAll('.src-frame')];
                const opened = frames.map(frame => frame.open);
                for (const frame of frames) frame.open = false;
                return {markup:clone.innerHTML, frames:opened};
            };
            window.previousSourceState = readSourceState(source);
            const frames = [...source.querySelectorAll('.src-frame')];
            frames[0].open = true;
            frames[frames.length - 1].open = false;
            const read = fgSourceHtml;
            window.repeatedSourceReads = 0;
            fgSourceHtml = key => { if (key === +source.dataset.src) repeatedSourceReads++; return read(key); };
        }''')
        before = self.page.evaluate('previousSourceState')
        self.assertEqual(before['frames'], [False, True])
        self.page.evaluate("index => fgView('timeline').probe().select(index)", second)
        heading.scroll_into_view_if_needed()
        source.locator('.tl-source-code').first.wait_for(state='attached')
        self.assertEqual(self.page.evaluate('repeatedSourceReads'), 0)
        self.assertEqual(source.evaluate('node => readSourceState(node)'), before)
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), second)
        self.assertEqual(heading.locator('[data-fg-source-mode]').count(), 1)

    def test_recorded_source_waits_for_its_open_summary_to_enter_the_pane(self):
        for mode, touch in product(('single', 'sidecar'), (True, False)):
            with self.subTest(mode=mode, touch=touch):
                width, height = (844, 390) if touch else (1440, 900)
                self.open(kind='focus', mode=mode, width=width, height=height, touch=touch)
                if not touch:
                    self.page.get_by_role('separator', name='Resize details').press('Home')
                self.page.evaluate('''async () => {
                    const model = fgView('timeline').probe();
                    model.select(0);
                    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                }''')
                source = self.page.locator('.tl-cs')
                self.assertTrue(source.evaluate('box => box.open'))
                self.assertTrue(source.evaluate('''box => box.getBoundingClientRect().top >
                    box.closest('.tl-detail').getBoundingClientRect().bottom'''))
                self.assertEqual(source.locator('.tl-cs-src > *').count(), 0)
                self.trigger.tap() if touch else self.trigger.click()
                self.assertEqual(source.locator('.tl-cs-src > *').count(), 0)
                self.page.keyboard.press('Escape')
                source.locator('summary').first.scroll_into_view_if_needed()
                source.locator('.source-line').first.wait_for(state='attached')
                self.assertIn('outer', source.locator('.source-line').first.inner_text())
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 0)

    def test_pointer_movement_keeps_menu_open_and_hover_is_not_required(self):
        self.open()
        self.open_menu()
        bounds = self.menu.bounding_box()
        self.menu.evaluate('''menu => {
            const original = menu.getBoundingClientRect.bind(menu);
            window.actionMenuReads = 0;
            menu.getBoundingClientRect = () => { actionMenuReads++; return original(); };
        }''')
        self.page.mouse.move(bounds['x'] + 30, bounds['y'] + 20, steps=5)
        self.assertTrue(self.menu.is_visible())
        self.assertEqual(self.page.evaluate('actionMenuReads'), 0)
        self.trigger.click()
        self.assertFalse(self.menu.is_visible())
        self.trigger.press('Space')
        self.assertTrue(self.menu.is_visible())
        self.assertEqual(self.focused_action(), 'distribution')

    def test_arrows_home_end_escape_and_tab_preserve_selection_and_time_range(self):
        self.open()
        self.trigger.focus()
        self.trigger.press('ArrowUp')
        self.assertEqual(self.focused_action(), 'focus-function')
        for key, action in [('ArrowDown', 'distribution'), ('ArrowUp', 'focus-function'), ('Home', 'distribution'),
                            ('End', 'focus-function'), ('ArrowDown', 'distribution')]:
            self.page.keyboard.press(key)
            self.assertEqual(self.focused_action(), action)
        self.page.keyboard.press('Shift')
        self.assertTrue(self.menu.is_visible())
        self.page.keyboard.press('Escape')
        self.assertFalse(self.menu.is_visible())
        self.assertTrue(self.trigger.evaluate('element => element === document.activeElement'))
        for key in ('Tab', 'Shift+Tab'):
            self.trigger.press('ArrowDown')
            self.assertEqual(self.focused_action(), 'distribution')
            self.page.keyboard.press(key)
            self.assertFalse(self.menu.is_visible())
            self.assertFalse(self.menu.evaluate('element => element.contains(document.activeElement)'))
            self.assertFalse(self.trigger.evaluate('element => element === document.activeElement'))
        self.assert_unchanged()

    def test_distribution_action_matches_existing_shortcut_for_click_enter_and_space(self):
        for mode, key in product(('single', 'sidecar'), (None, 'Enter', 'Space')):
            with self.subTest(mode=mode, key=key):
                self.open(mode=mode)
                self.open_menu()
                item = self.menu.locator('[data-call-action=distribution]')
                if key:
                    item.press(key)
                else:
                    item.click()
                self.assertFalse(self.menu.is_visible())
                self.assertFalse(self.menu.evaluate('element => element.contains(document.activeElement)'))
                self.assertTrue(self.page.locator('.tl-canvas').evaluate('element => element === document.activeElement'))
                self.assertTrue(self.page.locator('.an-dist-head').is_visible())
                actual = self.page.locator('.tl-detail').inner_text()
                self.page.evaluate("fgView('timeline').probe().select(1)")
                self.trigger.focus()
                self.page.keyboard.press('H')
                self.assertEqual(self.page.locator('.tl-detail').inner_text(), actual)

    def test_clipped_menu_keeps_keyboard_focus_visible_without_scrolling_the_report(self):
        for mode, theme, fullscreen in product(('single', 'sidecar'), ('light', 'dark'),
                                                ('normal', 'native', 'page')):
            with self.subTest(mode=mode, theme=theme, fullscreen=fullscreen):
                self.open(mode=mode, theme=theme, width=568, height=320, touch=True,
                          native=fullscreen != 'page')
                if fullscreen != 'normal':
                    self.page.locator('[data-act=max]').click()
                    self.page.wait_for_function('''() =>
                        document.getElementById('timelineView').classList.contains('tl-fullscreen') &&
                        !document.querySelector('[data-act=max]').disabled''')
                    self.page.evaluate('''async () => {
                        for (let frame = 0; frame < 3; frame++) await new Promise(requestAnimationFrame);
                    }''')
                self.page.evaluate('''() => {
                    Object.defineProperty(visualViewport, 'height', {configurable:true, value:120});
                    Object.defineProperty(visualViewport, 'offsetTop', {configurable:true, value:80});
                }''')
                self.trigger.focus()
                self.page.locator('.tl-call-nav:not([aria-busy])').wait_for()
                self.page.evaluate('''() => {
                    window.actionMenuScroll = [scrollX, scrollY,
                        document.querySelector('.tl-detail').scrollTop,
                        document.querySelector('.tl-scroll').scrollTop];
                }''')
                self.trigger.press('ArrowUp')
                section_actions = self.menu.locator('[data-call-detail-actions] button').evaluate_all(
                    'buttons => buttons.map(button => button.dataset.callAction).reverse()')
                navigation_actions = self.menu.locator('[data-call-nav-actions] button:enabled').evaluate_all(
                    'buttons => buttons.map(button => button.dataset.callAction).reverse()')
                for key, action in ((None, 'focus-function'), ('Home', 'distribution'),
                                    ('End', 'focus-function'), ('ArrowDown', 'distribution'),
                                    ('ArrowUp', 'focus-function'), ('ArrowUp', 'focus-task'),
                                    *(('ArrowUp', section_action) for section_action in section_actions),
                                    *(('ArrowUp', navigation_action) for navigation_action in navigation_actions),
                                    ('ArrowUp', 'text'), ('ArrowUp', 'distribution')):
                    if key:
                        self.page.keyboard.press(key)
                    self.assertEqual(self.focused_action(), action)
                    facts = self.assert_menu_focus_visible()
                    self.assertEqual(facts['outer'], self.page.evaluate('actionMenuScroll'))
                    self.assertTrue(self.menu.is_visible())
                self.page.keyboard.press('Escape')
                self.assertTrue(self.trigger.evaluate('element => element === document.activeElement'))
                self.assert_unchanged()

    def test_short_window_without_visual_viewport_reveals_keyboard_targets(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open(mode=mode, width=844, height=160, touch=True)
                self.page.evaluate('''() => {
                    Object.defineProperty(window, 'visualViewport', {configurable:true, value:undefined});
                }''')
                self.trigger.press('ArrowUp')
                self.assertEqual(self.focused_action(), 'focus-function')
                self.assertTrue(self.menu.evaluate('menu => menu.scrollHeight > menu.clientHeight'))
                self.assert_menu_focus_visible()
                self.page.keyboard.press('Home')
                self.assertEqual(self.focused_action(), 'distribution')
                self.assert_menu_focus_visible()
                self.page.keyboard.press('Escape')
                self.assert_unchanged()

    def test_menu_wheel_stays_inside_menu_at_both_boundaries(self):
        for mode, theme, clipped in product(('single', 'sidecar'), ('light', 'dark'), (False, True)):
            with self.subTest(mode=mode, theme=theme, clipped=clipped):
                self.open(mode=mode, theme=theme, width=568, height=320 if clipped else 560, touch=True)
                self.page.evaluate('''clipped => {
                    Object.defineProperty(visualViewport, 'height', {configurable:true, value:clipped ? 120 : innerHeight});
                    Object.defineProperty(visualViewport, 'offsetTop', {configurable:true, value:clipped ? 80 : 0});
                    if (!clipped) {
                        document.body.style.minHeight = '120vh';
                        scrollTo({top:24, behavior:'instant'});
                    }
                    window.actionWheelEvents = [];
                    document.getElementById('tlCallActions').addEventListener('wheel', event => {
                        actionWheelEvents.push(event.defaultPrevented);
                    }, {passive:true});
                }''', clipped)
                self.page.evaluate('''() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))''')
                self.trigger.press('ArrowDown')
                original = self.page.evaluate('''[scrollX, scrollY,
                    document.querySelector('.tl-detail').scrollTop,
                    document.querySelector('.tl-scroll').scrollTop]''')
                self.assertGreater(original[1], 0)
                maximum = self.menu.evaluate('menu => menu.scrollHeight - menu.clientHeight')
                if clipped:
                    self.assertGreater(maximum, 45)
                else:
                    self.assertEqual(maximum, 0)
                bounds = self.menu.bounding_box()
                self.page.mouse.move(bounds['x'] + bounds['width'] / 2, bounds['y'] + 20)
                steps = ((-400, 0), (45, 45), (400, maximum), (400, maximum),
                         (-40, maximum - 40), (-400, 0)) if clipped else ((-400, 0), (400, 0))
                for ordinal, (delta, target) in enumerate(steps, 1):
                    self.page.mouse.wheel(0, delta)
                    self.page.wait_for_function('count => actionWheelEvents.length === count', arg=ordinal)
                    self.page.evaluate('''async () => {
                        for (let frame = 0; frame < 8; frame++) await new Promise(requestAnimationFrame);
                    }''')
                    self.assertTrue(self.menu.is_visible())
                    self.assertAlmostEqual(self.menu.evaluate('menu => menu.scrollTop'), target, delta=1)
                    self.assertEqual(self.page.evaluate('''[scrollX, scrollY,
                        document.querySelector('.tl-detail').scrollTop,
                        document.querySelector('.tl-scroll').scrollTop]'''), original)
                self.assertEqual(self.page.evaluate('actionWheelEvents'), [False] * len(steps))
                self.assert_unchanged()

    def test_horizontal_menu_arrows_do_not_pan_the_underlying_timeline(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open(mode=mode)
                self.page.evaluate('''() => {
                    const model = fgView('timeline').probe();
                    model.zoomTo(200, 500, 1);
                    actionOriginal.view = model.view();
                }''')
                self.open_menu()
                for key in ('ArrowLeft', 'ArrowRight'):
                    self.page.keyboard.press(key)
                    self.assertTrue(self.menu.is_visible())
                    self.assertEqual(self.focused_action(), 'distribution')
                    self.assert_unchanged()
                    for modifier in ('altKey', 'ctrlKey', 'metaKey'):
                        self.assertFalse(self.page.evaluate('''([key, modifier]) => {
                            const event = new KeyboardEvent('keydown', {key, [modifier]: true,
                                bubbles: true, cancelable: true});
                            document.activeElement.dispatchEvent(event);
                            return event.defaultPrevented;
                        }''', [key, modifier]))
                        self.assertTrue(self.menu.is_visible())
                        self.assert_unchanged()

    def test_text_action_and_enter_shortcut_target_the_same_record(self):
        for mode, touch in product(('single', 'sidecar'), (False, True)):
            with self.subTest(mode=mode, touch=touch):
                self.open(mode=mode, width=390 if touch else 1440)
                before = len(self.requests)
                self.open_menu()
                self.assertFalse(any(url.endswith(('text.html', 'text-index.json', 'compare.json'))
                                     for url in self.requests[before:]))
                item = self.menu.locator('[data-call-action=text]')
                if touch:
                    item.tap()
                else:
                    item.press('Enter')
                self.page.wait_for_function("() => fgCurrentView() === 'text' && !fgTextDeferred()")
                self.assertFalse(self.menu.is_visible())
                self.assertTrue(self.page.locator('.view-tab[data-view=text]').evaluate(
                    'element => element === document.activeElement'))
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 1)
                if mode == 'sidecar':
                    self.assertEqual(sum(url.endswith('text.html') for url in self.requests[before:]), 1)
                self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
                self.page.locator('.tl-search').focus()
                self.page.locator('[data-call-actions]').focus()
                self.page.keyboard.press('Enter')
                self.assertTrue(self.menu.is_visible())
                self.page.keyboard.press('Escape')
                self.page.locator('.tl-search').evaluate('element => element.blur()')
                self.trigger.evaluate('element => element.blur()')
                self.page.keyboard.press('Enter')
                self.page.wait_for_function("() => fgCurrentView() === 'text'")
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 1)

    def test_action_focus_stays_visible_after_detail_expansion_and_fullscreen(self):
        for mode, presentation, action in product(('single', 'sidecar'),
                ('detail-expanded', 'page-fullscreen', 'native-fullscreen'), ('distribution', 'text')):
            with self.subTest(mode=mode, presentation=presentation, action=action):
                self.open(mode=mode, native=presentation != 'page-fullscreen')
                if presentation == 'detail-expanded':
                    self.page.evaluate('''() => {
                        localStorage.setItem('fgTl.detailFrac', '0.1');
                        fgView('timeline').resize();
                    }''')
                    self.page.locator('[data-detail-expand]').click()
                    self.assertTrue(self.page.locator('#timelineView.tl-detail-expanded').is_visible())
                else:
                    self.page.locator('[data-act=max]').click()
                    self.page.wait_for_function("() => document.getElementById('timelineView').classList.contains('tl-fullscreen')")
                self.open_menu()
                self.menu.locator(f'[data-call-action={action}]').press('Enter')
                if action == 'text':
                    self.page.wait_for_function("() => fgCurrentView() === 'text' && !fgTextDeferred() && !document.fullscreenElement")
                    self.page.wait_for_function('''() => document.activeElement ===
                        document.querySelector('.view-tab[data-view=text]')''')
                    self.assertTrue(self.page.locator('.view-tab[data-view=text]').evaluate(
                        'element => element === document.activeElement'))
                else:
                    self.assertTrue(self.page.locator('.an-dist-head').is_visible())
                    self.assertTrue(self.page.evaluate('''() => document.activeElement.matches(
                        '.tl-canvas, [data-detail-expand]')'''))
                self.assertFalse(self.menu.is_visible())
                self.assertTrue(self.page.evaluate('!!document.activeElement.getClientRects().length'))
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 1)

    def test_late_native_entry_and_exit_restore_the_original_text_action_focus(self):
        for mode, route in product(('single', 'sidecar'), ('menu', 'key', 'link')):
            with self.subTest(mode=mode, route=route):
                self.open_pending_text_action(mode, route)
                self.page.evaluate('() => fullscreenTest.enter()')
                self.page.wait_for_function('() => !!fullscreenTest.exit')
                self.page.evaluate('() => fullscreenTest.exit()')
                self.assertTrue(self.page.locator('.view-tab[data-view=text]').evaluate(
                    'element => element===document.activeElement'))
                self.assertFalse(self.page.evaluate('!!document.fullscreenElement'))
                self.assertFalse(self.page.evaluate('document.body.classList.contains("tl-max")'))
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 1)

    def test_late_fullscreen_exit_cannot_override_new_input_or_navigation(self):
        for mode, change in product(('single', 'sidecar'),
                ('focus', 'pointer', 'key', 'line', 'view', 'cancel')):
            with self.subTest(mode=mode, change=change):
                self.open_pending_text_action(mode)
                self.page.evaluate('() => fullscreenTest.enter()')
                self.page.wait_for_function('() => !!fullscreenTest.exit')
                target = self.page.evaluate('''async change => {
                    if (change==='focus') document.getElementById('filterInput').focus();
                    if (change==='pointer') document.body.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));
                    if (change==='key') document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'Shift',bubbles:true}));
                    if (change==='line') {
                        const number=fgTextNavigation.order.at(-1);
                        await fgRequestTextLine(number,element=>fgSelectMountedTextLine(element,false));
                        return 'L'+number;
                    }
                    if (change==='view') {await fgSwitchView('timeline'); await fgSwitchView('text')}
                    if (change==='cancel') fgCancelTextNavigation();
                    return null;
                }''', change)
                self.page.evaluate('() => fullscreenTest.exit(false)')
                expected = '#filterInput' if change == 'focus' else 'body'
                self.assertTrue(self.page.locator(expected).evaluate('element=>element===document.activeElement'))
                if target:
                    self.assertEqual(self.page.evaluate('keyboardSelectedLine.id'), target)
                self.assertEqual(self.page.evaluate('fgCurrentView()'), 'text')
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 1)

    def test_rejected_native_entry_releases_pending_text_focus(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open_pending_text_action(mode)
                self.page.evaluate('() => fullscreenTest.reject(new Error("denied"))')
                self.page.wait_for_function('() => !document.querySelector("[data-act=max]").disabled')
                self.assertTrue(self.page.locator('.view-tab[data-view=text]').evaluate(
                    'element=>element===document.activeElement'))
                self.page.evaluate('''() => {
                    document.activeElement.blur();
                    document.dispatchEvent(new Event('fullscreenchange'));
                }''')
                self.assertTrue(self.page.locator('body').evaluate('element=>element===document.activeElement'))

    def test_vertical_canvas_drag_does_not_open_new_details_or_measure_on_every_move(self):
        self.open(kind='focus')
        point = self.page.evaluate('''async () => {
            const timeline = fgView('timeline').probe();
            timeline.select(0);
            timeline.zoomTo(timeline.sStart[1], timeline.sEnd[1], -1);
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const canvas = document.querySelector('.tl-canvas');
            const bounds = canvas.getBoundingClientRect.bind(canvas);
            window.canvasDragReads = 0;
            canvas.getBoundingClientRect = () => { canvasDragReads++; return bounds(); };
            return timeline.xyOf(1);
        }''')
        horizontal, vertical = point
        self.assertTrue(self.page.evaluate('point => document.elementFromPoint(...point).matches(".tl-canvas")', point))
        previous = self.page.evaluate('fgView("timeline").probe().view()')
        self.page.mouse.move(horizontal, vertical)
        self.page.mouse.down()
        self.page.evaluate('canvasDragReads = 0')
        self.page.mouse.move(horizontal, vertical + 24, steps=6)
        self.page.mouse.move(horizontal, vertical, steps=6)
        reads = self.page.evaluate('canvasDragReads')
        self.page.mouse.up()
        self.assertEqual(self.page.evaluate('fgView("timeline").probe().sel()'), 0)
        self.assertEqual(self.page.evaluate('fgView("timeline").probe().view()'), previous)
        self.assertEqual(reads, 0)

        self.page.mouse.down()
        self.page.mouse.move(horizontal + 1, vertical + 1)
        self.page.mouse.up()
        self.assertEqual(self.page.evaluate('fgView("timeline").probe().sel()'), 1)

    def test_double_click_zooms_only_after_two_valid_clicks_on_the_same_call(self):
        for dragged_click, change_target in ((None, False), (1, False), (2, False), (None, True)):
            with self.subTest(dragged_click=dragged_click, change_target=change_target):
                self.open(kind='focus')
                horizontal, vertical = self.page.evaluate('''async () => {
                    const timeline = fgView('timeline').probe();
                    timeline.select(0);
                    timeline.zoomTo(timeline.sStart[0], timeline.sStart[0] + 400, -1);
                    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    window.nativeDoubleClicks = 0;
                    document.querySelector('.tl-canvas').addEventListener('dblclick', event => {
                        if (event.isTrusted) nativeDoubleClicks++;
                    });
                    return timeline.xyOf(1);
                }''')
                view = 'fgView("timeline").probe().view()'
                before = self.page.evaluate(view)
                self.page.mouse.move(horizontal, vertical)
                for click_count in (1, 2):
                    if change_target and click_count == 2:
                        self.page.evaluate('''() => {
                            const timeline = fgView('timeline').probe();
                            const briefCenter = (timeline.sStart[1] + timeline.sEnd[1]) / 2;
                            const newStart = timeline.sStart[0] + timeline.sStart[2] - briefCenter;
                            timeline.zoomTo(newStart, newStart + 400, -1);
                        }''')
                        before = self.page.evaluate(view)
                    self.page.mouse.down(click_count=click_count)
                    if click_count == dragged_click:
                        self.page.mouse.move(horizontal, vertical + 24, steps=3)
                        self.page.mouse.move(horizontal, vertical, steps=3)
                    self.page.mouse.up(click_count=click_count)
                self.assertEqual(self.page.evaluate('nativeDoubleClicks'), 1)
                self.assertEqual(self.page.evaluate('fgView("timeline").probe().sel()'), 2 if change_target else 1)
                after = self.page.evaluate(view)
                if dragged_click is None and not change_target:
                    self.assertLess(after[1] - after[0], before[1] - before[0])
                else:
                    self.assertEqual(after, before)

    def test_chart_activation_moves_focus_from_search_and_return_restores_it(self):
        for mode, touch, index in (('single', False, 0), ('sidecar', True, 1), ('single-windowed', False, 1)):
            with self.subTest(mode=mode, touch=touch, index=index):
                self.open(kind='focus', mode=mode, width=844 if touch else 1440, touch=touch)
                search = self.page.get_by_role('textbox', name='Search traced function names (regular expression)')
                search.fill('outer|brief')
                self.page.evaluate('''index => {
                    const model = fgView('timeline').probe();
                    model.zoomTo(model.sStart[index], model.sEnd[index], -1);
                }''', index)
                point = self.page.evaluate('''index => fgView('timeline').probe().xyOf(index)''', index)
                self.assertTrue(self.page.evaluate("point => document.elementFromPoint(...point)?.matches('.tl-canvas')", point))
                if touch:
                    self.page.touchscreen.tap(*point)
                else:
                    self.page.mouse.click(*point)
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), index)
                canvas = self.page.locator('.tl-canvas')
                self.assertTrue(canvas.evaluate('node => node === document.activeElement'))
                before = self.page.evaluate("fgView('timeline').probe().view()")
                self.page.keyboard.press('Enter')
                back = self.page.get_by_role('button', name='Return to Timeline', exact=True)
                back.wait_for()
                line = self.page.evaluate('index => fgData.read("tlData").cols.line[index]', index)
                self.page.locator(f'#L{line}').wait_for()
                back.click()
                self.assertTrue(canvas.evaluate('node => node === document.activeElement'))
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), index)
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().view()"), before)
                self.assertEqual(search.input_value(), 'outer|brief')

    def test_trace_jump_returns_in_both_directions_on_touch_and_desktop(self):
        for width, height, touch in ((320, 800, True), (844, 390, True),
                                     (1024, 600, True), (1024, 600, False)):
            with self.subTest(width=width, height=height, touch=touch):
                self.open(mode='sidecar', width=width, height=height, touch=touch)
                canvas = self.page.locator('.tl-canvas')
                canvas.press('Enter')
                back = self.page.get_by_role('button', name='Return to Timeline', exact=True)
                back.wait_for()
                self.assertEqual(back.inner_text(), '← Timeline' if width < 700 else 'Return to Timeline')
                self.assertEqual(self.page.evaluate('fgCurrentView()'), 'text')
                self.assert_return_target(back, touch)
                for tab in self.page.locator('.view-tab').all():
                    self.assert_return_target(tab, touch)
                (back.tap if touch else back.click)()
                self.assert_unchanged()
                self.assertTrue(canvas.evaluate('element => element === document.activeElement'))
                self.assertFalse(back.is_visible())

                number = self.page.evaluate('fgData.read("tlData").cols.line[1]')
                text_tab = self.page.locator('.view-tab[data-view=text]')
                (text_tab.tap if touch else text_tab.click)()
                row = self.page.locator(f'#L{number}')
                self.assertIsNone(row.get_attribute('tabindex'))
                content = row.locator('.line-content')
                (content.tap if touch else content.click)()
                jump = self.page.get_by_role('button', name=f'In Timeline · L{number}', exact=True)
                if width == 320:
                    self.assert_return_target(jump, touch)
                read_position = '[scrollY, document.getElementById("textViewport").scrollLeft]'
                before = self.page.evaluate(read_position)
                (jump.tap if touch else jump.click)()
                self.page.wait_for_function('() => fgCurrentView() === "timeline" && fgView("timeline").probe().sel() === 1')
                back = self.page.get_by_role('button', name='Return to Text', exact=True)
                self.assertEqual(back.inner_text(), '← Text' if width < 700 else 'Return to Text')
                self.assert_return_target(back, touch)
                row.evaluate('''row => {
                    document.getElementById('fgTreeGuides').sheet.disabled = true;
                    const bounds = row.getBoundingClientRect.bind(row);
                    window.returnRowGeometryReads = 0;
                    row.getBoundingClientRect = () => { returnRowGeometryReads++; return bounds(); };
                }''')
                (back.tap if touch else back.click)()
                self.assertEqual(self.page.evaluate('fgCurrentView()'), 'text')
                self.assertTrue(text_tab.evaluate('tab => tab === document.activeElement'))
                self.assertEqual(self.page.evaluate('returnRowGeometryReads'), 0)
                self.assertEqual(self.page.evaluate(read_position), before)
                self.assertFalse(back.is_visible())

    def test_keyboard_trace_jump_focuses_the_chart_and_enter_returns_to_the_original_row(self):
        for mode, origin in product(('single-windowed', 'sidecar'), ('entry', 'exit')):
            with self.subTest(mode=mode, origin=origin):
                self.open(kind='focus', mode=mode, width=844, height=390, touch=True)
                number = self.page.evaluate('''async origin => {
                    const columns = fgData.read('tlData').cols;
                    const line = (origin === 'entry' ? columns.line : columns.eline)[0];
                    await fgSwitchView('text');
                    await fgRequestTextLine(line, row => fgSelectMountedTextLine(row));
                    return line;
                }''', origin)
                jump = self.page.get_by_role('button', name=f'In Timeline · L{number}', exact=True)
                jump.scroll_into_view_if_needed()
                position = '[scrollY, document.getElementById("textViewport").scrollLeft, history.length]'
                before = self.page.evaluate(position)
                jump.press('Enter' if origin == 'entry' else 'Space')
                self.page.wait_for_function('document.activeElement.matches(".tl-canvas")')
                self.assertEqual(self.page.evaluate('fgView("timeline").probe().sel()'), 0)
                if origin == 'entry':
                    self.page.keyboard.press('ArrowDown')
                    self.assertEqual(self.page.evaluate('fgView("timeline").probe().sel()'), 1)
                    self.page.keyboard.press('ArrowUp')
                    self.assertEqual(self.page.evaluate('fgView("timeline").probe().sel()'), 0)
                self.page.keyboard.press('Enter')
                self.page.wait_for_function('fgCurrentView() === "text"')
                if origin == 'entry':
                    self.assertTrue(self.page.locator(f'#L{number}').evaluate('row => row === document.activeElement'))
                else:
                    self.assertTrue(self.page.locator('.view-tab[data-view=text]').evaluate('tab => tab === document.activeElement'))
                self.assertTrue(jump.is_enabled())
                self.assertFalse(self.page.locator('#fgJumpReturn').is_visible())
                self.assertEqual(self.page.evaluate(position), before)

    def test_alt_enter_from_a_text_row_jumps_and_enter_returns_without_tab_navigation(self):
        for mode in ('single', 'single-windowed', 'sidecar'):
            with self.subTest(mode=mode):
                self.open(kind='focus', mode=mode)
                number = self.page.evaluate('''async () => {
                    const line = fgData.read('tlData').cols.line[0];
                    await fgSwitchView('text');
                    await fgRequestTextLine(line, row => fgSelectMountedTextLine(row));
                    return line;
                }''')
                row = self.page.locator(f'#L{number}')
                self.assertTrue(row.evaluate('node => node === document.activeElement'))
                self.page.keyboard.press('Enter')
                self.assertEqual(self.page.evaluate('fgCurrentView()'), 'text')

                query = self.page.locator('#filterInput')
                if not query.is_visible():
                    self.page.locator('#textFilterToggle').click()
                query.focus()
                self.assertTrue(query.evaluate('node => node === document.activeElement'))
                self.page.keyboard.press('Alt+Enter')
                self.assertEqual(self.page.evaluate('fgCurrentView()'), 'text')
                self.page.keyboard.press('Escape')
                row.focus()

                position = '[scrollY, document.getElementById("textViewport").scrollLeft, history.length]'
                before = self.page.evaluate(position)
                self.page.keyboard.press('Alt+Enter')
                self.page.wait_for_function('document.activeElement.matches(".tl-canvas")')
                self.assertEqual(self.page.evaluate('fgView("timeline").probe().sel()'), 0)
                self.page.get_by_role('button', name='Return to Text', exact=True).wait_for()
                self.page.keyboard.press('Enter')
                self.page.wait_for_function('fgCurrentView() === "text"')
                self.assertTrue(row.evaluate('node => node === document.activeElement'))
                self.assertEqual(self.page.evaluate(position), before)

    def test_pending_keyboard_jump_does_not_take_focus_back_from_the_return_button(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.urls['focus', 'sidecar'])
        self.page.evaluate('''async () => {
            await fgSwitchView('text');
            if (fgData.ready('tlData')) throw Error('Expected an unloaded Timeline');
            const load = fgData.load;
            fgData.load = (name, options) => name === 'tlData' ?
                new Promise(resolve => { window.continueTimelineLoad = resolve; })
                    .then(() => load(name, options)) : load(name, options);
        }''')
        row = self.page.locator('.line-container[data-func="outer"]').first
        row.locator('.line-content').click()
        number = row.get_attribute('data-line-number')
        self.page.get_by_role('button', name=f'In Timeline · L{number}', exact=True).press('Enter')
        self.page.wait_for_function('typeof window.continueTimelineLoad === "function"')
        back = self.page.get_by_role('button', name='Return to Text', exact=True)
        back.focus()
        self.page.evaluate('continueTimelineLoad()')
        self.page.locator('[data-recorded-call="0"]').wait_for()
        self.assertTrue(back.evaluate('button => button === document.activeElement'))
        back.press('Enter')
        self.assertEqual(self.page.evaluate('fgCurrentView()'), 'text')

    def test_return_to_timeline_restores_visible_opener_without_moving_the_panes(self):
        for mode, route in product(('single', 'sidecar'), ('line', 'menu', 'detached', 'hidden')):
            with self.subTest(mode=mode, route=route):
                self.open(mode=mode, width=844, height=390, touch=True)
                if route == 'menu':
                    opener = self.trigger
                    self.open_menu()
                    jump = self.menu.locator('[data-call-action=text]')
                else:
                    opener = self.page.locator('.tl-detail button[data-line]').first
                    opener.scroll_into_view_if_needed()
                    jump = opener
                scroll = '''() => [scrollY, document.querySelector('.tl-detail').scrollTop,
                    document.querySelector('.tl-scroll').scrollTop]'''
                before = self.page.evaluate(scroll)
                jump.press('Enter')
                back = self.page.get_by_role('button', name='Return to Timeline', exact=True)
                back.wait_for()
                if route in ('detached', 'hidden'):
                    if route == 'detached':
                        opener.evaluate('element => element.remove()')
                    else:
                        opener.evaluate('element => element.style.visibility = "hidden"')
                    opener = self.page.locator('.view-tab[data-view=timeline]')
                back.press('Enter')
                self.assertTrue(opener.evaluate('element => element === document.activeElement'))
                self.assertFalse(self.menu.is_visible())
                self.assertEqual(self.page.evaluate(scroll), before)
                self.assert_unchanged()

    def test_return_to_timeline_reveals_the_opener_after_viewport_reflow(self):
        cases = (('menu', (844, 390), (240, 390)), ('line', (844, 390), (390, 844)),
                 ('line', (390, 844), (844, 390)), ('hidden', (844, 390), (240, 390)))
        for mode, (route, before, after) in product(('single', 'sidecar'), cases):
            with self.subTest(mode=mode, route=route, before=before, after=after):
                self.open(mode=mode, width=before[0], height=before[1], touch=True)
                if route in ('menu', 'hidden'):
                    opener = self.trigger
                    opener.tap()
                    jump = self.menu.locator('[data-call-action=text]')
                else:
                    opener = self.page.locator('.tl-detail button[data-line]').first
                    jump = opener
                jump.tap()
                self.page.wait_for_function('() => fgCurrentView() === "text" && !fgTextDeferred()')
                self.page.set_viewport_size(dict(width=after[0], height=after[1]))
                if route == 'hidden':
                    opener.evaluate('element => element.style.visibility = "hidden"')
                    opener = self.page.locator('.view-tab[data-view=timeline]')
                self.page.get_by_role('button', name='Return to Timeline', exact=True).tap()
                self.assertTrue(opener.evaluate('''async button => {
                    await new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)));
                    const bounds = button.getBoundingClientRect();
                    return button === document.activeElement && button.contains(document.elementFromPoint(
                        bounds.left + bounds.width / 2, bounds.top + bounds.height / 2));
                }'''))
                self.assertFalse(self.menu.is_visible())
                self.assert_unchanged()

    def test_filtered_text_jump_explains_the_failure_without_changing_the_filter(self):
        for mode in ('single', 'sidecar', 'single-windowed'):
            with self.subTest(mode=mode):
                self.open(kind='focus', mode=mode, width=320)
                self.page.evaluate('''async () => {
                    await fgSwitchView('text');
                    document.getElementById('filterInput').value = 'duration:>100';
                    await applyFilter();
                    await fgSwitchView('timeline');
                    fgView('timeline').probe().select(0);
                    window.jumpFilter = fgFilterState;
                    window.jumpScrolled = [];
                    window.jumpSelected = [];
                    const select = fgSelectMountedTextLine;
                    fgSelectMountedTextLine = (row, ...options) => {
                        jumpSelected.push(row.id);
                        return select(row, ...options);
                    };
                    const scroll = fgScrollTextLine;
                    fgScrollTextLine = (row, ...options) => {
                        jumpScrolled.push(row.id);
                        return scroll(row, ...options);
                    };
                }''')
                buttons = self.page.locator('.tl-detail button[data-line]')
                entry, exit_line = [int(button.get_attribute('data-line')) for button in buttons.all()]
                self.assertEqual(self.page.evaluate('''([entry, exit]) => [fgFilterState.lines.has(entry),
                    fgFilterState.lines.has(exit), !!fgTextWindow]''', [entry, exit_line]),
                    [False, True, mode == 'single-windowed'])
                buttons.first.tap()
                notice = self.page.get_by_role('status').filter(has_text=f'Line {entry} is hidden by the current filter')
                notice.wait_for(timeout=2000)
                self.assertTrue(notice.evaluate('element => element.getBoundingClientRect().right <= innerWidth'))
                self.assertTrue(self.page.evaluate('fgFilterState === jumpFilter'))
                self.assertEqual(self.page.evaluate('jumpScrolled'), [])
                self.assertNotIn(f'L{entry}', self.page.evaluate('jumpSelected'))
                self.assertEqual(self.page.locator(f'#L{entry}.tl-flash').count(), 0)
                self.page.get_by_role('button', name='Return to Timeline', exact=True).tap()
                self.assertFalse(notice.is_visible())
                self.assertTrue(buttons.first.evaluate('button => button === document.activeElement'))
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 0)

    def test_text_jump_opens_the_fold_hiding_the_exact_record(self):
        for mode, target in product(('single', 'sidecar', 'single-windowed'), ('descendant', 'return')):
            with self.subTest(mode=mode, target=target):
                self.open(kind='focus', mode=mode, width=320)
                fold = self.page.evaluate('''async target => {
                    await fgSwitchView('text');
                    const block = [...fgGetTextFolding().blocks.values()]
                        .find(block => block.entry?.getAttribute('data-func') === 'outer');
                    toggleFold(block.id, 'entry');
                    await fgTextWindow?.settled;
                    await fgSwitchView('timeline');
                    fgView('timeline').probe().select(target === 'return' ? 0 : 1);
                    return block.id;
                }''', target)
                links = self.page.locator('.tl-detail button[data-line]')
                link = links.last if target == 'return' else links.first
                number = link.get_attribute('data-line')
                self.assertTrue(self.page.evaluate('''([fold, number]) => foldedBlocks[fold] &&
                    (fgTextWindow ? !fgTextWindow.visibleNumbers.includes(+number) :
                        document.getElementById('L' + number).style.display === 'none')''', [fold, number]))
                link.tap()
                self.page.locator(f'#L{number}.keyboard-selected.tl-flash').wait_for(timeout=2500)
                self.assertFalse(self.page.evaluate('fold => foldedBlocks[fold] === true', fold))
                self.assertTrue(self.page.evaluate('fold => JSON.parse(lsGet("foldedBlocks"))[fold] === true', fold))
                self.assertEqual(self.page.locator('#fgJumpStatus').inner_text(), '')
                self.page.get_by_role('button', name='Return to Timeline', exact=True).tap()
                self.assertTrue(link.evaluate('button => button === document.activeElement'))

    def test_missing_failed_and_cancelled_text_jumps_keep_return_available(self):
        self.open(kind='focus', mode='sidecar')
        self.page.evaluate('''async () => {
            await fgSwitchView('text');
            await fgSwitchView('timeline');
            window.ensureJumpLine = fgEnsureTextLine;
        }''')
        link = self.page.locator('.tl-detail button[data-line]').first
        number = link.get_attribute('data-line')
        back = self.page.get_by_role('button', name='Return to Timeline', exact=True)
        for failure, message in [(False, f'Line {number} not found'),
                                 (True, f'Unable to open trace line {number}: fixture unavailable')]:
            self.page.evaluate('''failure => {
                window.fgEnsureTextLine = () => failure ? Promise.reject(new Error('fixture unavailable')) : Promise.resolve(null);
            }''', failure)
            link.click()
            notice = self.page.get_by_role('status').filter(has_text=message)
            notice.wait_for(timeout=2000)
            back.click()
            self.assertFalse(notice.is_visible())

        self.page.evaluate('''() => {
            window.fgEnsureTextLine = (number, {signal}) => new Promise((resolve, reject) => {
                window.pendingJump = {signal, reject};
            });
        }''')
        link.click()
        back.click()
        self.assertTrue(self.page.evaluate('pendingJump.signal.aborted'))
        self.page.evaluate('() => { window.fgEnsureTextLine = ensureJumpLine; }')
        link.click()
        self.page.locator(f'#L{number}.tl-flash').wait_for()
        self.page.evaluate('''async () => {
            pendingJump.reject(new Error('stale fixture error'));
            await new Promise(requestAnimationFrame);
        }''')
        self.assertFalse(self.page.get_by_text('stale fixture error', exact=False).is_visible())
        back.click()

    def test_return_to_text_restores_row_focus_without_scrolling(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open(kind='focus', mode=mode)
                number = self.page.evaluate('''async () => {
                    await fgSwitchView('text');
                    const number = fgData.read('tlData').cols.line[0];
                    await fgRequestTextLine(number, row => fgSelectMountedTextLine(row, false));
                    return number;
                }''')
                before = self.page.evaluate('[scrollY, document.getElementById("textViewport").scrollLeft]')
                self.page.get_by_role('button', name=f'In Timeline · L{number}', exact=True).press('Enter')
                back = self.page.get_by_role('button', name='Return to Text', exact=True)
                back.press('Enter')
                self.assertFalse(back.is_visible())
                self.assertTrue(self.page.locator(f'#L{number}').evaluate('row => row === document.activeElement'))
                self.assertEqual(self.page.evaluate('[scrollY, document.getElementById("textViewport").scrollLeft]'), before)

    def test_closing_a_call_releases_details_but_switching_views_keeps_them(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open(kind='focus', mode=mode)
                pane = self.page.locator('.tl-detail')
                canvas = self.page.locator('.tl-canvas')
                self.page.evaluate('''async () => {
                    fgView('timeline').probe().select(0);
                    window.savedCallHead = document.querySelector('.tl-recorded-call-head');
                    await fgSwitchView('text');
                    await fgSwitchView('timeline');
                }''')
                self.assertTrue(pane.evaluate('pane => pane.firstElementChild === savedCallHead'))
                self.assertEqual(self.page.evaluate('fgView("timeline").probe().sel()'), 0)
                pane.get_by_role('button', name='Close details', exact=True).press('Enter')
                self.assertTrue(canvas.evaluate('canvas => canvas === document.activeElement'))
                self.assertEqual(pane.locator(':scope > *').count(), 0)
                self.assertFalse(self.page.evaluate('savedCallHead.isConnected'))
                self.page.evaluate('fgView("timeline").probe().select(0)')
                self.assertTrue(pane.locator('[data-recorded-call="0"]').is_visible())

    def test_return_to_timeline_restores_fullscreen_and_scroll(self):
        for mode, presentation in product(('single', 'sidecar'), ('page', 'browser', 'rejected', 'detached')):
            with self.subTest(mode=mode, presentation=presentation):
                self.open(mode=mode, width=480, height=260, native=presentation != 'page')
                page = self.page
                root = page.locator('#timelineView')
                fullscreen = root.locator('[data-act=max]')
                fullscreen.tap()
                ready = '''native => {
                    const root = document.getElementById('timelineView');
                    return root.classList.contains('tl-fullscreen') && !root.querySelector('[data-act=max]').disabled &&
                        (document.fullscreenElement === root) === native;
                }'''
                page.wait_for_function(ready, arg=presentation != 'page')
                opener = root.locator('#tlDetail button[data-line]').first
                opener.scroll_into_view_if_needed()
                scroll = '''async () => {
                    for (let frame = 0; frame < 2; frame++) await new Promise(requestAnimationFrame);
                    const root = document.getElementById('timelineView');
                    return [root.scrollTop, root.querySelector('.tl-detail').scrollTop, root.querySelector('.tl-scroll').scrollTop];
                }'''
                opener.focus()
                before = page.evaluate(scroll)
                self.assertGreater(before[0], 0, before)
                opener.press('Enter')
                page.wait_for_function('() => fgCurrentView() === "text" && document.fullscreenElement === null')
                if presentation == 'rejected':
                    root.evaluate('root => { root.requestFullscreen = () => Promise.reject(new Error("Forbidden")); }')
                if presentation == 'detached':
                    opener.evaluate('button => button.remove()')
                back = page.get_by_role('button', name='Return to Timeline', exact=True)
                back.tap()
                page.wait_for_function(ready, arg=presentation in ('browser', 'detached'))
                self.assertFalse(back.is_visible())
                if presentation == 'detached':
                    self.assertTrue(fullscreen.evaluate('button => button === document.activeElement'))
                    self.assert_unchanged()
                    continue
                self.assertEqual(page.evaluate(scroll), before)
                self.assertTrue(opener.evaluate('button => button === document.activeElement'))
                self.assert_unchanged()

                opener.press('Enter')
                page.wait_for_function('() => fgCurrentView() === "text" && document.fullscreenElement === null')
                page.locator('.view-tab[data-view=timeline]').tap()
                self.assertFalse(page.evaluate('document.body.classList.contains("tl-max")'))
                self.assertFalse(back.is_visible())
                self.assert_unchanged()

    def test_return_to_text_from_timeline_fullscreen(self):
        for mode, native, (width, height) in product(
                ('single', 'sidecar'), (True, False), ((280, 390), (320, 240), (844, 390), (1024, 600))):
            with self.subTest(mode=mode, native=native, width=width, height=height):
                self.open(kind='focus', mode=mode, width=width, height=height, native=native, touch=True)
                number = self.page.evaluate('fgData.read("tlData").cols.line[0]')
                self.page.locator('.view-tab[data-view=text]').tap()
                row = self.page.locator(f'#L{number}')
                row.locator('.line-content').tap()
                read_position = '[scrollY, document.getElementById("textViewport").scrollLeft]'
                before = self.page.evaluate(read_position)
                self.page.get_by_role('button', name=f'In Timeline · L{number}', exact=True).tap()
                self.page.wait_for_function('() => fgView("timeline").probe().sel() === 0')
                back = self.page.locator('#fgJumpReturn')
                fullscreen = self.page.locator('[data-act=max]')
                self.assertTrue(back.evaluate('button => button.parentElement.matches(".view-tabs")'))
                fullscreen.tap()
                self.page.wait_for_function('native => document.querySelector("#timelineView").classList.contains("tl-fullscreen") && !!document.fullscreenElement === native', arg=native)
                self.assertTrue(back.evaluate('button => !!button.closest("#timelineView .tl-toolbar")'))
                self.assert_return_target(back, touch=True)
                self.assertEqual(back.get_attribute('aria-label'), 'Return to Text')
                self.assertEqual(back.inner_text(), '← Text')
                if width == 280:
                    self.trigger.tap()
                    self.menu.get_by_role('menuitem', name='Show source excerpt').tap()
                    source = self.page.locator('.tl-cs .source-line').first
                    source.wait_for(state='attached')
                    self.assertFalse(self.page.locator('.tl-primary .tl-search-group').is_visible())
                    local_return = self.page.get_by_role('button', name='Return to call details', exact=True)
                    for button in (local_return, fullscreen, back):
                        self.assert_return_target(button, touch=True)
                    local_return.tap()
                    self.assertTrue(self.page.locator('.tl-primary .tl-search-group').is_visible())
                fullscreen.tap()
                self.page.wait_for_function('() => !document.fullscreenElement && !document.querySelector("#timelineView").classList.contains("tl-fullscreen")')
                self.assertTrue(back.evaluate('button => button.parentElement.matches(".view-tabs") && !button.hidden'))
                fullscreen.tap()
                self.page.wait_for_function('native => document.querySelector("#timelineView").classList.contains("tl-fullscreen") && !!document.fullscreenElement === native', arg=native)
                if width == 320:
                    back.tap()
                else:
                    back.press('Enter')
                self.page.wait_for_function('() => fgCurrentView() === "text" && !document.fullscreenElement')
                self.assertFalse(back.is_visible())
                self.assertTrue(back.evaluate('button => button.parentElement.matches(".view-tabs")'))
                self.assertTrue(row.evaluate('row => row === document.activeElement'))
                self.assertEqual(self.page.evaluate(read_position), before)

    def test_short_fullscreen_toolbar_leaves_the_chart_reachable(self):
        for native in (True, False):
            with self.subTest(native=native):
                self.open(width=320, height=240, native=native, touch=True)
                self.page.locator('.view-tab[data-view=text]').tap()
                number = self.page.evaluate('fgData.read("tlData").cols.line[1]')
                self.page.locator(f'#L{number} .line-content').tap()
                self.page.get_by_role('button', name=f'In Timeline · L{number}', exact=True).tap()
                self.page.get_by_role('button', name='Full screen', exact=True).tap()
                self.page.wait_for_function('native => !!document.fullscreenElement === native && !!document.querySelector(".tl-fullscreen")', arg=native)
                facts = self.page.evaluate('''() => {
                    const primary = document.querySelector('.tl-primary');
                    const stage = document.querySelector('.tl-stage').getBoundingClientRect();
                    const canvas = primary.closest('#timelineView').querySelector('.tl-canvas');
                    const required = '#fgJumpReturn,.tl-search,[data-act=max],[data-act=help],'
                        + '[data-act=zoomout],[data-act=zoomin],[data-act=fit],[data-act=options]';
                    const targets = Array.from(primary.querySelectorAll(required), control => {
                        const {left, right, top, bottom, height} = control.getBoundingClientRect();
                        const hit = document.elementFromPoint((left + right) / 2, (top + bottom) / 2);
                        return {name: control.dataset.act || control.id || control.className,
                            fits: left >= 0 && right <= innerWidth && top >= 0 && bottom <= innerHeight,
                            height, hit: control.contains(hit)};
                    });
                    return {toolbar: primary.parentElement.offsetHeight, visibleStage: innerHeight - stage.top,
                        canvasHit: canvas.contains(document.elementFromPoint(stage.left + stage.width * .8, stage.top + 72)),
                        targets};
                }''')
                self.assertLessEqual(facts['toolbar'], 102, facts)
                self.assertGreaterEqual(facts['visibleStage'], 120, facts)
                self.assertTrue(facts['canvasHit'], facts)
                self.assertTrue(all(target['fits'] and target['hit'] and target['height'] >= 44
                                    for target in facts['targets']), facts)
                tools = self.page.get_by_role('button', name='Tools', exact=True)
                tools.tap()
                self.assertTrue(self.page.get_by_role('dialog', name='Timeline tools').is_visible())
                filter_field = self.page.get_by_role('textbox', name='Filter trace lines')
                filter_field.fill('duration:>0')
                filter_field.press('Enter')
                active_tools = self.page.get_by_role('button', name='Tools · Filter on', exact=True)
                active_tools.wait_for()
                self.page.keyboard.press('Escape')
                self.assertEqual(active_tools.get_attribute('aria-expanded'), 'false')
                self.assertTrue(active_tools.evaluate('button => document.activeElement === button'))
                self.assertEqual(self.page.locator('.tl-toolbar').evaluate('toolbar => toolbar.offsetHeight'),
                                 facts['toolbar'])
                self.page.get_by_role('button', name='Return to Text', exact=True).tap()
                self.page.wait_for_function('() => fgCurrentView() === "text" && !document.fullscreenElement')

    def test_text_jump_reopens_chart_without_changing_normal_tab_switching(self):
        self.open(mode='sidecar', width=480, height=240)
        chart = self.page.locator('.tl-canvas')
        self.page.locator('[data-detail-expand]').tap()
        self.assertFalse(chart.is_visible())
        self.page.locator('.tl-detail button[data-line]').first.tap()
        self.page.get_by_role('button', name='Return to Timeline', exact=True).tap()
        self.assertFalse(chart.is_visible())

        self.page.locator('.view-tab[data-view=text]').tap()
        number = self.page.evaluate('''async () => {
            const line = fgData.read('tlData').cols.line[0];
            await fgRequestTextLine(line, row => fgSelectMountedTextLine(row, false));
            return line;
        }''')
        self.page.locator('.view-tab[data-view=timeline]').tap()
        self.assertFalse(chart.is_visible())
        self.page.locator('.view-tab[data-view=text]').tap()
        self.page.get_by_role('button', name=f'In Timeline · L{number}', exact=True).tap()
        self.page.wait_for_function('fgView("timeline").probe().sel() === 0')
        self.assertTrue(chart.is_visible())
        back = self.page.get_by_role('button', name='Return to Text', exact=True)
        self.assertTrue(back.evaluate('''button => {
            const bottom = button.getBoundingClientRect().bottom;
            const targetY = fgView('timeline').probe().xyOf(0)[1];
            return bottom > 0 && targetY > bottom && targetY < innerHeight;
        }'''))

    def jump_to_call_from_text(self, name):
        number = self.page.evaluate('''async name => {
            const model = fgView('timeline').probe();
            const index = model.sName.findIndex((unused, index) => model.nameOf(index) === name);
            const line = fgData.read('tlData').cols.line[index];
            await fgSwitchView('text');
            await fgRequestTextLine(line, row => fgSelectMountedTextLine(row, false));
            return line;
        }''', name)
        self.page.get_by_role('button', name=f'In Timeline · L{number}', exact=True).click()
        self.page.wait_for_function('''name => {
            const model = fgView('timeline').probe();
            return model.sel() >= 0 && model.nameOf(model.sel()) === name;
        }''', arg=name)

    def test_text_jump_zooms_tiny_call_but_keeps_a_useful_zoom(self):
        for mode, width in (('single', 1440), ('sidecar', 480)):
            with self.subTest(mode=mode):
                self.open(kind='focus', mode=mode, width=width)
                initial = self.page.evaluate('fgView("timeline").probe().view()')
                self.jump_to_call_from_text('brief')
                zoomed = self.page.evaluate('''() => {
                    const model = fgView('timeline').probe();
                    const time = model.view(), index = model.sel();
                    const canvas = document.querySelector('.tl-canvas');
                    const start = model.sStart[index], end = model.sEnd[index];
                    return {time, width: (end - start) * canvas.clientWidth / (time[1] - time[0]),
                        inside: start > time[0] && end < time[1], duration: end - start};
                }''')
                self.assertNotEqual(zoomed['time'], initial)
                self.assertAlmostEqual(zoomed['width'], 56, delta=1)
                self.assertTrue(zoomed['inside'])
                self.assertEqual(zoomed['duration'], 8)
                self.jump_to_call_from_text('brief')
                self.assertEqual(self.page.evaluate('fgView("timeline").probe().view()'), zoomed['time'])

    def test_text_jump_marks_zero_duration_without_inventing_a_width(self):
        self.open(kind='focus', mode='single')
        self.page.evaluate('''() => {
            window.selectionStrokes = [];
            const context = document.querySelector('.tl-canvas').getContext('2d');
            const stroke = context.stroke;
            context.stroke = function (...args) {
                if (this.lineWidth === 2 && this.strokeStyle === '#111827') selectionStrokes.push(true);
                return stroke.apply(this, args);
            };
        }''')
        initial = self.page.evaluate('fgView("timeline").probe().view()')
        self.jump_to_call_from_text('point')
        self.assertEqual(self.page.evaluate('''() => {
            const model = fgView('timeline').probe();
            return [model.view(), model.sEnd[model.sel()] - model.sStart[model.sel()]];
        }'''), [initial, 0])
        self.assertTrue(self.page.evaluate('''async () => {
            await new Promise(requestAnimationFrame);
            return selectionStrokes.length > 0;
        }'''))

    def test_enter_shortcut_and_recorded_line_controls_focus_the_visible_text_tab(self):
        for mode, route in product(('single', 'sidecar'), ('key', 'click', 'Enter', 'Space')):
            with self.subTest(mode=mode, route=route):
                self.open(mode=mode, width=320 if mode == 'sidecar' else 1440)
                lines = self.page.locator('.tl-detail [data-line]')
                target = lines.first
                if route == 'key':
                    self.page.locator('.tl-canvas').focus()
                    self.page.keyboard.press('Enter')
                elif route == 'click':
                    target.click()
                else:
                    self.assertEqual(lines.count(), 2)
                    target = lines.last
                    lines.first.focus()
                    self.assertTrue(lines.first.evaluate('element => element === document.activeElement'))
                    self.page.keyboard.press('Tab')
                    self.assertTrue(target.evaluate('element => element === document.activeElement'))
                    self.page.keyboard.press(route)
                self.page.wait_for_function("() => fgCurrentView()==='text' && !fgTextDeferred()")
                number = target.get_attribute('data-line')
                self.page.locator(f'.line-container.keyboard-selected[data-line-number="{number}"]').wait_for()
                self.assertTrue(self.page.locator('.view-tab[data-view=text]').evaluate(
                    'element=>element===document.activeElement'))
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 1)

    def test_timeline_only_disables_text_with_an_accessible_reason_and_skips_it(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open('timeline-only', mode)
                self.open_menu()
                self.assertTrue(self.menu.locator('[data-call-action=text]').is_disabled())
                self.assertEqual(self.menu.get_attribute('aria-describedby'), 'tlCallActionsNote')
                self.assertIn('Text view is not included', self.page.locator('#tlCallActionsNote').inner_text())
                for key in ('ArrowDown', 'ArrowUp', 'Home', 'End'):
                    self.page.keyboard.press(key)
                    self.assertEqual(self.focused_action(), 'distribution')
                self.page.keyboard.press('Escape')
                self.assertEqual(self.page.evaluate('fgCurrentView()'), 'timeline')
                self.assert_unchanged()

    def test_escape_closes_only_the_menu_before_leaving_either_fullscreen_mode(self):
        for native in (True, False):
            with self.subTest(native=native):
                self.open(native=native)
                self.page.locator('[data-act=max]').click()
                self.page.wait_for_function("() => document.getElementById('timelineView').classList.contains('tl-fullscreen')")
                self.open_menu()
                self.page.keyboard.press('Escape')
                self.assertFalse(self.menu.is_visible())
                self.assertTrue(self.page.locator('#timelineView').evaluate("element => element.classList.contains('tl-fullscreen')"))
                self.assertTrue(self.trigger.evaluate('element => element === document.activeElement'))
                self.page.keyboard.press('Escape')
                self.page.wait_for_function("() => !document.getElementById('timelineView').classList.contains('tl-fullscreen')")
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 1)

    def test_outside_pointer_focus_tools_and_help_dismiss_the_menu(self):
        self.open()
        for target in ('.tl-search', '[data-act=options]', '#viewHelpBtn'):
            with self.subTest(target=target):
                self.open_menu()
                self.page.locator(target).click()
                self.assertFalse(self.menu.is_visible())
                if target == '[data-act=options]':
                    self.assertTrue(self.page.locator('.tl-options').is_visible())
                    self.page.locator(target).click()
                elif target == '#viewHelpBtn':
                    self.page.locator('.tl-help-close').click()
        self.open_menu()
        self.page.locator('.tl-search').focus()
        self.assertFalse(self.menu.is_visible())
        self.assertTrue(self.page.locator('.tl-search').evaluate('element => element === document.activeElement'))

    def test_selection_view_and_analysis_changes_cannot_reuse_a_stale_action(self):
        self.open()
        for action in ("fgView('timeline').probe().select(0)",
                       "fgView('timeline').probe().range(0, 1000)",
                       "fgView('timeline').probe().errors()",
                       "fgSwitchView('text')"):
            with self.subTest(action=action):
                self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
                self.page.evaluate("fgView('timeline').probe().select(1)")
                self.open_menu()
                self.page.evaluate(action)
                self.assertFalse(self.menu.is_visible())
                self.page.locator('[data-call-action=text]').dispatch_event('click')
                if action == "fgSwitchView('text')":
                    self.assertEqual(self.page.evaluate('fgCurrentView()'), 'text')
                else:
                    self.assertEqual(self.page.evaluate('fgCurrentView()'), 'timeline')
                self.assertFalse(self.page.locator('.an-dist-head').is_visible())

    def test_scroll_and_resize_close_the_menu_without_leaving_focus_hidden(self):
        for action in ("document.querySelector('.tl-detail').scrollTop += 40",
                       "window.scrollBy(0, 10)", None):
            with self.subTest(action=action):
                self.open()
                self.page.evaluate("document.body.style.paddingBottom = '160px'")
                self.open_menu()
                before = self.page.evaluate('''() => ({window: scrollY,
                    detail: document.querySelector('.tl-detail').scrollTop})''')
                if action:
                    self.page.evaluate(action)
                    self.page.wait_for_function('''before => scrollY !== before.window ||
                        document.querySelector('.tl-detail').scrollTop !== before.detail''', arg=before)
                else:
                    self.page.set_viewport_size({'width': 1400, 'height': 940})
                self.menu.wait_for(state='hidden')
                self.assertTrue(self.trigger.evaluate('element => element === document.activeElement'))
                self.assert_unchanged()

    def test_menu_stays_in_viewport_and_is_not_clipped_by_a_short_detail(self):
        for width, height in ((320, 568), (390, 844), (844, 390), (1440, 300)):
            with self.subTest(width=width, height=height):
                self.open(width=width, height=height)
                self.page.evaluate('''() => {
                    localStorage.setItem('fgTl.detailFrac', '0.1');
                    fgView('timeline').resize();
                }''')
                self.open_menu()
                bounds = self.menu.bounding_box()
                self.assertGreaterEqual(bounds['x'], 7)
                self.assertGreaterEqual(bounds['y'], 7)
                self.assertLessEqual(bounds['x'] + bounds['width'], width - 7)
                self.assertLessEqual(bounds['y'] + bounds['height'], height - 7)
                self.assertTrue(self.menu.locator('[data-call-action=text]').evaluate('''element => {
                    const bounds = element.getBoundingClientRect();
                    return element.contains(document.elementFromPoint(bounds.x + bounds.width / 2,
                        bounds.y + bounds.height / 2));
                }'''))

    def test_opening_actions_never_fetches_text_or_compare_or_changes_model_data(self):
        self.open(mode='sidecar')
        self.assertFalse(self.page.evaluate('fgData.ready("fgText")'))
        before = len(self.requests)
        for _ in range(4):
            self.open_menu()
            self.page.keyboard.press('Escape')
        self.assertFalse(any(url.endswith(('text.html', 'text-index.json', 'compare.json'))
                             for url in self.requests[before:]))
        self.assertFalse(self.page.evaluate('fgData.ready("fgText")'))
        self.assert_unchanged()

    def test_recorded_symbol_reveals_text_first_and_keeps_keyboard_actions(self):
        self.open(kind='symbols', mode='sidecar', width=320, height=390, theme='dark')
        self.page.evaluate('''symbol => {
            localStorage.setItem('fgTl.detailFrac', '0.1');
            const data = fgData.read('tlData');
            fgView('timeline').probe().select(data.cols.name.indexOf(data.strings.indexOf(symbol)));
            fgView('timeline').resize();
        }''', CLIPPED_SYMBOL)
        self.trigger.press('ArrowDown')
        reveal = self.menu.get_by_role('menuitem', name='Show recorded symbol')
        reveal.press('Enter')
        panel = self.page.get_by_role('region', name='Recorded symbol')
        self.assertEqual(panel.locator('code').text_content(), CLIPPED_SYMBOL)
        self.assertEqual(self.page.evaluate('document.activeElement.textContent'), 'Recorded symbol')
        facts = panel.evaluate('''panel => {
            const detail = panel.closest('.tl-detail'), box = detail.getBoundingClientRect();
            const pre = panel.querySelector('pre'), text = pre.getBoundingClientRect();
            const firstLine = document.createRange(); firstLine.setStart(pre.firstChild.firstChild, 0);
            firstLine.setEnd(pre.firstChild.firstChild, 1);
            const first = firstLine.getBoundingClientRect();
            return { visible: first.top >= box.top && first.bottom <= box.top + detail.clientHeight,
                wrapped: pre.scrollWidth <= pre.clientWidth + 1 && text.width <= detail.clientWidth,
                textFirst: text.top < panel.querySelector('[data-copy-symbol]').getBoundingClientRect().top };
        }''')
        self.assertEqual(facts, dict(visible=True, wrapped=True, textFirst=True))
        self.page.evaluate('''() => {
            window.copiedSymbols = [];
            Object.defineProperty(navigator, 'clipboard', {configurable: true,
                value: {writeText: async text => copiedSymbols.push(text)}});
        }''')
        self.page.keyboard.press('Tab')
        copy = panel.get_by_role('button', name='Copy symbol')
        self.assertTrue(copy.evaluate('button => button === document.activeElement'))
        self.assertGreaterEqual(copy.bounding_box()['height'], 44)
        self.page.keyboard.press('Enter')
        panel.get_by_role('status').get_by_text('Symbol copied').wait_for()
        self.assertEqual(self.page.evaluate('copiedSymbols'), [CLIPPED_SYMBOL])
        self.page.keyboard.press('Escape')
        self.assertFalse(panel.count())
        self.assertTrue(self.trigger.evaluate('button => button === document.activeElement'))
        self.open_menu()
        reveal.click()
        self.page.keyboard.press('Escape')
        self.assertFalse(panel.count())
        self.assertTrue(self.trigger.evaluate('button => button === document.activeElement'))

    def test_recorded_symbol_title_opens_with_touch_and_keyboard_and_restores_focus(self):
        for mode, viewport in (('single', (320, 640)), ('sidecar', (844, 390))):
            with self.subTest(mode=mode, viewport=viewport):
                self.open(kind='symbols', mode=mode, width=viewport[0], height=viewport[1], touch=True)
                self.page.evaluate('''symbol => {
                    const data = fgData.read('tlData');
                    fgView('timeline').probe().select(data.cols.name.indexOf(data.strings.indexOf(symbol)));
                }''', CLIPPED_SYMBOL)
                if viewport[0] == 844:
                    self.page.get_by_role('separator', name='Resize details').press('Home')
                title = self.page.get_by_role('button', name=f'Recorded symbol: {CLIPPED_SYMBOL}', exact=True)
                bounds = title.bounding_box(timeout=2000)
                self.assertIsNotNone(bounds)
                self.assertGreaterEqual(bounds['width'], 44)
                self.assertGreaterEqual(bounds['height'], 44)
                actions = self.trigger.bounding_box()
                self.assertLessEqual(bounds['x'] + bounds['width'], actions['x'])
                state = '() => { const model = fgView("timeline").probe(); return [model.sel(), model.view()]; }'
                original = self.page.evaluate(state)
                panel = self.page.get_by_role('region', name='Recorded symbol')
                self.assertEqual(title.get_attribute('aria-expanded'), 'false')
                self.assertFalse(panel.count())
                for method in ('tap', 'Space'):
                    if method == 'tap':
                        title.tap()
                    else:
                        title.press(method)
                    self.assertFalse(self.menu.is_visible())
                    self.assertEqual(title.get_attribute('aria-expanded'), 'true')
                    self.assertEqual(panel.locator('code').text_content(), CLIPPED_SYMBOL)
                    self.assertTrue(panel.get_by_role('heading').evaluate('heading => heading === document.activeElement'))
                    if method == 'tap':
                        self.page.keyboard.press('Escape')
                    else:
                        panel.get_by_role('button', name='Hide symbol').press('Enter')
                    self.assertFalse(panel.count())
                    self.assertEqual(title.get_attribute('aria-expanded'), 'false')
                    self.assertTrue(title.evaluate('button => button === document.activeElement'))
                    self.assertEqual(self.page.evaluate(state), original)

    def test_short_recorded_symbol_keeps_name_and_return_visible(self):
        for mode, viewport, route in product(('single', 'sidecar'), ((320, 740), (568, 320)), ('title', 'menu')):
            with self.subTest(mode=mode, viewport=viewport, route=route):
                self.open(kind='symbols', mode=mode, width=viewport[0], height=viewport[1], touch=True)
                self.page.evaluate('''symbol => {
                    const data = fgData.read('tlData'), model = fgView('timeline').probe();
                    model.select(-1);
                    localStorage.setItem('fgTl.detailFrac', '0.09');
                    model.select(data.cols.name.indexOf(data.strings.indexOf(symbol)));
                }''', CLIPPED_SYMBOL)
                title = self.page.locator('[data-call-symbol]')
                opener = title if route == 'title' else self.trigger
                scroll = '''() => [scrollX, scrollY, document.querySelector('.tl-scroll').scrollTop,
                    document.querySelector('.tl-detail').scrollTop, document.querySelector('.tl-detail').scrollLeft]'''
                opener.scroll_into_view_if_needed()
                before = self.page.evaluate(scroll)
                opener.tap()
                if route == 'menu':
                    self.menu.get_by_role('menuitem', name='Show recorded symbol', exact=True).tap()
                panel = self.page.get_by_role('region', name='Recorded symbol')
                back = panel.get_by_role('button', name='Hide symbol', exact=True)
                self.assertEqual(back.inner_text(), '←')
                self.assertEqual(panel.locator('code').text_content(), CLIPPED_SYMBOL)
                self.assertTrue(panel.get_by_role('heading').evaluate('heading => heading === document.activeElement'))
                target = back.evaluate('''button => {
                    const panel = button.closest('.tl-recorded-symbol'), pane = panel.closest('.tl-detail');
                    const bounds = pane.getBoundingClientRect(), control = button.getBoundingClientRect();
                    const code = panel.querySelector('code'), first = document.createRange();
                    first.setStart(code.firstChild, 0);
                    first.setEnd(code.firstChild, 1);
                    const point = rect => [rect.left + rect.width / 2, rect.top + rect.height / 2];
                    const inside = rect => rect.top >= Math.max(0, bounds.top + pane.clientTop) &&
                        rect.bottom <= Math.min(innerHeight, bounds.top + pane.clientTop + pane.clientHeight);
                    const text = first.getBoundingClientRect();
                    if (!inside(control) || !inside(text) || control.width < 44 || control.height < 44 ||
                        !code.contains(document.elementFromPoint(...point(text))) ||
                        !button.contains(document.elementFromPoint(...point(control)))) return null;
                    return point(control);
                }''')
                self.assertIsNotNone(target)
                if route == 'title':
                    self.page.touchscreen.tap(*target)
                else:
                    self.page.keyboard.press('Shift+Tab')
                    self.assertTrue(back.evaluate('button => button === document.activeElement'))
                    self.page.keyboard.press('Space')
                self.assertFalse(panel.count())
                self.assertEqual(self.page.evaluate(scroll), before)
                self.assertTrue(opener.evaluate('button => button === document.activeElement'))
                self.assertEqual(title.get_attribute('aria-expanded'), 'false')

    def test_recorded_symbol_action_includes_abbreviated_clones_but_not_ordinary_names(self):
        self.open(kind='symbols')
        for symbol, expected in [(CLONE_SYMBOL, True), ('main', False)]:
            with self.subTest(symbol=symbol):
                self.page.evaluate('''symbol => {
                    const data = fgData.read('tlData');
                    fgView('timeline').probe().select(data.cols.name.indexOf(data.strings.indexOf(symbol)));
                }''', symbol)
                self.open_menu()
                action = self.menu.get_by_role('menuitem', name='Show recorded symbol')
                self.assertEqual(action.is_visible(), expected)
                if expected:
                    action.click()
                    self.assertEqual(self.page.get_by_role('region', name='Recorded symbol').locator('code').text_content(), symbol)
                else:
                    self.page.keyboard.press('Escape')

    def test_synthetic_frames_do_not_offer_recorded_call_actions(self):
        self.open('kernel')
        indices = self.page.evaluate('''() => {
            const model = fgView('timeline').probe();
            return Array.from({length: model.NA - model.N}, (_, offset) => model.N + offset);
        }''')
        self.assertTrue(indices)
        for index in indices[:5]:
            self.page.evaluate("index => fgView('timeline').probe().select(index)", index)
            self.assertEqual(self.page.locator('[data-call-actions]').count(), 0)
            self.assertFalse(self.menu.is_visible())


if __name__ == '__main__':
    unittest.main()
