"""Selected-call actions stay compact, accessible and lazy in every report mode."""

from itertools import product
from pathlib import Path
import threading
import unittest

import helpers
from fglib.viewer import create_server
from test_uftrace_import import capture, stream

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
        for kind, mode in product(('mixed', 'user', 'kernel', 'timeline-only'), ('single', 'sidecar')):
            trace = helpers.fixture('pi4_nodur') if kind == 'kernel' else str(
                Path(cls.kernel.dir, ('mixed' if kind == 'timeline-only' else kind) + '.jsonl'))
            destination = kind + '-' + mode + ('.html' if mode == 'single' else '')
            options = ['--no-config', '--timeline', '--enable-fold', '--filter', '--report-mode', mode]
            if kind == 'timeline-only':
                options.append('--no-text-view')
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

    def open_menu(self):
        self.trigger.click()
        self.assertTrue(self.menu.is_visible())
        self.assertEqual(self.trigger.get_attribute('aria-expanded'), 'true')

    def focused_action(self):
        return self.page.evaluate('document.activeElement.dataset.callAction')

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
            self.page.locator('[data-trace-metadata] > summary').click()
            self.page.locator('.tl-detail a[data-line]').first.click()
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
                self.assertEqual(self.menu.locator('[role=menuitem]').count(), 2)
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

    def test_touch_targets_stay_44px_in_landscape_and_on_wide_tablets(self):
        for mode, viewport in product(('single', 'sidecar'), ((844, 390), (1024, 768), (1280, 800))):
            with self.subTest(mode=mode, viewport=viewport):
                self.open(mode=mode, width=viewport[0], height=viewport[1], touch=True)
                self.trigger.tap()
                self.assertTrue(self.menu.is_visible())
                self.assertGreaterEqual(self.trigger.bounding_box()['height'], 44)
                for item in self.menu.locator('[role=menuitem]').all():
                    self.assertGreaterEqual(item.bounding_box()['height'], 44)
                self.trigger.tap()
                self.assertFalse(self.menu.is_visible())
                self.assert_unchanged()

    def test_pointer_movement_keeps_menu_open_and_hover_is_not_required(self):
        self.open()
        self.open_menu()
        bounds = self.menu.bounding_box()
        self.page.mouse.move(bounds['x'] + 30, bounds['y'] + 20, steps=5)
        self.assertTrue(self.menu.is_visible())
        self.trigger.click()
        self.assertFalse(self.menu.is_visible())
        self.trigger.press('Space')
        self.assertTrue(self.menu.is_visible())
        self.assertEqual(self.focused_action(), 'distribution')

    def test_arrows_home_end_escape_and_tab_preserve_selection_and_time_range(self):
        self.open()
        self.trigger.focus()
        self.trigger.press('ArrowUp')
        self.assertEqual(self.focused_action(), 'text')
        for key, action in [('ArrowDown', 'distribution'), ('ArrowUp', 'text'), ('Home', 'distribution'),
                            ('End', 'text'), ('ArrowDown', 'distribution')]:
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

    def test_enter_shortcut_and_recorded_line_links_focus_the_visible_text_tab(self):
        for mode, route in product(('single', 'sidecar'), ('key', 'link')):
            with self.subTest(mode=mode, route=route):
                self.open(mode=mode)
                if route == 'key':
                    self.page.locator('.tl-canvas').focus()
                    self.page.keyboard.press('Enter')
                else:
                    self.page.locator('[data-trace-metadata] > summary').click()
                    self.page.locator('.tl-detail a[data-line]').first.click()
                self.page.wait_for_function("() => fgCurrentView()==='text' && !fgTextDeferred()")
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
