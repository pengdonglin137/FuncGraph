"""Timeline shortcuts must not consume input-method or native editing keys."""

from pathlib import Path
import threading
import unittest

import helpers
from fglib.viewer import create_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class TimelineKeyboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel()
        cls.addClassCleanup(cls.kernel.close)
        common = ['--no-config', '--timeline', '--enable-fold']
        cls.kernel.run(helpers.fixture('pi4_sched'), *common, '--filter', out='single.html')
        cls.kernel.run(helpers.fixture('pi4_sched'), *common, out='no-filter.html')
        cls.kernel.run(helpers.fixture('pi4_sched'), *common, '--filter',
                       '--report-mode', 'sidecar', out='sidecar')
        large_trace = Path(cls.kernel.dir, 'large.txt')
        large_trace.write_text(''.join(f'0) task-42 | composition_{index:04d}();\n' for index in range(5000)))
        cls.kernel.run(str(large_trace), '--no-config', '--timeline', '--filter', '--no-text-view', out='large.html')
        cls.server = create_server(Path(cls.kernel.dir, 'sidecar'))
        thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(thread.join, 5)
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 960})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.open_report()

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open_report(self, mode='single'):
        if mode in ('sidecar', 'windowed'):
            url = self.server.url + ('?text=windowed' if mode == 'windowed' else '')
        else:
            url = Path(self.kernel.dir, mode + '.html').as_uri()
        self.page.goto(url)
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.page.locator('.tl-canvas').wait_for(state='visible')

    def prepare_find(self):
        self.page.locator('.tl-search').fill('.*')
        self.page.locator('.tl-search').press('Enter')
        self.page.wait_for_function("() => document.querySelector('.tl-count').getAttribute('aria-busy') === 'false'")
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 0)

    def assert_protected_keys(self, selector, keys):
        self.page.locator(selector).focus()
        results = self.page.evaluate('''({selector,keys}) => {
            const input = document.querySelector(selector), results = [];
            const snapshot = () => JSON.stringify({find:document.querySelector('.tl-search').value,
                draft:document.querySelector('.tl-ftext').value,filter:fgFilterState.text,
                selected:fgView('timeline').probe().sel(),view:fgView('timeline').probe().view(),
                active:document.activeElement.className,current:fgCurrentView(),
                options:document.querySelector('.tl-options').className,
                fullscreen:document.getElementById('timelineView').className,
                help:document.querySelector('.tl-help').className,
                dialogs:Array.from(document.querySelectorAll('dialog')).map(dialog => dialog.open)});
            for (const key of keys) for (const options of [
                {isComposing:true},{keyCode:229},{ctrlKey:true},{metaKey:true},{altKey:true},{consumed:true}
            ]) {
                const before = snapshot();
                const event = new KeyboardEvent('keydown', {key,bubbles:true,cancelable:true,...options});
                if (options.consumed) event.preventDefault();
                input.dispatchEvent(event);
                results.push({key,options,unchanged:before === snapshot(),prevented:event.defaultPrevented});
            }
            return results;
        }''', dict(selector=selector, keys=keys))
        for result in results:
            with self.subTest(selector=selector, key=result['key'], options=result['options']):
                self.assertTrue(result['unchanged'], result)
                self.assertEqual(result['prevented'], result['options'].get('consumed', False), result)

    def test_find_preserves_ime_modifiers_and_consumed_keys_in_all_renderers(self):
        for mode in ('single', 'no-filter', 'sidecar', 'windowed'):
            with self.subTest(mode=mode):
                self.open_report(mode)
                self.prepare_find()
                self.assert_protected_keys('.tl-search', ['Enter', 'Escape'])

    def test_filter_preserves_draft_focus_and_active_query_for_protected_keys(self):
        for mode in ('single', 'sidecar', 'windowed'):
            with self.subTest(mode=mode):
                self.open_report(mode)
                self.prepare_find()
                helpers.open_timeline_tools(self.page)
                self.page.locator('.tl-ftext').fill('func:mutex')
                self.assert_protected_keys('.tl-ftext', ['Enter', 'Escape'])
                self.assertTrue(self.page.locator('.tl-fdraft').is_visible())

    def test_global_shortcuts_preserve_input_method_and_native_key_combinations(self):
        self.prepare_find()
        self.assert_protected_keys('.tl-canvas', [
            'n', 'N', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', '[', ']',
            'Enter', '/', 'g', 'h', 'm', '?', 'Escape',
        ])

    def test_protected_escape_does_not_close_tools_or_fullscreen(self):
        self.prepare_find()
        self.page.evaluate("document.getElementById('timelineView').requestFullscreen = undefined")
        self.page.locator('[data-act=max]').click()
        self.assertTrue(self.page.locator('#timelineView').evaluate("root => root.classList.contains('tl-fullscreen')"))
        helpers.open_timeline_tools(self.page)
        self.page.locator('.tl-ftext').fill('func:mutex')
        for selector in ('.tl-search', '.tl-ftext', '.tl-canvas'):
            self.assert_protected_keys(selector, ['Escape'])
        helpers.open_timeline_tools(self.page)
        self.page.locator('.tl-ftext').press('Escape')
        self.assertFalse(self.page.locator('.tl-options').is_visible())
        self.assertTrue(self.page.locator('#timelineView').evaluate("root => root.classList.contains('tl-fullscreen')"))
        self.page.locator('.tl-search').press('Escape')
        self.assertFalse(self.page.locator('#timelineView').evaluate("root => root.classList.contains('tl-fullscreen')"))

    def start_composition(self, value):
        self.page.locator('.tl-search').focus()
        self.page.evaluate('''value => {
            const input = document.querySelector('.tl-search');
            input.dispatchEvent(new CompositionEvent('compositionstart', {bubbles:true}));
            input.value = value;
            input.dispatchEvent(new InputEvent('input', {bubbles:true,isComposing:true,data:value}));
        }''', value)

    def end_composition(self, value):
        self.page.evaluate('''value => {
            const input = document.querySelector('.tl-search');
            input.value = value;
            input.dispatchEvent(new CompositionEvent('compositionend', {bubbles:true,data:value}));
            input.dispatchEvent(new InputEvent('input', {bubbles:true,isComposing:false,data:value}));
        }''', value)

    def test_composition_defers_regex_evaluation_and_commits_once_without_navigation(self):
        self.prepare_find()
        selected = self.page.evaluate("fgView('timeline').probe().sel()")
        unique_names = self.page.evaluate('''() => {
            const probe = fgView('timeline').probe(), original = RegExp.prototype.test;
            window.compositionSearchTests = [];
            RegExp.prototype.test = function(value) {
                if (this.source === '^__arm64_sys_openat$') compositionSearchTests.push(value);
                return original.call(this, value);
            };
            return [...new Set(Array.from(probe.sName.slice(0,probe.N), name => probe.S[name]))];
        }''')
        self.start_composition('^__arm64_sys_openat$')
        self.page.wait_for_timeout(250)
        self.assertEqual(self.page.evaluate('compositionSearchTests'), [])
        self.assertEqual(self.page.locator('.tl-count').inner_text(), 'Composing…')
        self.assertEqual(self.page.locator('.tl-count').get_attribute('aria-busy'), 'true')
        self.assertTrue(self.page.locator('[data-act=searchnext]').is_disabled())
        self.page.locator('.tl-search').press('Enter')
        self.page.locator('.tl-search').press('Escape')
        self.assertEqual(self.page.locator('.tl-search').input_value(), '^__arm64_sys_openat$')
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), selected)
        self.end_composition('^__arm64_sys_openat$')
        self.page.wait_for_function("() => document.querySelector('.tl-count').getAttribute('aria-busy') === 'false'")
        self.assertEqual(sorted(self.page.evaluate('compositionSearchTests')), sorted(unique_names))
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), selected)
        self.page.locator('.tl-search').press('Enter')
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().nameOf(fgView('timeline').probe().sel())"), '__arm64_sys_openat')
        self.assertEqual(len(self.page.evaluate('compositionSearchTests')), len(unique_names))

    def test_intermediate_invalid_or_empty_composition_does_not_clear_previous_results(self):
        self.prepare_find()
        selected = self.page.evaluate("fgView('timeline').probe().sel()")
        for draft in ('[', '', '中文🙂'):
            with self.subTest(draft=draft):
                self.start_composition(draft)
                self.page.wait_for_timeout(200)
                self.assertEqual(self.page.locator('.tl-search').get_attribute('aria-invalid'), 'false')
                self.assertEqual(self.page.locator('.tl-count').inner_text(), 'Composing…')
                self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), selected)
                self.end_composition('.*')
                self.page.wait_for_function("() => document.querySelector('.tl-count').getAttribute('aria-busy') === 'false'")
        self.end_composition('[')
        self.page.wait_for_function("() => document.querySelector('.tl-search').getAttribute('aria-invalid') === 'true'")
        self.assertEqual(self.page.locator('.tl-count').inner_text(), 'Invalid regex')
        self.page.locator('.tl-search').press('Escape')
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '')

    def test_composition_cancels_debounce_and_uses_latest_shared_filter(self):
        self.page.locator('.tl-search').fill('.*')
        self.start_composition('^mutex_unlock$')
        self.page.evaluate("async () => { document.getElementById('filterInput').value='func:^not_present$'; await applyFilter(); }")
        self.page.wait_for_timeout(220)
        self.assertEqual(self.page.locator('.tl-count').inner_text(), 'Composing…')
        self.end_composition('^mutex_unlock$')
        self.page.wait_for_function("() => document.querySelector('.tl-count').textContent === '0 calls'")
        self.assertIn('shared filter excludes', self.page.locator('.tl-count').get_attribute('title'))
        self.page.evaluate('clearFilter()')
        self.page.wait_for_function("() => document.querySelector('.tl-count').textContent === '1 calls'")
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), -1)

    def test_composition_end_while_text_is_open_stays_paused_until_timeline_returns(self):
        self.prepare_find()
        selected = self.page.evaluate("fgView('timeline').probe().sel()")
        self.start_composition('^mutex_unlock$')
        self.page.evaluate("async () => { await fgSwitchView('text'); }")
        self.end_composition('^mutex_unlock$')
        self.page.wait_for_timeout(220)
        self.assertEqual(self.page.locator('.tl-count').text_content(), 'Search paused')
        self.assertEqual(self.page.evaluate('fgCurrentView()'), 'text')
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.page.wait_for_function("() => document.querySelector('.tl-count').getAttribute('aria-busy') === 'false'")
        self.assertEqual(self.page.locator('.tl-search').input_value(), '^mutex_unlock$')
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), selected)

    def test_composition_cancels_pending_index_work_and_its_navigation(self):
        self.open_report('large')
        pending = self.page.evaluate('''() => {
            const input = document.querySelector('.tl-search'), original = performance.now.bind(performance);
            let clock = original();
            performance.now = () => clock += 8;
            try {
                input.value = '.*';
                input.dispatchEvent(new InputEvent('input', {bubbles:true}));
                input.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter',bubbles:true,cancelable:true}));
                const status = document.querySelector('.tl-count').textContent;
                input.dispatchEvent(new CompositionEvent('compositionstart', {bubbles:true}));
                input.value = '^composition_4999$';
                input.dispatchEvent(new InputEvent('input', {bubbles:true,isComposing:true}));
                return status;
            } finally { performance.now = original; }
        }''')
        self.assertIn('Indexing', pending)
        self.page.wait_for_timeout(220)
        self.assertEqual(self.page.locator('.tl-count').inner_text(), 'Composing…')
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), -1)
        self.end_composition('^composition_4999$')
        self.page.wait_for_function("() => document.querySelector('.tl-count').textContent === '1 calls'")
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), -1)
        self.page.locator('.tl-search').press('Enter')
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 4999)

    def test_composing_status_keeps_compact_and_desktop_layout_with_both_themes(self):
        for width in (320, 1440):
            self.page.set_viewport_size({'width': width, 'height': 800})
            for theme in ('light', 'dark'):
                with self.subTest(width=width, theme=theme):
                    self.page.evaluate("theme => document.documentElement.setAttribute('data-theme',theme)", theme)
                    self.start_composition('[')
                    self.assertTrue(self.page.locator('.tl-search').is_visible())
                    self.assertTrue(self.page.locator('.tl-count').is_visible())
                    self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth === document.documentElement.clientWidth'))
                    self.assertEqual(self.page.locator('.tl-count').inner_text(), 'Composing…')
                    self.end_composition('.*')
                    self.page.wait_for_function("() => document.querySelector('.tl-count').getAttribute('aria-busy') === 'false'")

    def test_chromium_native_composition_waits_for_committed_text(self):
        self.prepare_find()
        selected = self.page.evaluate("fgView('timeline').probe().N - 1")
        self.page.evaluate("selected => fgView('timeline').probe().select(selected)", selected)
        self.page.locator('.tl-search').select_text()
        self.page.evaluate('''() => {
            window.nativeCompositionEvents = [];
            for (const kind of ['compositionstart','compositionend','input']) {
                document.querySelector('.tl-search').addEventListener(kind, event => {
                    nativeCompositionEvents.push({kind,trusted:event.isTrusted,composing:!!event.isComposing});
                });
            }
        }''')
        session = self.context.new_cdp_session(self.page)
        self.addCleanup(session.detach)
        session.send('Input.imeSetComposition', dict(text='[', selectionStart=1, selectionEnd=1))
        self.page.wait_for_timeout(220)
        self.assertEqual(self.page.locator('.tl-search').input_value(), '[')
        self.assertEqual(self.page.locator('.tl-count').inner_text(), 'Composing…')
        self.assertEqual(self.page.locator('.tl-search').get_attribute('aria-invalid'), 'false')
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), selected)
        session.send('Input.insertText', dict(text='^mutex_unlock$'))
        self.page.wait_for_function("() => document.querySelector('.tl-count').getAttribute('aria-busy') === 'false'")
        self.assertEqual(self.page.locator('.tl-search').input_value(), '^mutex_unlock$')
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), selected)
        events = self.page.evaluate('nativeCompositionEvents')
        self.assertTrue(any(event['kind'] == 'compositionstart' and event['trusted'] for event in events))
        self.assertTrue(any(event['kind'] == 'input' and event['trusted'] and event['composing'] for event in events))
        self.assertEqual(sum(event['kind'] == 'compositionend' for event in events), 1)
        self.page.locator('.tl-search').press('Enter')
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().nameOf(fgView('timeline').probe().sel())"), 'mutex_unlock')

    def test_unmodified_find_filter_and_native_buttons_keep_existing_behavior(self):
        self.prepare_find()
        total = self.page.evaluate("fgView('timeline').probe().N")
        self.page.locator('.tl-search').press('Shift+Enter')
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), total - 1)
        self.page.locator('[data-act=searchnext]').press('Enter')
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 0)
        self.page.locator('[data-act=searchnext]').press('Space')
        self.assertEqual(self.page.evaluate("fgView('timeline').probe().sel()"), 1)
        helpers.open_timeline_tools(self.page)
        self.page.locator('.tl-ftext').fill('func:mutex')
        self.page.locator('.tl-ftext').press('Enter')
        self.page.wait_for_function("() => fgFilterState.text === 'func:mutex' && !fgFilterProgress")
        helpers.open_timeline_tools(self.page)
        self.page.locator('.tl-ftext').fill('unfinished')
        self.page.locator('.tl-ftext').press('Escape')
        self.assertEqual(self.page.locator('.tl-ftext').input_value(), 'func:mutex')
        self.assertFalse(self.page.locator('.tl-options').is_visible())
        self.page.locator('.tl-search').press('Escape')
        self.assertEqual(self.page.locator('.tl-search').input_value(), '')
        self.assertEqual(self.page.evaluate('fgFilterState.text'), 'func:mutex')
