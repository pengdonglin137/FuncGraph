"""Headless browser smoke test of a generated report (skipped without Playwright + Chromium)."""
import base64
import csv
from itertools import product
import os
import unittest
from urllib.parse import unquote, urlsplit

import helpers

try:
    from playwright.sync_api import sync_playwright
except ImportError:      # pragma: no cover
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class BrowserSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.k = helpers.FakeKernel()
        cls.k.run(helpers.fixture('pi4_sched'), '--enable-fold', '--filter', '--func-links',
                  '--base-url', 'https://example.org/src', '--timeline',
                  '--compare', helpers.fixture('pi4_default'), out='report.html')
        cls.url = 'file://' + cls.k.dir + '/report.html'
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch(ignore_default_args=['--hide-scrollbars'])
        except Exception as e:      # browser binaries not installed
            cls.pw.stop()
            cls.k.close()
            raise unittest.SkipTest(f'chromium not available: {e}')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.k.close()

    def setUp(self):
        self.errors = []
        self.page = self.browser.new_page(viewport={'width': 1500, 'height': 900})
        self.page.on('pageerror', lambda e: self.errors.append(str(e)))
        self.page.on('console', lambda m: self.errors.append(m.text) if m.type == 'error' else None)
        self.page.goto(self.url, wait_until='load')
        self.page.evaluate('localStorage.clear()')

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [], 'JavaScript errors in the page')

    def ev(self, js, arg=None):
        return self.page.evaluate(js, arg)

    def install_deferred_text_renderer(self):
        return self.ev('''() => {
            const indices = fgTextNavigation.expandable.slice(1,4);
            const rows = new Map(indices.map(index => {
                const number = fgTextNavigation.order[index], element = document.getElementById('L' + number);
                const placeholder = document.createComment('unmounted Text row');
                element.replaceWith(placeholder);
                return [number, {element, placeholder}];
            }));
            window.textRendererQueue = [];
            fgViews.text = {el:'content', init:() => ({ensureLine(number, {signal}) {
                return new Promise((resolve, reject) => textRendererQueue.push({number, signal, reject,
                    finish() {
                        const row = rows.get(number);
                        if (!signal.aborted && row) row.placeholder.replaceWith(row.element);
                        resolve(row?.element || document.getElementById('L' + number));
                    }}));
            }})};
            fgView('text');
            return indices;
        }''')

    def test_text_only_report_keeps_synchronous_navigation_without_view_boot(self):
        self.k.run(helpers.fixture('pi4_sched'), '--enable-fold', '--filter', '--func-links', out='text-only.html')
        self.page.goto('file://' + self.k.dir + '/text-only.html', wait_until='load')
        facts = self.ev('''() => {
            const index = fgTextNavigation.expandable.at(-1), number = fgTextNavigation.order[index];
            const result = setKeyboardSelectedLine(index, false);
            history.replaceState(null, '', '#L' + number);
            const anchor = handleAnchorNavigation();
            return {views:typeof fgView, synchronous:!result?.then && !anchor?.then,
                selected:keyboardSelectedLine.id, expected:'L'+number, highlighted:highlightedLine.id};
        }''')
        self.assertEqual(facts['views'], 'undefined')
        self.assertTrue(facts['synchronous'])
        self.assertEqual(facts['selected'], facts['expected'])
        self.assertEqual(facts['highlighted'], facts['expected'])

    def test_unmounted_keyboard_navigation_tracks_latest_requested_line_and_discards_late_completion(self):
        indices = self.install_deferred_text_renderer()
        facts = self.ev('''async indices => {
            keyboardSelectedIndex = indices[0] - 1;
            window.firstNavigation = navigateToExpandableLine(1);
            window.secondNavigation = navigateToExpandableLine(1);
            const requested = textRendererQueue.map(request => request.number);
            const cancelled = textRendererQueue[0].signal.aborted;
            textRendererQueue[1].finish(); await secondNavigation;
            textRendererQueue[0].finish(); await firstNavigation;
            return {requested, cancelled, selected:keyboardSelectedLine.id, index:keyboardSelectedIndex,
                selectedRows:document.querySelectorAll('.keyboard-selected').length, pending:fgTextRequestedLine};
        }''', indices)
        numbers = self.ev('indices => indices.map(index => fgTextNavigation.order[index])', indices)
        self.assertEqual(facts, dict(requested=numbers[:2], cancelled=True, selected='L' + str(numbers[1]),
                                    index=indices[1], selectedRows=1, pending=None))

    def test_unmounted_navigation_abort_settles_even_when_renderer_never_finishes(self):
        indices = self.install_deferred_text_renderer()
        facts = self.ev('''async index => {
            const previous = keyboardSelectedLine?.id;
            const controller = new AbortController();
            const pending = fgEnsureTextLine(fgTextNavigation.order[index], {signal:controller.signal}).catch(error => error.name);
            controller.abort();
            return {result:await pending, cancelled:textRendererQueue[0].signal.aborted,
                previous, selected:keyboardSelectedLine?.id};
        }''', indices[0])
        self.assertEqual(facts['result'], 'AbortError')
        self.assertTrue(facts['cancelled'])
        self.assertEqual(facts['previous'], facts['selected'])

    def test_text_line_service_preserves_sync_rows_and_rejects_wrong_renderer_identity(self):
        facts = self.ev('''async () => {
            const number = fgTextNavigation.order[fgTextNavigation.expandable[0]];
            const row = fgEnsureTextLine(number);
            const synchronous = !row?.then && row.id === 'L' + number;
            const previous = keyboardSelectedLine?.id;
            const wrong = document.getElementById('L' + fgTextNavigation.order.at(-1));
            const api = {ensureLine:() => wrong};
            fgViews.text = {el:'content', init:() => api}; fgView('text');
            const messages = [];
            for (const value of [0,-1,1.5,'2',Number.MAX_SAFE_INTEGER+1]) {
                try { await fgEnsureTextLine(value); messages.push('accepted'); }
                catch (error) { messages.push(error.message); }
            }
            for (const candidate of [wrong, row.cloneNode(true)]) {
                api.ensureLine = () => candidate;
                try { await fgEnsureTextLine(number); messages.push('accepted'); }
                catch (error) { messages.push(error.message); }
            }
            return {synchronous, messages, previous, selected:keyboardSelectedLine?.id};
        }''')
        self.assertTrue(facts['synchronous'])
        self.assertEqual(facts['messages'], ['Invalid Text line identity'] * 5 +
                         ['Text renderer returned a different line'] * 2)
        self.assertEqual(facts['previous'], facts['selected'])

    def test_unmounted_navigation_keeps_previous_selection_on_failure_and_can_retry(self):
        indices = self.install_deferred_text_renderer()
        facts = self.ev('''async index => {
            const previous = keyboardSelectedLine?.id;
            const first = setKeyboardSelectedLine(index, false);
            textRendererQueue[0].reject(new Error('injected Text renderer failure'));
            await first;
            const retained = keyboardSelectedLine?.id;
            const retry = setKeyboardSelectedLine(index, false);
            textRendererQueue[1].finish(); await retry;
            return {previous, retained, selected:keyboardSelectedLine.id, pending:fgTextRequestedLine};
        }''', indices[0])
        self.assertEqual(facts['previous'], facts['retained'])
        self.assertEqual(facts['selected'], self.ev('index => "L" + fgTextNavigation.order[index]', indices[0]))
        self.assertIsNone(facts['pending'])

    def test_changing_view_or_escape_cancels_unmounted_navigation_without_late_focus(self):
        indices = self.install_deferred_text_renderer()
        facts = self.ev('''async indices => {
            const previous = keyboardSelectedLine?.id;
            const first = setKeyboardSelectedLine(indices[0], false);
            fgSwitchView('timeline');
            const cancelled = textRendererQueue[0].signal.aborted;
            textRendererQueue[0].finish(); await first;
            const retained = keyboardSelectedLine?.id;
            fgSwitchView('text');
            const second = setKeyboardSelectedLine(indices[1], false);
            document.body.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}));
            textRendererQueue[1].finish(); await second;
            return {previous, retained, cancelled, escape:textRendererQueue[1].signal.aborted,
                selected:keyboardSelectedLine, pending:fgTextRequestedLine};
        }''', indices)
        self.assertEqual(facts['previous'], facts['retained'])
        self.assertTrue(facts['cancelled'])
        self.assertTrue(facts['escape'])
        self.assertIsNone(facts['selected'])
        self.assertIsNone(facts['pending'])

    def test_late_hash_request_cannot_override_newer_keyboard_navigation(self):
        indices = self.install_deferred_text_renderer()
        facts = self.ev('''async indices => {
            const target = fgTextNavigation.order[indices[0]];
            history.replaceState(null, '', '#L' + target);
            const anchor = handleAnchorNavigation();
            const keyboard = setKeyboardSelectedLine(indices[1], false);
            textRendererQueue[1].finish(); await keyboard;
            textRendererQueue[0].finish(); await anchor;
            await new Promise(resolve => setTimeout(resolve, 150));
            return {selected:keyboardSelectedLine.id, highlighted:highlightedLine?.id || null,
                cancelled:textRendererQueue[0].signal.aborted};
        }''', indices)
        self.assertEqual(facts['selected'], self.ev('index => "L" + fgTextNavigation.order[index]', indices[1]))
        self.assertIsNone(facts['highlighted'])
        self.assertTrue(facts['cancelled'])

    def test_timeline_text_flash_is_cancelled_when_immediately_returning_to_timeline(self):
        facts = self.ev('''async () => {
            fgSwitchView('timeline');
            fgView('timeline').probe().select(0);
            window.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', cancelable:true}));
            const switched = fgCurrentView();
            fgSwitchView('timeline');
            await new Promise(resolve => setTimeout(resolve, 100));
            return {switched, current:fgCurrentView(), flashes:document.querySelectorAll('#content .tl-flash').length};
        }''')
        self.assertEqual(facts, dict(switched='text', current='timeline', flashes=0))

    def load_startup_report(self, saved='text', theme='light', fragment='', filename='report.html'):
        self.ev("({saved, theme}) => { lsSet('view', saved); localStorage.setItem('theme', theme); }",
                dict(saved=saved, theme=theme))
        with open(os.path.join(self.k.dir, filename), encoding='utf-8') as stream:
            report = stream.read()
        instrument = '''<script>
            window.startupFacts = {
                visibility: getComputedStyle(document.getElementById('content')).contentVisibility,
                theme: document.documentElement.getAttribute('data-theme'),
                textFocus: 0, themeChanges: 0
            };
            const startupFocus = HTMLElement.prototype.focus;
            HTMLElement.prototype.focus = function (...args) {
                if (this.matches('.line-container')) startupFacts.textFocus++;
                return startupFocus.apply(this, args);
            };
            new MutationObserver(records => startupFacts.themeChanges += records.length)
                .observe(document.documentElement, {attributes:true, attributeFilter:['data-theme']});
        </script>'''
        report = report.replace('<div id="content">', '<div id="content">' + instrument, 1)
        path = os.path.join(self.k.dir, 'startup.html')
        with open(path, 'w', encoding='utf-8') as stream:
            stream.write(report)
        self.page.goto('file://' + path + fragment, wait_until='load')

    def test_saved_timeline_skips_initial_text_render_and_focus(self):
        self.load_startup_report(saved='timeline', theme='dark')
        facts = self.ev('startupFacts')
        self.assertEqual(facts, dict(visibility='hidden', theme='dark', textFocus=0, themeChanges=0))
        self.assertEqual(self.ev('fgCurrentView()'), 'timeline')
        self.assertTrue(self.ev("document.getElementById('content').classList.contains('fg-off')"))
        self.assertTrue(self.ev("typeof fgInitialView === 'undefined'"))
        self.ev("fgSwitchView('text')")
        self.assertEqual(self.ev("getComputedStyle(document.getElementById('content')).contentVisibility"), 'visible')
        self.page.fill('#filterInput', 'comm:sh')
        self.page.press('#filterInput', 'Enter')
        self.assertEqual(self.ev('fgFilterState.text'), 'comm:sh')
        self.ev("fgSwitchView('timeline')")
        self.assertIn('of', self.page.locator('.tl-fcount').inner_text())
        self.ev('toggleTheme()')
        self.assertEqual(self.ev("document.documentElement.getAttribute('data-theme')"), 'light')

    def test_saved_compare_skips_initial_text_render_and_focus(self):
        self.load_startup_report(saved='compare')
        self.assertEqual(self.ev('startupFacts.visibility'), 'hidden')
        self.assertEqual(self.ev('startupFacts.textFocus'), 0)
        self.assertEqual(self.ev('fgCurrentView()'), 'compare')
        self.assertGreater(self.page.locator('.cmp-table tbody tr').count(), 0)
        self.ev("fgSwitchView('text')")
        self.assertEqual(self.ev("getComputedStyle(document.getElementById('content')).contentVisibility"), 'visible')

    def test_initial_text_and_anchors_override_unavailable_or_saved_views(self):
        for saved, fragment in [('text', ''), ('missing', ''), ('timeline', '#L10')]:
            with self.subTest(saved=saved, fragment=fragment):
                self.load_startup_report(saved=saved, fragment=fragment)
                self.assertEqual(self.ev('startupFacts.visibility'), 'visible')
                self.assertEqual(self.ev('fgCurrentView()'), 'text')
                self.assertGreater(self.ev('startupFacts.textFocus'), 0)
                self.assertEqual(self.ev('startupFacts.themeChanges'), 0)
                self.assertTrue(self.ev("typeof fgInitialView === 'undefined'"))

    def test_timeline_only_default_precedes_saved_view_and_anchor(self):
        self.k.run(helpers.fixture('pi4_sched'), '--timeline', '--no-text-view',
                   '--compare', helpers.fixture('pi4_default'), out='timeline_only.html')
        self.load_startup_report(saved='compare', fragment='#L1', filename='timeline_only.html')
        self.assertEqual(self.ev('startupFacts.visibility'), 'hidden')
        self.assertEqual(self.ev('startupFacts.textFocus'), 0)
        self.assertEqual(self.ev('fgCurrentView()'), 'timeline')
        self.assertGreater(self.ev("fgView('timeline').probe().N"), 100)

        self.page.locator('#viewHelpBtn').click()
        self.page.wait_for_selector('.tl-help.show')

    def test_plain_report_has_early_theme_and_text_keyboard_focus(self):
        self.k.run(helpers.fixture('pi4_default'), out='plain.html')
        self.load_startup_report(saved='timeline', theme='dark', filename='plain.html')
        self.assertEqual(self.ev('startupFacts.visibility'), 'visible')
        self.assertEqual(self.ev('startupFacts.theme'), 'dark')
        self.assertEqual(self.ev('startupFacts.themeChanges'), 0)
        self.assertGreater(self.ev('startupFacts.textFocus'), 0)
        self.assertTrue(self.ev("typeof fgInitialView === 'undefined'"))
        self.ev('toggleTheme()')
        self.assertEqual(self.ev("document.documentElement.getAttribute('data-theme')"), 'light')
        self.page.locator('#viewHelpBtn').click()
        self.assertTrue(self.page.locator('#keyboardHint').is_visible())

    def test_text_selection_without_scrolling_preserves_viewport_and_focus(self):
        for width, theme in product([390, 1500], ['light', 'dark']):
            with self.subTest(width=width, theme=theme):
                self.page.set_viewport_size(dict(width=width, height=640))
                facts = self.ev('''theme => {
                    document.documentElement.setAttribute('data-theme', theme);
                    document.activeElement.blur();
                    window.scrollTo(0, 400);
                    const before = [scrollX, scrollY];
                    const target = fgTextNavigation.expandable[fgTextNavigation.expandableCount - 2];
                    setKeyboardSelectedLine(target, false);
                    return {before, after:[scrollX, scrollY],
                        focused:document.activeElement === document.getElementById('L' + fgTextNavigation.order[target])};
                }''', theme)
                self.assertTrue(facts['focused'])
                self.assertEqual(facts['after'], facts['before'])

    def test_text_navigation_from_nonexpandable_rows_uses_the_nearest_call(self):
        for direction in [1, -1]:
            with self.subTest(direction=direction):
                facts = self.ev('''direction => {
                    const rows = Array.from(document.querySelectorAll('#content .line-container'));
                    const candidates = rows.map((row, index) => row.matches('.expandable') ? index : -1)
                        .filter(index => index >= 0);
                    const start = rows.findIndex((row, index) => !row.matches('.expandable') &&
                        (direction > 0 ? candidates.some(candidate => candidate > index) :
                            candidates.some(candidate => candidate < index)));
                    const expected = direction > 0 ? candidates.find(index => index > start) :
                        candidates.filter(index => index < start).pop();
                    setKeyboardSelectedLine(start, false);
                    navigateToExpandableLine(direction);
                    return {start, expected:rows[expected].id, actual:keyboardSelectedLine.id};
                }''', direction)
                self.assertGreaterEqual(facts['start'], 0)
                self.assertEqual(facts['actual'], facts['expected'])

    def test_text_navigation_selection_keeps_its_line_identity_after_reordering(self):
        facts = self.ev('''() => {
            const original = Array.from(document.querySelectorAll('#content .line-container'));
            fgSetTextOrder('task');
            const grouped = Array.from(document.querySelectorAll('#content .line-container'));
            const target = original.find((row, index) => row.matches('.expandable') && grouped.indexOf(row) !== index);
            const before = original.indexOf(target);
            const expected = grouped.indexOf(target);
            fgSetTextOrder('time');
            setKeyboardSelectedLine(before, false);
            fgSetTextOrder('task');
            return {before, expected, actual:keyboardSelectedIndex, same:keyboardSelectedLine === target};
        }''')
        self.assertNotEqual(facts['before'], facts['expected'])
        self.assertTrue(facts['same'])
        self.assertEqual(facts['actual'], facts['expected'])

    def test_text_navigation_hot_paths_do_not_scan_the_complete_dom(self):
        facts = self.ev('''() => {
            const root = document.getElementById('content');
            const target = root.querySelector('.line-container.expandable');
            const documentQuery = Document.prototype.querySelectorAll;
            const elementQuery = Element.prototype.querySelectorAll;
            const guard = function(original, selector) {
                if ((this === document || this === root) && selector.includes('line-container')) {
                    throw new Error('Whole-report navigation query: ' + selector);
                }
                return original.call(this, selector);
            };
            Document.prototype.querySelectorAll = function(selector) { return guard.call(this, documentQuery, selector); };
            Element.prototype.querySelectorAll = function(selector) { return guard.call(this, elementQuery, selector); };
            try {
                updateExpandableLines();
                navigateToExpandableLine(1);
                handleLineClick({target:target.querySelector('.line-content'), clientX:0, clientY:0,
                    preventDefault() {}}, target.dataset.lineId);
                clearTimeout(clickTimer); clickTimer = null;
                const clicked = keyboardSelectedLine === target;
                history.replaceState(null, '', '#' + target.id);
                handleAnchorNavigation();
                return {clicked, anchored:keyboardSelectedLine === target, highlighted:highlightedLine === target};
            } finally {
                Document.prototype.querySelectorAll = documentQuery;
                Element.prototype.querySelectorAll = elementQuery;
            }
        }''')
        self.assertEqual(facts, dict(clicked=True, anchored=True, highlighted=True))
        self.page.keyboard.press('Escape')
        self.assertEqual(self.page.locator('#content .highlighted').count(), 0)

    def test_text_navigation_visibility_and_order_match_filters_folds_and_duration_sort(self):
        facts = self.ev('''() => {
            const snapshots = [];
            const check = () => {
                const rows = Array.from(document.querySelectorAll('#content .line-container'));
                snapshots.push({order:rows.map(row => +row.dataset.lineNumber), model:fgTextNavigation.order.slice(),
                    expected:rows.filter(row => row.matches('.expandable') && row.style.display !== 'none')
                        .map(row => +row.dataset.lineNumber), actual:fgTextNavigation.expandable.map(index => fgTextNavigation.order[index])});
            };
            check();
            fgSetTextOrder('task'); check();
            const entry = document.querySelector('#content [data-fold-type=entry]');
            _setFolded(entry.dataset.foldId, true); check();
            const input = document.getElementById('filterInput');
            input.value = 'duration:>1 sort:desc'; applyFilter(); check();
            clearFilter(); check();
            input.value = 'cpu:999999'; applyFilter(); check();
            const selected = keyboardSelectedLine;
            navigateToExpandableLine(1); navigateToTextBoundary(false); navigateToTextBoundary(true);
            return {snapshots, empty:fgTextNavigation.expandableCount === 0,
                boundary:fgTextNavigation.boundary(false), unchanged:keyboardSelectedLine === selected};
        }''')
        for snapshot in facts['snapshots']:
            self.assertEqual(snapshot['order'], snapshot['model'])
            self.assertEqual(snapshot['expected'], snapshot['actual'])
        self.assertTrue(facts['empty'])
        self.assertTrue(facts['unchanged'])
        self.assertEqual(facts['boundary'], -1)

    def test_text_navigation_scrolls_explicitly_after_focusing_without_native_scroll(self):
        facts = self.ev('''() => {
            window.scrollTo(0, 0);
            const target = fgTextNavigation.expandable[fgTextNavigation.expandableCount - 2];
            const row = document.getElementById('L' + fgTextNavigation.order[target]);
            const original = window.scrollBy;
            const calls = [];
            const before = scrollY;
            const initialTop = row.getBoundingClientRect().top;
            window.scrollBy = function(options) {
                calls.push({options, focused:document.activeElement === row, scroll:scrollY});
                return original.call(this, options);
            };
            try { setKeyboardSelectedLine(target); }
            finally { window.scrollBy = original; }
            return {before, initialTop, height:innerHeight, calls};
        }''')
        self.assertGreater(facts['initialTop'], facts['height'])
        self.assertEqual(len(facts['calls']), 1)
        self.assertEqual(facts['calls'][0]['options']['behavior'], 'smooth')
        self.assertGreater(facts['calls'][0]['options']['top'], 0)
        self.assertTrue(facts['calls'][0]['focused'])
        self.assertEqual(facts['calls'][0]['scroll'], facts['before'])
        self.page.wait_for_function('''() => {
            const bounds = keyboardSelectedLine.getBoundingClientRect();
            return bounds.top >= document.querySelector('.controls').getBoundingClientRect().bottom
                && bounds.bottom <= innerHeight;
        }''')

    def test_text_navigation_reveals_rows_occluded_by_compact_sticky_toolbar(self):
        self.page.set_viewport_size(dict(width=390, height=640))
        for theme in ['light', 'dark']:
            with self.subTest(theme=theme):
                self.page.reload(wait_until='load')
                facts = self.ev('''theme => {
                    document.documentElement.setAttribute('data-theme', theme);
                    window.scrollTo(0, 0);
                    const target = fgTextNavigation.expandable[Math.floor(fgTextNavigation.expandableCount / 2)];
                    const row = document.getElementById('L' + fgTextNavigation.order[target]);
                    const height = row.getBoundingClientRect().height;
                    const toolbar = document.querySelector('.controls');
                    const toolbarHeight = toolbar.getBoundingClientRect().height;
                    window.scrollTo(0, row.getBoundingClientRect().top - toolbarHeight + height / 2);
                    const top = row.getBoundingClientRect().top;
                    const toolbarBottom = toolbar.getBoundingClientRect().bottom;
                    const original = window.scrollBy;
                    const calls = [];
                    window.scrollBy = function(options) {
                        calls.push(options);
                        return original.call(this, options);
                    };
                    try { setKeyboardSelectedLine(target); }
                    finally { window.scrollBy = original; }
                    return {top, height, toolbarBottom, calls};
                }''', theme)
                self.assertGreater(facts['top'], facts['height'])
                self.assertLess(facts['top'], facts['toolbarBottom'])
                self.assertEqual(len(facts['calls']), 1)
                self.assertEqual(facts['calls'][0]['behavior'], 'smooth')
                self.assertLess(facts['calls'][0]['top'], 0)
                self.page.wait_for_function('''() => {
                    const bounds = keyboardSelectedLine.getBoundingClientRect();
                    return bounds.top >= document.querySelector('.controls').getBoundingClientRect().bottom
                        && bounds.bottom <= innerHeight;
                }''')

    def stage_timeline(self, slow=False):
        self.ev('''slow => {
            const original = fgViews.timeline.steps;
            fgViews.timeline.staged = true;
            window.stageFactories = 0; window.stageClosed = 0;
            fgViews.timeline.steps = function* () {
                stageFactories++;
                try {
                    if (slow) for (let phase = 0; phase < 20; phase++) {
                        yield 'Test preparation phase ' + phase;
                        const end = performance.now() + 10;
                        while (performance.now() < end) {}
                    }
                    return yield* original();
                } finally { stageClosed++; }
            };
        }''', slow)

    def test_large_saved_timeline_automatically_stages_before_initial_paint(self):
        with open(os.path.join(self.k.dir, 'report.html'), encoding='utf-8') as stream:
            report = stream.read()
        marker = '<script type="application/json" id="tlData">'
        self.assertEqual(report.count(marker), 1)
        report = report.replace(marker, marker + ' ' * (1024 * 1024), 1)
        report = report.replace('</body>', '''<script>
            window.addEventListener('DOMContentLoaded', () => {
                window.largeStartup = {staged:fgViews.timeline.staged,
                    busy:document.getElementById('timelineView').getAttribute('aria-busy'),
                    canvas:!!document.querySelector('.tl-canvas')};
            });
        </script></body>''', 1)
        with open(os.path.join(self.k.dir, 'large.html'), 'w', encoding='utf-8') as stream:
            stream.write(report)
        self.load_startup_report(saved='timeline', filename='large.html')
        self.assertEqual(self.ev('largeStartup'), dict(staged=True, busy='true', canvas=False))
        self.assertEqual(self.ev('startupFacts.textFocus'), 0)
        self.page.wait_for_selector('.tl-canvas')
        self.assertEqual(self.ev('fgCurrentView()'), 'timeline')
        self.assertFalse(self.page.locator('#timelineView').get_attribute('aria-busy'))

    def test_staged_status_wraps_in_compact_light_and_dark_viewports(self):
        self.stage_timeline(slow=True)
        for width in [320, 390, 800, 1500]:
            for theme in ['light', 'dark']:
                with self.subTest(width=width, theme=theme):
                    self.page.set_viewport_size({'width': width, 'height': 700})
                    self.ev('theme => document.documentElement.setAttribute("data-theme", theme)', theme)
                    self.ev("() => { fgSwitchView('timeline'); }")
                    self.assertEqual(self.page.locator('#timelineView').get_attribute('aria-busy'), 'true')
                    self.assertTrue(self.page.locator('.fg-loading-status[role=status]').is_visible())
                    bounds = self.page.locator('.fg-loading button').bounding_box()
                    self.assertGreaterEqual(bounds['x'], 0)
                    self.assertLessEqual(bounds['x'] + bounds['width'], width)
                    self.assertLessEqual(bounds['y'] + bounds['height'], 700)
                    self.assertLessEqual(self.ev('document.documentElement.scrollWidth'), width)
                    self.page.locator('.fg-loading button').focus()
                    self.page.keyboard.press('Space')
                    self.assertEqual(self.ev('fgCurrentView()'), 'text')

    def test_staged_timeline_has_paintable_status_and_awaitable_cached_api(self):
        self.stage_timeline()
        initial = self.ev('''() => {
            window.stageReady = fgSwitchView('timeline');
            const repeated = fgSwitchView('timeline');
            requestAnimationFrame(() => requestAnimationFrame(() => {
                window.stagePaint = {
                    busy:document.getElementById('timelineView').getAttribute('aria-busy'),
                    status:document.querySelector('.fg-loading-status').textContent,
                    canvas:!!document.querySelector('.tl-canvas')
                };
            }));
            return {same:repeated === stageReady, promise:typeof stageReady.then === 'function',
                busy:document.getElementById('timelineView').getAttribute('aria-busy')};
        }''')
        self.assertEqual(initial, dict(same=True, promise=True, busy='true'))
        self.assertTrue(self.ev('async () => (await stageReady) === fgView("timeline")'))
        self.assertEqual(self.ev('stagePaint'), dict(busy='true', status='Reading Timeline data…', canvas=False))
        self.assertEqual(self.ev('[stageFactories, stageClosed]'), [1, 1])
        self.assertFalse(self.page.locator('#timelineView').get_attribute('aria-busy'))
        self.assertEqual(self.page.locator('.fg-loading').count(), 0)
        self.assertGreater(self.ev('fgView("timeline").probe().N'), 100)
        self.ev("fgSwitchView('text')")
        self.assertTrue(self.ev('''() => {
            const api = fgSwitchView('timeline');
            return !api.then && api === fgView('timeline');
        }'''))
        self.assertEqual(self.ev('stageFactories'), 1)

    def test_staged_cancel_button_restores_text_scroll_and_discards_pending_model(self):
        self.stage_timeline(slow=True)
        initial_scroll = self.ev("() => { window.scrollTo(0, 450); return scrollY; }")
        self.ev("() => { window.cancelledStage = fgSwitchView('timeline'); }")
        self.page.locator('.fg-loading button').click()
        self.assertTrue(self.ev('async () => await cancelledStage === null'))
        self.page.wait_for_timeout(100)
        self.assertEqual(self.ev('fgCurrentView()'), 'text')
        self.assertAlmostEqual(self.ev('scrollY'), initial_scroll, delta=2)
        self.assertTrue(self.ev("document.activeElement.matches('.view-tab[data-view=text]')"))
        self.assertEqual(self.page.locator('.tl-canvas').count(), 0)
        self.assertEqual(self.ev('[stageFactories, stageClosed]'), [1, 1])
        self.assertTrue(self.ev("typeof fgOnFilter === 'undefined'"))
        self.ev("fgViews.timeline.steps = () => __tlSteps()")
        self.ev("fgSwitchView('timeline')")
        self.assertEqual(self.page.locator('.tl-toolbar').count(), 1)

    def test_staged_escape_and_compare_switch_cancel_without_stale_activation(self):
        self.stage_timeline(slow=True)
        self.ev("() => { window.escapeStage = fgSwitchView('timeline'); }")
        self.page.keyboard.press('Escape')
        self.assertTrue(self.ev('async () => await escapeStage === null'))
        self.assertEqual(self.ev('fgCurrentView()'), 'text')
        self.ev("() => { window.compareStage = fgSwitchView('timeline'); fgSwitchView('compare'); }")
        self.assertTrue(self.ev('async () => await compareStage === null'))
        self.page.wait_for_timeout(100)
        self.assertEqual(self.ev('fgCurrentView()'), 'compare')
        self.assertGreater(self.page.locator('.cmp-table tbody tr').count(), 0)
        self.assertEqual(self.page.locator('.tl-canvas').count(), 0)
        self.assertEqual(self.ev('[stageFactories, stageClosed]'), [2, 2])

    def test_staged_direct_probe_finishes_same_iterator_exactly_once(self):
        self.stage_timeline()
        self.assertTrue(self.ev('''async () => {
            const ready = fgSwitchView('timeline');
            const api = fgView('timeline');
            return api.probe().N > 100 && await ready === api;
        }'''))
        self.page.wait_for_timeout(100)
        self.assertEqual(self.ev('[stageFactories, stageClosed]'), [1, 1])
        self.assertEqual(self.page.locator('.tl-toolbar').count(), 1)
        self.assertFalse(self.page.locator('#timelineView').get_attribute('aria-busy'))

    def test_staged_timeline_uses_filter_updated_during_preparation(self):
        self.stage_timeline()
        expected = self.ev('''() => {
            const data = JSON.parse(document.getElementById('tlData').textContent);
            const lines = new Set(data.cols.line.slice(0, 3));
            window.filterStage = fgSwitchView('timeline');
            fgNotifyFilter('func:pending', lines);
            return data.cols.line.filter((line, index) => lines.has(line) ||
                (data.cols.eline && lines.has(data.cols.eline[index]))).length;
        }''')
        self.assertTrue(self.ev('async () => !!(await filterStage)'))
        count = self.ev("fgView('timeline').probe().N")
        self.assertEqual(self.page.locator('.tl-fcount').inner_text(),
                         f'{expected} of {count} calls')
        self.assertEqual(self.page.locator('.tl-ftext').input_value(), 'func:pending')

    def test_staged_alt_click_reveals_requested_line_after_ready(self):
        self.stage_timeline()
        line = self.page.locator('.line-container.expandable').first
        number = int(line.get_attribute('data-line-number'))
        expected = self.ev("line => JSON.parse(document.getElementById('tlData').textContent).cols.line.indexOf(line)", number)
        self.assertGreaterEqual(expected, 0)
        line.locator('.line-content').click(modifiers=['Alt'])
        self.page.wait_for_selector('.tl-detail.show')
        self.assertEqual(self.ev('fgView("timeline").probe().sel()'), expected)
        self.assertEqual(self.ev('[stageFactories, stageClosed]'), [1, 1])

    def test_staged_failure_is_escaped_and_returns_to_text(self):
        unsafe = '<img src=x onerror=alert(1)>'
        self.ev('''message => {
            fgViews.timeline.staged = true;
            fgViews.timeline.steps = function* () { yield 'Starting…'; throw new Error(message); };
            window.failedStage = fgSwitchView('timeline');
        }''', unsafe)
        self.page.wait_for_selector('.fg-loading-status[role=alert]')
        self.assertTrue(self.ev('async () => await failedStage === null'))
        self.assertIn(unsafe, self.page.locator('.fg-loading-status').inner_text())
        self.assertEqual(self.page.locator('.fg-loading img').count(), 0)
        self.assertFalse(self.page.locator('#timelineView').get_attribute('aria-busy'))
        self.assertEqual(len(self.errors), 1)
        self.assertIn('[timeline] startup failed:', self.errors.pop())
        self.page.locator('.fg-loading button').focus()
        self.page.keyboard.press('Enter')
        self.assertEqual(self.ev('fgCurrentView()'), 'text')
        self.assertTrue(self.ev("document.activeElement.matches('.view-tab[data-view=text]')"))
        self.ev("fgViews.timeline.steps = () => __tlSteps()")
        self.ev("fgSwitchView('timeline')")
        self.assertEqual(self.page.locator('.tl-toolbar').count(), 1)

    def test_text_view_lazy_expand(self):
        line = self.page.query_selector('.line-container.expandable')
        lid = line.get_attribute('data-line-id')
        self.assertEqual(self.ev("id => document.getElementById(id + '_content').childElementCount", lid), 0)
        line.query_selector('.line-content').click()
        self.page.wait_for_timeout(200)
        shown, kids = self.ev("id => { const c = document.getElementById(id + '_content'); return [getComputedStyle(c).display, c.childElementCount]; }", lid)
        self.assertEqual(shown, 'block')
        self.assertGreater(kids, 0)

    def test_top_bar_and_filtered_count(self):
        bar = self.ev("(() => { const r = document.querySelector('.controls').getBoundingClientRect(); return [r.top, getComputedStyle(document.querySelector('.controls')).position]; })()")
        self.assertEqual(bar[1], 'sticky')
        self.assertLess(bar[0], 200)
        self.assertEqual(self.ev("getComputedStyle(document.getElementById('keyboardHint')).display"), 'none')
        self.page.fill('#filterInput', 'comm:sh')
        self.page.click('.filter-btn')
        self.page.wait_for_timeout(300)
        self.assertIn('Filtered:', self.ev("document.querySelector('.header-stats').innerText"))

    def test_header_help_opens_the_active_views_help(self):
        for view, expected in [('text', 'Keyboard Shortcuts'), ('timeline', 'Navigate'), ('compare', 'Compare Help')]:
            with self.subTest(view=view):
                self.ev('view => fgSwitchView(view)', view)
                self.page.locator('.header-actions button').filter(has_text='?').click()
                panel = self.page.locator('.tl-help' if view == 'timeline' else '#keyboardHint')
                self.assertTrue(panel.is_visible())
                self.assertIn(expected, panel.inner_text())
                self.assertEqual(self.ev('fgCurrentView()'), view)
                self.page.keyboard.press('Escape')
                self.assertFalse(panel.is_visible())

    def test_header_help_native_buttons_and_compact_themes(self):
        for view, theme, width in product(['text', 'timeline', 'compare'], ['light', 'dark'], [390, 1500]):
            with self.subTest(view=view, theme=theme, width=width):
                self.page.set_viewport_size(dict(width=width, height=700))
                self.ev('theme => document.documentElement.dataset.theme = theme', theme)
                self.ev('view => fgSwitchView(view)', view)
                button = self.page.locator('#viewHelpBtn')
                button.focus()
                button.press('Enter')
                panel = self.page.locator('.tl-help' if view == 'timeline' else '#keyboardHint')
                self.page.wait_for_function('selector => getComputedStyle(document.querySelector(selector)).display !== "none"',
                                            arg='.tl-help' if view == 'timeline' else '#keyboardHint')
                self.assertTrue(panel.is_visible())
                self.assertTrue(panel.evaluate('element => { const box = element.getBoundingClientRect(); return box.left >= 0 && box.right <= innerWidth && box.top >= 0 && box.bottom <= innerHeight; }'))
                close = panel.get_by_role('button', name='Close help', exact=True)
                close.focus()
                close.press('Space')
                self.assertFalse(panel.is_visible())
                self.assertEqual(self.ev('fgCurrentView()'), view)

    def test_timeline_help_has_one_visible_entry_in_normal_and_maximized_layouts(self):
        self.ev("fgSwitchView('timeline')")
        header = self.page.locator('#viewHelpBtn')
        fallback = self.page.locator('.tl-toolbar [data-act=help]')
        for width in [390, 1500]:
            with self.subTest(width=width):
                self.page.set_viewport_size(dict(width=width, height=700))
                self.assertTrue(header.is_visible())
                self.assertFalse(fallback.is_visible())
                self.page.locator('[data-act=max]').click()
                self.assertFalse(header.is_visible())
                self.assertTrue(fallback.is_visible())
                fallback.focus()
                fallback.press('Enter')
                panel = self.page.locator('.tl-help')
                self.assertTrue(panel.is_visible())
                self.assertIn('Search and filter', panel.inner_text())
                self.page.keyboard.press('Escape')
                self.assertFalse(panel.is_visible())
                self.ev("fgSwitchView('text')")
                self.assertTrue(header.is_visible())
                self.ev("fgSwitchView('timeline')")
                self.page.wait_for_function('() => !document.fullscreenElement')
                self.assertTrue(header.is_visible())
                self.assertFalse(fallback.is_visible())

    def test_header_help_waits_for_preparation_and_closes_shared_popup_on_switch(self):
        self.stage_timeline(slow=True)
        facts = self.ev('''() => {
            window.helpStage = fgSwitchView('timeline');
            document.getElementById('viewHelpBtn').click();
            return {canvas:!!document.querySelector('.tl-canvas'), busy:document.getElementById('timelineView').getAttribute('aria-busy')};
        }''')
        self.assertEqual(facts, dict(canvas=False, busy='true'))
        self.page.wait_for_selector('.tl-help.show')
        self.assertTrue(self.ev('async () => !!(await helpStage)'))
        self.assertEqual(self.ev('stageFactories'), 1)
        self.ev("fgSwitchView('text')")
        self.ev("fgSwitchView('compare')")
        self.page.locator('#viewHelpBtn').click()
        self.assertTrue(self.page.locator('#keyboardHint').is_visible())
        self.ev("fgSwitchView('text')")
        self.assertFalse(self.page.locator('#keyboardHint').is_visible())

    def test_header_help_cancelled_preparation_does_not_open_stale_help(self):
        self.stage_timeline(slow=True)
        self.ev('''() => {
            window.cancelledHelp = fgSwitchView('timeline');
            document.getElementById('viewHelpBtn').click();
            fgSwitchView('compare');
        }''')
        self.assertTrue(self.ev('async () => await cancelledHelp === null'))
        self.page.wait_for_timeout(100)
        self.assertEqual(self.ev('fgCurrentView()'), 'compare')
        self.assertEqual(self.page.locator('.tl-help.show').count(), 0)
        self.assertFalse(self.page.locator('#keyboardHint').is_visible())

    def test_page_header_stays_in_viewport_with_wide_trace(self):
        for width, height in [(1500, 900), (800, 600), (390, 844), (320, 700)]:
            self.page.set_viewport_size({'width': width, 'height': height})
            for view in ['text', 'timeline', 'compare']:
                with self.subTest(width=width, view=view):
                    self.ev('view => fgSwitchView(view)', view)
                    self.ev('window.scrollTo(0, 0)')
                    self.page.wait_for_timeout(100)
                    bounds = self.ev("""() => {
                        const header = document.querySelector('.page-header').getBoundingClientRect();
                        const actions = document.querySelector('.header-actions').getBoundingClientRect();
                        return [header.left, header.right, header.height, actions.right,
                                document.documentElement.clientWidth];
                    }""")
                    self.assertGreaterEqual(bounds[0], 0)
                    self.assertLessEqual(bounds[1], bounds[4] + 1)
                    self.assertLessEqual(bounds[3], bounds[4] + 1)
                    self.assertLessEqual(bounds[2], 80)
                    self.assertLessEqual(self.ev('document.documentElement.scrollWidth'), width)
                    if view == 'text':
                        self.assertGreater(self.ev('document.getElementById("textViewport").scrollWidth'), width)

    def test_timeline_compact_layout_keeps_canvas_and_detail_reachable(self):
        self.open_timeline()
        self.ev("fgView('timeline').probe().select(0)")
        for width, height in [(1500, 900), (800, 600), (390, 844), (320, 700), (800, 420)]:
            with self.subTest(width=width, height=height):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.ev('window.scrollTo(0, 0)')
                self.page.wait_for_timeout(100)
                bounds = self.ev("""() => {
                    const stage = document.querySelector('.tl-stage').getBoundingClientRect();
                    const detail = document.querySelector('.tl-detail');
                    const toolbar = document.querySelector('.tl-toolbar');
                    const notes = document.querySelector('.tl-notes');
                    return {stageHeight: stage.height, detailBottom: detail.getBoundingClientRect().bottom,
                            detailOverflow: detail.scrollWidth - detail.clientWidth,
                            toolbarOverflow: toolbar.scrollWidth - toolbar.clientWidth,
                            notesOverflow: notes.scrollWidth - notes.clientWidth,
                            pageWidth: document.documentElement.scrollWidth,
                            pageHeight: document.documentElement.scrollHeight};
                }""")
                self.assertGreaterEqual(bounds['stageHeight'], 160)
                self.assertLessEqual(bounds['detailBottom'], height + 1)
                self.assertLessEqual(bounds['detailOverflow'], 1)
                self.assertLessEqual(bounds['toolbarOverflow'], 1)
                self.assertLessEqual(bounds['notesOverflow'], 1)
                self.assertLessEqual(bounds['pageWidth'], width)
                self.assertLessEqual(bounds['pageHeight'], height)

    def test_timeline_touch_navigation(self):
        for width, height in [(390, 844), (320, 700), (844, 390)]:
            with self.subTest(width=width, height=height), self.browser.new_context(
                    viewport=dict(width=width, height=height), is_mobile=True, has_touch=True,
                    device_scale_factor=3) as context:
                page = context.new_page()
                page.on('pageerror', lambda error: self.errors.append(str(error)))
                page.goto(self.url, wait_until='load')
                page.locator('.view-tab[data-view=timeline]').tap()
                canvas = page.locator('.tl-canvas')
                canvas.wait_for()
                fit = page.locator('[data-act=fit]')
                zoom_in = page.get_by_role('button', name='Zoom timeline in')
                zoom_out = page.get_by_role('button', name='Zoom timeline out')
                cdp = context.new_cdp_session(page)

                def state():
                    return page.evaluate('''() => {
                        const model = fgView('timeline').probe();
                        const scroll = document.querySelector('.tl-scroll');
                        return {window: model.view(), selected: model.sel(), scale: visualViewport.scale,
                            scroll: scroll.scrollTop, maxScroll: scroll.scrollHeight - scroll.clientHeight};
                    }''')

                def gesture(start, end, *, cancel=False, release_one=False):
                    def send(kind, points):
                        cdp.send('Input.dispatchTouchEvent', dict(type=kind, touchPoints=[
                            dict(id=index, x=round(x), y=round(y)) for index, (x, y) in enumerate(points)]))
                    send('touchStart', start)
                    for step in range(1, 9):
                        send('touchMove', [(x + (target_x - x) * step / 8, y + (target_y - y) * step / 8)
                                           for (x, y), (target_x, target_y) in zip(start, end)])
                    if release_one:
                        send('touchEnd', end[:1])
                        send('touchMove', [(end[0][0] + 16, end[0][1])])
                    send('touchCancel' if cancel else 'touchEnd', [])

                full = state()['window']
                zoom_in.tap()
                zoom_in.tap()
                left, right = state()['window']
                span = right - left
                self.assertLess(span, full[1] - full[0])
                zoom_out.tap()
                expanded_left, expanded_right = state()['window']
                self.assertGreater(expanded_right - expanded_left, span)
                zoom_in.tap()
                bounds = canvas.bounding_box()
                center_x = bounds['x'] + bounds['width'] / 2
                center_y = bounds['y'] + bounds['height'] / 2
                gesture([(center_x + 30, center_y)], [(center_x - 60, center_y)])
                pan_left, pan_right = state()['window']
                self.assertGreater(pan_left, left)
                self.assertAlmostEqual(pan_right - pan_left, span, delta=1)
                self.assertEqual(state()['selected'], -1)

                gesture([(center_x - 30, center_y), (center_x + 30, center_y)],
                        [(center_x - 60, center_y), (center_x + 76, center_y)], release_one=True)
                pinched = state()
                pinch_left, pinch_right = pinched['window']
                self.assertAlmostEqual(pinch_right - pinch_left, span * 60 / 136, delta=span * 0.02)
                anchor = (pan_left + pan_right) / 2
                self.assertAlmostEqual(pinch_left + (pinch_right - pinch_left) * (0.5 + 8 / bounds['width']),
                                       anchor, delta=span * 0.02)
                self.assertEqual(pinched['scale'], 1)
                self.assertEqual(pinched['selected'], -1)

                self.assertGreater(pinched['maxScroll'], 0)
                page.evaluate('''() => {
                    document.querySelector('.tl-scroll').addEventListener('scrollend', () => {
                        window.trackScrollFinished = true;
                    }, {once: true});
                }''')
                vertical_distance = min(44, bounds['height'] / 3)
                gesture([(center_x, center_y + vertical_distance)], [(center_x, center_y - vertical_distance)])
                page.wait_for_function("window.trackScrollFinished && document.querySelector('.tl-scroll').scrollTop > 0")
                self.assertEqual(state()['window'], pinched['window'])
                self.assertEqual(state()['selected'], -1)

                fit.tap()
                self.assertEqual(state()['window'], full)
                index = page.evaluate('''() => {
                    const model = fgView('timeline').probe();
                    return model.sStart.findIndex((start, index) =>
                        !model.isGhost(index) && model.sEnd[index] - start > 300000);
                }''')
                self.assertGreaterEqual(index, 0)
                page.evaluate('''index => {
                    const model = fgView('timeline').probe();
                    model.select(index);
                    model.select(-1);
                }''', index)
                point = page.evaluate('''() => {
                    const model = fgView('timeline').probe();
                    const canvas = document.querySelector('.tl-canvas');
                    for (let index = 0; index < model.N; index++) {
                        if (model.isGhost(index) || model.TRACKS[model.sTask[index]].collapsed) continue;
                        const [x, y] = model.xyOf(index);
                        if (document.elementFromPoint(x, y) === canvas) return [x, y];
                    }
                    return null;
                }''')
                self.assertIsNotNone(point)
                gesture([point], [point], cancel=True)
                self.assertEqual(state()['selected'], -1)
                page.touchscreen.tap(*point)
                self.assertGreaterEqual(state()['selected'], 0)
                page.locator('.tl-detail .tl-close').tap()
                self.assertEqual(state()['selected'], -1)

    def test_text_phone_toolbar_keeps_filters_and_actions_off_the_reading_area(self):
        for width, height in [(320, 700), (390, 844), (844, 390)]:
            with self.subTest(width=width, height=height), self.browser.new_context(
                    viewport=dict(width=width, height=height), is_mobile=True, has_touch=True) as context:
                page = context.new_page()
                page.on('pageerror', lambda error: self.errors.append(str(error)))
                page.goto(self.url, wait_until='load')
                toolbar = page.locator('.controls')
                filter_toggle = page.locator('#textFilterToggle')
                more_toggle = page.locator('#textMoreToggle')
                filter_box = page.locator('#filterBox')
                options = page.locator('#textToolbarOptions')
                first_row = page.locator('#content .line-container').first
                original_height = toolbar.bounding_box()['height']
                original_row = first_row.bounding_box()['y']
                self.assertLessEqual(original_height, 60)
                self.assertFalse(filter_box.is_visible())
                self.assertFalse(options.is_visible())

                filter_toggle.tap()
                self.assertTrue(filter_box.is_visible())
                self.assertEqual(filter_toggle.get_attribute('aria-expanded'), 'true')
                self.assertEqual(toolbar.bounding_box()['height'], original_height)
                self.assertEqual(first_row.bounding_box()['y'], original_row)
                page.locator('.field-btn[data-field=cpu]').tap()
                dropdown = page.locator('.field-dropdown')
                bounds = dropdown.bounding_box()
                self.assertLessEqual(bounds['x'] + bounds['width'], width)
                self.assertLessEqual(bounds['y'] + bounds['height'], height)
                page.keyboard.press('Escape')
                self.assertFalse(dropdown.is_visible())
                self.assertTrue(filter_box.is_visible())
                query = page.get_by_role('textbox', name='Filter trace lines')
                query.fill('func:do_sys_openat2')
                query.press('Enter')
                page.wait_for_function("window.fgFilterState.lines?.size > 0")
                query.press('Escape')
                self.assertFalse(filter_box.is_visible())
                self.assertTrue(filter_toggle.evaluate('node => node === document.activeElement'))
                self.assertTrue(page.locator('#textFilterStatus').is_visible())

                more_toggle.tap()
                self.assertTrue(options.is_visible())
                self.assertEqual(toolbar.bounding_box()['height'], original_height)
                guides = page.locator('#foldGuidesBtn')
                previous = guides.get_attribute('aria-pressed')
                guides.tap()
                self.assertNotEqual(guides.get_attribute('aria-pressed'), previous)
                filter_toggle.tap()
                self.assertFalse(options.is_visible())
                self.assertTrue(filter_box.is_visible())
                page.locator('.view-tab[data-view=timeline]').tap()
                page.locator('.tl-canvas').wait_for()
                self.assertEqual(page.locator('.tl-ftext').input_value(), 'func:do_sys_openat2')
                page.locator('.view-tab[data-view=text]').tap()
                self.assertFalse(filter_box.is_visible())
                self.assertTrue(page.locator('#textFilterStatus').is_visible())
                page.evaluate("lsSet('filterBoxVisible', 'false')")
                page.set_viewport_size(dict(width=1100, height=800))
                more_toggle.wait_for(state='hidden')
                self.assertFalse(more_toggle.is_visible())
                self.assertFalse(filter_box.is_visible())
                filter_toggle.click()
                self.assertTrue(filter_box.is_visible())

    def test_timeline_phone_options_leave_canvas_and_selection_in_view(self):
        for width, height, theme in [(320, 700, 'light'), (390, 844, 'dark'), (844, 390, 'light')]:
            with self.subTest(width=width, height=height), self.browser.new_context(
                    viewport=dict(width=width, height=height), is_mobile=True, has_touch=True) as context:
                page = context.new_page()
                page.on('pageerror', lambda error: self.errors.append(str(error)))
                page.goto(self.url, wait_until='load')
                page.evaluate("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                page.locator('.view-tab[data-view=timeline]').tap()
                canvas = page.locator('.tl-canvas')
                canvas.wait_for()
                more = page.locator('[data-act=options]')
                notes = page.locator('[data-act=notes]')
                options = page.locator('#tlOptions')
                toolbar = page.locator('.tl-toolbar')
                self.assertLessEqual(toolbar.bounding_box()['height'], 120)
                self.assertFalse(options.is_visible())
                self.assertFalse(page.locator('.tl-notes').is_visible())
                self.assertEqual(notes.get_attribute('aria-pressed'), 'false')

                before = canvas.bounding_box()
                more.tap()
                self.assertTrue(options.is_visible())
                self.assertEqual(more.get_attribute('aria-expanded'), 'true')
                self.assertEqual(canvas.bounding_box(), before)
                self.assertLessEqual(options.bounding_box()['y'] + options.bounding_box()['height'], height)
                trace_filter = page.get_by_role('textbox', name='Filter trace lines')
                trace_filter.fill('pid:1')
                trace_filter.press('Enter')
                page.wait_for_function("!!window.fgFilterState?.lines")
                self.assertTrue(page.get_by_text('· Filter on', exact=True).is_visible())
                trace_filter.press('Escape')
                self.assertFalse(options.is_visible())
                self.assertEqual(more.get_attribute('aria-expanded'), 'false')
                self.assertTrue(more.evaluate('node => document.activeElement === node'))
                self.assertLessEqual(toolbar.evaluate('node => node.scrollWidth'), width)
                more.tap()
                page.locator('[data-act=fclear]').tap()
                page.wait_for_function("!window.fgFilterState?.lines")
                page.locator('.tl-search').tap()
                self.assertFalse(options.is_visible())

                point = page.evaluate('''() => {
                    const model = fgView('timeline').probe();
                    const canvas = document.querySelector('.tl-canvas');
                    const bounds = canvas.getBoundingClientRect();
                    let lowest = null;
                    for (let index = 0; index < model.N; index++) {
                        if (model.isGhost(index) || model.TRACKS[model.sTask[index]].collapsed) continue;
                        const [sliceX, sliceY] = model.xyOf(index);
                        if (sliceY < bounds.top + 22 || (lowest && sliceY <= lowest[1])) continue;
                        if (document.elementFromPoint(sliceX, sliceY) === canvas) lowest = [sliceX, sliceY];
                    }
                    return lowest;
                }''')
                self.assertIsNotNone(point)
                page.touchscreen.tap(*point)
                page.locator('.tl-detail.show').wait_for()
                selection = page.evaluate('''() => {
                    const model = fgView('timeline').probe();
                    const bounds = document.querySelector('.tl-canvas').getBoundingClientRect();
                    return [model.xyOf(model.sel())[1], bounds.top, bounds.bottom];
                }''')
                self.assertGreaterEqual(selection[0], selection[1])
                self.assertLess(selection[0], selection[2])
                detail_head = page.locator('.tl-slice-head').bounding_box()
                close = page.locator('.tl-slice-head .tl-close').bounding_box()
                self.assertAlmostEqual(detail_head['y'], close['y'], delta=1)
                self.assertAlmostEqual(detail_head['x'] + detail_head['width'], close['x'] + close['width'], delta=5)
                more.tap()
                more.press('Escape')
                self.assertTrue(page.locator('.tl-detail.show').is_visible())
                canvas_with_detail = canvas.bounding_box()
                notes.tap()
                self.assertTrue(page.locator('.tl-notes').is_visible())
                self.assertEqual(notes.get_attribute('aria-pressed'), 'true')
                self.assertEqual(canvas.bounding_box(), canvas_with_detail)
                self.assertLessEqual(page.locator('.tl-detail').evaluate('node => node.getBoundingClientRect().bottom'), height + 1)
                page.reload(wait_until='load')
                page.locator('.tl-notes').wait_for()
                self.assertEqual(notes.get_attribute('aria-pressed'), 'true')
                page.set_viewport_size(dict(width=1100, height=800))
                self.assertFalse(options.is_visible())
                self.assertTrue(more.is_visible())
                more.click()
                options.wait_for()
                self.assertEqual(page.evaluate("localStorage.getItem('fgTl.notes')"), None)

    def test_timeline_compact_controls_resize_and_maximize_in_both_themes(self):
        self.open_timeline()
        self.ev("fgView('timeline').probe().select(0)")
        for theme in ['light', 'dark']:
            for width, height in [(320, 700), (800, 420)]:
                with self.subTest(theme=theme, width=width, height=height):
                    self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.page.click('[data-act=notes]')
                    self.assertFalse(self.page.locator('.tl-notes').is_visible())
                    self.page.click('[data-act=max]')
                    self.assertFalse(self.page.locator('.view-tabs').is_visible())
                    handle = self.page.locator('.tl-resizer').bounding_box()
                    center = handle['x'] + handle['width'] / 2
                    self.page.mouse.move(center, handle['y'] + 4)
                    self.page.mouse.down()
                    self.page.mouse.move(center, 0, steps=5)
                    self.page.mouse.up()
                    self.page.wait_for_timeout(100)
                    self.assertGreaterEqual(self.page.locator('.tl-stage').bounding_box()['height'], 160)
                    detail = self.page.locator('.tl-detail').bounding_box()
                    self.assertLessEqual(detail['y'] + detail['height'], height + 1)
                    self.page.dblclick('.tl-resizer')
                    self.page.click('[data-act=max]')
                    self.page.wait_for_function('() => !document.fullscreenElement')
                    self.page.click('[data-act=notes]')
                    self.assertTrue(self.page.locator('.view-tabs').is_visible())
                    self.assertTrue(self.page.locator('.tl-notes').is_visible())

    def test_fold_hides_children(self):
        self.page.query_selector('.fold-entry .fold-icon').click()
        self.page.wait_for_timeout(200)
        hidden = self.ev("""() => { const e = document.querySelector('.fold-entry.folded'); const id = e.getAttribute('data-fold-id');
            const n = document.querySelectorAll('[data-fold-ancestors~="' + id + '"]');
            return Array.from(n).every(x => x.style.display === 'none') && n.length > 0; }""")
        self.assertTrue(hidden)

    def nested_fold_rows(self):
        return self.ev("""() => {
            const grandchild = [...document.querySelectorAll('.fold-entry.expandable')].find(entry =>
                entry.dataset.foldAncestors?.split(' ').length === 2);
            const ancestors = grandchild.dataset.foldAncestors.split(' ');
            const entries = ancestors.map(foldId => document.querySelector(
                '[data-fold-id="' + foldId + '"][data-fold-type="entry"]'));
            entries.push(grandchild);
            const outside = [...document.querySelectorAll('.line-container.expandable')].find(entry =>
                entry.dataset.foldId !== ancestors[0] &&
                !(entry.dataset.foldAncestors || '').split(' ').includes(ancestors[0]));
            return [...entries, outside].map(entry => ({
                row: entry.id, line: entry.dataset.lineId, fold: entry.dataset.foldId
            }));
        }""")

    def expand_fold_source(self, entry):
        self.page.locator('#' + entry['row'] + ' .line-content').click()
        self.page.locator('#' + entry['line'] + '_content').wait_for(state='visible')

    def test_fold_controls_follow_actual_function_indentation(self):
        rows = self.nested_fold_rows()[:3]
        facts = self.ev("""entries => entries.map(entry => {
            const row = document.getElementById(entry.row);
            const icon = row.querySelector('.fold-icon');
            const name = row.querySelector('.func-name-link, .hl-func');
            return {icon:icon.getBoundingClientRect().x, right:icon.getBoundingClientRect().right,
                name:name.getBoundingClientRect().x, indent:row.querySelector('.call-indent').getBoundingClientRect().width,
                height:row.getBoundingClientRect().height, text:icon.textContent,
                nested:icon.closest('.line-content') !== null};
        })""", rows)
        for fact in facts:
            self.assertTrue(fact['nested'])
            self.assertEqual(fact['text'], '')
            self.assertAlmostEqual(fact['right'], fact['name'], delta=1)
            self.assertLessEqual(fact['height'], 26)
        for before, after in zip(facts, facts[1:]):
            self.assertGreater(after['icon'], before['icon'])
            self.assertAlmostEqual(after['icon'] - before['icon'], after['indent'] - before['indent'], delta=1)
        self.assertEqual(self.page.locator('.line-container > .fold-marker').count(), 0)

    def test_phone_fold_gutter_reaches_nested_calls_without_panning_or_overlap(self):
        entries = self.nested_fold_rows()[:3]
        for width, height, theme in [(320, 700, 'dark'), (390, 844, 'light'), (844, 390, 'dark')]:
            with self.subTest(width=width, height=height), self.browser.new_context(
                    viewport=dict(width=width, height=height), is_mobile=True, has_touch=True) as context:
                page = context.new_page()
                page.on('pageerror', lambda error: self.errors.append(str(error)))
                page.goto(self.url, wait_until='load')
                page.evaluate("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                facts = page.evaluate('''entries => entries.map(entry => {
                    const row = document.getElementById(entry.row);
                    const icon = row.querySelector('.fold-icon');
                    const bounds = row.getBoundingClientRect();
                    const button = icon.getBoundingClientRect();
                    const number = row.querySelector('.line-number').getBoundingClientRect();
                    return {left: button.left, right: button.right, width: button.width,
                        height: button.height, rowHeight: bounds.height, number: number.left,
                        controls: row.querySelectorAll('.fold-icon').length};
                })''', entries)
                for fact in facts:
                    self.assertEqual(fact['controls'], 1)
                    self.assertGreaterEqual(fact['width'], 44)
                    self.assertLessEqual(fact['right'], fact['number'])
                    self.assertAlmostEqual(fact['height'], fact['rowHeight'], delta=1)
                    self.assertAlmostEqual(fact['left'], facts[0]['left'], delta=1)

                outer = page.locator('#' + entries[0]['row'] + ' .fold-icon')
                inner = page.locator('#' + entries[1]['row'] + ' .fold-icon')
                outer.evaluate('''node => window.scrollTo({left: 0,
                    top: window.scrollY + node.getBoundingClientRect().top - visualViewport.height * 0.55,
                    behavior: 'instant'})''')
                inner.tap(position=dict(x=3, y=10))
                self.assertEqual(inner.get_attribute('aria-expanded'), 'false')
                self.assertEqual(outer.get_attribute('aria-expanded'), 'true')
                self.assertFalse(page.locator('#' + entries[2]['row']).is_visible())
                outer.tap(position=dict(x=41, y=10))
                self.assertFalse(inner.is_visible())
                outer.tap(position=dict(x=3, y=10))
                self.assertTrue(inner.is_visible())
                self.assertEqual(inner.get_attribute('aria-expanded'), 'false')
                inner.tap(position=dict(x=41, y=10))
                self.assertTrue(page.locator('#' + entries[2]['row']).is_visible())
                self.assertEqual(page.evaluate('window.scrollX'), 0)

                row = page.locator('#' + entries[0]['row'])
                number = row.locator('.line-number')
                number.tap()
                self.assertEqual(outer.get_attribute('aria-expanded'), 'true')
                self.assertEqual(page.evaluate('location.hash'), '#' + entries[0]['row'])
                page.set_viewport_size(dict(width=1100, height=800))
                geometry = row.evaluate('''node => {
                    const icon = node.querySelector('.fold-icon').getBoundingClientRect();
                    const name = node.querySelector('.func-name-link, .hl-func').getBoundingClientRect();
                    return {width: icon.width, right: icon.right, name: name.left};
                }''')
                self.assertLess(geometry['width'], 44)
                self.assertAlmostEqual(geometry['right'], geometry['name'], delta=1)

    def test_fold_braces_are_independent_native_controls_with_truthful_summary(self):
        parent, child, grandchild, outside = self.nested_fold_rows()
        self.expand_fold_source(grandchild)
        row = self.page.locator('#' + parent['row'])
        row.locator('.fold-brace').click()
        for selector in ['.fold-icon', '.fold-brace']:
            self.assertEqual(row.locator(selector).get_attribute('aria-expanded'), 'false')
            self.assertIn('Expand call', row.locator(selector).get_attribute('aria-label'))
        self.assertFalse(self.page.locator('#' + grandchild['line'] + '_content').is_visible())
        self.assertFalse(self.page.locator('#' + parent['line'] + '_content').is_visible())
        expected = self.ev("""id => [...document.querySelectorAll('[data-fold-ancestors~="' + id + '"]')]
            .filter(row => row.hasAttribute('data-func') && row.dataset.foldType !== 'exit').length""", parent['fold'])
        self.assertEqual(row.locator('.summary-count').inner_text(), str(expected) + ' calls')
        self.assertEqual(row.locator('.fold-brace + .fold-summary').count(), 1)
        row.locator('.fold-icon').press('Enter')
        self.assertTrue(self.page.locator('#' + grandchild['line'] + '_content').is_visible())
        self.assertEqual(row.locator('.fold-brace').get_attribute('aria-expanded'), 'true')
        row.locator('.fold-icon').press('Space')
        self.assertEqual(row.locator('.fold-brace').get_attribute('aria-expanded'), 'false')
        self.assertFalse(self.page.locator('#' + parent['line'] + '_content').is_visible())
        row.locator('.fold-icon').press('0')
        self.assertTrue(self.page.locator('#' + grandchild['row']).is_visible())

    def test_fold_scope_highlights_only_the_matching_braces(self):
        parent, child, grandchild, outside = self.nested_fold_rows()
        for entry in [parent, child]:
            row = self.page.locator('#' + entry['row'])
            row.locator('.fold-icon').hover()
            self.assertEqual(self.ev("[...document.querySelectorAll('.fold-scope')].map(row => row.id)"),
                             [entry['row'], row.get_attribute('data-fold-end')])
            row.locator('.fold-brace').hover()
            self.assertIn('Shift-click', row.locator('.fold-brace').get_attribute('title'))
        self.page.mouse.move(0, 0)
        self.assertEqual(self.page.locator('.fold-scope').count(), 0)
        self.page.locator('#' + parent['row'] + ' .fold-icon').focus()
        self.assertEqual(self.page.locator('.fold-scope').count(), 2)
        self.ev('document.activeElement.blur()')
        self.assertEqual(self.page.locator('.fold-scope').count(), 0)

    def test_fold_guides_toggle_preserves_text_geometry_and_controls(self):
        parent, child, grandchild, outside = self.nested_fold_rows()
        row = self.page.locator('#' + parent['row'])
        guide_button = self.page.locator('#foldGuidesBtn')
        snapshot = """id => {
            const row = document.getElementById(id);
            const icon = row.querySelector('.fold-icon').getBoundingClientRect();
            return {text:row.querySelector('.line-content').textContent,
                width:row.getBoundingClientRect().width, height:row.getBoundingClientRect().height,
                icon:icon.x, name:row.querySelector('.func-name-link, .hl-func').getBoundingClientRect().x};
        }"""
        before = self.ev(snapshot, parent['row'])
        for theme in ['light', 'dark']:
            with self.subTest(theme=theme):
                self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                guide_button.press('Enter')
                self.assertEqual(guide_button.inner_text(), 'Tree guides: off')
                self.assertEqual(guide_button.get_attribute('aria-pressed'), 'false')
                self.assertTrue(self.ev("document.getElementById('fgTreeGuides').sheet.disabled"))
                self.assertEqual(self.ev("id => getComputedStyle(document.getElementById(id).querySelector('.call-indent'), '::before').content", parent['row']), 'none')
                self.assertEqual(self.ev(snapshot, parent['row']), before)
                row.locator('.fold-icon').press(' ')
                self.assertFalse(self.page.locator('#' + child['row']).is_visible())
                row.locator('.fold-icon').press('Enter')
                self.assertTrue(self.page.locator('#' + child['row']).is_visible())
                guide_button.press(' ')
                self.assertEqual(guide_button.inner_text(), 'Tree guides: on')
                self.assertEqual(guide_button.get_attribute('aria-pressed'), 'true')
                self.assertFalse(self.ev("document.getElementById('fgTreeGuides').sheet.disabled"))
                self.assertEqual(self.ev(snapshot, parent['row']), before)

    def test_text_paint_containment_preserves_overflow_geometry_and_focus_pixels(self):
        if not self.ev("CSS.supports('overflow-clip-margin', '6px')"):
            self.skipTest('paint clip margins are not supported')
        trace = '# tracer: function_graph\n' + '# padding\n' * 510
        trace += (' 0)               |  parent() { /* <-caller+0x1/0x20 */\n'
                  ' 0)               |    child() { /* <-caller+0x1/0x20 */\n'
                  ' 0)   0.001 us    |      leaf(); /* <-caller+0x1/0x20 */\n'
                  ' 0)   0.002 us    |    } /* child */\n'
                  ' 0)   0.003 us    |  } /* parent */\n')
        trace_path = os.path.join(self.k.dir, 'chunk-boundary.txt')
        with open(trace_path, 'w') as stream:
            stream.write(trace)
        self.k.run(trace_path, '--enable-fold', '--filter', '--func-links', out='chunk-boundary.html')
        self.page.goto('file://' + self.k.dir + '/chunk-boundary.html', wait_until='load')
        parent = {'row': 'L512'}
        self.assertEqual(self.page.locator('#L512').evaluate('row => row.parentElement.lastElementChild.id'), 'line_511_content')
        self.assertEqual(self.page.locator('#L513').evaluate('row => row.parentElement.firstElementChild.id'), 'L513')
        self.ev("id => document.getElementById(id).querySelector('.line-content').append(' long_argument=' + '0123456789'.repeat(100))",
                parent['row'])
        for width in [390, 1500]:
            for theme, row_id in product(['light', 'dark'], ['L512', 'L513']):
                with self.subTest(width=width, theme=theme, row=row_id):
                    self.page.set_viewport_size({'width': width, 'height': 900})
                    self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                    baseline = self.page.add_style_tag(content='.text-chunk {contain:none; overflow-clip-margin:0px; will-change:auto}')
                    icon = self.page.locator('#' + row_id + ' .fold-icon')
                    self.assertEqual(icon.evaluate("control => getComputedStyle(control.closest('.text-chunk')).contain"), 'none')
                    icon.evaluate("control => control.scrollIntoView({block:'center', inline:'center'})")
                    icon.focus()
                    self.ev('async () => {await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame)}')
                    bounds = icon.bounding_box()
                    clip = dict(x=max(0, bounds['x'] - 5), y=max(0, bounds['y'] - 5),
                                width=bounds['width'] + 10, height=bounds['height'] + 10)
                    geometry = """() => ({pageWidth:document.documentElement.scrollWidth, pageHeight:document.documentElement.scrollHeight,
                        textWidth:document.getElementById('textViewport').scrollWidth,
                        rows:Array.from(document.querySelectorAll('#content .line-container'), row => {
                            const rect = row.getBoundingClientRect();
                            return [row.id, rect.x, rect.y, rect.width, rect.height, row.scrollWidth];
                        })})"""
                    before_geometry = self.ev(geometry)
                    before_pixels = self.page.screenshot(clip=clip)
                    hit_targets = """bounds => [bounds.y + 1, bounds.y + bounds.height - 1].map(top => {
                        const hit = document.elementFromPoint(bounds.x + bounds.width / 2, top);
                        return hit?.closest('.line-container')?.id;
                    })"""
                    before_hits = self.ev(hit_targets, bounds)
                    baseline.evaluate('style => style.remove()')
                    self.ev('async () => {await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame)}')
                    self.assertEqual(self.ev(geometry), before_geometry)
                    after_pixels = self.page.screenshot(clip=clip)
                    images = [before_pixels, after_pixels]
                    if row_id == 'L513':
                        wider_focus = self.page.add_style_tag(content='.fold-icon:focus-visible {outline-offset:4px}')
                        images.append(self.page.screenshot(clip=clip))
                        clipped = self.page.add_style_tag(content='.text-chunk {overflow-clip-margin:0px}')
                        images.append(self.page.screenshot(clip=clip))
                        clipped.evaluate('style => style.remove()')
                        wider_focus.evaluate('style => style.remove()')
                    outline_color = icon.evaluate("control => getComputedStyle(control).outlineColor.match(/[0-9.]+/g).slice(0, 3).map(Number)")
                    outlines = self.ev("""async ({images, color}) => {
                        const decode = async encoded => {
                            const image = new Image();
                            image.src = 'data:image/png;base64,' + encoded; await image.decode();
                            const canvas = document.createElement('canvas');
                            canvas.width = image.width; canvas.height = image.height;
                            const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
                            const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
                            const result = {count:0, left:canvas.width, top:canvas.height, right:0, bottom:0};
                            for (let index = 0; index < pixels.length; index += 4) {
                                const column = index / 4 % canvas.width, row = Math.floor(index / 4 / canvas.width);
                                if (Math.abs(column - canvas.width / 2) <= 3) continue;
                                if (!color.every((channel, offset) => Math.abs(pixels[index + offset] - channel) <= 24)) continue;
                                result.count++; result.left = Math.min(result.left, column); result.right = Math.max(result.right, column);
                                result.top = Math.min(result.top, row); result.bottom = Math.max(result.bottom, row);
                            }
                            return result;
                        };
                        return Promise.all(images.map(decode));
                    }""", dict(images=[base64.b64encode(image).decode() for image in images], color=outline_color))
                    self.assertGreater(outlines[0]['count'], 20)
                    self.assertGreaterEqual(outlines[1]['count'], outlines[0]['count'] * 0.95, outlines)
                    for edge in ['left', 'top']:
                        self.assertLessEqual(outlines[1][edge], outlines[0][edge] + 1, outlines)
                    for edge in ['right', 'bottom']:
                        self.assertGreaterEqual(outlines[1][edge], outlines[0][edge] - 1, outlines)
                    if row_id == 'L513':
                        self.assertGreater(outlines[3]['top'], outlines[2]['top'] + 1, outlines)
                    self.assertEqual(self.ev(hit_targets, bounds), before_hits)
                    self.assertEqual(icon.evaluate("control => getComputedStyle(control.closest('.text-chunk')).contain"), 'paint')
                    self.assertEqual(icon.evaluate("control => getComputedStyle(control.closest('.text-chunk')).willChange"), 'transform')
                    self.assertEqual(icon.evaluate("control => getComputedStyle(control.closest('.line-container')).contain"), 'none')
                    self.assertLessEqual(before_geometry['pageWidth'], width)
                    self.assertGreater(before_geometry['textWidth'], width)
                    self.ev('document.activeElement.blur()')
        self.ev("window.chunkGuideChanges = 0; document.getElementById('content').addEventListener('fg-guides-change', () => chunkGuideChanges++)")
        self.ev("toggleFold(document.getElementById('L512').dataset.foldId, 'entry')")
        self.assertEqual(self.ev('chunkGuideChanges'), 1)
        for row in ['L513', 'L514', 'L515', 'L516']:
            self.assertFalse(self.page.locator('#' + row).is_visible())
        self.page.locator('#L512 .fold-icon').click()
        for row in ['L513', 'L514', 'L515', 'L516']:
            self.assertTrue(self.page.locator('#' + row).is_visible())
        self.ev('document.activeElement.blur()')
        self.page.mouse.move(0, 0)
        self.record_guide_paint()
        self.page.locator('#L512 .fold-icon').hover()
        self.page.wait_for_function('guidePaint.length > 0')
        child_center = self.page.locator('#L513').evaluate('row => { const bounds = row.getBoundingClientRect(); return bounds.top + bounds.height / 2; }')
        self.assertTrue(any(segment['top'] <= child_center <= segment['top'] + segment['height']
                            for segment in self.ev('guidePaint')))

    def test_text_paint_containment_keeps_selection_and_source_panels_outside_rows(self):
        parent, child, grandchild, outside = self.nested_fold_rows()
        self.expand_fold_source(grandchild)
        source = self.page.locator('#' + grandchild['line'] + '_content')
        self.assertTrue(source.is_visible())
        self.assertEqual(source.evaluate('panel => getComputedStyle(panel).contain'), 'none')
        self.assertGreater(source.bounding_box()['height'], 0)
        self.assertTrue(source.evaluate("panel => panel.parentElement.classList.contains('text-chunk')"))
        self.assertEqual(source.evaluate('panel => panel.previousElementSibling.id'), grandchild['row'])
        select = """ids => {
            const range = document.createRange();
            range.setStartBefore(document.getElementById(ids[0]).querySelector('.line-content'));
            range.setEndAfter(document.getElementById(ids[1]).querySelector('.line-content'));
            const selection = getSelection();
            selection.removeAllRanges(); selection.addRange(range);
            return selection.toString();
        }"""
        baseline = self.page.add_style_tag(content='.text-chunk {contain:none}')
        before = self.ev(select, [parent['row'], grandchild['row']])
        baseline.evaluate('style => style.remove()')
        after = self.ev(select, [parent['row'], grandchild['row']])
        self.assertEqual(after, before)
        for entry in [parent, grandchild]:
            self.assertIn(self.page.locator('#' + entry['row']).get_attribute('data-func'), after)
        self.assertGreater(source.locator('.location-link').count(), 0)

    def test_text_chunk_compositing_preserves_pointer_selection_across_boundaries(self):
        if not self.ev("CSS.supports('overflow-clip-margin', '6px')"):
            self.skipTest('paint clip margins are not supported')
        trace = '# tracer: function_graph\n' + '# padding\n' * 509
        trace += ('# selection_start\n'
                  ' 0)               |  parent() { /* <-caller+0x1/0x20 */\n'
                  ' 0)               |    child() { /* <-caller+0x1/0x20 */\n'
                  ' 0)   0.001 us    |      leaf(); /* <-caller+0x1/0x20 */\n'
                  ' 0)   0.002 us    |    } /* child */\n'
                  ' 0)   0.003 us    |  } /* parent */\n'
                  '# selection_end\n')
        trace_path = os.path.join(self.k.dir, 'chunk-selection.txt')
        with open(trace_path, 'w') as stream:
            stream.write(trace)
        self.k.run(trace_path, '--enable-fold', '--func-links', out='chunk-selection.html')
        self.page.goto('file://' + self.k.dir + '/chunk-selection.html', wait_until='load')
        self.assertEqual(self.page.locator('.text-chunk').count(), 2)
        self.assertEqual(self.ev("getComputedStyle(document.querySelector('.text-chunk')).willChange"), 'transform')
        geometry = """() => Array.from(document.querySelectorAll('#content .line-container'), row => {
            const bounds = row.getBoundingClientRect();
            return [row.id, bounds.x, bounds.y, bounds.width, bounds.height];
        })"""

        def drag_selection():
            self.ev('getSelection().removeAllRanges()')
            points = self.ev("""() => ['L511', 'L517'].map((rowId, index) => {
                const content = document.getElementById(rowId).querySelector('.line-content');
                const walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT);
                const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
                const node = index ? nodes[nodes.length - 1] : nodes[0];
                const range = document.createRange();
                range.setStart(node, index ? node.textContent.length : 0); range.collapse(true);
                const bounds = range.getBoundingClientRect();
                return [bounds.x, bounds.y + bounds.height / 2];
            })""")
            self.page.mouse.move(*points[0])
            self.page.mouse.down()
            self.page.mouse.move(*points[1], steps=12)
            self.page.mouse.up()
            return self.ev('getSelection().toString()')

        for theme, width in product(['light', 'dark'], [390, 1500]):
            with self.subTest(theme=theme, width=width):
                self.page.set_viewport_size(dict(width=width, height=900))
                self.ev('theme => document.documentElement.dataset.theme = theme', theme)
                self.page.locator('#L512').evaluate("row => row.scrollIntoView({block:'center', inline:'start'})")
                baseline = self.page.add_style_tag(content='.text-chunk {will-change:auto}')
                before_geometry = self.ev(geometry)
                before = drag_selection()
                baseline.evaluate('style => style.remove()')
                self.assertEqual(self.ev(geometry), before_geometry)
                self.assertEqual(drag_selection(), before)
                for text in ['selection_start', 'parent()', 'child()', 'leaf()', 'selection_end']:
                    self.assertIn(text, before)

    def test_fold_guides_restore_before_text_is_parsed(self):
        self.page.locator('#foldGuidesBtn').click()
        self.assertEqual(self.ev("lsGet('foldGuides')"), 'false')
        self.assertIsNone(self.ev("localStorage.getItem('foldGuides')"))
        with open(os.path.join(self.k.dir, 'report.html'), encoding='utf-8') as stream:
            report = stream.read()
        instrument = """<script>
            window.initialGuides = {
                disabled:document.getElementById('fgTreeGuides').sheet.disabled,
                rows:document.querySelectorAll('.line-container').length
            };
        </script>"""
        path = os.path.join(self.k.dir, 'guides-startup.html')
        with open(path, 'w', encoding='utf-8') as stream:
            stream.write(report.replace('</head>', instrument + '</head>', 1))
        self.page.goto('file://' + path, wait_until='load')
        self.assertEqual(self.ev('initialGuides'), dict(disabled=True, rows=0))
        self.assertEqual(self.page.locator('#foldGuidesBtn').get_attribute('aria-pressed'), 'false')
        self.page.locator('#foldGuidesBtn').click()
        self.page.reload(wait_until='load')
        self.assertEqual(self.ev('initialGuides'), dict(disabled=False, rows=0))
        self.assertEqual(self.page.locator('#foldGuidesBtn').get_attribute('aria-pressed'), 'true')

    def test_fold_guides_hover_only_emphasizes_the_current_pair(self):
        parent, child, grandchild, outside = self.nested_fold_rows()
        self.record_guide_paint()
        row = self.page.locator('#' + child['row'])
        for marker in [row.locator('.fold-icon'), self.page.locator('#' + row.get_attribute('data-fold-end') + ' .fold-close')]:
            marker.hover()
            self.page.wait_for_function('guidePaint.length > 0')
            self.assertGreater(self.ev('guideBasePaint.length'), 0)
            self.assertEqual(self.page.locator('.fold-scope').count(), 2)
            self.page.mouse.move(0, 0)
            self.page.wait_for_function('guidePaint.length === 0')
        close = self.page.locator('#' + row.get_attribute('data-fold-end') + ' .fold-close')
        close.scroll_into_view_if_needed()
        corner = close.evaluate('''node => {
            const rect = node.getBoundingClientRect();
            const unit = parseFloat(getComputedStyle(node.closest('.line-container').querySelector('.call-indent'), '::before').width) / 2;
            return {x:rect.left - unit, y:rect.top + 2};
        }''')
        self.page.mouse.move(corner['x'], corner['y'])
        self.assertEqual(self.page.locator('.fold-scope').count(), 2)
        self.page.mouse.move(0, 0)
        self.assertEqual(self.page.locator('.fold-scope').count(), 0)

    def record_guide_paint(self):
        self.ev("""() => {
            window.guidePaint = [];
            window.guideBasePaint = [];
            const fill = CanvasRenderingContext2D.prototype.fillRect;
            const clear = CanvasRenderingContext2D.prototype.clearRect;
            CanvasRenderingContext2D.prototype.fillRect = function (left, top, width, height) {
                if (this.canvas.classList.contains('fold-guide-layer')) {
                    const active = getComputedStyle(document.documentElement).getPropertyValue('--tree-guide-active').trim();
                    (this.fillStyle === active ? guidePaint : guideBasePaint).push({left, top, width, height, color:this.fillStyle});
                }
                return fill.call(this, left, top, width, height);
            };
            CanvasRenderingContext2D.prototype.clearRect = function (...args) {
                if (this.canvas.classList.contains('fold-guide-layer')) { guidePaint = []; guideBasePaint = []; }
                return clear.apply(this, args);
            };
        }""")

    def test_hierarchy_guides_share_visible_neutral_colors_in_both_themes(self):
        child = self.nested_fold_rows()[1]
        self.record_guide_paint()
        for theme in ['light', 'dark']:
            with self.subTest(theme=theme):
                self.page.mouse.move(0, 0)
                self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                colors = self.ev("""id => {
                    const entry = document.getElementById(id);
                    const guide = getComputedStyle(entry).getPropertyValue('--tree-guide').trim();
                    const active = getComputedStyle(entry).getPropertyValue('--tree-guide-active').trim();
                    const source = document.createElement('div');
                    source.className = 'expanded-content src-chain';
                    source.style.color = guide;
                    source.innerHTML = '<details class="src-frame"><summary>traced</summary><details class="src-frame src-call"><summary>inline</summary></details></details>';
                    document.body.append(source);
                    const frameColors = Array.from(source.querySelectorAll('.src-frame'), frame => getComputedStyle(frame).borderLeftColor);
                    const outer = getComputedStyle(source).borderLeftColor;
                    const guideRgb = getComputedStyle(source).color;
                    source.remove();
                    return {guide, guideRgb, active, frameColors, outer};
                }""", child['row'])
                self.page.wait_for_function('guideBasePaint.length > 0')
                self.assertTrue(all(segment['color'] == colors['guide'] for segment in self.ev('guideBasePaint')))
                self.assertEqual(colors['frameColors'], [colors['outer']] * 2)
                self.assertEqual(colors['outer'], colors['guideRgb'])
                self.assertTrue(colors['guide'].startswith('#'))
                self.assertNotEqual(colors['guide'], colors['active'])
                self.page.locator('#' + child['row'] + ' .fold-icon').hover()
                self.page.wait_for_function('guidePaint.length > 3')
                self.assertTrue(all(segment['color'] == colors['active'] for segment in self.ev('guidePaint')))

    def test_ancestor_guides_actually_paint_at_standard_and_high_pixel_density(self):
        child = self.nested_fold_rows()[1]
        for scale in [1, 1.25, 2]:
            with self.subTest(scale=scale):
                page = self.browser.new_page(viewport={'width': 1500, 'height': 900}, device_scale_factor=scale)
                try:
                    page.goto(self.url, wait_until='load')
                    indent = page.locator('#' + child['row'] + ' .call-indent')
                    self.assertGreater(indent.bounding_box()['width'], 20)
                    visible = indent.screenshot()
                    page.locator('#foldGuidesBtn').click()
                    hidden = indent.screenshot()
                    self.assertNotEqual(visible, hidden, 'Ancestor guides must paint pixels, not just have a CSS color')
                    page.locator('#foldGuidesBtn').click()
                    self.assertEqual(indent.screenshot(), visible)
                finally:
                    page.close()

    def test_deep_guides_have_uniform_physical_width_before_and_during_hover(self):
        depth = 18
        lines = ['# tracer: function_graph\n']
        for level in range(depth):
            lines.append(' 0)               |  ' + '  ' * level + f'frame_{level}() {{ /* <-caller+0x1/0x20 */\n')
        lines.append(' 0)   0.001 us    |  ' + '  ' * depth + 'leaf(); /* <-caller+0x1/0x20 */\n')
        for level in reversed(range(depth)):
            lines.append(' 0)   0.002 us    |  ' + '  ' * level + f'}} /* frame_{level} */\n')
        path = os.path.join(self.k.dir, 'deep-guides.txt')
        with open(path, 'w', encoding='utf-8') as stream:
            stream.writelines(lines)
        self.k.run(path, '--enable-fold', out='deep-guides.html')
        url = 'file://' + os.path.join(self.k.dir, 'deep-guides.html')
        for scale, theme in product([1, 1.25, 1.5, 2], ['light', 'dark']):
            with self.subTest(scale=scale, theme=theme):
                page = self.browser.new_page(viewport=dict(width=1500, height=900), device_scale_factor=scale)
                try:
                    page.goto(url, wait_until='load')
                    page.evaluate('theme => document.documentElement.dataset.theme = theme', theme)
                    leaf = page.locator('[data-func=leaf]')
                    leaf.scroll_into_view_if_needed()
                    clip = leaf.evaluate('''row => {
                        const rect = row.getBoundingClientRect();
                        const indent = row.querySelector('.call-indent');
                        const bounds = indent.getBoundingClientRect();
                        const step = document.querySelector('.fold-icon').getBoundingClientRect().width;
                        return {x:Math.floor(bounds.left), y:Math.floor(rect.top + rect.height / 2) - 1,
                            width:Math.floor(bounds.width - step), height:3};
                    }''')
                    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                    visible = page.screenshot(clip=clip)
                    page.evaluate('''() => document.querySelector('[data-func=frame_6] .fold-icon')
                        .dispatchEvent(new PointerEvent('pointerover', {bubbles:true}))''')
                    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                    hovered = page.screenshot(clip=clip)
                    page.evaluate("document.getElementById('foldGuidesBtn').click()")
                    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                    hidden = page.screenshot(clip=clip)
                    widths = page.evaluate('''async images => {
                        const decode = async encoded => {
                            const image = new Image(); image.src = 'data:image/png;base64,' + encoded;
                            await image.decode();
                            const canvas = document.createElement('canvas');
                            canvas.width = image.width; canvas.height = image.height;
                            const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
                            return {pixels:context.getImageData(0, 0, image.width, image.height).data,
                                width:image.width, height:image.height};
                        };
                        const [normal, hovered, hidden] = await Promise.all(images.map(decode));
                        return [normal, hovered].map(image => {
                            const runs = []; let width = 0;
                            const row = Math.floor(image.height / 2);
                            for (let column = 0; column < image.width; column++) {
                                const offset = (row * image.width + column) * 4;
                                const changed = [0, 1, 2].some(channel =>
                                    Math.abs(image.pixels[offset + channel] - hidden.pixels[offset + channel]) >= 12);
                                if (changed) width++;
                                else if (width) { runs.push(width); width = 0; }
                            }
                            if (width) runs.push(width);
                            return runs;
                        });
                    }''', [base64.b64encode(image).decode() for image in [visible, hovered, hidden]])
                    for measured in widths:
                        self.assertEqual(len(measured), depth, measured)
                        self.assertEqual(measured, [max(1, int(scale + 0.5))] * depth)
                    self.assertNotEqual(visible, hovered)
                finally:
                    page.close()

    def test_tree_layout_preserves_trace_geometry_tabs_and_native_selection(self):
        self.ev("""() => {
            const content = document.getElementById('content');
            for (const prefix of ['x|', 'xxxx|', 'xxxxxxxx|']) {
                const row = document.createElement('div');
                row.className = 'line-container';
                row.innerHTML = '<div class="line-content">' + prefix +
                    '<span class="call-indent">\\t  </span><span class="hl-func">probe</span>()</div>';
                content.append(row);
            }
        }""")
        legacy = self.page.add_style_tag(content='''
            .call-indent {display:inline; height:auto; vertical-align:baseline}
            .fold-marker {height:1em; vertical-align:baseline}
            .fold-close {display:inline}
        ''')
        snapshot = """() => {
            const content = document.getElementById('content');
            const range = document.createRange(); range.selectNodeContents(content);
            const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
            const text = selection.toString(); selection.removeAllRanges();
            return {text, rows:Array.from(content.querySelectorAll('.line-container'), row => {
                const rect = row.getBoundingClientRect();
                const name = row.querySelector('.func-name-link, .hl-func')?.getBoundingClientRect();
                return [rect.x, rect.y, rect.width, rect.height, name?.x, name?.y];
            })};
        }"""
        before = self.ev(snapshot)
        legacy.evaluate('style => style.remove()')
        self.assertEqual(self.ev(snapshot), before)

    def test_tree_rails_join_across_rows_and_controls_share_the_same_axis(self):
        parent, child = self.nested_fold_rows()[:2]
        for scale, theme in product([1, 1.25, 2], ['light', 'dark']):
            with self.subTest(scale=scale, theme=theme):
                page = self.browser.new_page(viewport={'width': 1500, 'height': 900}, device_scale_factor=scale)
                try:
                    page.goto(self.url, wait_until='load')
                    page.evaluate("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                    page.locator('#' + child['row']).scroll_into_view_if_needed()
                    facts = page.evaluate("""({parent, child}) => {
                        const entry = document.getElementById(parent.row);
                        const icon = entry.querySelector('.fold-icon');
                        const iconRect = icon.getBoundingClientRect();
                        const arrow = getComputedStyle(icon, '::before');
                        const rows = Array.from(document.querySelectorAll('#content .line-container'));
                        const start = rows.findIndex(row => row.id === child.row);
                        const sampled = rows.slice(start, start + 4);
                        if (sampled.some(row => !(row.dataset.foldAncestors || '').split(' ').includes(parent.fold)))
                            throw new Error('The sampled rail must stay inside the same recorded parent');
                        const first = sampled[0].getBoundingClientRect();
                        const last = sampled[sampled.length - 1].getBoundingClientRect();
                        const axis = iconRect.left + iconRect.width / 2;
                        const row = entry.getBoundingClientRect();
                        return {clip:{x:Math.floor(axis - 2), y:Math.floor(first.top), width:5,
                                      height:Math.ceil(last.bottom) - Math.floor(first.top)},
                            centerOffset:iconRect.top + iconRect.height / 2 - row.top - row.height / 2,
                            arrow:arrow.content,
                            stroke:parseFloat(arrow.borderRightWidth)};
                    }""", dict(parent=parent, child=child))
                    self.assertAlmostEqual(facts['centerOffset'], 0, delta=0.02)
                    self.assertEqual(facts['arrow'], '\"\"')
                    self.assertEqual(facts['stroke'], 1)
                    page.mouse.move(0, 0)
                    page.wait_for_timeout(350)
                    visible = page.screenshot(clip=facts['clip'])
                    page.locator('#foldGuidesBtn').click()
                    hidden = page.screenshot(clip=facts['clip'])
                    gaps = page.evaluate("""async images => {
                        const decode = async encoded => {
                            const image = new Image();
                            image.src = 'data:image/png;base64,' + encoded; await image.decode();
                            const canvas = document.createElement('canvas');
                            canvas.width = image.width; canvas.height = image.height;
                            const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
                            return {pixels:context.getImageData(0, 0, image.width, image.height).data,
                                    width:image.width, height:image.height};
                        };
                        const [visible, hidden] = await Promise.all(images.map(decode));
                        const missing = [];
                        for (let row = 3; row < visible.height - 3; row++) {
                            let painted = false;
                            for (let column = 0; column < visible.width; column++) {
                                const offset = (row * visible.width + column) * 4;
                                if ([0, 1, 2].some(channel => Math.abs(visible.pixels[offset + channel] -
                                    hidden.pixels[offset + channel]) >= 12)) painted = true;
                            }
                            if (!painted) missing.push(row);
                        }
                        return missing;
                    }""", [base64.b64encode(image).decode() for image in [visible, hidden]])
                    self.assertEqual(gaps, [], 'Ancestor rails must paint through every row boundary')
                finally:
                    page.close()

    def test_fold_hover_highlights_the_visible_column_not_just_endpoints(self):
        parent, child, grandchild, outside = self.nested_fold_rows()
        self.record_guide_paint()
        self.page.locator('#' + child['row'] + ' .fold-icon').hover()
        self.page.wait_for_function('guidePaint.length > 3')
        segments = self.ev('guidePaint')
        self.assertGreater(len(segments), 3)
        self.assertTrue(all(segment['width'] <= 1 for segment in segments))
        self.assertLessEqual(max(segment['left'] for segment in segments) - min(segment['left'] for segment in segments), 1)
        self.page.locator('#foldGuidesBtn').click()
        self.page.wait_for_function('guidePaint.length === 0')
        self.page.locator('#foldGuidesBtn').click()
        self.page.locator('#' + child['row'] + ' .fold-icon').hover()
        self.page.wait_for_function('guidePaint.length > 3')
        self.page.mouse.move(0, 0)
        self.page.wait_for_function('guidePaint.length === 0')

    def test_fold_column_hover_does_not_hit_test_the_large_document(self):
        child = self.nested_fold_rows()[1]
        self.record_guide_paint()
        self.ev("""id => {
            document.elementFromPoint = () => { throw new Error('full-document hit test during guide hover'); };
            const query = Element.prototype.querySelectorAll;
            Element.prototype.querySelectorAll = function(selector) {
                if (this.id === 'content' && selector.includes('.line-container'))
                    throw new Error('full-document row query during guide hover');
                return query.call(this, selector);
            };
            document.getElementById(id).querySelector('.fold-icon')
                .dispatchEvent(new PointerEvent('pointerover', {bubbles:true}));
        }""", child['row'])
        self.page.wait_for_function('guidePaint.length > 3')
        self.ev('delete document.elementFromPoint')

    def test_fold_hover_column_skips_source_panels_and_unrelated_tasks(self):
        parent, child, grandchild, outside = self.nested_fold_rows()
        self.expand_fold_source(grandchild)
        self.ev("""id => {
            const original = document.getElementById(id);
            const foreign = original.cloneNode(true);
            foreign.id = 'foreign-task-row';
            foreign.setAttribute('data-fold-id', 'foreign');
            foreign.removeAttribute('data-fold-ancestors');
            original.before(foreign);
        }""", grandchild['row'])
        self.record_guide_paint()
        self.page.locator('#' + child['row'] + ' .fold-icon').hover()
        self.page.wait_for_function('guidePaint.length > 2')
        segments = self.ev('guidePaint')
        for selector in ['#foreign-task-row', '#' + grandchild['line'] + '_content']:
            bounds = self.page.locator(selector).bounding_box()
            self.assertGreater(bounds['height'], 0)
            self.assertTrue(all(segment['top'] + segment['height'] <= bounds['y'] + 1 or
                                segment['top'] >= bounds['y'] + bounds['height'] - 1 for segment in segments))

    def test_fold_guides_button_is_at_the_left_and_visible_in_compact_windows(self):
        for width in [380, 800, 1500]:
            self.page.set_viewport_size({'width':width, 'height':600})
            self.ev('window.scrollTo(0, 0)')
            bounds = self.page.locator('#foldGuidesBtn').bounding_box()
            self.assertGreaterEqual(bounds['x'], 0)
            self.assertLess(bounds['x'] + bounds['width'], width)
            self.assertLess(bounds['x'], 150)
            self.assertLess(bounds['y'], 200)
            self.page.locator('#foldGuidesBtn').click()

    def test_fold_palette_remains_muted_in_both_themes(self):
        parent = self.nested_fold_rows()[0]
        self.page.locator('#' + parent['row'] + ' .fold-icon').click()
        self.page.mouse.move(0, 0)
        self.ev('document.activeElement.blur()')
        for theme in ['light', 'dark']:
            with self.subTest(theme=theme):
                self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                colors = self.ev("""id => {
                    const row = document.getElementById(id);
                    const style = getComputedStyle(row);
                    return {icon:getComputedStyle(row.querySelector('.fold-icon')).color,
                        guide:style.getPropertyValue('--tree-guide'), background:style.getPropertyValue('--fold-bg'),
                        summary:getComputedStyle(row.querySelector('.fold-summary')).color,
                        count:getComputedStyle(row.querySelector('.summary-count')).color};
                }""", parent['row'])
                self.assertEqual(colors['summary'], colors['count'])
                for name in ['icon', 'guide', 'background']:
                    color = colors[name].strip()
                    components = ([int(color[offset:offset + 2], 16) for offset in [1, 3, 5]] if color.startswith('#') else
                                  [float(value) for value in color.split('(')[1].rstrip(')').split(',')])
                    self.assertLessEqual(max(components[:3]) - min(components[:3]), 40)
                    if len(components) == 4:
                        self.assertLessEqual(components[3], 0.25)

    def test_fold_summary_reserves_error_color_for_known_errors(self):
        trace = '# tracer: function_graph\n'
        for name, returned in [('normal', '0x0'), ('error', '-2'), ('unknown', '-9999'), ('boolean', 'false')]:
            trace += (' 0)               |  ' + name + '() { /* <-caller+0x1/0x20 */\n'
                      ' 0)   0.001 us    |    leaf(); /* <-caller+0x1/0x20 */\n'
                      ' 0)   0.010 us    |  } /* ' + name + ' ret=' + returned + ' */\n')
        path = os.path.join(self.k.dir, 'fold-returns.txt')
        with open(path, 'w', encoding='utf-8') as stream:
            stream.write(trace)
        self.k.run(path, '--enable-fold', out='fold-returns.html')
        self.page.goto('file://' + self.k.dir + '/fold-returns.html', wait_until='load')
        entries = self.ev("""() => {
            const cases = {};
            for (const row of document.querySelectorAll('[data-fold-end]')) {
                cases[row.getAttribute('data-func')] = row.id;
            }
            return cases;
        }""")
        self.assertEqual(set(entries), {'error', 'normal', 'unknown', 'boolean'})
        for kind, row_id in entries.items():
            self.ev("id => _setFolded(document.getElementById(id).getAttribute('data-fold-id'), true)", row_id)
            for theme in ['light', 'dark']:
                self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                colors = self.ev("""id => {
                    const summary = document.getElementById(id).querySelector('.fold-summary');
                    const returned = summary.querySelector('.summary-ret');
                    return {summary:getComputedStyle(summary).color, returned:getComputedStyle(returned).color, text:returned.textContent,
                        error:returned.classList.contains('summary-error')};
                }""", row_id)
                self.assertEqual(colors['error'], kind == 'error')
                self.assertEqual(colors['summary'] == colors['returned'], kind != 'error')
                self.assertIn({'normal':'0x0', 'error':'-2', 'unknown':'-9999', 'boolean':'false'}[kind], colors['text'])

    def test_fold_control_stays_reachable_with_long_arguments(self):
        parent = self.nested_fold_rows()[0]
        name = self.page.locator('#' + parent['row']).get_attribute('data-func')
        with open(helpers.fixture('pi4_sched')) as stream:
            trace = stream.read()
        long_call = name + '(arg=0x' + 'a' * 400 + ')'
        trace = trace.replace(name + '()', long_call, 1)
        path = os.path.join(self.k.dir, 'long-fold.txt')
        with open(path, 'w') as stream:
            stream.write(trace)
        self.k.run(path, '--enable-fold', '--func-links', out='long-fold.html')
        self.page.set_viewport_size({'width':800, 'height':600})
        self.page.goto('file://' + self.k.dir + '/long-fold.html', wait_until='load')
        row = self.page.locator('#' + parent['row'])
        self.assertGreater(row.locator('.fold-brace').bounding_box()['x'], 800)
        icon = row.locator('.fold-icon').bounding_box()
        self.assertGreaterEqual(icon['x'], 0)
        self.assertLess(icon['x'] + icon['width'], 800)
        row.locator('.fold-icon').click()
        self.assertEqual(row.locator('.fold-icon').get_attribute('aria-expanded'), 'false')
        self.assertEqual(self.ev('window.scrollX'), 0)

    def test_fold_hides_expanded_descendant_sources(self):
        for grouped in [False, True]:
            with self.subTest(grouped=grouped):
                self.ev('localStorage.clear()')
                self.page.reload(wait_until='load')
                if grouped:
                    self.page.click('#textOrderBtn')
                parent, child, grandchild, outside = self.nested_fold_rows()
                for entry in [parent, child, grandchild, outside]:
                    self.expand_fold_source(entry)
                self.page.locator('#' + parent['row'] + ' .fold-icon').click()
                for entry in [child, grandchild]:
                    self.assertFalse(self.page.locator('#' + entry['row']).is_visible())
                    self.assertFalse(self.page.locator('#' + entry['line'] + '_content').is_visible())
                for entry in [parent, outside]:
                    self.assertTrue(self.page.locator('#' + entry['row']).is_visible())
                    self.assertTrue(self.page.locator('#' + entry['line'] + '_content').is_visible())
                self.page.locator('#' + parent['row'] + ' .fold-icon').click()
                for entry in [parent, child, grandchild, outside]:
                    self.assertTrue(self.page.locator('#' + entry['line'] + '_content').is_visible())
                    self.page.locator('#' + entry['row'] + ' .line-content').click()
                    self.page.locator('#' + entry['line'] + '_content').wait_for(state='hidden')

    def test_fold_keeps_nested_child_closed_when_parent_reopens(self):
        parent, child, grandchild, outside = self.nested_fold_rows()
        self.expand_fold_source(grandchild)
        self.page.locator('#' + child['row'] + ' .fold-icon').click()
        self.assertFalse(self.page.locator('#' + grandchild['line'] + '_content').is_visible())
        self.page.locator('#' + parent['row'] + ' .fold-icon').click()
        self.page.locator('#' + parent['row'] + ' .fold-icon').click()
        self.assertTrue(self.page.locator('#' + child['row']).is_visible())
        self.assertFalse(self.page.locator('#' + grandchild['row']).is_visible())
        self.assertFalse(self.page.locator('#' + grandchild['line'] + '_content').is_visible())
        self.page.locator('#' + child['row'] + ' .fold-icon').click()
        self.assertTrue(self.page.locator('#' + grandchild['row']).is_visible())
        self.assertTrue(self.page.locator('#' + grandchild['line'] + '_content').is_visible())

    def test_filter_and_clear_preserve_collapsed_ancestors(self):
        parent, child, grandchild, outside = self.nested_fold_rows()
        self.ev('id => toggleFold(id)', parent['fold'])
        self.ev('() => { document.getElementById("filterInput").value = "comm:.*"; applyFilter(); }')
        self.assertFalse(self.page.locator('#' + child['row']).is_visible())
        self.assertFalse(self.page.locator('#' + grandchild['row']).is_visible())
        self.ev('clearFilter()')
        self.assertFalse(self.page.locator('#' + child['row']).is_visible())
        self.assertFalse(self.page.locator('#' + grandchild['row']).is_visible())

    def test_unfold_does_not_reveal_filter_rejected_rows_or_sources(self):
        parent, child, grandchild, outside = self.nested_fold_rows()
        self.expand_fold_source(grandchild)
        self.ev('''row => {
            const name = document.getElementById(row).dataset.func;
            document.getElementById('filterInput').value = 'func:^' + name + '$';
            applyFilter();
        }''', parent['row'])
        self.assertFalse(self.page.locator('#' + grandchild['row']).is_visible())
        matches = self.ev('Array.from(fgFilterState.lines)')
        self.ev('id => { toggleFold(id); toggleFold(id); }', parent['fold'])
        self.assertFalse(self.page.locator('#' + grandchild['row']).is_visible())
        self.assertFalse(self.page.locator('#' + grandchild['line'] + '_content').is_visible())
        self.assertEqual(self.ev('Array.from(fgFilterState.lines)'), matches)

    def test_clear_filter_restores_only_visible_saved_source_expansions(self):
        parent, child, grandchild, outside = self.nested_fold_rows()
        self.expand_fold_source(grandchild)
        self.expand_fold_source(outside)
        self.ev('id => toggleFold(id)', parent['fold'])
        self.ev('() => { document.getElementById("filterInput").value = "func:no_such_call"; applyFilter(); clearFilter(); }')
        self.assertFalse(self.page.locator('#' + grandchild['line'] + '_content').is_visible())
        self.assertTrue(self.page.locator('#' + outside['line'] + '_content').is_visible())
        self.ev('id => toggleFold(id)', parent['fold'])
        self.assertTrue(self.page.locator('#' + grandchild['line'] + '_content').is_visible())

    def test_local_folding_and_keyboard_navigation_never_materialize_full_candidate_snapshots(self):
        facts = self.ev('''() => {
            const folding = fgGetTextFolding();
            const target = [...folding.blocks.values()].find(block => block.entry && block.calls &&
                [...folding.members(block.id)].length < 32);
            if (!target) throw new Error('No bounded local subtree');
            const id = target.id;
            const navigation = fgTextNavigation, order = navigation.order;
            const descriptors = Object.fromEntries(['visible','expandable'].map(name =>
                [name, Object.getOwnPropertyDescriptor(navigation, name)]));
            let visits = 0;
            navigation.order = new Proxy(order, {get(target, property) {
                if (/^\\d+$/.test(String(property))) visits++;
                return Reflect.get(target, property);
            }});
            for (const name of ['visible','expandable']) Object.defineProperty(navigation, name,
                {configurable:true, get() { throw new Error('Full navigation snapshot: ' + name); }});
            try {
                _setFolded(id, true);
                navigateToExpandableLine(1);
                navigateToExpandableLine(-1);
                navigateToTextBoundary(false);
                navigateToTextBoundary(true);
                _setFolded(id, false);
            } finally {
                navigation.order = order;
                Object.defineProperties(navigation, descriptors);
            }
            const rows = [...document.querySelectorAll('#content .line-container')];
            return {visits, total:rows.length, visible:navigation.visibleCount, expandable:navigation.expandableCount,
                expectedVisible:rows.filter(row => row.style.display !== 'none').length,
                expectedExpandable:rows.filter(row => row.style.display !== 'none' && row.matches('.expandable')).length};
        }''')
        self.assertLess(facts['visits'], 32)
        self.assertGreater(facts['total'], facts['visits'])
        self.assertEqual(facts['visible'], facts['expectedVisible'])
        self.assertEqual(facts['expandable'], facts['expectedExpandable'])

    def test_bulk_folding_projects_once_without_dom_membership_or_summary_scans(self):
        parent, child, grandchild, outside = self.nested_fold_rows()
        facts = self.ev('''id => {
            const folding = fgGetTextFolding();
            const rows = document.querySelectorAll('#content .line-container').length;
            const project = fgProjectFoldRecords;
            const documentQuery = Document.prototype.querySelectorAll;
            const elementQuery = Element.prototype.querySelectorAll;
            let projections = 0;
            const guard = original => function(selector) {
                if (selector.includes('data-fold')) throw new Error('DOM membership scan: ' + selector);
                return original.call(this, selector);
            };
            fgProjectFoldRecords = (...args) => { projections++; return project(...args); };
            Document.prototype.querySelectorAll = guard(documentQuery);
            Element.prototype.querySelectorAll = guard(elementQuery);
            try {
                foldToDepth(1);
                unfoldAllBlocks();
                toggleFoldRecursive(id);
            } finally {
                fgProjectFoldRecords = project;
                Document.prototype.querySelectorAll = documentQuery;
                Element.prototype.querySelectorAll = elementQuery;
            }
            return {projections, rows, after:document.querySelectorAll('#content .line-container').length,
                same:folding === fgGetTextFolding(),
                navigable:fgTextNavigation.expandable.every(index =>
                    document.getElementById('L' + fgTextNavigation.order[index]).style.display !== 'none')};
        }''', parent['fold'])
        self.assertEqual(facts['projections'], 3)
        self.assertEqual(facts['rows'], facts['after'])
        self.assertTrue(facts['same'])
        self.assertTrue(facts['navigable'])
        self.assertFalse(self.page.locator('#' + grandchild['row']).is_visible())

    def test_reprojecting_unchanged_fold_visibility_does_not_rewrite_rows_or_sources(self):
        facts = self.ev('''() => {
            const folding = fgGetTextFolding();
            const observer = new MutationObserver(() => {});
            observer.observe(document.getElementById('content'), {attributes:true, childList:true, subtree:true});
            fgProjectFoldRecords(folding, folding.records);
            const changes = observer.takeRecords().map(record => ({type:record.type, attribute:record.attributeName}));
            observer.disconnect();
            return {changes, rows:folding.records.length};
        }''')
        self.assertGreater(facts['rows'], 100)
        self.assertEqual(len(facts['changes']), 0, facts['changes'][:5])

    def test_bulk_fold_projects_only_the_unique_union_of_changed_scopes(self):
        parent, child, grandchild, outside = self.nested_fold_rows()
        facts = self.ev('''ids => {
            const root = document.getElementById('content');
            const expected = [...root.querySelectorAll('.line-container')].filter(line =>
                ids.scopes.includes(line.dataset.foldId) ||
                (line.dataset.foldAncestors || '').split(/\\s+/).some(id => ids.scopes.includes(id)))
                .map(line => +line.dataset.lineNumber).sort((left, right) => left - right);
            const project = fgProjectFoldRecords;
            const projections = [];
            fgProjectFoldRecords = (folding, records) => {
                const selected = Array.from(records);
                projections.push(selected.map(record => record.row[0]));
                return project(folding, selected);
            };
            try {
                fgSetFoldStates([[ids.scopes[0],true], [ids.scopes[1],true], [ids.scopes[0],true], ['missing',true]]);
                fgSetFoldStates([[ids.scopes[0],true], [ids.scopes[1],true], ['missing',true]]);
                fgSetFoldStates([[ids.scopes[0],false], [ids.scopes[1],false]]);
            } finally { fgProjectFoldRecords = project; }
            return {expected, projections, total:fgGetTextFolding().records.length,
                unrelated:document.getElementById(ids.outside).style.display};
        }''', dict(scopes=[child['fold'], grandchild['fold']], outside=outside['row']))
        self.assertEqual(facts['projections'], [facts['expected']] * 2)
        self.assertLess(len(facts['expected']), facts['total'])
        self.assertEqual(facts['unrelated'], '')

    def test_recursive_fold_keeps_projection_local_and_unrelated_source_dom_untouched(self):
        parent, child, grandchild, outside = self.nested_fold_rows()
        self.expand_fold_source(grandchild)
        self.expand_fold_source(outside)
        facts = self.ev('''entries => {
            const folding = fgGetTextFolding();
            const expected = Array.from(folding.members(entries.child.fold), record => record.row[0]);
            const project = fgProjectFoldRecords;
            const projections = [];
            const observer = new MutationObserver(() => {});
            observer.observe(document.getElementById(entries.outside.row), {subtree:true, attributes:true, childList:true});
            observer.observe(document.getElementById(entries.outside.line + '_content'), {subtree:true, attributes:true, childList:true});
            fgProjectFoldRecords = (model, records) => {
                const selected = Array.from(records);
                projections.push(selected.map(record => record.row[0]));
                return project(model, selected);
            };
            let hidden;
            try {
                toggleFoldRecursive(entries.child.fold);
                hidden = document.getElementById(entries.grandchild.line + '_content').style.display;
                toggleFoldRecursive(entries.child.fold);
            } finally { fgProjectFoldRecords = project; }
            const changes = observer.takeRecords().length;
            observer.disconnect();
            return {expected, projections, hidden, changes,
                restored:document.getElementById(entries.grandchild.line + '_content').style.display,
                outside:document.getElementById(entries.outside.line + '_content').style.display};
        }''', dict(child=child, grandchild=grandchild, outside=outside))
        self.assertEqual(facts['projections'], [facts['expected']] * 2)
        self.assertEqual(facts['hidden'], 'none')
        self.assertEqual(facts['restored'], 'block')
        self.assertEqual(facts['outside'], 'block')
        self.assertEqual(facts['changes'], 0)

    def test_scoped_bulk_folds_preserve_complete_visibility_after_filtering_and_reordering(self):
        parent, child, grandchild, outside = self.nested_fold_rows()
        facts = self.ev('''entries => {
            const failures = [];
            for (const order of ['task', 'time']) {
                fgSetTextOrder(order);
                for (const text of ['', 'func:^' + document.getElementById(entries.grandchild.row).dataset.func + '$']) {
                    document.getElementById('filterInput').value = text;
                    applyFilter();
                    const before = Array.from(fgFilterState.lines || []);
                    for (const folded of [true, false]) {
                        fgSetFoldStates([[entries.child.fold,folded], [entries.grandchild.fold,folded]]);
                        const folding = fgGetTextFolding();
                        for (const record of folding.records) {
                            const expected = folding.visible(record, foldedBlocks, fgFilterState.lines);
                            const visible = document.getElementById('L' + record.row[0]).style.display !== 'none';
                            if (visible !== expected) failures.push([order, text, folded, record.row[0]]);
                        }
                        if (JSON.stringify(Array.from(fgFilterState.lines || [])) !== JSON.stringify(before)) failures.push('filter changed');
                    }
                }
            }
            return failures;
        }''', dict(child=child, grandchild=grandchild))
        self.assertEqual(facts, [])

    def test_parent_fold_cancels_pending_descendant_source_click(self):
        parent, child, grandchild, outside = self.nested_fold_rows()
        self.page.locator('#' + grandchild['row'] + ' .line-content').click()
        self.ev('toggleFold(' + repr(parent['fold']) + ')')
        self.page.wait_for_function('clickTimer === null')
        self.assertFalse(self.page.locator('#' + grandchild['row']).is_visible())
        self.assertFalse(self.page.locator('#' + grandchild['line'] + '_content').is_visible())

    def test_fold_restores_hidden_sources_after_reload(self):
        parent, child, grandchild, outside = self.nested_fold_rows()
        for entry in [parent, child, grandchild, outside]:
            self.expand_fold_source(entry)
        icon = self.page.locator('#' + parent['row'] + ' .fold-icon')
        icon.press('Enter')
        self.ev('flushExpandedState()')
        self.page.reload(wait_until='load')
        self.assertTrue(self.page.locator('#' + parent['row'] + '.folded').is_visible())
        for entry in [child, grandchild]:
            self.assertFalse(self.page.locator('#' + entry['row']).is_visible())
            source = self.page.locator('#' + entry['line'] + '_content')
            self.assertFalse(source.is_visible())
            self.assertEqual(source.inner_html(), '')
        self.assertTrue(self.page.locator('#' + outside['line'] + '_content').is_visible())
        self.page.locator('#' + parent['row'] + ' .fold-icon').press('Space')
        for entry in [child, grandchild]:
            self.assertTrue(self.page.locator('#' + entry['row']).is_visible())
            self.assertTrue(self.page.locator('#' + entry['line'] + '_content').is_visible())

    def test_recursive_and_depth_folds_do_not_reveal_hidden_children(self):
        parent, child, grandchild, outside = self.nested_fold_rows()
        self.expand_fold_source(grandchild)
        self.page.locator('#' + parent['row'] + ' .fold-icon').click(modifiers=['Shift'])
        self.assertFalse(self.page.locator('#' + grandchild['line'] + '_content').is_visible())
        self.ev('foldToDepth(1)')
        self.assertTrue(self.page.locator('#' + child['row']).is_visible())
        self.assertFalse(self.page.locator('#' + grandchild['row']).is_visible())
        self.assertFalse(self.page.locator('#' + grandchild['line'] + '_content').is_visible())
        self.ev('unfoldAllBlocks()')
        self.assertTrue(self.page.locator('#' + grandchild['row']).is_visible())
        self.assertTrue(self.page.locator('#' + grandchild['line'] + '_content').is_visible())
        self.page.locator('#' + parent['row'] + ' .fold-icon').click(modifiers=['Shift'])
        self.ev('toggleFoldRecursive(' + repr(child['fold']) + ')')
        self.assertFalse(self.page.locator('#' + child['row']).is_visible())
        self.assertFalse(self.page.locator('#' + grandchild['row']).is_visible())
        self.assertFalse(self.page.locator('#' + grandchild['line'] + '_content').is_visible())

    def open_timeline(self):
        self.page.click('.view-tab[data-view=timeline]')
        self.page.wait_for_timeout(400)
        self.assertTrue(self.ev("document.body.dataset.view === 'timeline'"))

    def open_compare(self, rows=None, adjusted=None):
        if rows is not None:
            self.ev("""({rows, adjusted}) => {
                const node = document.getElementById('cmpData');
                const data = JSON.parse(node.textContent);
                data.rows = rows;
                data.rows_adj = adjusted;
                if (adjusted) data.overhead = [{child: 10, self: 5}, {child: 10, self: 5}];
                node.textContent = JSON.stringify(data);
            }""", {'rows': rows, 'adjusted': adjusted})
        self.page.click('.view-tab[data-view=compare]')
        self.page.wait_for_selector('.cmp-table')

    def open_range_data(self, names, mode='abs', starts=None, durations=None, parents=None, callers=None, extra=None, columns=None):
        count = len(names)
        strings = list(dict.fromkeys(names + ([caller for caller in callers if caller is not None] if callers else [])))
        string_ids = {name: index for index, name in enumerate(strings)}
        data = self.ev("JSON.parse(document.getElementById('tlData').textContent)")
        starts = starts if starts is not None else [index * 1000 for index in range(count)]
        durations = durations if durations is not None else [1000] * count
        data.update(n=count, mode=mode, strings=strings,
                    tasks=[dict(pid=1, comm='range-test', kind=0, cpu=0)],
                    errs=[], chains=[], offcpu=[], resched=[], wakeups=[], irqoff=[],
                    sections=[], atomic=[], inline={}, sources={}, overhead=None)
        data['cols'] = dict(task=[0] * count, depth=[0] * count,
                            start=[start - (starts[index - 1] if index else 0)
                                   for index, start in enumerate(starts)],
                            dur=durations, name=[string_ids[name] for name in names],
                            parent=parents if parents is not None else [-1] * count,
                            line=list(range(1, count + 1)), flags=[0] * count,
                            ret=None, caller=[string_ids.get(caller, -1) for caller in callers] if callers else None,
                            args=None, module=None, cpu=None,
                            adj=[duration / 2 for duration in durations])
        if columns:
            data['cols'].update(columns)
        if extra:
            data.update(extra)
        self.ev("data => { document.getElementById('tlData').textContent = JSON.stringify(data); }", data)
        self.open_timeline()

    def drag_range(self):
        box = self.page.locator('.tl-canvas').bounding_box()
        self.page.keyboard.down('Shift')
        self.page.keyboard.down('Alt')
        self.page.mouse.move(box['x'] + box['width'] * 0.1, box['y'] + 30)
        self.page.mouse.down()
        self.page.mouse.move(box['x'] + box['width'] * 0.9, box['y'] + 30, steps=4)
        self.page.mouse.up()
        self.page.keyboard.up('Alt')
        self.page.keyboard.up('Shift')

    def test_timeline_range_includes_all_recorded_event_ends(self):
        cases = [('runs', {'tasks': [dict(pid=1, comm='late', kind=0, cpu=0, runs=[[0, 9000, 0]])]}),
                 ('wait', {'offcpu': [[0, 8000, None, 0, 0, 1, 0, -1, 0, -1]]}),
                 ('resched', {'resched': [[0, 1000, 9000, 1, 0, 0]]}),
                 ('irqoff', {'irqoff': [[0, 1000, 9000]]}),
                 ('section', {'sections': [[0, 0, 1000, 9000, 0, 0, 0, 1, 1]]}),
                 ('wake', {'wakeups': [[0, 9000, 0, 0, -1, -1]]})]
        for label, extra in cases:
            with self.subTest(event=label):
                self.ev("fgSwitchView('text')")
                self.page.reload()
                self.open_range_data(['early'], starts=[1000], durations=[1000], extra=extra)
                self.page.click('[data-act=fit]')
                expected = 8000 if label == 'wait' else 9000
                self.assertEqual(self.ev("fgView('timeline').probe().view()"), [0 if label == 'runs' else 1000, expected])

    def click_cpu_at(self, timestamp):
        self.page.click('[data-act=fit]')
        self.ev("document.querySelector('.tl-scroll').scrollTop = 0")
        self.page.wait_for_timeout(60)
        start, end = self.ev("fgView('timeline').probe().view()")
        box = self.page.locator('.tl-canvas').bounding_box()
        self.page.mouse.click(box['x'] + box['width'] * (timestamp - start) / (end - start), box['y'] + 28)

    def test_many_task_tracks_only_visit_the_viewport_and_relayout_after_filtering(self):
        count, target = 512, 490
        self.open_range_data([f'worker_{index}' for index in range(count)], starts=[0] * count,
                             durations=[10000] * count, columns={'task': list(range(count))},
                             extra={'tasks': [dict(pid=index + 1, comm=f'task_{index}', kind=0, cpu=0)
                                              for index in range(count)]})
        self.ev('''async target => {
            const probe = fgView('timeline').probe(), scroll = document.querySelector('.tl-scroll');
            scroll.scrollTop = probe.TRACKS[target].y - 5;
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            window.trackReads = {positions: 0, hidden: 0};
            for (const track of probe.TRACKS) for (const [key, counter] of [['y', 'positions'], ['hidden', 'hidden']]) {
                let value = track[key];
                Object.defineProperty(track, key, {configurable: true,
                    get() { trackReads[counter]++; return value; }, set(next) { value = next; }});
            }
        }''', target)
        point = self.ev('target => fgView("timeline").probe().xyOf(target)', target)
        self.ev('trackReads.positions = trackReads.hidden = 0')
        self.page.mouse.move(*point)
        self.ev('async () => { await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); }')
        self.assertIn(f'worker_{target}', self.page.locator('.tl-tip').inner_text())
        counts = self.ev('trackReads')
        self.assertLess(counts['positions'], 150, 'hover/redraw scanned offscreen task positions')
        self.assertLess(counts['hidden'], 50, 'hover/redraw scanned offscreen task visibility')
        self.page.mouse.click(*point)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), target)
        self.page.click('.tl-detail .tl-close')
        helpers.click_timeline_tool(self.page, '[data-act=expand]')
        self.assertTrue(self.ev("fgView('timeline').probe().TRACKS.every(track => track.collapsed)"))
        helpers.click_timeline_tool(self.page, '[data-act=expand]')
        self.assertFalse(self.ev("fgView('timeline').probe().TRACKS.some(track => track.collapsed)"))
        self.ev('target => fgNotifyFilter("chosen task", new Set([target + 1]))', target)
        helpers.click_timeline_tool(self.page, '[data-act=fhide]')
        self.assertEqual(self.ev("fgView('timeline').probe().TRACKS.filter(track => track.h > 0).map(track => track.id)"), [target])
        self.ev("document.querySelector('.tl-scroll').scrollTop = 0")
        point = self.ev('target => fgView("timeline").probe().xyOf(target)', target)
        self.page.mouse.click(*point)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), target)
        self.page.click('.tl-detail .tl-close')
        self.ev('fgNotifyFilter("", null)')
        self.assertEqual(self.ev("fgView('timeline').probe().TRACKS.filter(track => track.h > 0).length"), count)
        gap = self.ev('''target => {
            const probe = fgView('timeline').probe(), track = probe.TRACKS[target];
            const scroll = document.querySelector('.tl-scroll'), canvas = document.querySelector('.tl-canvas');
            scroll.scrollTop = track.y;
            const box = canvas.getBoundingClientRect();
            return [box.left + box.width / 2, box.top + track.h - 3];
        }''', target)
        self.page.mouse.click(*gap)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), -1)
        self.assertFalse(self.ev("fgView('timeline').probe().TRACKS.some(track => track.collapsed)"))
        self.page.mouse.click(gap[0], gap[1] + 3)
        self.assertTrue(self.ev('target => fgView("timeline").probe().TRACKS[target + 1].collapsed', target))
        self.assertFalse(self.ev('target => fgView("timeline").probe().TRACKS[target].collapsed', target))

    def test_cpu_only_overview_classifies_track_kinds_once_per_rebuild(self):
        count = 512
        self.open_range_data(['worker'] * count, starts=[0] * count, durations=[10000] * count,
                             columns={'task': list(range(count))},
                             extra={'tasks': [dict(pid=-1, comm=f'CPU {index}', kind=2, cpu=index)
                                              for index in range(count)]})
        result = self.ev('''async () => {
            const api = fgView('timeline'), canvas = document.querySelector('.tl-head-canvas');
            const before = canvas.toDataURL();
            window.kindReads = 0;
            for (const track of api.probe().TRACKS) {
                const value = track.kind;
                Object.defineProperty(track, 'kind', {configurable: true, get() { kindReads++; return value; }});
            }
            api.resize();
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            return {reads: kindReads, same: canvas.toDataURL() === before};
        }''')
        self.assertTrue(result['same'])
        self.assertLess(result['reads'], count * 5, 'overview repeatedly searched all track kinds')

    def test_cpu_navigation_reveals_scheduler_only_task_and_clears_stale_selection(self):
        tasks = [dict(pid=1, comm='calls', kind=0, cpu=0, runs=[[0, 3000, 0]]),
                 dict(pid=2, comm='scheduler-only', kind=0, cpu=0, runs=[[3000, 9000, 0]])]
        self.open_range_data(['early'], starts=[0], durations=[2000], extra={'tasks': tasks})
        self.ev("fgView('timeline').probe().select(0)")
        self.assertEqual(self.ev("fgView('timeline').probe().TRACKS[1].h"), 0)
        self.click_cpu_at(6000)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), -1)
        self.assertGreater(self.ev("fgView('timeline').probe().TRACKS[1].h"), 0)
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 1)
        self.assertIn('scheduler-only', self.page.locator('.an-task-table').inner_text())
        self.assertNotIn('early', self.page.locator('.tl-detail').inner_text())
        self.assertEqual(self.ev("fgView('timeline').probe().view()"), [0, 9000])

    def test_cpu_navigation_selects_innermost_real_nonzero_depth_call(self):
        tasks = [dict(pid=1, comm='nested', kind=0, cpu=0, runs=[[0, 9000, 0]])]
        self.open_range_data(['root', 'leaf'], starts=[0, 1000], durations=[9000, 2000], parents=[-1, 0],
                             columns={'depth': [3, 4]}, extra={'tasks': tasks})
        self.click_cpu_at(2000)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 1)
        self.click_cpu_at(3100)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 0)

    def open_tasks_data(self, count=230, mode='abs'):
        self.ev("fgSwitchView('text')")
        self.page.reload()
        tasks = [dict(pid=index + 1, comm=('=unsafe' if index == 0 else 'task_' + str(index).zfill(4)),
                      comms=['old_' + str(index)], kind=0, cpu=0,
                      runs=[[index * 1000, index * 1000 + 500, index % 4]]) for index in range(count)]
        tasks += [dict(pid=-1, comm='<identity only>', kind=3, cpu=None),
                  dict(pid=0, comm='idle', kind=1, cpu=0), dict(pid=-1, comm='cpu fallback', kind=2, cpu=0)]
        self.open_range_data(['root', 'child'], starts=[0, 100], durations=[1000, 200], parents=[-1, 0],
                             columns={'depth': [0, 1]}, mode=mode,
                             extra={'tasks': tasks, 'offcpu': [[1, 100, None, 0, 0, 1, 0, -1, 0, -1]],
                                    'wakeups': [[-1, 250000, 2, 0, -1, -1]],
                                    'chains': [[1, 0, 12, 0, 2, 1, 1]], 'errnames': {'12': 'ENOMEM'},
                                    'overhead': {'child': 10, 'self': 5, 'source': 'test'}})
        helpers.click_timeline_tool(self.page, '[data-act=an-tasks]')

    def test_tasks_paginate_export_all_and_preserve_focused_pager_on_refresh(self):
        self.open_tasks_data()
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 100)
        self.page.locator('[data-task-page="1"]').press('Enter')
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 100)
        self.assertEqual(self.ev("document.activeElement.dataset.taskPage"), '1')
        self.assertEqual(self.page.locator('.an-task-footer [role=status]').inner_text(), 'Tasks 101–200 of 233')
        rows = self.download_csv('[data-act=tasks-csv]')
        self.assertEqual(len(rows), 233)
        self.assertEqual(rows[0]['task'], "'=unsafe")
        self.assertEqual(rows[0]['calls'], '2')
        self.assertEqual(rows[0]['error_origins_including_minor'], '1')
        self.assertEqual(rows[1]['open_waits'], '1')
        self.assertEqual(rows[2]['end_ns'], '250000')
        self.assertEqual(rows[-3]['start_ns'], '')
        self.assertEqual(rows[-2]['cpus'], '0')
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 100)
        self.assertEqual(self.page.locator('.an-task-footer [role=status]').inner_text(), 'Tasks 101–200 of 233')
        self.assertEqual(self.download_csv('[data-act=tasks-csv]'), rows)
        self.page.locator('[data-task-page="1"]').press('Space')
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 33)
        self.assertTrue(self.page.locator('[data-task-page="1"]').is_disabled())
        self.page.locator('[data-task-page="-1"]').press('Space')
        self.assertEqual(self.page.locator('.an-task-footer [role=status]').inner_text(), 'Tasks 101–200 of 233')

    def test_tasks_literal_query_kind_shared_matches_and_navigation(self):
        self.open_tasks_data(count=12)
        self.page.fill('[data-task-query]', 'old_2')
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 1)
        self.assertIn('task_0002', self.page.locator('.an-task-table').inner_text())
        self.page.fill('[data-task-query]', 'CPU 3')
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 3)
        self.page.fill('[data-task-query]', '[')
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 0)
        self.page.click('[data-act=tasks-reset]')
        self.page.select_option('[data-task-kind]', '1')
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 1)
        self.assertTrue(self.page.locator('[data-task-show]').is_disabled())
        self.page.click('[data-act=tasks-reset]')
        self.ev("fgNotifyFilter('child only', new Set([2]))")
        self.page.check('[data-task-matching]')
        rows = self.download_csv('[data-act=tasks-csv]')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['matched_calls'], '1')
        self.assertEqual(rows[0]['calls'], '2')
        self.page.click('[data-act=tasks-reset]')
        self.page.locator('[data-task-show="2"]').click()
        self.assertGreater(self.ev("fgView('timeline').probe().TRACKS[2].h"), 0)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), -1)
        self.assertEqual(self.page.locator('.tl-detail-title').inner_text(), 'Tasks')

    def test_tasks_count_and_packed_exports_do_not_claim_nanosecond_measurements(self):
        for mode in ['count', 'compressed']:
            with self.subTest(mode=mode):
                self.open_tasks_data(count=4, mode=mode)
                rows = self.download_csv('[data-act=tasks-csv]')
                suffix = 'units' if mode == 'count' else 'ns'
                self.assertIn('span_' + suffix, rows[0])
                self.assertEqual(rows[0]['position_basis'], 'call_order' if mode == 'count' else 'packed_duration')
                self.assertIn('not CPU time', self.page.locator('.tl-detail').inner_text())

    def test_tasks_reuse_prepared_facts_and_keep_sort_keyboard_focus(self):
        self.open_tasks_data()
        self.ev("""() => {
            for (const task of fgView('timeline').probe().TRACKS) {
                task.runs[Symbol.iterator] = function* () { throw new Error('Task facts rescanned'); };
            }
        }""")
        self.page.locator('[data-task-sort=name]').press('Enter')
        self.assertEqual(self.ev("document.activeElement.dataset.taskSort"), 'name')
        self.assertEqual(self.page.locator('th[aria-sort=ascending]').count(), 1)
        self.assertEqual(len(self.download_csv('[data-act=tasks-csv]')), 233)
        self.page.fill('[data-task-query]', 'task_')
        self.page.type('[data-task-query]', '0')
        self.assertEqual(self.ev("document.activeElement.hasAttribute('data-task-query')"), True)
        self.assertEqual(len(self.download_csv('[data-act=tasks-csv]')), 229)
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        self.ev("fgNotifyFilter('child', new Set([2]))")
        self.page.check('[data-task-matching]')
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 0)
        self.page.fill('[data-task-query]', '')
        self.assertEqual(self.download_csv('[data-act=tasks-csv]')[0]['matched_calls'], '1')

    def test_tasks_compact_layout_preserves_controls_during_horizontal_scroll(self):
        tasks = [dict(pid=1, comm='<script>long_name_' + 'x' * 600, kind=0, cpu=0)]
        self.open_range_data(['root'], extra={'tasks': tasks})
        helpers.click_timeline_tool(self.page, '[data-act=an-tasks]')
        for theme in ['light', 'dark']:
            for width, height in [(320, 700), (390, 844), (800, 420), (1500, 900)]:
                with self.subTest(theme=theme, width=width, height=height):
                    self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.page.wait_for_timeout(60)
                    self.ev("document.querySelector('.tl-detail').scrollLeft = 5000")
                    metrics = self.ev("""() => {
                        const panel = document.querySelector('.tl-detail').getBoundingClientRect();
                        const head = document.querySelector('.an-task-head');
                        const tools = document.querySelector('.an-task-tools').getBoundingClientRect();
                        const footer = document.querySelector('.an-task-footer').getBoundingClientRect();
                        return [document.documentElement.scrollWidth, tools.left - panel.left, footer.left - panel.left,
                            head.getBoundingClientRect().left - panel.left, document.querySelector('.an-task-name').offsetHeight,
                            getComputedStyle(head).top, document.querySelector('.an-task-table th').style.top];
                    }""")
                    self.assertLessEqual(metrics[0], width)
                    self.assertLessEqual(abs(metrics[1]), 2)
                    self.assertLessEqual(abs(metrics[2]), 2)
                    self.assertLessEqual(abs(metrics[3]), 2)
                    self.assertLessEqual(metrics[4], 40)
                    if width <= 640 or height <= 600:
                        self.assertEqual(metrics[5:], ['auto', '0px'])
        self.assertEqual(self.page.locator('.an-task-table script').count(), 0)

    def test_window_panels_filter_overlapping_calls_and_adjust_self_not_positions(self):
        extra = {'resched': [[0, 0, 1000, 2, 1, 0], [0, 2000, 3000, 3, 0, 0]],
                 'sections': [[0, 0, 0, 1000, 0, 1, 0, 1, 2], [0, 0, 2000, 3000, 2, 2, 0, 3, 4],
                              [0, 1, 2000, 3000, 2, 2, 3, 3, 4]]}
        self.open_range_data(['inside', 'same_window', 'elsewhere'], starts=[0, 400, 2000],
                             durations=[400, 600, 1000], columns={'flags': [4, 0, 0]}, extra=extra)
        self.ev("fgNotifyFilter('inside only', new Set([1]))")
        for action, rows in [('latency', 'tr[data-win]'), ('sections', 'tr[data-sec]')]:
            with self.subTest(action=action):
                self.ev(f"fgView('timeline').probe().{action}()")
                self.assertEqual(self.page.locator(rows).count(), 1)
                self.assertIn('inside 400 ns', self.page.locator(rows).first.inner_text())
                self.assertNotIn('same_window', self.page.locator(rows).first.locator('td').nth(5).inner_text())
                raw = self.download_csv('[data-act=window-csv]')[0]
                helpers.click_timeline_tool(self.page, '[data-act=adj]')
                adjusted = self.download_csv('[data-act=window-csv]')[0]
                self.assertEqual(float(raw['top_1_self_estimate_ns']), 400)
                self.assertEqual(float(adjusted['top_1_self_estimate_ns']), 200)
                self.assertEqual(adjusted['self_duration_basis'], 'overhead_subtracted_estimate')
                self.assertEqual(raw['start_ns'], adjusted['start_ns'])
                self.assertEqual(raw['end_ns'], adjusted['end_ns'])
                self.assertEqual(raw['window_duration_ns'], adjusted['window_duration_ns'])
                self.assertEqual(raw['window_duration_basis'], 'timestamp_span')
                self.assertEqual(raw['filter'], 'inside only')
                if action == 'latency':
                    self.assertEqual(float(adjusted['irqoff_self_estimate_ns']), 200)
                else:
                    self.assertEqual(adjusted['ended_in'], 'same_window')
                    self.assertEqual(self.page.locator('[data-sk="0"]').inner_text(), 'irqs off (1)')
                    self.assertEqual(self.page.locator('[data-sk="1"]').inner_text(), 'preempt off (0)')
                helpers.click_timeline_tool(self.page, '[data-act=adj]')
        self.ev("fgNotifyFilter('no calls', new Set())")
        for action in ['latency', 'sections']:
            self.ev(f"fgView('timeline').probe().{action}()")
            self.assertIn('current filter', self.page.locator('.tl-detail').inner_text())
            self.assertTrue(self.page.locator('[data-act=window-csv]').is_disabled())

    def test_window_latency_overview_aggregates_functions_outside_each_top_three(self):
        names = ['a1', 'b1', 'c1', 'common', 'a2', 'b2', 'c2', 'common']
        starts = [0, 1100, 2200, 3300, 20000, 21100, 22200, 23300]
        self.open_range_data(names, starts=starts, durations=[1000, 900, 800, 700] * 2,
                             extra={'resched': [[0, 0, 5000, 0, 0, 0], [0, 20000, 25000, 0, 0, 0]]})
        self.ev("fgView('timeline').probe().latency()")
        self.assertEqual(self.page.locator('.an-blame .an-chip .k').first.inner_text(), 'common')
        self.assertIn('1.400 us', self.page.locator('.an-blame .an-chip').first.inner_text())
        self.assertNotIn('common', self.page.locator('tr[data-win]').first.inner_text())

    def test_window_panels_paginate_export_and_sort_without_reaggregating(self):
        count = 650
        extra = {'resched': [[0, index * 2000, index * 2000 + 1000, index % 4, 0, index % 3] for index in range(count)],
                 'sections': [[0, 0, index * 2000, index * 2000 + 1000, index, index, 0, index + 1, index + 1]
                              for index in range(count)]}
        self.open_range_data(['function'] * count, starts=[index * 2000 for index in range(count)], extra=extra)
        for action, selector, page_size, sort_attr in [('latency', 'tr[data-win]', 300, 'lsort'),
                                                     ('sections', 'tr[data-sec]', 200, 'ssort')]:
            with self.subTest(action=action):
                self.ev(f"fgView('timeline').probe().{action}()")
                self.assertEqual(self.page.locator(selector).count(), page_size)
                first = self.page.locator(selector).first.element_handle()
                self.page.click('[data-act=window-next]')
                self.assertFalse(first.evaluate('(node) => node.isConnected'))
                self.assertEqual(self.page.locator(selector).count(), page_size)
                exported = self.download_csv('[data-act=window-csv]')
                self.assertEqual(len(exported), count)
                self.ev("""() => {
                    window.windowAggregateWrites = 0;
                    const originalSet = Map.prototype.set;
                    Map.prototype.set = function(key, value) {
                        if (typeof key === 'number' && typeof value === 'number') window.windowAggregateWrites++;
                        return originalSet.call(this, key, value);
                    };
                }""")
                self.page.locator(f'th[data-{sort_attr}=start] button').press('Enter')
                self.assertEqual(self.ev('window.windowAggregateWrites'), 0)
                self.assertEqual(self.page.locator(selector).count(), page_size)
                self.assertEqual(self.page.locator(f'th[data-{sort_attr}=start]').get_attribute('aria-sort'), 'ascending')
                while self.page.locator('[data-act=window-next]').is_enabled():
                    self.page.click('[data-act=window-next]')
                self.assertEqual(self.page.locator(selector).count(), count % page_size)
                self.page.click('[data-act=window-prev]')
                self.assertEqual(self.page.locator(selector).count(), page_size)
        self.ev("fgNotifyFilter('last call', new Set([650]))")
        self.assertEqual(self.page.locator('tr[data-sec]').count(), 1)
        self.assertEqual(len(self.download_csv('[data-act=window-csv]')), 1)

    def test_window_panels_sort_preempt_irq_top_end_and_task_columns(self):
        windows = [[0, 0, 1000, 1, 1, 2], [0, 2000, 3000, 3, 1, 0], [0, 4000, 5000, 2, 0, 1]]
        self.open_range_data(['first', 'second', 'third'], starts=[0, 2000, 4000], durations=[100, 200, 300],
                             columns={'flags': [4, 4, 0]},
                             extra={'resched': windows,
                                    'sections': [[0, kind, raw[1], raw[2], index, index, raw[3], index + 1, index + 1]
                                                 for kind in [0, 1] for index, raw in enumerate(windows)]})
        self.ev("fgView('timeline').probe().latency()")
        for key, order in [('pre', [1, 2, 0]), ('irq', [1, 0, 2]), ('top', [2, 1, 0]), ('end', [1, 2, 0]), ('task', [0, 1, 2])]:
            with self.subTest(key=key):
                self.page.locator(f'th[data-lsort={key}] button').click()
                self.assertTrue(self.page.locator(f'th[data-lsort={key}] button').evaluate('(node) => node === document.activeElement'))
                rows = self.download_csv('[data-act=window-csv]')
                self.assertEqual([int(row['window_index']) for row in rows], order)
        self.ev("fgView('timeline').probe().sections()")
        for key, order in [('top', [2, 1, 0]), ('start', [0, 1, 2]), ('task', [0, 1, 2]), ('dur', [0, 1, 2])]:
            with self.subTest(section_sort=key):
                self.page.locator(f'th[data-ssort={key}] button').press('Enter')
                self.assertTrue(self.page.locator(f'th[data-ssort={key}] button').evaluate('(node) => node === document.activeElement'))
                rows = self.download_csv('[data-act=window-csv]')
                self.assertEqual([int(row['section_index']) for row in rows], order)
        self.page.locator('[data-sk="1"]').press('Space')
        self.page.locator('th[data-ssort=pre] button').press('Enter')
        self.assertEqual([int(row['section_index']) for row in self.download_csv('[data-act=window-csv]')], [4, 5, 3])

    def test_window_overlap_index_keeps_long_calls_zero_boundaries_and_skips_ghosts(self):
        self.open_range_data(['long', 'short', 'later', 'point'], starts=[0, 500, 2000, 5000],
                             durations=[10000, 100, 100, 0],
                             extra={'resched': [[0, 5000, 6000, 0, 0, 0]],
                                    'sections': [[0, 0, 5000, 6000, 0, 0, 0, 1, 1]]})
        for action in ['latency', 'sections']:
            self.ev(f"fgView('timeline').probe().{action}()")
            self.assertEqual(float(self.download_csv('[data-act=window-csv]')[0]['top_1_self_estimate_ns']), 1000)
        self.ev("fgNotifyFilter('point', new Set([4]))")
        self.assertEqual(len(self.download_csv('[data-act=window-csv]')), 1)
        self.assertEqual(self.download_csv('[data-act=window-csv]')[0]['top_1_function'], '')
        self.ev("fgSwitchView('text')")
        self.page.reload()
        self.open_range_data(['parent', 'child'], starts=[0, 1000], durations=[10000, 1000], parents=[-1, 0],
                             columns={'depth': [0, 3]},
                             extra={'resched': [[0, 0, 10000, 0, 0, 0]]})
        self.assertGreater(self.ev("fgView('timeline').probe().NA"), 2)
        self.ev("fgView('timeline').probe().latency()")
        row = self.download_csv('[data-act=window-csv]')[0]
        self.assertEqual(float(row['top_1_self_estimate_ns']) + float(row['top_2_self_estimate_ns']), 10000)

    def test_window_panels_preserve_untraced_intervals_until_a_call_filter_is_applied(self):
        self.open_range_data(['elsewhere'], starts=[10000], extra={'resched': [[0, 0, 1000, 0, 0, 0]],
                             'sections': [[0, 0, 0, 1000, -1, -1, 0, 1, -1]]})
        for action in ['latency', 'sections']:
            self.ev(f"fgView('timeline').probe().{action}()")
            self.assertEqual(len(self.download_csv('[data-act=window-csv]')), 1)
        self.ev("fgNotifyFilter('elsewhere', new Set([1]))")
        self.assertEqual(self.page.locator('tr[data-sec]').count(), 0)
        self.assertEqual(self.page.locator('[data-sk="0"]').inner_text(), 'irqs off (0)')
        self.ev("fgView('timeline').probe().latency()")
        self.assertEqual(self.page.locator('tr[data-win]').count(), 0)

    def test_window_csv_units_quoting_and_compact_actions(self):
        for mode in ['abs', 'rel', 'compressed', 'count']:
            if mode != 'abs':
                self.ev("fgSwitchView('text')")
                self.page.reload()
            self.open_range_data(['=SUM(1,2)'], mode=mode,
                                 extra={'resched': [[0, 0, 1000, 1, 0, 0]],
                                        'sections': [[0, 0, 0, 1000, 0, 0, 0, 1, 1]]})
            for action in ['latency', 'sections']:
                self.ev(f"fgView('timeline').probe().{action}()")
                row = self.download_csv('[data-act=window-csv]')[0]
                self.assertEqual(row['top_1_function'], "'=SUM(1,2)")
                self.assertEqual(row['time_mode'], mode)
                self.assertEqual(row['self_duration_basis'], 'call_order' if mode == 'count' else 'measured')
                self.assertEqual(row['window_duration_basis'], 'call_order' if mode == 'count' else 'packed_estimate' if mode == 'compressed' else 'timestamp_span')
                if mode in ['count', 'compressed']:
                    self.assertIn('not measured scheduling latency', self.page.locator('.tl-detail').inner_text())
                unit = 'units' if mode == 'count' else 'ns'
                self.assertEqual(float(row['window_duration_' + unit]), 1 if mode == 'count' else 1000)
                for width, height in [(390, 844), (320, 700), (800, 420), (1500, 900)]:
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.ev("document.querySelector('.tl-detail').scrollTop = 0")
                    self.page.wait_for_timeout(100)
                    for selector in ['[data-act=window-csv]', '.tl-close']:
                        bounds = self.page.locator(selector).bounding_box()
                        self.assertGreaterEqual(bounds['x'], 0)
                        self.assertLessEqual(bounds['x'] + bounds['width'], width)
                        self.assertGreaterEqual(bounds['y'], 0)
                        self.assertLessEqual(bounds['y'] + bounds['height'], height)
                    self.ev("() => { const detail = document.querySelector('.tl-detail'); detail.scrollLeft = detail.scrollWidth; }")
                    self.assertEqual(self.page.locator('[data-act=window-next], [data-act=window-prev]').count(), 0)
                    for selector in ['[data-act=window-csv]', '.tl-close']:
                        bounds = self.page.locator(selector).bounding_box()
                        self.assertGreaterEqual(bounds['x'], 0)
                        self.assertLessEqual(bounds['x'] + bounds['width'], width)
                    if width <= 640 or height <= 600:
                        self.assertEqual(self.page.locator('.an-window-head').evaluate('(node) => getComputedStyle(node).top'), 'auto')
                        self.assertTrue(self.page.locator('.an-window-table th').evaluate_all('(nodes) => nodes.every(node => getComputedStyle(node).top === "0px")'))
                    row = self.page.locator('tr[data-win], tr[data-sec]').first
                    row.scroll_into_view_if_needed()
                    bounds, detail = row.bounding_box(), self.page.locator('.tl-detail').bounding_box()
                    self.assertGreaterEqual(bounds['y'], detail['y'])
                    self.assertLessEqual(bounds['y'] + bounds['height'], detail['y'] + detail['height'])

    def test_window_latency_many_windows_does_not_spread_arguments(self):
        count = 150000
        self.open_range_data(['parent'], durations=[count * 1000],
                             extra={'resched': [[0, index * 1000, (index + 1) * 1000, 0, 0, 0] for index in range(count)]})
        self.ev("fgView('timeline').probe().latency()")
        self.assertEqual(self.page.locator('tr[data-win]').count(), 300)
        self.assertIn('150000', self.page.locator('.tl-detail-head').inner_text())

    def test_error_atomic_only_respects_filter_and_separates_callers(self):
        self.open_range_data(['worker', 'sleeper', 'other_worker', 'sleeper'], starts=[0, 100, 3000, 3100],
                             durations=[2000, 1000, 2000, 1000], parents=[-1, 0, -1, 2],
                             columns={'depth': [0, 1, 0, 1]}, extra={'atomic': [[1, 1], [3, 1]]})
        self.ev("fgView('timeline').probe().errors()")
        self.assertEqual(self.page.locator('tr.an-atomic').count(), 2)
        self.assertIn('2 atomic calls', self.page.locator('.tl-detail-head').inner_text())
        self.assertTrue(self.page.locator('[data-act=errors-csv]').is_disabled())
        rows = self.download_csv('[data-act=atomic-csv]')
        self.assertEqual([row['caller'] for row in rows], ['worker', 'other_worker'])
        self.page.locator('tr.an-atomic').nth(1).click()
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 3)
        self.assertEqual(self.page.locator('.tl-detail-title').inner_text(), 'Errors')
        self.ev("fgNotifyFilter('one sleeper', new Set([2]))")
        self.assertEqual(self.page.locator('tr.an-atomic').count(), 1)
        self.assertEqual(len(self.download_csv('[data-act=atomic-csv]')), 1)
        self.ev("fgNotifyFilter('no calls', new Set())")
        self.assertEqual(self.page.locator('tr.an-atomic').count(), 0)
        self.assertNotIn('2 atomic calls', self.page.locator('.tl-detail').inner_text())
        self.assertIn('current filter', self.page.locator('.tl-detail').inner_text())

    def test_error_occurrences_navigate_beyond_fifty_and_preserve_position(self):
        self.open_range_data(['origin'] * 60, extra={'chains': [[index, index, 2, index % 3, 1, 1, 0] for index in range(60)]})
        self.ev("fgView('timeline').probe().errors()")
        self.assertEqual(self.page.locator('tr[data-chains]').count(), 1)
        self.ev("() => { for (let index = 0; index < 51; index++) document.querySelector('tr[data-chains]').click(); }")
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 50)
        self.assertIn('51 / 60', self.page.locator('tr[data-chains]').inner_text())
        self.page.locator('tr[data-chains] [data-error-step="1"]').press('Space')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 51)
        self.page.locator('tr[data-chains] [data-error-step="-1"]').press('Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 50)
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        self.page.locator('th[data-esort=origin] button').press('Enter')
        self.assertIn('51 / 60', self.page.locator('tr[data-chains]').inner_text())
        self.page.locator('tr[data-chains] [data-error-step="1"]').press('Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 51)
        self.assertEqual(len(self.download_csv('[data-act=error-occurrences-csv]')), 60)
        self.assertEqual(self.page.locator('.tl-detail-title').inner_text(), 'Errors')

    def test_error_entry_classification_does_not_merge_syscalls_with_ordinary_roots(self):
        self.open_range_data(['open', 'origin', '__x64_sys_open', 'origin'], starts=[0, 100, 3000, 3100],
                             durations=[2000, 1000, 2000, 1000], parents=[-1, 0, -1, 2],
                             columns={'depth': [0, 1, 0, 1]},
                             extra={'chains': [[1, 1, 2, 0, 1, 1, 0], [3, 3, 2, 1, 2, 0, 0]]})
        self.ev("fgView('timeline').probe().errors()")
        self.assertEqual(self.page.locator('tr[data-chains]').count(), 2)
        self.assertEqual(self.page.locator('tr[data-chains] .tl-pill').count(), 1)
        rows = self.download_csv('[data-act=errors-csv]')
        self.assertEqual([row['entry'] for row in rows], ['open', 'open'])
        self.assertEqual([int(row['entry_is_syscall']) for row in rows], [0, 1])
        self.assertEqual([int(row['unverified_chains']) for row in rows], [0, 1])

    def test_error_all_columns_sort_real_fates_tasks_and_chain_lengths(self):
        names = ['entryA', 'entryB', 'entryC', 'entryB', 'entryC', 'entryC']
        tasks = [dict(pid=index + 1, comm='task' + str(index), kind=0, cpu=0) for index in range(3)]
        fates, lengths, errnos = [0, 1, 2, 1, 2, 2], [4, 2, 3, 2, 3, 3], [22, 5, 2, 5, 2, 2]
        self.open_range_data(names, columns={'task': [0, 0, 0, 1, 1, 2]},
                             extra={'tasks': tasks, 'chains': [[index, index, errnos[index], fates[index], lengths[index], 1, 0] for index in range(6)]})
        self.ev("fgView('timeline').probe().errors()")
        for key, expected in [('h', ['entryA', 'entryB', 'entryC']), ('user', ['entryB', 'entryA', 'entryC']),
                              ('t', ['entryC', 'entryB', 'entryA']), ('x', ['entryA', 'entryC', 'entryB']),
                              ('unknown', ['entryC', 'entryA', 'entryB']), ('errno', ['entryC', 'entryB', 'entryA']),
                              ('sys', ['entryA', 'entryB', 'entryC']), ('origin', ['entryA', 'entryB', 'entryC'])]:
            with self.subTest(key=key):
                self.page.locator(f'th[data-esort={key}] button').press('Enter')
                self.assertTrue(self.page.locator(f'th[data-esort={key}] button').evaluate('(node) => node === document.activeElement'))
                rows = self.download_csv('[data-act=errors-csv]')
                self.assertEqual([row['origin'] for row in rows], expected)
                self.assertTrue(all(int(row['chains']) == int(row['handled']) + int(row['to_user']) + int(row['unknown']) for row in rows))
        self.ev("fgNotifyFilter('last origin', new Set([6]))")
        row = self.download_csv('[data-act=errors-csv]')[0]
        self.assertEqual((int(row['chains']), int(row['unknown']), int(row['tasks'])), (1, 1, 1))

    def test_error_groups_paginate_export_all_and_reuse_counts(self):
        count = 650
        self.open_range_data(['origin_' + str(index).zfill(4) for index in range(count)],
                             extra={'chains': [[index, index, 2, 0, 1, 1, 0] for index in range(count)]})
        self.ev("fgView('timeline').probe().errors()")
        self.assertEqual(self.page.locator('tr[data-chains]').count(), 200)
        first = self.page.locator('tr[data-chains]').first.element_handle()
        self.page.locator('[data-act=errors-next]').press('Enter')
        self.assertTrue(self.page.locator('[data-act=errors-next]').evaluate('(node) => node === document.activeElement'))
        self.assertFalse(first.evaluate('(node) => node.isConnected'))
        self.assertEqual(self.page.locator('tr[data-chains]').count(), 200)
        self.assertEqual(len(self.download_csv('[data-act=errors-csv]')), count)
        occurrences = self.download_csv('[data-act=error-occurrences-csv]')
        self.assertEqual(len(occurrences), count)
        self.assertEqual(int(occurrences[-1]['origin_line']), count)
        writes = self.ev("""() => {
            let writes = 0;
            const original = Set.prototype.add;
            Set.prototype.add = function(value) { if (typeof value === 'number') writes++; return original.call(this, value); };
            document.querySelector('th[data-esort=origin] button').click();
            document.querySelector('[data-act=adj]').click();
            Set.prototype.add = original;
            return writes;
        }""")
        self.assertEqual(writes, 0)
        self.assertEqual(self.page.locator('tr[data-chains]').count(), 200)
        while self.page.locator('[data-act=errors-next]').is_enabled():
            self.page.click('[data-act=errors-next]')
        self.assertEqual(self.page.locator('tr[data-chains]').count(), 50)
        self.page.click('[data-act=errors-prev]')
        self.assertEqual(self.page.locator('tr[data-chains]').count(), 200)
        self.ev("fgNotifyFilter('last origin', new Set([650]))")
        self.assertEqual(self.page.locator('tr[data-chains]').count(), 1)
        self.assertEqual(len(self.download_csv('[data-act=errors-csv]')), 1)

    def test_error_atomic_groups_paginate_navigate_and_export_every_call(self):
        names = ['sleeper'] * 60 + ['sleeper_' + str(index) for index in range(250)]
        self.open_range_data(names, extra={'atomic': [[index, 1] for index in range(len(names))]})
        self.ev("fgView('timeline').probe().errors()")
        self.assertEqual(self.page.locator('tr.an-atomic').count(), 100)
        first = self.page.locator('tr.an-atomic').first.element_handle()
        self.page.locator('tr.an-atomic').first.locator('[data-error-step="-1"]').press('Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 59)
        self.ev("() => { for (let index = 0; index < 52; index++) document.querySelector('tr.an-atomic').click(); }")
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 51)
        self.page.locator('[data-act=atomic-next]').press('Enter')
        self.assertTrue(self.page.locator('[data-act=atomic-next]').evaluate('(node) => node === document.activeElement'))
        self.assertFalse(first.evaluate('(node) => node.isConnected'))
        self.assertEqual(self.page.locator('tr.an-atomic').count(), 100)
        rows = self.download_csv('[data-act=atomic-csv]')
        self.assertEqual(len(rows), len(names))
        self.assertEqual(len({row['slice_index'] for row in rows}), len(names))
        self.page.click('[data-act=atomic-next]')
        self.assertEqual(self.page.locator('tr.an-atomic').count(), 51)
        self.page.click('[data-act=atomic-prev]')
        self.page.click('[data-act=atomic-prev]')
        self.assertIn('52 / 60', self.page.locator('tr.an-atomic').first.inner_text())
        self.ev("fgNotifyFilter('last atomic', new Set([310]))")
        self.assertEqual(self.page.locator('tr.an-atomic').count(), 1)
        self.assertEqual(len(self.download_csv('[data-act=atomic-csv]')), 1)

    def test_error_minor_toggle_is_reachable_in_filtered_empty_state(self):
        self.open_range_data(['major', 'minor', 'minor'],
                             extra={'chains': [[0, 0, 2, 1, 2, 1, 0], [1, 1, 2, 0, 1, 1, 1], [2, 2, 2, 0, 1, 0, 1]]})
        self.ev("fgView('timeline').probe().errors()")
        self.assertEqual(len(self.download_csv('[data-act=error-occurrences-csv]')), 1)
        self.assertIn('Show 2 minor', self.page.locator('.an-minor').inner_text())
        self.ev("fgNotifyFilter('minor only', new Set([2, 3]))")
        self.assertEqual(self.page.locator('tr[data-chains]').count(), 0)
        self.assertTrue(self.page.locator('[data-act=errors-csv]').is_disabled())
        self.page.locator('[data-act=minor]').press('Space')
        self.assertTrue(self.page.locator('[data-act=minor]').is_checked())
        self.assertEqual(self.page.locator('tr[data-chains]').count(), 1)
        rows = self.download_csv('[data-act=error-occurrences-csv]')
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['minor'] == '1' and row['include_minor'] == '1' for row in rows))
        self.page.locator('[data-act=minor]').press('Space')
        self.assertEqual(self.page.locator('tr[data-chains]').count(), 0)

    def test_error_csv_units_adjusted_geometry_quoting_and_compact_controls(self):
        for mode in ['abs', 'count']:
            if mode != 'abs':
                self.ev("fgSwitchView('text')")
                self.page.reload()
            self.open_range_data(['=SUM(1,2)'], mode=mode,
                                 extra={'chains': [[0, 0, 2, 2, 1, 0, 0]], 'atomic': [[0, 1]]})
            self.ev("fgView('timeline').probe().errors()")
            row = self.download_csv('[data-act=error-occurrences-csv]')[0]
            unit = 'units' if mode == 'count' else 'ns'
            self.assertEqual(row['origin'], "'=SUM(1,2)")
            self.assertEqual(row['fate'], 'unknown')
            self.assertEqual(row['time_mode'], mode)
            self.assertEqual(float(row['origin_duration_' + unit]), 1 if mode == 'count' else 1000)
            if mode == 'abs':
                helpers.click_timeline_tool(self.page, '[data-act=adj]')
                adjusted = self.download_csv('[data-act=error-occurrences-csv]')[0]
                self.assertEqual(float(adjusted['origin_duration_ns']), 500)
                self.assertEqual(adjusted['duration_basis'], 'overhead_subtracted_estimate')
                self.assertEqual(row['origin_start_ns'], adjusted['origin_start_ns'])
                self.assertEqual(row['origin_end_ns'], adjusted['origin_end_ns'])
                self.assertEqual(float(self.download_csv('[data-act=atomic-csv]')[0]['duration_ns']), 500)
            else:
                self.assertEqual(row['duration_basis'], 'call_order')
            for width, height in [(320, 700), (390, 844), (800, 420), (1500, 900)]:
                self.page.set_viewport_size({'width': width, 'height': height})
                self.ev("() => { const detail = document.querySelector('.tl-detail'); detail.scrollTop = 0; detail.scrollLeft = 0; }")
                self.page.wait_for_timeout(100)
                for selector in ['[data-act=errors-csv]', '.tl-close']:
                    bounds = self.page.locator(selector).bounding_box()
                    self.assertGreaterEqual(bounds['x'], 0)
                    self.assertLessEqual(bounds['x'] + bounds['width'], width)
                    self.assertGreaterEqual(bounds['y'], 0)
                    self.assertLessEqual(bounds['y'] + bounds['height'], height)
                self.ev("() => { const detail = document.querySelector('.tl-detail'); detail.scrollLeft = detail.scrollWidth; }")
                for selector in ['[data-act=errors-csv]', '[data-act=error-occurrences-csv]', '[data-act=atomic-csv]', '.tl-close']:
                    bounds = self.page.locator(selector).bounding_box()
                    self.assertGreaterEqual(bounds['x'], 0)
                    self.assertLessEqual(bounds['x'] + bounds['width'], width)
                if width <= 640 or height <= 600:
                    self.assertEqual(self.page.locator('.an-error-head').evaluate('(node) => getComputedStyle(node).top'), 'auto')
                    self.assertTrue(self.page.locator('.tl-detail table.tl-sum th').evaluate_all('(nodes) => nodes.every(node => getComputedStyle(node).top === "0px")'))

    def test_range_replaces_analysis_state_and_resets_search_position(self):
        self.open_timeline()
        total = self.ev("fgView('timeline').probe().N")
        self.page.fill('.tl-search', '.*')
        self.page.press('.tl-search', 'Enter')
        for action in ('an-errors', 'an-lat', 'an-sec'):
            with self.subTest(action=action):
                helpers.click_timeline_tool(self.page, f'[data-act={action}]')
                self.drag_range()
                self.assertTrue(self.page.locator('.tl-detail-title').inner_text().startswith('Range '))
                self.assertEqual(self.page.locator('.tl-count').inner_text(), f'{total} calls')
                self.ev("fgNotifyFilter('', null)")
                self.assertTrue(self.page.locator('.tl-detail-title').inner_text().startswith('Range '))

    def test_analysis_highlights_sync_search_without_replacing_panel(self):
        self.open_timeline()
        total = self.ev("fgView('timeline').probe().N")
        self.page.fill('.tl-search', '.*')
        self.page.press('.tl-search', 'Enter')
        helpers.click_timeline_tool(self.page, '[data-act=an-errors]')
        self.page.locator('tr[data-chains]').first.click()
        selected = self.ev("fgView('timeline').probe().sel()")
        self.assertEqual(self.page.locator('.tl-count').inner_text(), f'{selected + 1} / {total} calls')
        self.assertEqual(self.page.locator('.tl-detail-title').inner_text(), 'Errors')
        self.ev("fgNotifyFilter('no calls', new Set())")
        self.assertTrue(self.page.locator('.tl-detail.show').is_visible())
        self.assertEqual(self.page.locator('.tl-detail-title').inner_text(), 'Errors')
        self.ev("fgNotifyFilter('', null)")
        self.ev("() => { const probe = fgView('timeline').probe(); probe.dist(probe.sName[19]); }")
        title = self.page.locator('.tl-detail-title').inner_text()
        self.page.locator('.an-list tr[data-slice]').last.click()
        selected = self.ev("fgView('timeline').probe().sel()")
        self.assertEqual(self.page.locator('.tl-count').inner_text(), f'{selected + 1} / {total} calls')
        self.assertEqual(self.page.locator('.tl-detail-title').inner_text(), title)

    def test_wait_panel_survives_refresh_with_retained_range(self):
        self.open_timeline()
        self.ev("fgView('timeline').probe().range(0, 1000000)")
        self.ev("() => { const probe = fgView('timeline').probe(); probe.wait(probe.TRACKS.find(track => track.off.length).off[0]); }")
        self.ev("fgNotifyFilter('', null)")
        self.assertTrue(self.page.locator('.tl-detail-title').inner_text().startswith('Wait chain'))

    def open_wait_data(self, count=125, mode='abs', offcpu=None, wakeups=None, caps=None, task_names=None, task_kinds=None):
        self.ev("fgSwitchView('text')")
        self.page.reload()
        names = task_names or ['waiter', '<b>source</b>']
        tasks = [dict(pid=index + 1, comm=name, kind=task_kinds[index] if task_kinds else 0, cpu=index)
                 for index, name in enumerate(names)]
        records = offcpu if offcpu is not None else [
            [index % len(tasks), (count - index) * 3000, (count - index) * 3000 + 1000,
             0, 0, -1, 0, 1, 0, -1] for index in range(count)]
        wakes = wakeups if wakeups is not None else [
            [(index + 1) % len(tasks), record[2] - 100, record[0], 0, -1, index]
            for index, record in enumerate(records[:-1])]
        extra = dict(tasks=tasks, offcpu=records, wakeups=wakes)
        if caps is not None:
            extra['caps'] = caps
        self.open_range_data(['waiting_function'] * len(tasks), mode=mode,
                             durations=[max(count * 4000, 1000000)] * len(tasks),
                             columns={'task': list(range(len(tasks)))}, extra=extra)
        self.ev("() => { const probe = fgView('timeline').probe(); probe.wait(probe.TRACKS[0].off[0]); }")

    def test_wait_chain_escapes_task_names_and_keeps_observed_root_without_sched(self):
        self.open_wait_data(count=2, caps={'sched_events': False})
        self.assertEqual(self.page.locator('tr[data-wait]').count(), 2)
        self.assertEqual(self.page.locator('tr[data-wait="0"] td').nth(3).locator('b').count(), 0)
        self.assertIn('<b>source</b>', self.page.locator('tr[data-wait="0"] td').nth(3).inner_text())
        self.assertIn('temporal', self.page.locator('.an-wait-hint').inner_text())
        self.open_wait_data(count=1, wakeups=[], caps={'sched_events': False})
        self.assertEqual(self.page.locator('tr[data-wait]').count(), 1)
        self.assertIn('fgcapture.sh --sched', self.page.locator('.an-wait-hint').inner_text())

    def test_wait_chain_many_intervals_resolves_only_requested_pages(self):
        self.open_wait_data(count=150000)
        self.assertEqual(self.page.locator('tr[data-wait]').count(), 100)
        self.assertIn('Showing 100+', self.page.locator('.an-wait-footer').inner_text())
        self.page.locator('[data-act=wait-more]').press('Enter')
        self.assertEqual(self.page.locator('tr[data-wait]').count(), 200)
        self.assertIn('Showing 200+', self.page.locator('.an-wait-footer').inner_text())
        self.assertEqual(self.page.locator('tr[data-wait]').last.get_attribute('data-wait'), '199')

    def test_wait_chain_paginates_exports_and_refreshes_without_rewalking(self):
        self.open_wait_data(count=225)
        self.assertEqual(self.page.locator('tr[data-wait]').count(), 100)
        first = self.page.locator('tr[data-wait]').first.element_handle()
        rows = self.download_csv('[data-act=wait-csv]')
        self.assertEqual(len(rows), 225)
        self.assertEqual(rows[0]['link_kind'], 'selected')
        self.assertEqual(rows[1]['link_kind'], 'temporal')
        self.assertEqual(int(rows[1]['prior_resume_to_wake_ns']), 2900)
        self.assertEqual(rows[-1]['terminal_reason'], 'wakeup-not-recorded')
        self.assertEqual(self.page.locator('tr[data-wait]').count(), 100)
        self.page.locator('[data-act=wait-more]').press('Enter')
        self.assertEqual(self.page.locator('tr[data-wait]').count(), 200)
        self.assertTrue(first.evaluate('(node) => node.isConnected'))
        self.assertTrue(self.page.locator('[data-act=wait-more]').evaluate('(node) => node === document.activeElement'))
        self.page.locator('[data-act=wait-more]').press('Enter')
        self.assertEqual(self.page.locator('tr[data-wait]').count(), 225)
        self.assertTrue(self.page.locator('[data-act=wait-more]').is_disabled())
        writes = self.ev("""() => {
            let writes = 0;
            const original = Set.prototype.add;
            Set.prototype.add = function(value) { if (typeof value === 'number') writes++; return original.call(this, value); };
            fgNotifyFilter('no calls', new Set());
            document.querySelector('[data-act=adj]').click();
            Set.prototype.add = original;
            return writes;
        }""")
        self.assertEqual(writes, 0)
        self.assertEqual(self.page.locator('tr[data-wait]').count(), 225)
        self.assertEqual(self.download_csv('[data-act=wait-csv]'), rows)

    def test_wait_chain_keyboard_navigation_preserves_panel(self):
        self.open_wait_data(count=3)
        self.page.locator('.an-wait-hint summary').press('Enter')
        self.assertTrue(self.page.locator('.an-wait-hint details').evaluate('(node) => node.open'))
        self.assertIn('Overhead', self.page.locator('.an-wait-hint').inner_text())
        self.page.locator('tr[data-wait="1"] button').press('Enter')
        self.assertTrue(self.page.locator('.tl-detail-title').inner_text().startswith('Wait chain'))
        start, end = self.ev("fgView('timeline').probe().view()")
        self.assertLessEqual(start, 6000)
        self.assertGreaterEqual(end, 7000)
        self.page.locator('tr[data-wait="2"] button').press('Space')
        self.assertTrue(self.page.locator('.tl-detail.show').is_visible())

    def test_wait_chain_reports_all_termination_reasons_and_gap_boundary(self):
        root = [0, 70000000, 80000000, 0, 0, -1, 0, 1, 0, -1]
        prior = [1, 24000000, 25000000, 0, 0, -1, 0, 1, 0, -1]
        cases = [
            ('boundary', [root, prior], [[1, 75000000, 0, 0, -1, 0]], 2, 'wakeup-not-recorded'),
            ('gap', [root, prior], [[1, 75000001, 0, 0, -1, 0]], 1, 'gap-exceeded'),
            ('interrupt', [root, prior], [[1, 75000000, 0, 2, -1, 0]], 1, 'interrupt-context'),
            ('no prior', [root], [[1, 75000000, 0, 0, -1, 0]], 1, 'no-prior-wait'),
            ('missing', [root], [], 1, 'wakeup-not-recorded'),
            ('cycle', [[0, 0, 0, 0, 0, -1, 0, 1, 0, -1]], [[0, 0, 0, 0, -1, 0]], 1, 'cycle'),
        ]
        for label, records, wakes, length, reason in cases:
            with self.subTest(label=label):
                self.open_wait_data(offcpu=records, wakeups=wakes)
                self.assertEqual(self.page.locator('tr[data-wait]').count(), length)
                self.assertIn('Stopped:', self.page.locator('.an-wait-footer').inner_text())
                self.assertEqual(self.download_csv('[data-act=wait-csv]')[-1]['terminal_reason'], reason)
        self.open_wait_data(count=2, task_kinds=[0, 1])
        self.assertEqual(self.download_csv('[data-act=wait-csv]')[0]['terminal_reason'], 'non-task-context')

    def test_wait_chain_indexes_unordered_ends_stable_ties_and_excludes_open_waits(self):
        records = [[0, 9000, 10000, 0, 0, -1, 0, 1, 0, -1],
                   [1, 0, 6000, 0, 0, -1, 0, 1, 0, -1],
                   [1, 0, 8000, 0, 0, -1, 0, 1, 0, -1],
                   [1, 0, 7000, 0, 0, -1, 0, 1, 0, -1],
                   [1, 0, 8000, 0, 0, -1, 0, 1, 0, -1],
                   [1, 0, None, 0, 0, -1, 0, -1, 0, -1]]
        self.open_wait_data(offcpu=records, wakeups=[[0, 9200, 0, 0, -1, 0], [1, 9500, 0, 0, -1, 0],
                                                   [0, 900000, 1, 2, -1, 5]])
        self.assertEqual(self.page.locator('tr[data-wait]').evaluate_all('(rows) => rows.map(row => +row.dataset.wait)'), [0, 2])
        self.ev("() => { const probe = fgView('timeline').probe(); probe.wait(probe.TRACKS[1].off[4]); }")
        row = self.download_csv('[data-act=wait-csv]')[0]
        self.assertEqual(row['end_status'], 'trace_end_lower_bound')
        self.assertEqual(row['wait_end_ns'], '')
        self.assertEqual(row['wake_latency_ns'], '')
        self.assertEqual(row['resume_cpu'], '')
        self.assertIn('≥', self.page.locator('tr[data-wait]').inner_text())

    def test_wait_chain_csv_formula_protection_and_count_mode_units(self):
        for mode in ['abs', 'count']:
            with self.subTest(mode=mode):
                self.open_wait_data(count=2, mode=mode, task_names=['=SUM(1,2)', '@source'])
                rows = self.download_csv('[data-act=wait-csv]')
                self.assertEqual(rows[0]['comm'], "'=SUM(1,2)")
                self.assertEqual(rows[0]['waker_comm'], "'@source")
                self.assertEqual(rows[0]['filter_scope'], 'all_tasks')
                self.assertEqual(rows[0]['duration_basis'], 'call_order' if mode == 'count' else 'recorded_timestamps')
                if mode == 'count':
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]['waited_units'], '1')
                    self.assertEqual(rows[0]['terminal_reason'], 'no-timestamps')
                    self.assertFalse(any(name.endswith('_ns') for name in rows[0]))
                    self.assertEqual(self.page.locator('.an-wait-table .err').count(), 0)
                    self.assertNotIn('ms', self.page.locator('.an-wait-hint').inner_text())

    def test_wait_chain_compact_layout_keeps_controls_and_rows_reachable(self):
        self.open_wait_data(count=3, task_names=['very_long_task_name_' * 20, 'source'])
        for theme in ['light', 'dark']:
            for width, height in [(1500, 900), (320, 700), (800, 420)]:
                with self.subTest(theme=theme, width=width):
                    self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.ev("() => { const panel = document.querySelector('.tl-detail'); panel.scrollTop = 0; panel.scrollLeft = panel.scrollWidth; }")
                    self.page.wait_for_timeout(100)
                    bounds = self.ev("""() => {
                        const panel = document.querySelector('.tl-detail').getBoundingClientRect();
                        const close = document.querySelector('.an-wait-head .tl-close').getBoundingClientRect();
                        const head = document.querySelector('.an-wait-head');
                        return {left: panel.left, right: panel.right, bottom: panel.bottom,
                                closeLeft: close.left, closeRight: close.right, closeBottom: close.bottom,
                                top: getComputedStyle(head).top, tableTop: document.querySelector('.an-wait-table th').style.top,
                                pageWidth: document.documentElement.scrollWidth};
                    }""")
                    self.assertGreaterEqual(bounds['closeLeft'], bounds['left'])
                    self.assertLessEqual(bounds['closeRight'], bounds['right'])
                    self.assertLessEqual(bounds['closeBottom'], bounds['bottom'])
                    self.assertLessEqual(bounds['pageWidth'], width)
                    self.assertLessEqual(self.ev("document.querySelector('tr[data-wait]').offsetHeight"), 48)
                    self.assertGreaterEqual(self.ev("document.querySelector('tr[data-wait] button').offsetWidth"), 140)
                    if width <= 640 or height <= 600:
                        self.assertEqual(bounds['top'], 'auto')
                        self.assertEqual(bounds['tableTop'], '0px')
                    self.assertFalse(self.page.locator('.an-wait-hint details').evaluate('(node) => node.open'))
                    self.page.locator('tr[data-wait="2"] button').press('Enter')
                    self.assertTrue(self.page.locator('.tl-detail.show').is_visible())
        self.page.locator('.an-wait-head .tl-close').click()
        self.assertFalse(self.page.locator('.tl-detail').is_visible())


    def test_range_pagination_sorting_and_cache(self):
        self.open_range_data([f'function_{index:04d}' for index in range(650)])
        self.ev("fgView('timeline').probe().range(0, 650000)")
        self.assertEqual(self.page.locator('.tl-range-table tbody tr[data-name]').count(), 300)
        first = self.page.locator('.tl-range-table tbody tr').first.element_handle()
        self.page.locator('[data-act=range-next]').click()
        self.assertFalse(first.evaluate('(node) => node.isConnected'))
        self.assertEqual(self.page.locator('.tl-range-table tbody tr[data-name]').count(), 300)
        self.ev("""() => {
            const originalSet = Map.prototype.set;
            window.rangeAggregationWrites = 0;
            Map.prototype.set = function(key, value) {
                if (value && typeof value.calls === 'number' && typeof value.incl === 'number') window.rangeAggregationWrites++;
                return originalSet.call(this, key, value);
            };
        }""")
        self.page.locator('.tl-range-table th[data-sort=name] button').click()
        self.assertEqual(self.ev('window.rangeAggregationWrites'), 0)
        self.assertEqual(self.page.locator('.tl-range-table tbody tr[data-name]').count(), 300)
        self.assertEqual(self.page.locator('.tl-range-table th[data-sort=name]').get_attribute('aria-sort'), 'ascending')
        self.page.locator('[data-act=range-next]').click()
        self.page.locator('[data-act=range-next]').click()
        self.assertEqual(self.page.locator('.tl-range-table tbody tr[data-name]').count(), 50)
        self.assertTrue(self.page.locator('[data-act=range-next]').is_disabled())
        self.page.locator('[data-act=range-prev]').click()
        self.assertEqual(self.page.locator('.tl-range-table tbody tr').first.get_attribute('data-name'), 'function_0300')
        self.ev("fgNotifyFilter('one call', new Set([650]))")
        self.assertEqual(self.page.locator('.tl-range-table tbody tr[data-name]').count(), 1)
        self.assertEqual(self.page.locator('.tl-range-table tbody tr').first.get_attribute('data-name'), 'function_0649')
        self.ev("fgNotifyFilter('no calls', new Set())")
        self.assertIn('No calls overlap', self.page.locator('.tl-detail').inner_text())
        self.assertTrue(self.page.locator('[data-act=range-csv]').is_disabled())

    def test_range_many_distinct_functions_does_not_spread_arguments(self):
        self.open_range_data([f'function_{index}' for index in range(150000)])
        self.ev("fgView('timeline').probe().range(0, 150000000)")
        self.assertEqual(self.page.locator('.tl-range-table tbody tr[data-name]').count(), 300)
        self.assertIn('150000 functions', self.page.locator('.tl-range-footer').inner_text())

    def download_csv(self, selector):
        with self.page.expect_download() as download_info:
            self.page.click(selector)
        with open(download_info.value.path(), encoding='utf-8-sig', newline='') as source:
            return list(csv.DictReader(source))

    def download_range_csv(self):
        return self.download_csv('[data-act=range-csv]')

    def open_distribution(self):
        self.ev("fgView('timeline').probe().dist(0)")
        self.page.wait_for_selector('.an-dist-tools')

    def test_distribution_call_sites_remain_reversible_and_page_without_resorting(self):
        self.open_range_data(['target'] * 80, callers=[f'caller_{index % 20:02d}' for index in range(80)])
        self.open_distribution()
        self.assertEqual(self.page.locator('tr[data-site]').count(), 12)
        first = self.page.locator('tr[data-site]').first.element_handle()
        self.page.click('[data-act=dist-sites-more]')
        self.assertTrue(first.evaluate('(node) => node.isConnected'))
        self.assertEqual(self.page.locator('tr[data-site]').count(), 20)
        self.ev("""() => {
            window.distributionSortLengths = [];
            const originalSort = Array.prototype.sort;
            Array.prototype.sort = function(...args) {
                window.distributionSortLengths.push(this.length);
                return originalSort.apply(this, args);
            };
        }""")
        self.page.locator('tr[data-site] button').first.press('Enter')
        self.assertEqual(self.page.locator('tr[data-site] button[aria-pressed=true]').count(), 1)
        self.assertIn('1–4 of 4', self.page.locator('.an-dist-count').inner_text())
        self.page.locator('tr[data-site] button[aria-pressed=true]').press('Space')
        self.assertIn('1–50 of 80', self.page.locator('.an-dist-count').inner_text())
        self.assertEqual(self.ev('window.distributionSortLengths'), [])

    def test_distribution_empty_site_can_be_cleared_after_filtering(self):
        self.open_range_data(['target'] * 8, callers=['caller_a'] * 4 + ['caller_b'] * 4)
        self.open_distribution()
        self.page.locator('tr[data-site="caller_a"] button').click()
        self.ev("fgNotifyFilter('only b', new Set([5, 6, 7, 8]))")
        self.assertEqual(self.page.locator('.an-list tr').count(), 0)
        self.assertTrue(self.page.locator('[data-act=dist-csv]').is_disabled())
        self.assertIn('No calls', self.page.locator('.tl-detail').inner_text())
        self.assertEqual(self.page.locator('.an-dist .tl-kv .v').all_inner_texts(), ['—'] * 6)
        self.page.click('[data-act=dist-reset-site]')
        self.assertEqual(self.page.locator('.an-list tr').count(), 4)
        rows = self.download_csv('[data-act=dist-csv]')
        self.assertEqual({row['caller'] for row in rows}, {'caller_b'})
        self.assertEqual({row['filter'] for row in rows}, {'only b'})

    def test_distribution_loads_and_exports_every_call_not_just_initial_rows(self):
        self.open_range_data(['=SUM(1,2)'] * 320, durations=list(range(1000, 1320)),
                             callers=['\tcaller,"\n函数'] * 320)
        self.open_distribution()
        self.assertEqual(self.page.locator('.an-list tr').count(), 50)
        first = self.page.locator('.an-list tr').first.element_handle()
        self.page.click('[data-act=dist-next]')
        self.assertFalse(first.evaluate('(node) => node.isConnected'))
        self.assertEqual(self.page.locator('.an-list tr').count(), 50)
        rows = self.download_csv('[data-act=dist-csv]')
        self.assertEqual(len(rows), 320)
        self.assertEqual([int(row['duration_ns']) for row in rows], list(range(1319, 999, -1)))
        self.assertEqual({row['function'] for row in rows}, {"'=SUM(1,2)"})
        self.assertEqual({row['caller'] for row in rows}, {"'\tcaller,\"\n函数"})
        self.assertEqual({row['duration_basis'] for row in rows}, {'measured'})
        self.assertEqual([int(row['trace_line']) for row in rows], list(range(320, 0, -1)))
        while self.page.locator('[data-act=dist-next]').is_enabled():
            self.page.click('[data-act=dist-next]')
        self.assertEqual(self.page.locator('.an-list tr').count(), 20)

    def test_distribution_histogram_bucket_filters_list_and_export_then_resets(self):
        self.open_range_data(['target'] * 240, durations=[10 ** (index // 60 + 1) for index in range(240)])
        self.open_distribution()
        options = self.page.locator('[data-act=dist-bucket] option').evaluate_all('(nodes) => nodes.map(node => node.value)')
        self.assertEqual(len(options), 5)
        last = options[-1]
        self.page.select_option('[data-act=dist-bucket]', last)
        self.assertIn('1–50 of 60', self.page.locator('.an-dist-count').inner_text())
        self.page.click('[data-act=dist-next]')
        self.assertEqual(self.page.locator('.an-list tr').count(), 10)
        rows = self.download_csv('[data-act=dist-csv]')
        self.assertEqual(len(rows), 60)
        for row in rows:
            self.assertEqual(float(row['duration_ns']), 10000)
            self.assertGreaterEqual(float(row['duration_ns']), float(row['bucket_lower_ns']))
            self.assertLess(float(row['duration_ns']), float(row['bucket_upper_ns']))
        self.page.click('[data-act=dist-reset-bucket]')
        self.assertIn('1–50 of 240', self.page.locator('.an-dist-count').inner_text())
        self.page.focus('[data-act=dist-bucket]')
        self.page.keyboard.press('End')
        self.page.keyboard.press('Enter')
        self.assertEqual(self.page.locator('[data-act=dist-bucket]').input_value(), last)
        self.page.click('[data-act=dist-reset-bucket]')
        canvas = self.page.locator('.an-hist')
        canvas.scroll_into_view_if_needed()
        bounds = canvas.bounding_box()
        bucket_count = int(last) + 1
        self.page.mouse.click(bounds['x'] + 40 + (int(last) + 0.5) * (bounds['width'] - 48) / bucket_count,
                              bounds['y'] + 50)
        self.assertEqual(self.page.locator('[data-act=dist-bucket]').input_value(), last)
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        self.assertEqual(self.page.locator('[data-act=dist-bucket]').input_value(), '-1')
        self.assertEqual(len(self.download_csv('[data-act=dist-csv]')), 240)
        self.page.select_option('[data-act=dist-bucket]', self.page.locator('[data-act=dist-bucket] option').last.get_attribute('value'))
        self.ev("fgNotifyFilter('first group', new Set(Array.from({length: 60}, (_, index) => index + 1)))")
        self.assertEqual(self.page.locator('[data-act=dist-bucket]').input_value(), '-1')
        self.assertEqual(len(self.download_csv('[data-act=dist-csv]')), 60)

    def test_distribution_quantiles_sites_filter_and_adjustment_share_one_population(self):
        self.open_range_data(['target'] * 4, durations=[1000, 2000, 5000, 10000], callers=['a', 'a', 'b', 'b'])
        self.open_distribution()
        values = self.ev("""() => Object.fromEntries([...document.querySelector('.an-dist .tl-kv').children]
            .filter(node => node.classList.contains('k')).map(node => [node.textContent, node.nextElementSibling.textContent]))""")
        self.assertEqual(values['p50'], '5.000 us')
        self.assertEqual(values['mean'], '4.500 us')
        self.assertEqual(self.page.locator('tr[data-site="a"] td').nth(2).inner_text(), '2.000 us')
        self.page.locator('tr[data-site="a"] button').click()
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        adjusted = self.download_csv('[data-act=dist-csv]')
        self.assertEqual([float(row['duration_ns']) for row in adjusted], [1000, 500])
        self.assertEqual([(float(row['start_ns']), float(row['end_ns'])) for row in adjusted], [(1000, 3000), (0, 1000)])
        self.assertEqual({row['duration_basis'] for row in adjusted}, {'overhead_subtracted_estimate'})
        self.assertEqual({row['call_site_filter'] for row in adjusted}, {'a'})
        self.ev("fgNotifyFilter('one call', new Set([1]))")
        self.assertEqual(len(self.download_csv('[data-act=dist-csv]')), 1)
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        self.assertEqual(float(self.download_csv('[data-act=dist-csv]')[0]['duration_ns']), 1000)

    def test_distribution_exports_truthful_units_in_each_time_mode(self):
        for mode in ('abs', 'rel', 'compressed', 'count'):
            with self.subTest(mode=mode):
                if mode != 'abs':
                    self.ev("fgSwitchView('text')")
                    self.page.reload()
                self.open_range_data(['target'] * 2, mode=mode)
                self.open_distribution()
                row = self.download_csv('[data-act=dist-csv]')[0]
                self.assertEqual(row['time_mode'], mode)
                if mode == 'count':
                    self.assertNotIn('duration_ns', row)
                    self.assertEqual(float(row['duration_units']), 1)
                    self.assertEqual(row['duration_basis'], 'call_order')
                else:
                    self.assertEqual(float(row['duration_ns']), 1000)
                    self.assertEqual(row['duration_basis'], 'measured')

    def test_distribution_histogram_resizes_and_repaints_after_theme_and_view_changes(self):
        self.open_range_data(['long_function_' * 15] * 4, callers=['long_caller_' * 20] * 2 + ['other'] * 2,
                             durations=[1000, 2000, 5000, 10000])
        self.open_distribution()
        self.page.locator('tr[data-site] button').first.click()
        for width, height in [(390, 844), (320, 700), (1500, 900)]:
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.page.wait_for_timeout(150)
                dimensions = self.ev("""() => {
                    const canvas = document.querySelector('.an-hist');
                    const bounds = canvas.getBoundingClientRect();
                    return [bounds.width, canvas.width, bounds.right, window.devicePixelRatio];
                }""")
                self.assertLessEqual(dimensions[2], width)
                self.assertLessEqual(abs(dimensions[1] - dimensions[0] * dimensions[3]), 1)
                close = self.page.locator('.tl-close').bounding_box()
                self.assertLessEqual(close['x'] + close['width'], width)
                self.assertLessEqual(self.page.locator('.an-dist-head').bounding_box()['height'], 160)
        before = self.ev("document.querySelector('.an-hist').toDataURL()")
        self.ev('toggleTheme()')
        self.page.wait_for_timeout(100)
        after = self.ev("document.querySelector('.an-hist').toDataURL()")
        self.assertNotEqual(before, after)
        self.ev("fgSwitchView('compare')")
        self.ev('toggleTheme()')
        self.ev("fgSwitchView('timeline')")
        self.page.wait_for_timeout(100)
        self.assertEqual(before, self.ev("document.querySelector('.an-hist').toDataURL()"))

    def test_range_csv_exports_all_rows_safely_in_sort_order(self):
        names = ['=SUM(1,2)', '+cmd', '-cmd', '@cmd', '\tcmd', ' quoted,"\n函数']
        names += [f'function_{index:04d}' for index in range(650)]
        self.open_range_data(names)
        self.ev("fgView('timeline').probe().range(0, 656000)")
        self.page.locator('.tl-range-table th[data-sort=name] button').click()
        rows = self.download_range_csv()
        self.assertEqual(len(rows), len(names))
        expected = ["'" + name if name in names[:5] else name for name in sorted(names)]
        self.assertEqual([row['function'] for row in rows], expected)
        self.assertEqual(rows[0]['duration_basis'], 'measured')
        self.assertEqual(rows[0]['range_end_ns'], '656000')
        self.assertEqual(rows[0]['time_mode'], 'abs')

    def test_range_csv_statistics_adjustment_filter_and_count_units(self):
        self.open_range_data(['parent', 'child', 'zero-start', 'zero-end'],
                             starts=[0, 2000, 4000, 6000], durations=[10000, 4000, 0, 0],
                             parents=[-1, 0, -1, -1])
        self.ev("fgView('timeline').probe().range(4000, 6000, 0)")
        rows = {row['function']: row for row in self.download_range_csv()}
        self.assertEqual(set(rows), {'parent', 'child', 'zero-start'})
        self.assertEqual(float(rows['parent']['self_estimate_ns']), 1200)
        self.assertEqual(float(rows['parent']['inclusive_estimate_ns']), 2000)
        self.assertEqual(float(rows['child']['inclusive_estimate_ns']), 2000)
        self.assertEqual(rows['parent']['max_call_ns'], '10000')
        self.assertEqual(rows['parent']['calls'], '0')
        self.assertEqual(rows['zero-start']['calls'], '1')
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        adjusted = {row['function']: row for row in self.download_range_csv()}
        self.assertEqual(float(adjusted['parent']['self_estimate_ns']), 600)
        self.assertEqual(adjusted['parent']['duration_basis'], 'overhead_subtracted_estimate')
        self.ev("fgNotifyFilter('=one call', new Set([2]))")
        filtered = self.download_range_csv()
        self.assertEqual([row['function'] for row in filtered], ['child'])
        self.assertEqual(filtered[0]['filter'], "'=one call")
        self.ev("fgSwitchView('text')")
        self.page.reload()
        self.open_range_data(['ordered_call'], mode='count')
        self.ev("fgView('timeline').probe().range(0, 1000)")
        row = self.download_range_csv()[0]
        self.assertNotIn('self_estimate_ns', row)
        self.assertEqual(float(row['self_estimate_units']), 1)
        self.assertEqual(float(row['range_end_units']), 1)
        self.assertEqual(row['duration_basis'], 'call_order')

    def test_range_compact_header_keeps_export_and_close_reachable(self):
        self.open_range_data(['wide_function_name_' * 8])
        self.ev("fgView('timeline').probe().range(0, 1000)")
        for width, height in [(390, 844), (320, 700), (800, 420)]:
            with self.subTest(width=width, height=height):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.page.wait_for_timeout(100)
                for selector in ('[data-act=range-csv]', '.tl-close'):
                    bounds = self.page.locator(selector).bounding_box()
                    self.assertGreaterEqual(bounds['x'], 0)
                    self.assertLessEqual(bounds['x'] + bounds['width'], width)
                self.assertTrue(self.ev("""() => {
                    const head = document.querySelector('.tl-range-head');
                    const header = document.querySelector('.tl-range-table th');
                    const style = getComputedStyle(head);
                    const pinned = style.position === 'sticky' && style.top !== 'auto' ? head.offsetHeight : 0;
                    return parseInt(header.style.top, 10) === pinned;
                }"""))
        self.page.click('.tl-close')
        self.assertFalse(self.page.locator('.tl-detail.show').count())

    def test_timeline_search_previous_starts_at_last_and_wraps(self):
        self.open_timeline()
        total = self.ev("fgView('timeline').probe().N")
        self.page.fill('.tl-search', '.*')
        self.page.press('.tl-search', 'Shift+Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), total - 1)
        self.assertEqual(self.page.locator('.tl-count').inner_text(), f'{total} / {total} calls')
        self.page.press('.tl-search', 'Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 0)
        self.assertEqual(self.page.locator('.tl-count').inner_text(), f'1 / {total} calls')

    def test_timeline_search_invalid_and_empty_results_do_not_reuse_old_hits(self):
        self.open_timeline()
        self.page.fill('.tl-search', '.*')
        self.page.press('.tl-search', 'Enter')
        selected = self.ev("fgView('timeline').probe().sel()")
        self.page.fill('.tl-search', '[')
        self.page.press('.tl-search', 'Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), selected)
        self.assertEqual(self.page.locator('.tl-search').get_attribute('aria-invalid'), 'true')
        self.assertEqual(self.page.locator('.tl-count').inner_text(), 'Invalid regex')
        self.assertTrue(self.page.locator('[data-act=searchnext]').is_disabled())
        self.page.fill('.tl-search', '^not_a_captured_function$')
        self.page.press('.tl-search', 'Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), selected)
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '0 calls')
        self.assertEqual(self.page.locator('.tl-search').get_attribute('aria-invalid'), 'false')
        self.assertTrue(self.page.locator('[data-act=searchprev]').is_disabled())
        self.page.press('.tl-search', 'Escape')
        self.assertEqual(self.page.locator('.tl-search').input_value(), '')
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '')

    def test_timeline_search_tests_function_names_once_and_cancels_pending_work(self):
        self.open_timeline()
        names = self.ev("""() => {
            const probe = fgView('timeline').probe();
            window.testedSearchNames = [];
            const originalTest = RegExp.prototype.test;
            RegExp.prototype.test = function(text) {
                if (this.source === '^__arm64_sys_openat$') window.testedSearchNames.push(text);
                return originalTest.call(this, text);
            };
            return [...new Set([...probe.sName.slice(0, probe.N)].map(index => probe.S[index]))];
        }""")
        self.page.fill('.tl-search', '^__arm64_sys_openat$')
        self.page.press('.tl-search', 'Enter')
        self.assertEqual(sorted(self.ev('window.testedSearchNames')), sorted(names))
        self.page.wait_for_timeout(300)
        self.page.press('.tl-search', 'Enter')
        self.assertEqual(len(self.ev('window.testedSearchNames')), len(names))
        self.ev("fgNotifyFilter('', null)")
        self.assertEqual(len(self.ev('window.testedSearchNames')), len(names))

    def test_timeline_search_tracks_shared_filter_and_refreshes_count(self):
        self.open_timeline()
        total = self.ev("fgView('timeline').probe().N")
        self.page.fill('.tl-search', '.*')
        self.page.press('.tl-search', 'Enter')
        self.ev("""() => {
            const data = JSON.parse(document.getElementById('tlData').textContent);
            fgNotifyFilter('one call', new Set([data.cols.line[data.n - 1]]));
        }""")
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '1 calls')
        self.page.press('.tl-search', 'Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), total - 1)
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '1 / 1 calls')
        self.ev("fgNotifyFilter('', null)")
        self.assertEqual(self.page.locator('.tl-count').inner_text(), f'{total} / {total} calls')
        self.ev("fgNotifyFilter('no calls', new Set())")
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '0 calls')
        self.assertTrue(self.page.locator('[data-act=searchnext]').is_disabled())

    def test_timeline_search_buttons_and_selection_counter(self):
        self.open_timeline()
        self.assertEqual(self.page.locator('[data-act=searchnext]').count(), 1)
        self.assertTrue(self.page.locator('[data-act=searchnext]').is_disabled())
        matches = self.ev("""() => {
            const probe = fgView('timeline').probe();
            return Array.from({length: probe.N}, (unused, index) => index)
                .filter(index => /^(mutex_unlock|__arm64_sys_openat)$/.test(probe.nameOf(index)));
        }""")
        self.assertGreaterEqual(len(matches), 2)
        self.page.fill('.tl-search', '^(mutex_unlock|__arm64_sys_openat)$')
        self.page.press('.tl-search', 'Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), matches[0])
        self.page.click('[data-act=searchnext]')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), matches[1])
        self.page.press('[data-act=searchprev]', 'Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), matches[0])
        self.assertEqual(self.ev('fgCurrentView()'), 'timeline')
        self.ev("index => fgView('timeline').probe().select(index)", matches[-1])
        self.assertEqual(self.page.locator('.tl-count').inner_text(), f'{len(matches)} / {len(matches)} calls')
        self.page.press('[data-act=searchnext]', 'Space')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), matches[0])
        self.ev("fgView('timeline').probe().select(1)")
        self.assertEqual(self.page.locator('.tl-count').inner_text(), f'{len(matches)} calls')
        self.page.click('[data-act=searchnext]')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), matches[1])

    def test_timeline_search_shortcuts_flush_pending_query_and_keep_input_focus(self):
        self.open_timeline()
        self.page.keyboard.press('/')
        self.assertTrue(self.ev("document.activeElement.matches('.tl-search')"))
        pending = self.ev("""() => {
            const input = document.querySelector('.tl-search');
            input.value = '  ^MUTEX_UNLOCK$  ';
            input.dispatchEvent(new Event('input', {bubbles: true}));
            input.blur();
            return document.querySelector('.tl-count').textContent;
        }""")
        self.assertEqual(pending, 'Searching…')
        self.page.keyboard.press('n')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 0)
        self.assertTrue(self.ev("document.activeElement.matches('.tl-canvas')"))
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '1 / 1 calls')
        self.page.keyboard.press('/')
        self.page.press('.tl-search', 'Escape')
        self.page.wait_for_timeout(300)
        self.assertEqual(self.page.locator('.tl-count').inner_text(), '')
        self.assertTrue(self.page.locator('[data-act=searchnext]').is_disabled())

    def test_timeline_control_enter_keeps_native_button_activation(self):
        self.open_timeline()
        self.ev("fgView('timeline').probe().select(0)")
        self.page.press('#viewHelpBtn', 'Enter')
        self.assertEqual(self.ev('fgCurrentView()'), 'timeline')
        self.assertTrue(self.page.locator('.tl-help').is_visible())

    def test_timeline_search_status_wrap_keeps_canvas_dimensions_in_sync(self):
        self.page.set_viewport_size({'width': 1400, 'height': 900})
        self.open_timeline()
        for query in ['.*', '^not_a_captured_function$', '']:
            with self.subTest(query=query):
                self.page.fill('.tl-search', query)
                self.page.wait_for_timeout(250)
                canvas, client = self.ev("""() => [
                    document.querySelector('.tl-canvas').getBoundingClientRect().height,
                    document.querySelector('.tl-scroll').clientHeight]
                """)
                self.assertAlmostEqual(canvas, client, delta=1)

    def test_compare_changed_only_covers_every_metric(self):
        baseline = [2, 100, 50, 70, 0]
        rows = [['unchanged'] + baseline + baseline]
        metrics = ['calls', 'inclusive', 'self', 'max', 'errors']
        for index, metric in enumerate(metrics):
            current = baseline.copy()
            current[index] += 1
            rows.append([metric] + baseline + current)
        self.open_compare(rows)
        names = self.page.locator('.cmp-table tbody td.l').all_text_contents()
        self.assertEqual(set(names), set(metrics))
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '5 of 6 functions')
        self.page.uncheck('.cmp-changed')
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '6 of 6 functions')

    def test_compare_empty_filter_has_explanation(self):
        self.open_compare()
        self.page.fill('.cmp-q', '^not_a_captured_function$')
        self.page.wait_for_function("document.querySelector('.cmp-count').textContent.startsWith('0 of ')")
        self.assertEqual(self.page.locator('.cmp-empty').count(), 1)
        self.assertTrue(self.page.locator('.cmp-empty').is_visible())
        self.assertIn('No functions match', self.page.locator('.cmp-empty').inner_text())
        self.page.fill('.cmp-q', '')
        self.page.wait_for_function("!document.querySelector('.cmp-count').textContent.startsWith('0 of ')")
        self.assertFalse(self.page.locator('.cmp-empty').is_visible())

    def test_compare_responsive_height_and_sticky_function_names(self):
        self.open_compare()
        for width, height in [(1280, 720), (800, 600), (390, 844)]:
            with self.subTest(width=width, height=height):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.page.wait_for_timeout(100)
                bounds = self.page.locator('.cmp-wrap').bounding_box()
                self.assertLessEqual(bounds['y'] + bounds['height'], height + 1)
                positions = self.ev("""() => {
                    const wrap = document.querySelector('.cmp-wrap');
                    wrap.scrollLeft = wrap.scrollWidth - wrap.clientWidth;
                    return [wrap.getBoundingClientRect().left,
                            document.querySelector('.cmp-table tbody td.l').getBoundingClientRect().left,
                            document.querySelector('.cmp-table th.l').getBoundingClientRect().left];
                }""")
                self.assertAlmostEqual(positions[0], positions[1], delta=2)
                self.assertAlmostEqual(positions[0], positions[2], delta=2)

    def test_compare_csv_exports_all_matches_and_pending_filter(self):
        rows = [[f'worker_{index:04d}', 2, 100, 50, 70, 0,
                 2, 200 + index, 100 + index, 120 + index, 0] for index in range(605)]
        rows.append(['other_function', 2, 100, 50, 70, 0, 2, 2000, 1500, 1700, 0])
        self.open_compare(rows)
        self.assertEqual(self.page.locator('.cmp-export').count(), 1)
        with self.page.expect_download() as pending:
            self.ev("""() => {
                const input = document.querySelector('.cmp-q');
                input.value = '^worker_';
                input.dispatchEvent(new Event('input', {bubbles: true}));
                document.querySelector('.cmp-export').click();
            }""")
        download = pending.value
        self.assertEqual(download.suggested_filename, 'funcgraph-compare-raw.csv')
        with open(download.path(), encoding='utf-8-sig', newline='') as stream:
            exported = list(csv.DictReader(stream))
        self.assertEqual(len(exported), 605)
        self.assertEqual(len(exported[0]), 19)
        self.assertEqual(exported[0]['function'], 'worker_0604')
        self.assertEqual(exported[-1]['function'], 'worker_0000')
        self.assertEqual(exported[0]['base_self_ns'], '50')
        self.assertEqual(exported[0]['current_self_ns'], '704')
        self.assertEqual(exported[0]['delta_self_ns'], '654')
        self.assertEqual(self.page.locator('.cmp-table tbody tr').count(), 401)
        self.assertEqual(self.page.locator('.cmp-count').inner_text(), '605 of 606 functions')

    def test_compare_csv_adjusted_values_and_escaping(self):
        names = ['worker,"quoted"\nnext_line', '=SUM(1,2)', '处理函数']
        rows = [[name, 2, 100, 50, 70, 0, 2, 150, 80, 90, 1] for name in names]
        adjusted = [[name, 2, 90, 40, 60, 0, 2, 130, 65, 80, 1] for name in names]
        self.open_compare(rows, adjusted)
        self.assertEqual(self.page.locator('.cmp-export').count(), 1)
        self.page.check('.cmp-adj input')
        self.assertIn('summary pills stay measured', self.page.locator('.cmp-note').inner_text())
        with self.page.expect_download() as pending:
            self.page.click('.cmp-export')
        download = pending.value
        self.assertEqual(download.suggested_filename, 'funcgraph-compare-adjusted.csv')
        with open(download.path(), encoding='utf-8-sig', newline='') as stream:
            exported = list(csv.DictReader(stream))
        self.assertEqual([row['function'] for row in exported], [names[0], "'" + names[1], names[2]])
        for row in exported:
            self.assertEqual(row['base_incl_ns'], '90')
            self.assertEqual(row['current_incl_ns'], '130')
            self.assertEqual(row['base_self_ns'], '40')
            self.assertEqual(row['delta_self_ns'], '25')
            self.assertEqual(row['delta_mean_ns'], '20')
            self.assertEqual(row['delta_err'], '1')

    def test_compare_invalid_regex_and_empty_results_disable_export(self):
        self.open_compare([['worker', 2, 100, 50, 70, 0, 2, 150, 80, 90, 1]])
        self.assertEqual(self.page.locator('.cmp-export').count(), 1)
        self.ev("""() => {
            const input = document.querySelector('.cmp-q');
            input.value = '[';
            input.dispatchEvent(new Event('input', {bubbles: true}));
            document.querySelector('.cmp-export').click();
        }""")
        self.assertTrue(self.page.locator('.cmp-export').is_disabled())
        self.assertEqual(self.page.locator('.cmp-q').get_attribute('aria-invalid'), 'true')
        self.assertTrue(self.page.locator('.cmp-filter-error').is_visible())
        self.assertIn('Invalid regular expression', self.page.locator('.cmp-filter-error').inner_text())
        self.assertEqual(self.page.locator('.cmp-table tbody tr').count(), 1)
        self.page.fill('.cmp-q', '^missing$')
        self.page.wait_for_function("document.querySelector('.cmp-count').textContent.startsWith('0 of ')")
        self.assertTrue(self.page.locator('.cmp-export').is_disabled())
        self.assertFalse(self.page.locator('.cmp-filter-error').is_visible())
        self.page.fill('.cmp-q', '^worker$')
        self.page.press('.cmp-q', 'Enter')
        self.assertFalse(self.page.locator('.cmp-export').is_disabled())

    def test_compare_load_more_reuses_sorted_rows_and_existing_nodes(self):
        rows = [[f'worker_{index:04d}', 2, 100, 50, 70, 0,
                 2, 200 + index, 100 + index, 120 + index, 0] for index in range(1205)]
        self.open_compare(rows)
        self.ev("""() => {
            window.firstCompareRow = document.querySelector('.cmp-table tbody tr');
            window.compareSortCalls = 0;
            const originalSort = Array.prototype.sort;
            Array.prototype.sort = function(...args) {
                const row = Array.isArray(this[0]) ? this[0] : this[0] && this[0].row;
                if (this.length === 1205 && row && row[0].startsWith('worker_')) window.compareSortCalls++;
                return originalSort.apply(this, args);
            };
        }""")
        self.page.click('.cmp-table .more')
        self.assertEqual(self.page.locator('.cmp-table tbody tr').count(), 1205)
        self.assertEqual(self.ev('window.compareSortCalls'), 0)
        self.assertTrue(self.ev("window.firstCompareRow === document.querySelector('.cmp-table tbody tr')"))

    def test_compare_cached_results_follow_every_sort_mode(self):
        self.open_compare([
            ['added', 0, 0, 0, 0, 0, 2, 200, 100, 150, 0],
            ['grown', 1, 100, 50, 100, 0, 5, 500, 250, 150, 0],
            ['shrunk', 10, 1000, 500, 150, 0, 4, 400, 200, 150, 0],
        ])
        self.page.click('.cmp-table th[data-k=calls]')
        expected_orders = {
            'absdiff': ['shrunk', 'grown', 'added'],
            'diff': ['grown', 'added', 'shrunk'],
            'pct': ['added', 'grown', 'shrunk'],
            'cur': ['grown', 'shrunk', 'added'],
            'base': ['shrunk', 'grown', 'added'],
        }
        for mode, expected in expected_orders.items():
            with self.subTest(mode=mode):
                self.page.click(f'.cmp-sortby button[data-v={mode}]')
                names = self.page.locator('.cmp-table tbody td.l[title]').evaluate_all(
                    'cells => cells.map(cell => cell.title)')
                self.assertEqual(names, expected)
        self.page.click('.cmp-table th[data-k=name]')
        names = self.page.locator('.cmp-table tbody td.l[title]').evaluate_all(
            'cells => cells.map(cell => cell.title)')
        self.assertEqual(names, ['added', 'grown', 'shrunk'])

    def test_compare_only_report_has_styled_controls_and_exports(self):
        self.k.run(helpers.fixture('pi4_sched'), '--compare', helpers.fixture('pi4_default'),
                   out='compare_only.html')
        self.page.goto('file://' + self.k.dir + '/compare_only.html', wait_until='load')
        self.page.click('.view-tab[data-view=compare]')
        self.assertEqual(self.page.locator('#tlData').count(), 0)
        self.assertEqual(self.ev("getComputedStyle(document.querySelector('.cmp-sortby')).display"), 'flex')
        self.assertEqual(self.ev("getComputedStyle(document.querySelector('.cmp-sortby button.on')).backgroundColor"),
                         'rgb(59, 111, 216)')
        self.assertEqual(self.ev("getComputedStyle(document.querySelector('.cmp-export')).borderRadius"), '6px')
        with self.page.expect_download() as pending:
            self.page.click('.cmp-export')
        with open(pending.value.path(), encoding='utf-8-sig', newline='') as stream:
            exported = list(csv.DictReader(stream))
        self.assertGreater(len(exported), 100)

    def test_timeline_error_navigation_and_jump_to_text(self):
        self.open_timeline()
        self.page.keyboard.press('e')
        self.page.wait_for_timeout(300)
        self.assertTrue(self.ev("document.querySelector('.tl-detail').classList.contains('show')"))
        self.assertIn('error', self.ev("document.querySelector('.tl-detail-head').textContent"))
        self.assertTrue(self.ev("document.activeElement.matches('.tl-canvas')"))
        self.page.keyboard.press('Enter')
        self.page.wait_for_timeout(300)
        self.assertFalse(self.ev("document.body.dataset.view === 'timeline'"))
        self.assertTrue(self.ev("!!document.querySelector('.line-container.tl-flash')"))

    def test_timeline_follows_text_filter(self):
        self.open_timeline()
        helpers.open_timeline_tools(self.page)
        self.page.fill('.tl-ftext', 'comm:sh')
        self.page.press('.tl-ftext', 'Enter')
        self.page.wait_for_timeout(400)
        count = self.ev("document.querySelector('.tl-fcount').textContent")
        self.assertRegex(count, r'^\d+ of \d+ calls$')
        helpers.click_timeline_tool(self.page, '.tl-filterbar [data-act=fclear]')
        self.page.wait_for_timeout(300)
        total = self.ev('fgView("timeline").probe().N')
        self.assertEqual(self.ev("document.querySelector('.tl-fcount').textContent"), 'All ' + str(total) + ' calls')
        self.assertIsNone(self.ev('fgFilterState.lines'))

    def test_detail_call_site_source(self):
        # the detail panel reuses the text view's resolved source block of the call site
        self.open_timeline()
        self.page.keyboard.press('e')
        self.page.wait_for_timeout(300)
        self.assertTrue(self.ev("!!document.querySelector('.tl-detail.show')"))
        # the fake kernel has no source tree: position link only, no empty source block
        self.assertFalse(self.ev("!!document.querySelector('.tl-cs')"))
        at = self.ev("(document.querySelector('.tl-cs-at .tl-link') || {}).textContent || ''")
        self.assertRegex(at, r'\.[ch]:\d+$')
        self.assertTrue(self.ev("(document.querySelector('.tl-cs-at a') || {}).href || ''").startswith('https://example.org/src/'))

    def test_timeline_tab_has_no_counter_and_header_explains_recorded_calls(self):
        self.assertEqual(self.page.locator('[data-view=timeline]').inner_text(), 'Timeline')
        count = self.ev("fgData.read('tlData').n")
        self.assertEqual(self.page.locator('.header-calls').inner_text(), f'{count:,} recorded calls')
        self.assertIn('Not a count of unique functions or trace lines', self.page.locator('.header-calls').get_attribute('title'))
        self.open_timeline()
        helpers.open_timeline_tools(self.page)
        self.page.fill('.tl-ftext', 'no such function')
        self.page.press('.tl-ftext', 'Enter')
        self.assertEqual(self.page.locator('.header-calls').inner_text(), f'{count:,} recorded calls')

    def open_tail_jump(self, evidence=None, depth=1):
        extra = {'tail_calls': evidence} if evidence else None
        self.open_range_data(['parent', 'child'], starts=[0, 100], durations=[1000, 600], parents=[-1, 0],
                             callers=['root+0x1/0x20', 'root+0x1/0x20'], extra=extra,
                             columns=dict(depth=[0, depth], flags=[0, 1 << 19]))
        self.ev("fgView('timeline').probe().select(1)")

    def test_caller_mismatch_does_not_assert_a_missing_frame_or_tail_call(self):
        self.open_tail_jump()
        detail = self.page.locator('.tl-detail')
        self.assertIn('Cause unresolved', detail.inner_text())
        self.assertIn('--tail-calls', detail.inner_text())
        self.assertNotIn('untraced frame between', detail.inner_text())
        self.assertEqual(detail.locator('.tl-tail-tag').count(), 0)

    def test_static_tail_jump_markers_sources_csv_and_geometry_are_truthful(self):
        evidence = dict(version=1, status='available', trace_build_id_verified=False, pairs={
            '0:-1:1:-1': dict(status='supported', reason='direct_external_jump', sites=[
                dict(symbol='parent+0x14/0x20', address='0x1014', mnemonic='jmp', target='0x2000',
                     source=['kernel/tail.c', 7, 'https://example.org/tail.c#L7']),
                dict(symbol='parent+0x18/0x20', address='0x1018', mnemonic='jmp', target='0x2000',
                     source=['kernel/tail.c', 8, 'javascript:alert(1)']),
            ])})
        self.open_tail_jump(evidence)
        original = self.ev("JSON.stringify(fgData.read('tlData'))")
        for theme, width in product(['light', 'dark'], [390, 1500]):
            with self.subTest(theme=theme, width=width):
                self.page.set_viewport_size(dict(width=width, height=900))
                self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                tag = self.page.locator('[data-context-list=stack] .tl-tail-tag')
                self.assertEqual(tag.count(), 1)
                self.assertIn('Static evidence, not a captured branch', tag.get_attribute('title'))
                self.assertIn('trace/binary version is not verified', tag.get_attribute('title'))
                self.assertEqual(self.page.locator('a[href^="javascript:"]').count(), 0)
                self.assertEqual(self.page.locator('a[href="https://example.org/tail.c#L7"]').count(), 1)
                self.assertIn('actual branch site not captured', self.page.locator('.tl-detail').inner_text())
                facts = self.ev("""() => {
                    const model = fgView('timeline').probe();
                    return {count:model.N, parent:Array.from(model.sParent).slice(0, model.N),
                        start:Array.from(model.sStart).slice(0, model.N), end:Array.from(model.sEnd).slice(0, model.N)};
                }""")
                self.assertEqual(facts, dict(count=2, parent=[-1, 0], start=[0, 100], end=[1000, 700]))
        exported = self.download_csv('[data-context-export=stack]')
        self.assertEqual(exported[-1]['relationship'], 'tail_jump_evidence')
        self.assertEqual(exported[-1]['relationship_basis'], 'supplied_binary_static')
        self.assertEqual(exported[-1]['trace_binary_version_verified'], 'false')
        self.assertIn('parent+0x18/0x20', exported[-1]['tail_jump_sites'])
        self.page.context.route('https://example.org/**', lambda route: route.fulfill(body='tail source'))
        with self.page.expect_popup() as pending:
            self.page.locator('a[href="https://example.org/tail.c#L7"]').click()
        pending.value.close()
        self.assertEqual(self.ev("JSON.stringify(fgData.read('tlData'))"), original)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 1)

    def test_static_tail_evidence_does_not_bridge_missing_depths_or_unknown_versions(self):
        evidence = dict(version=1, pairs={'0:-1:1:-1': dict(status='supported', reason='direct_external_jump',
                        sites=[dict(symbol='parent+0x14/0x20', mnemonic='jmp', address='0x1014')])})
        self.open_tail_jump(evidence, depth=2)
        self.assertEqual(self.page.locator('.tl-tail-tag').count(), 0)
        self.assertIn('recorded depth gap', self.page.locator('.tl-detail').inner_text())
        self.ev('localStorage.clear()')
        self.page.goto(self.url, wait_until='load')
        evidence['version'] = 2
        self.open_tail_jump(evidence)
        self.assertEqual(self.page.locator('.tl-tail-tag').count(), 0)
        self.assertIn('unsupported evidence version', self.page.locator('.tl-detail').inner_text())

    def test_call_stack_source_links_are_separate_native_links_without_text_dom(self):
        self.open_timeline()
        selected = self.ev('''() => {
            document.getElementById('content').replaceChildren();
            const data = fgData.read('tlData');
            const index = data.cols.parent.findIndex(parent => parent >= 0);
            fgView('timeline').probe().select(index);
            return index;
        }''')
        section = self.page.locator('[data-context-list=stack]')
        rows = section.locator('[data-context-kind=traced]')
        self.assertGreaterEqual(rows.count(), 2)
        self.assertEqual(rows.count(), section.locator('.tl-context-source a').count())
        self.assertEqual(section.locator('button a').count(), 0)
        self.assertIn('function entry, not the calling line', rows.last.locator('.tl-context-source a').get_attribute('title'))
        self.assertIn('Call site inside this frame', rows.first.locator('.tl-context-source a').get_attribute('title'))
        self.assertEqual(section.locator('.tl-source-kind').count(), 0)
        exported = self.download_csv('[data-context-export=stack]')
        self.assertEqual(exported[-1]['source_kind'], 'function_entry')
        self.assertEqual(exported[0]['source_kind'], 'call_site')
        self.assertTrue(all(row['source_url'].startswith('https://example.org/src/') for row in exported))
        self.page.context.route('https://example.org/**', lambda route: route.fulfill(body='source fixture'))
        with self.page.expect_popup() as popup:
            rows.first.locator('.tl-context-source a').press('Enter')
        popup.value.wait_for_load_state()
        self.assertIn('kernel/fake.c', popup.value.url)
        popup.value.close()
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), selected)

    def test_call_stack_columns_align_without_indentation_and_keep_line_numbers(self):
        names = ['root', 'long_function_' * 18, 'leaf']
        sources = {'functions': {f'{index}:-1': ['kernel/' + 'long_directory/' * 18 + f'frame{index}.c', 100 + index,
                                               f'https://example.org/frame{index}#L{100 + index}']
                                 for index in range(3)}, 'callers': {}}
        self.open_range_data(names, starts=[0, 100, 200], durations=[100000, 80000, 1000], parents=[-1, 0, 1],
                             columns={'depth': [0, 1, 2]}, extra={'sources': sources})
        self.ev("fgView('timeline').probe().select(2)")
        for theme in ['light', 'dark']:
            for width in [320, 390, 800, 1500]:
                with self.subTest(theme=theme, width=width):
                    self.page.set_viewport_size({'width': width, 'height': 900})
                    self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                    rows = self.ev("""() => Array.from(document.querySelectorAll('[data-context-list=stack] .tl-context-row'), row => {
                        const bounds = selector => row.querySelector(selector).getBoundingClientRect();
                        const frame = row.getBoundingClientRect();
                        const name = bounds('.tl-context-text'), value = bounds('.tl-context-value');
                        const source = bounds('.tl-context-source'), line = bounds('.tl-source-line');
                        const file = row.querySelector('.tl-source-file');
                        return {padding:getComputedStyle(row).paddingLeft, nameLeft:name.left, nameHeight:name.height,
                            nameRight:name.right, valueRight:value.right, sourceLeft:source.left, sourceTop:source.top,
                            nameTop:name.top, lineRight:line.right, frameRight:frame.right, lineWidth:line.width,
                            fileClipped:file.scrollWidth > file.clientWidth, sourceTitle:file.parentElement.title};
                    })""")
                    self.assertEqual(len(rows), 3)
                    for row in rows:
                        self.assertEqual(row['padding'], '0px')
                        self.assertAlmostEqual(row['nameLeft'], rows[0]['nameLeft'], delta=0.1)
                        self.assertAlmostEqual(row['valueRight'], rows[0]['valueRight'], delta=0.1)
                        self.assertAlmostEqual(row['sourceLeft'], rows[0]['sourceLeft'], delta=0.1)
                        self.assertLess(row['nameRight'], row['sourceLeft'])
                        self.assertAlmostEqual(row['nameTop'], row['sourceTop'], delta=3)
                        self.assertLessEqual(row['lineRight'], row['frameRight'])
                        self.assertGreater(row['lineWidth'], 0)
                        self.assertLessEqual(row['nameHeight'], 30)
                        self.assertTrue(row['fileClipped'])
                        self.assertIn('Resolved function entry', row['sourceTitle'])
                    self.assertLessEqual(rows[0]['frameRight'], width)
                    self.assertEqual(self.page.locator('[data-context-list=stack] .tl-source-line').all_text_contents(),
                                     [':100', ':101', ':102'])

    def test_call_stack_mixed_frame_types_share_columns_for_long_values_and_larger_fonts(self):
        names = ['__root', '__child', 'default_wake_function']
        self.open_range_data(names, starts=[0, 100, 200],
                             durations=[12345678900000, 123456789000, 3000], parents=[-1, 0, 1],
                             callers=[None, '__root+0x1/0x20', '__child+0x1/0x20'],
                             columns={'depth': [3, 6, 7]},
                             extra={'inline': {'3': [['__root', 'a.c', 10], ['__inline_jgp', 'b.h', 20]]}})
        self.ev("fgView('timeline').probe().select(2)")
        section = self.page.locator('[data-context-list=stack]')
        self.assertEqual(section.locator('.tl-context-row').count(), 7)
        for theme, width, size in product(['light', 'dark'], [390, 1500], [11.5, 14, 18]):
            with self.subTest(theme=theme, width=width, size=size):
                self.page.set_viewport_size(dict(width=width, height=900))
                self.ev('theme => document.documentElement.dataset.theme = theme', theme)
                self.ev('size => document.querySelector("[data-context-list=stack] .tl-crumbs").style.fontSize = size + "px"', size)
                facts = section.evaluate('''section => Array.from(section.querySelectorAll('.tl-context-row'), row => {
                    const name = row.querySelector('.tl-context-text'), value = row.querySelector('.tl-context-value');
                    return {left:name.getBoundingClientRect().left, valueRight:value.getBoundingClientRect().right,
                        clipped:value.scrollWidth > value.clientWidth, name:name.textContent};
                })''')
                for row in facts:
                    self.assertAlmostEqual(row['left'], facts[0]['left'], delta=0.1)
                    self.assertAlmostEqual(row['valueRight'], facts[0]['valueRight'], delta=0.1)
                    self.assertFalse(row['clipped'])
                self.assertIn('__inline_jgp', [row['name'] for row in facts])
                self.assertEqual(facts[-1]['name'], 'default_wake_function')

    def test_call_stack_text_does_not_clip_underscores_or_descenders(self):
        self.open_range_data(['__jgp_y', '__jgp_y'], starts=[0, 100], durations=[10000, 1000], parents=[-1, 0],
                             callers=[None, '__jgp_y+0x1/0x20'], columns={'depth': [0, 1]},
                             extra={'inline': {'1': [['__jgp_y', 'a.c', 10], ['__jgp_y', 'b.h', 20]]}})
        self.ev("fgView('timeline').probe().select(1)")
        self.page.mouse.move(0, 0)
        for size, family in product([11.5, 14, 18], ['monospace', 'Liberation Mono', 'DejaVu Sans Mono']):
            with self.subTest(size=size, family=family):
                self.ev('''({size, family}) => {
                    const list = document.querySelector('[data-context-list=stack] .tl-crumbs');
                    list.style.fontSize = size + 'px'; list.style.fontFamily = family;
                }''', dict(size=size, family=family))
                section = self.page.locator('[data-context-list=stack]')
                clips = section.locator('.tl-context-text').evaluate_all('''nodes => nodes.map(node => {
                    const rect = node.getBoundingClientRect();
                    return {x:Math.floor(rect.left - 3), y:Math.floor(rect.top - 3),
                        width:Math.ceil(rect.width + 6), height:Math.ceil(rect.height + 6)};
                })''')
                before = [self.page.screenshot(clip=clip) for clip in clips]
                reference = self.page.add_style_tag(content='''
                    .tl-context-select, .tl-context-text {overflow:visible}
                    .tl-context-text {-webkit-line-clamp:unset}
                ''')
                after = [self.page.screenshot(clip=clip) for clip in clips]
                reference.evaluate('node => node.remove()')
                self.assertEqual(before, after)

    def test_call_stack_unresolved_sources_are_explicit_and_unsafe_urls_stay_text(self):
        self.open_range_data(['root', 'leaf'], starts=[0, 1], durations=[10000, 1000], parents=[-1, 0],
                             columns={'depth': [0, 1]},
                             extra={'sources': {'functions': {'0:-1': ['<b>root.c</b>', 10, 'javascript:alert(1)']}, 'callers': {}}})
        self.ev("fgView('timeline').probe().select(1)")
        section = self.page.locator('[data-context-list=stack]')
        self.assertEqual(section.locator('.tl-context-source').first.text_content(), '<b>root.c</b>:10')
        self.assertIn('Source unavailable', section.inner_text())
        self.assertEqual(section.locator('.tl-context-source a, .tl-context-source b').count(), 0)

    def test_recorded_navigation_repeated_child_and_parent_follow_each_level(self):
        self.open_range_data(['root', 'branch', 'leaf'], starts=[0, 1, 2],
                             durations=[10000, 8000, 1000], parents=[-1, 0, 1],
                             columns={'depth': [0, 1, 2]})
        self.ev("fgView('timeline').probe().select(0)")
        self.assertEqual(self.page.locator('[data-call-nav]').evaluate_all(
            'buttons => buttons.map(button => button.dataset.callNav)'), ['parent', 'child', 'previous', 'next'])
        for expected in [1, 2]:
            self.page.locator('[data-call-nav=child]').press('Enter')
            self.assertEqual(self.ev("fgView('timeline').probe().sel()"), expected)
        self.assertTrue(self.page.locator('[data-call-nav=child]').is_disabled())
        self.assertTrue(self.page.locator('.tl-canvas').evaluate('node => node === document.activeElement'))
        for expected in [1, 0]:
            self.page.locator('[data-call-nav=parent]').press('Space')
            self.assertEqual(self.ev("fgView('timeline').probe().sel()"), expected)
        self.assertTrue(self.page.locator('[data-call-nav=parent]').is_disabled())
        self.assertTrue(self.page.locator('.tl-canvas').evaluate('node => node === document.activeElement'))

    def test_recorded_navigation_branching_is_explicit_and_refresh_keeps_targets(self):
        self.open_range_data(['root', 'first_leaf', 'branch', 'other_leaf', 'deep_branch', 'last_leaf'],
                             starts=[0, 1, 2, 3, 4, 5], durations=[10000, 0, 8000, 0, 6000, 1000],
                             parents=[-1, 0, 0, 2, 2, 4], columns={'depth': [0, 1, 1, 2, 2, 3]})
        self.ev("fgView('timeline').probe().select(0)")
        self.page.locator('[data-call-nav=child]').press('Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 1)
        self.assertTrue(self.page.locator('[data-call-nav=child]').is_disabled())
        for action, expected in [('next', 2), ('child', 3), ('next', 4), ('child', 5)]:
            self.page.locator(f'[data-call-nav={action}]').press('Enter')
            self.assertEqual(self.ev("fgView('timeline').probe().sel()"), expected)
        for expected in [4, 2, 0]:
            self.page.locator('[data-call-nav=parent]').press('Enter')
            self.assertEqual(self.ev("fgView('timeline').probe().sel()"), expected)
        self.ev("fgView('timeline').probe().select(4)")
        self.page.locator('[data-call-nav=parent]').press('Enter')
        self.page.locator('[data-call-nav=child]').press('Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 3)
        targets = self.page.locator('[data-call-nav]').evaluate_all('buttons => buttons.map(button => button.title)')
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        self.ev("fgView('timeline').probe().inline(true)")
        self.ev("fgNotifyFilter('keep selected', new Set([5]))")
        self.assertEqual(self.page.locator('[data-call-nav]').evaluate_all('buttons => buttons.map(button => button.title)'), targets)
        self.assertEqual(self.page.locator('[data-call-nav]').count(), 4)

    def test_stack_sources_distinguish_inline_sites_entries_and_mismatched_parents(self):
        sources = {'functions': {'0:-1': ['root.c', 1, 'https://example.org/root#L1'],
                                 '1:-1': ['leaf.c', 40, 'https://example.org/leaf#L40']},
                   'callers': {'2': [['root', 'root.c', 10, 'https://example.org/root#L10'],
                                     ['helper', 'helper.h', 20, 'https://example.org/helper#L20']]}}
        self.open_range_data(['root', 'leaf'], starts=[0, 100], durations=[10000, 1000], parents=[-1, 0],
                             callers=[None, 'root+0x1/0x20'], columns={'depth': [0, 1]},
                             extra={'inline': {'2': [['root', 'root.c', 10], ['helper', 'helper.h', 20]]}, 'sources': sources})
        self.ev("fgView('timeline').probe().select(1)")
        section = self.page.locator('[data-context-list=stack]')
        self.assertEqual(section.locator('.tl-context-source a').count(), 3)
        self.assertEqual(section.locator('.tl-source-kind').count(), 0)
        self.assertEqual(['Call site inside this frame' in link.get_attribute('title')
                          for link in section.locator('.tl-context-source a').all()], [True, True, False])
        rows = self.download_csv('[data-context-export=stack]')
        self.assertEqual([row['source_line'] for row in rows], ['10', '20', '40'])
        self.assertEqual([row['kind'] for row in rows], ['traced', 'inline', 'traced'])
        self.assertEqual(section.locator('button a').count(), 0)
        synthetic = self.ev("() => { const probe = fgView('timeline').probe(); probe.select(probe.N); return probe.N; }")
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), synthetic)
        self.assertEqual(self.page.locator('[data-context-list=inline] .tl-context-source a').count(), 2)
        self.page.locator('[data-call-nav=child]').click()
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 1)
        self.ev("fgSwitchView('text')")
        self.page.reload()
        sources['callers']['2'][0][0] = 'different_parent'
        self.open_range_data(['root', 'leaf'], starts=[0, 100], durations=[10000, 1000], parents=[-1, 0],
                             callers=[None, 'root+0x1/0x20'], columns={'depth': [0, 1]}, extra={'sources': sources})
        self.ev("fgView('timeline').probe().select(1)")
        root = self.page.locator('[data-context-list=stack] [data-context-kind=traced]').first
        self.assertIn('Resolved function entry', root.locator('a').get_attribute('title'))
        self.assertEqual(root.locator('a').get_attribute('href'), 'https://example.org/root#L1')

    def test_detail_source_position_does_not_depend_on_text_dom(self):
        self.open_timeline()
        selected = self.ev("""() => {
            document.getElementById('content').replaceChildren();
            const data = fgData.read('tlData');
            const index = data.cols.line.findIndex(line => fgSourceForLine(line) !== null);
            fgView('timeline').probe().select(index);
            return index;
        }""")
        self.assertGreaterEqual(selected, 0)
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)
        self.assertRegex(self.page.locator('.tl-cs-at .tl-link').inner_text(), r'\.[ch]:\d+$')
        self.assertTrue(self.page.locator('.tl-cs-at a').get_attribute('href').startswith('https://example.org/src/'))

    def test_timeline_flags_are_visible_without_opening_trace_metadata(self):
        flags = (1 << 15) | 4 | 8 | (2 << 4) | (3 << 7) | (2 << 11)
        self.open_range_data(['flagged'], columns={'flags': [flags]},
                             extra={'preempt_base': 1, 'atomic': [[0, 1]]})
        original = self.ev('JSON.stringify(fgData.read("tlData"))')
        self.ev("fgView('timeline').probe().select(0)")
        label = self.page.locator('.tl-call-facts > .k').filter(has_text='flags')
        self.assertTrue(label.is_visible())
        value = label.locator('xpath=following-sibling::span[1]').inner_text()
        for expected in ['irqs off', 'BH off', 'need-resched + preempt (N)', 'preempt 3',
                         'migrate-off 2', 'baseline 1', 'preemption disabled 2 levels']:
            self.assertIn(expected, value)
        self.assertEqual(self.page.locator('[data-trace-metadata], .tl-metadata').count(), 0)
        self.assertEqual(self.page.get_by_text('Trace metadata', exact=True).count(), 0)
        self.assertEqual(self.page.locator('.tl-call-facts > .k').all_text_contents(),
                         ['duration', 'self', 'start', 'task', 'flags', 'context', 'depth', 'trace lines'])
        self.assertTrue(self.page.locator('.tl-call-facts > .v.err').is_visible())
        self.assertIn('sleeping function in atomic context', self.page.locator('.tl-call-facts').inner_text())
        for field in ('task', 'depth', 'trace lines'):
            self.assertTrue(self.page.locator('.tl-call-facts > .k').filter(has_text=field).is_visible())
        self.assertEqual(self.ev('JSON.stringify(fgData.read("tlData"))'), original)

    def test_timeline_zero_latency_flags_are_explicit_not_missing(self):
        self.open_range_data(['clear_flags'], columns={'flags': [1 << 15]}, extra={'preempt_base': 0})
        self.ev("fgView('timeline').probe().select(0)")
        label = self.page.locator('.tl-call-facts > .k').filter(has_text='flags')
        self.assertTrue(label.is_visible())
        self.assertEqual(label.locator('xpath=following-sibling::span[1]').inner_text(), 'none')

    def test_timeline_user_calls_do_not_invent_unrecorded_kernel_flags(self):
        self.open_range_data(['user_call', 'kernel_call'], columns={'flags': [1 << 25, (1 << 15) | 4]},
                             extra={'uftrace': {'spaces': ['user', 'kernel']}})
        self.ev("fgView('timeline').probe().select(0)")
        self.assertEqual(self.page.locator('.tl-call-facts > .k').filter(has_text='flags').count(), 0)
        self.ev("fgView('timeline').probe().select(1)")
        label = self.page.locator('.tl-call-facts > .k').filter(has_text='flags')
        self.assertTrue(label.is_visible())
        self.assertIn('irqs off', label.locator('xpath=following-sibling::span[1]').inner_text())

    def test_timeline_calls_without_latency_data_do_not_show_false_flags(self):
        self.open_range_data(['no_flags'])
        self.ev("fgView('timeline').probe().select(0)")
        self.assertEqual(self.page.locator('.tl-call-facts > .k').filter(has_text='flags').count(), 0)

    def test_timeline_cursor_reuses_scene_until_hover_or_display_state_changes(self):
        self.check_cursor_scene()

    def test_timeline_cursor_scene_preserves_fractional_and_high_dpi_pixels(self):
        original = self.page
        try:
            for ratio in (1.25, 2):
                with self.subTest(ratio=ratio), self.browser.new_context(device_scale_factor=ratio) as context:
                    self.page = context.new_page()
                    self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                    self.page.goto(self.url)
                    self.check_cursor_scene()
        finally:
            self.page = original

    def check_cursor_scene(self):
        self.open_range_data(['root', 'first', 'second'], starts=[0, 1000, 9000],
                             durations=[20000, 5000, 6000], parents=[-1, 0, 0], columns={'depth': [0, 1, 1]},
                             extra=dict(offcpu=[[0, 3000, 6000, 2, 0, -1, 0, 0, 0, -1]],
                                 tasks=[dict(pid=1, comm='worker', kind=0, cpu=0,
                                             runs=[[0, 8000, 0], [8000, 20000, 0]])]))
        self.ev('''() => {
            const canvas = document.querySelector('.tl-canvas'), ctx = canvas.getContext('2d');
            const fill = ctx.fillRect, image = ctx.drawImage;
            const create = document.createElement.bind(document);
            window.cursorCanvases = [];
            document.createElement = function (name, ...args) {
                const node = create(name, ...args);
                if (name === 'canvas') cursorCanvases.push(node);
                return node;
            };
            window.sceneWork = {full: 0, restore: 0};
            ctx.fillRect = function (...args) {
                if (args[0] === 0 && args[1] === 0 && args[2] === canvas.width && args[3] === canvas.height) sceneWork.full++;
                return fill.apply(this, args);
            };
            ctx.drawImage = function (...args) { sceneWork.restore++; return image.apply(this, args); };
            window.settleCursor = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            window.scenePixels = () => ['.tl-canvas', '.tl-head-canvas'].map(selector => document.querySelector(selector).toDataURL());
        }''')
        points = self.ev('''() => {
            const probe = fgView('timeline').probe(), box = document.querySelector('.tl-canvas').getBoundingClientRect();
            return [probe.xyOf(1), probe.offXY(probe.TRACKS[0].off[0]),
                [box.left + box.width * 0.1, box.top + 28], [box.left + 50, box.bottom - 10]];
        }''')
        for target, point in zip(('slice', 'wait', 'cpu', 'empty'), points):
            with self.subTest(target=target):
                self.page.mouse.move(*point)
                self.ev('settleCursor()')
                self.ev('sceneWork.full = sceneWork.restore = 0')
                self.page.mouse.move(point[0] + 2, point[1])
                self.ev('settleCursor()')
                self.assertEqual(self.ev('sceneWork.full'), 0, 'same-hover cursor move rebuilt the scene')
                self.assertEqual(self.ev('sceneWork.restore'), 1)
                fast = self.ev('scenePixels()')
                self.ev("fgView('timeline').draw()")
                self.ev('settleCursor()')
                self.assert_cursor_pixels(fast)
        self.ev('sceneWork.full = 0')
        self.ev('''() => {
            const canvas = document.querySelector('.tl-canvas'), box = canvas.getBoundingClientRect();
            canvas.dispatchEvent(new MouseEvent('mousemove', {bubbles:true, clientX:box.left + 60, clientY:box.bottom - 10}));
            fgView('timeline').draw();
        }''')
        self.ev('settleCursor()')
        self.assertEqual(self.ev('sceneWork.full'), 1, 'cursor request swallowed a pending scene redraw')
        self.ev('sceneWork.full = 0; toggleTheme()')
        self.ev('settleCursor()')
        self.assertGreater(self.ev('sceneWork.full'), 0)
        self.page.set_viewport_size(dict(width=1100, height=760))
        self.ev('settleCursor()')
        point = self.ev("fgView('timeline').probe().xyOf(2)")
        self.page.mouse.move(*point)
        self.ev('settleCursor()')
        self.ev('sceneWork.full = sceneWork.restore = 0')
        self.page.mouse.move(point[0] + 2, point[1])
        self.ev('settleCursor()')
        self.assertEqual(self.ev('sceneWork.full'), 0)
        self.assertEqual(self.ev('sceneWork.restore'), 1)
        fast = self.ev('scenePixels()')
        self.ev("fgView('timeline').draw()")
        self.ev('settleCursor()')
        self.assert_cursor_pixels(fast)
        self.assertEqual(self.ev('cursorCanvases.filter(canvas => canvas.width > 6).length'), 2)
        self.page.mouse.move(0, 0)
        self.ev('settleCursor()')
        self.assertTrue(self.ev('cursorCanvases.filter(canvas => canvas.width !== 6).every(canvas => canvas.width === 0 && canvas.height === 0)'))
        self.page.mouse.move(*point)
        self.ev('settleCursor()')
        self.assertEqual(self.ev('cursorCanvases.filter(canvas => canvas.width > 6).length'), 2)
        self.ev('''async () => {
            fgView('timeline').draw();
            await fgSwitchView('text');
            await settleCursor();
        }''')
        self.assertTrue(self.ev('cursorCanvases.filter(canvas => canvas.width !== 6).every(canvas => canvas.width === 0 && canvas.height === 0)'))

    def assert_cursor_pixels(self, expected):
        actual = self.ev('scenePixels()')
        if actual == expected:
            return
        differences = self.ev('''async ({actual, expected}) => Promise.all(actual.map(async (url, index) => {
            const decode = async source => {
                const image = new Image(); image.src = source; await image.decode();
                const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
                const context = canvas.getContext('2d'); context.drawImage(image, 0, 0);
                return {width: canvas.width, pixels: context.getImageData(0, 0, canvas.width, canvas.height).data};
            };
            const [left, right] = await Promise.all([decode(url), decode(expected[index])]);
            const samples = []; let count = 0;
            for (let pixel = 0; pixel < left.pixels.length; pixel += 4) {
                if (left.pixels.slice(pixel, pixel + 4).every((value, channel) => value === right.pixels[pixel + channel])) continue;
                count++;
                if (samples.length < 5) samples.push({position:[pixel / 4 % left.width, Math.floor(pixel / 4 / left.width)],
                    actual:Array.from(left.pixels.slice(pixel, pixel + 4)), expected:Array.from(right.pixels.slice(pixel, pixel + 4))});
            }
            return {count, samples};
        }))''', dict(actual=actual, expected=expected))
        self.assertEqual(differences, [dict(count=0, samples=[]), dict(count=0, samples=[])])

    def test_timeline_hover_uses_compact_positions_without_reading_source_html(self):
        self.ev('''() => {
            fgData.readSource = fgData.loadSource = () => { throw new Error('Tooltip read source body'); };
            const read = fgData.read;
            fgData.read = name => {
                if (name === 'fgSrc' || name === 'fgSrcLines') throw new Error('Tooltip read full source data');
                return read(name);
            };
        }''')
        self.open_timeline()
        target = self.ev('''() => {
            const data = fgData.read('tlData'), probe = fgView('timeline').probe();
            const index = data.cols.caller.findIndex(caller => data.sources.callers[caller]?.length);
            const chain = data.sources.callers[data.cols.caller[index]];
            const frame = chain[chain.length - 1];
            probe.zoomTo(probe.sStart[index], probe.sEnd[index], index);
            return {point:probe.xyOf(index), source:frame[1] + ':' + frame[2]};
        }''')
        self.page.mouse.move(*target['point'])
        self.page.locator('.tl-tip').wait_for(state='visible')
        self.assertEqual(self.page.locator('.tl-tip .t-row').filter(has=self.page.locator('.t-k', has_text='source')).locator('.t-v').inner_text(), target['source'])

    def test_empty_source_chain_does_not_guess_a_callsite_or_break_depth_gaps(self):
        self.open_range_data(['root', 'leaf'], starts=[0, 100], durations=[10000, 1000], parents=[-1, 0],
                             callers=[None, 'root+0x1/0x20'], columns={'depth': [0, 3]},
                             extra={'sources': {'functions': {'0:-1': ['root.c', 10, 'https://example.org/root#L10']},
                                                'callers': {'2': []}}})
        point = self.ev('''() => {
            window.originalSourceForLine = fgSourceForLine;
            fgSourceForLine = () => { throw new Error('Known unavailable source used legacy fallback'); };
            const probe = fgView('timeline').probe();
            probe.zoomTo(probe.sStart[1], probe.sEnd[1], 1);
            return probe.xyOf(1);
        }''')
        self.page.mouse.move(*point)
        self.page.locator('.tl-tip').wait_for(state='visible')
        self.assertEqual(self.page.locator('.tl-tip .t-k', has_text='source').count(), 0)
        self.assertNotIn('root.c:10', self.page.locator('.tl-tip').inner_text())
        self.ev('fgSourceForLine = originalSourceForLine; fgView("timeline").probe().select(1)')
        self.assertEqual(self.page.locator('[data-context-list=stack] [data-context-kind=untraced]').count(), 2)
        self.assertEqual(self.page.locator('[data-context-list=stack] .tl-context-source').first.text_content(), 'root.c:10')

    def test_legacy_hover_keeps_source_positions_and_caches_html_parsing(self):
        target = self.ev('''() => {
            const data = fgData.read('tlData');
            const index = data.cols.caller.findIndex(caller => data.sources.callers[caller]?.length);
            const frame = data.sources.callers[data.cols.caller[index]].slice(-1)[0];
            delete data.sources;
            const readSource = fgData.readSource;
            window.legacySourceReads = 0;
            fgData.readSource = source => { legacySourceReads++; return readSource(source); };
            return {index, source:frame[1] + ':' + frame[2]};
        }''')
        self.open_timeline()
        point = self.ev('index => { const probe = fgView("timeline").probe(); probe.zoomTo(probe.sStart[index], probe.sEnd[index], index); return probe.xyOf(index); }', target['index'])
        for _ in range(2):
            self.page.mouse.move(0, 0)
            self.page.mouse.move(*point)
            self.page.locator('.tl-tip').wait_for(state='visible')
            self.assertIn(target['source'], self.page.locator('.tl-tip').inner_text())
        self.assertEqual(self.ev('legacySourceReads'), 1)

    def test_special_source_names_open_exact_paths_and_lines_from_text_and_call_stack(self):
        path = 'kernel/space dir/version:123/percent%2F-#?&"雪.c'
        requests = []

        def serve(route):
            requests.append(route.request.url)
            route.fulfill(content_type='text/html', body='<!doctype html><title>Source fixture</title>')

        def open_source(link):
            expected = link.get_attribute('href')
            with self.page.expect_popup() as opened:
                link.click()
            popup = opened.value
            try:
                popup.wait_for_load_state('domcontentloaded')
                self.assertEqual(popup.url, expected)
                parsed = urlsplit(popup.url)
                self.assertEqual(unquote(parsed.path), '/src/' + path)
                self.assertEqual(parsed.query, '')
                self.assertIn(parsed.fragment, ['L2', 'L3'])
            finally:
                popup.close()

        self.page.context.route('https://source.test/**', serve)
        try:
            for listing in [False, True]:
                with self.subTest(listing=listing):
                    kernel = helpers.FakeKernel(inline_source=True, listing=listing, source_file=path)
                    self.addCleanup(kernel.close)
                    kernel.run(helpers.fixture('pi4_full'), '--no-config', '--no-btf', '--timeline',
                               '--func-links', '--base-url', 'https://source.test/src')
                    self.page.goto('file://' + kernel.dir + '/out.html', wait_until='load')
                    self.ev("fgSwitchView('text')")
                    function = self.page.locator('.func-name-link').first
                    self.assertEqual(function.get_attribute('title'), 'Click to open ' + path + ':2')
                    self.assertIsNone(function.get_attribute('onmouseover'))
                    open_source(function)
                    self.ev("fgSwitchView('timeline')")
                    self.ev("""() => {
                        const data = fgData.read('tlData');
                        const index = data.cols.caller.findIndex(caller => data.inline[caller]?.length === 2);
                        fgView('timeline').probe().select(index);
                    }""")
                    stack = self.page.locator('[data-context-list=stack] .tl-context-source a').first
                    self.assertIn(path, stack.text_content())
                    open_source(stack)
            self.assertEqual(len(requests), 4)
            self.assertTrue(all(unquote(urlsplit(url).path) == '/src/' + path for url in requests))
        finally:
            self.page.context.unroute('https://source.test/**', serve)

    def test_plain_source_mode_keeps_clickable_inline_call_stack_without_code_snippets(self):
        kernel = helpers.FakeKernel(inline_source=True, listing=False)
        try:
            kernel.run(helpers.fixture('pi4_full'), '--timeline', '--no-text-view', '--keep-source',
                       '--base-url', 'https://example.org/src')
            self.page.goto('file://' + kernel.dir + '/out.html', wait_until='load')
            self.page.wait_for_function("fgCurrentView() === 'timeline' && !!fgView('timeline')")
            chain = self.ev("""() => {
                const data = fgData.read('tlData');
                const index = data.cols.caller.findIndex((caller, index) =>
                    data.inline[caller]?.length === 2 && fgSourceForLine(data.cols.line[index]) !== null);
                if (index < 0) return null;
                fgView('timeline').probe().select(index);
                return data.sources.callers[data.cols.caller[index]];
            }""")
            self.assertIsNotNone(chain)
            self.assertEqual([frame[0] for frame in chain], ['traced_outer', 'inline_helper'])
            self.assertEqual([frame[2] for frame in chain], [3, 2])
            links = self.page.locator('[data-context-list=stack] [data-context-kind=inline] .tl-context-source a')
            self.assertGreater(links.count(), 0)
            self.assertIn('https://example.org/src/kernel/fake.c#L2',
                          links.evaluate_all('elements => elements.map(element => element.href)'))
            self.assertEqual(self.page.locator('.tl-cs').count(), 0)
            self.assertEqual(self.page.locator('.tl-cs .source-line').count(), 0)
        finally:
            kernel.close()

    def test_timeline_only_retains_source_code_inline_frames_and_source_controls(self):
        kernel = helpers.FakeKernel(inline_source=True)
        try:
            kernel.run(helpers.fixture('pi4_full'), '--timeline', '--no-text-view', '--keep-source',
                       '--highlight-code', '--base-url', 'https://example.org/src')
            self.page.goto('file://' + kernel.dir + '/out.html', wait_until='load')
            self.page.wait_for_function("fgCurrentView() === 'timeline' && !!fgView('timeline')")
            selected = self.ev("""() => {
                const data = fgData.read('tlData');
                const index = data.cols.line.findIndex(line => fgSourceForLine(line) !== null);
                fgView('timeline').probe().select(index);
                return index;
            }""")
            self.assertGreaterEqual(selected, 0)
            self.assertTrue(self.ev("Object.keys(fgData.read('tlData').inline).length > 0"))
            self.assertGreater(self.page.locator('[data-context-list=stack] [data-context-kind=inline] .tl-context-source a').count(), 0)
            self.assertEqual(self.page.locator('#content .expandable').count(), 0)
            self.assertGreater(self.page.locator('.tl-cs .source-line').count(), 0)
            self.assertEqual(self.page.locator('.tl-cs .src-frame').count(), 2)
            self.assertEqual(self.page.locator('.tl-cs .src-frame summary a.src-at').count(), 2)
            self.assertEqual(self.page.locator('.tl-cs .src-frame.src-call summary a.src-at').get_attribute('href'),
                             'https://example.org/src/kernel/fake.c#L2')
            self.assertIn('kernel/fake.c:2', self.page.locator('.tl-cs-at').inner_text())
            source_mode = self.page.locator('.tl-cs [data-fg-source-mode]')
            source_mode.click()
            self.assertEqual(source_mode.inner_text(), 'Source: flat')
            self.assertTrue(self.page.locator('.tl-cs').evaluate('element => element.open'))
            self.assertEqual(self.page.locator('.tl-cs .src-frame').count(), 0)
            self.assertGreater(self.page.locator('.tl-cs .source-line').count(), 0)
            source_mode.press('Enter')
            self.assertEqual(source_mode.inner_text(), 'Source: chain')
            self.assertEqual(self.page.locator('.tl-cs .src-frame').count(), 2)
            source_mode.press('Space')
            self.assertEqual(source_mode.inner_text(), 'Source: flat')
            source_mode.click()
            self.assertEqual(source_mode.inner_text(), 'Source: chain')
            self.page.locator('.tl-cs > summary').click()
            self.page.wait_for_function("localStorage.getItem('fgTl.csOpen') === '0'")
            self.ev('index => fgView("timeline").probe().select(index)', selected)
            self.assertFalse(self.page.locator('.tl-cs').evaluate('element => element.open'))
            self.page.locator('.tl-detail [data-call-actions]').click()
            self.assertTrue(self.page.locator('#tlCallActions [data-call-action=text]').is_disabled())
            self.assertIn('Text view is not included', self.page.locator('#tlCallActionsNote').inner_text())
            self.page.keyboard.press('Escape')
            self.assertEqual(self.ev('fgCurrentView()'), 'timeline')
            self.assertEqual(self.ev("fgView('timeline').probe().sel()"), selected)
        finally:
            kernel.close()

    def test_untraced_frames_fill_depth_gaps(self):
        # pi4_nodur has calls two levels below their parent (kernel depth jump): the missing level is
        # drawn as an "untraced" frame named after the calls' caller, and selecting it opens its panel
        self.k.run(helpers.fixture('pi4_nodur'), '--timeline', out='gap.html')
        self.page.goto('file://' + self.k.dir + '/gap.html', wait_until='load')
        self.open_timeline()
        r = self.ev("""() => { const q = fgView('timeline').probe(); const g = [];
            for (let i = q.N; i < q.NA; i++) if (q.isGhost(i)) {
                const y = q.SYN[i - q.N];
                g.push([i, q.S[q.sName[i]], q.sDepth[i] - q.sDepth[y.p], y.kids.every(c => q.sDepth[c] === q.sDepth[i] + 1)]);
            }
            return g; }""")
        self.assertTrue(r)
        for i, name, dd, nested in r:
            self.assertEqual(dd, 1)
            self.assertTrue(nested)
            self.assertNotEqual(name, '')
        self.ev(f"fgView('timeline').probe().select({r[0][0]})")
        self.page.wait_for_timeout(300)
        self.assertIn('not traced', self.ev("document.querySelector('.tl-detail').innerText"))

    def test_ruler_aligned_with_tracks_when_scrollbar_shown(self):
        # headless Chromium hides scrollbars by default; with a real one the track area is narrower
        # than the ruler, and the ruler must not be stretched (selection marks drifted apart)
        b = self.pw.chromium.launch(ignore_default_args=['--hide-scrollbars'])
        try:
            pg = b.new_page(viewport={'width': 1200, 'height': 420})
            pg.goto(self.url, wait_until='load')
            pg.evaluate("fgSwitchView('timeline')")
            pg.wait_for_timeout(400)
            r = pg.evaluate("""() => { const s = document.querySelector('.tl-scroll');
                return [document.querySelector('.tl-head-canvas').getBoundingClientRect().width,
                        document.querySelector('.tl-canvas').getBoundingClientRect().width,
                        s.clientWidth, s.offsetWidth]; }""")
        finally:
            b.close()
        head, main, client, offset = r
        if offset == client:
            self.skipTest('no visible scrollbar in this environment')
        self.assertEqual(head, main)
        self.assertEqual(main, client)

    def test_detail_panel_resize_and_maximize(self):
        self.open_timeline()
        helpers.click_timeline_tool(self.page, '[data-act=an-errors]')
        self.page.wait_for_timeout(200)
        box = lambda sel: self.page.locator(sel).bounding_box()
        h0, st0 = box('.tl-detail')['height'], box('.tl-stage')['height']
        r = box('.tl-resizer')
        x, y = r['x'] + r['width'] / 2, r['y'] + 4
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        self.page.mouse.move(x, y - 120, steps=5)
        self.page.mouse.up()
        self.page.wait_for_timeout(200)
        self.assertAlmostEqual(box('.tl-detail')['height'], h0 + 120, delta=3)
        self.assertAlmostEqual(box('.tl-stage')['height'], st0 - 120, delta=3)
        self.assertLessEqual(box('.tl-detail')['y'] + box('.tl-detail')['height'], 900 + 1)
        self.page.dblclick('.tl-resizer')
        self.page.wait_for_timeout(200)
        self.assertAlmostEqual(box('.tl-detail')['height'], h0, delta=3)
        top0 = box('.tl-stage')['y']
        self.page.click('[data-act=max]')
        self.page.wait_for_timeout(200)
        self.assertFalse(self.page.locator('.view-tabs').is_visible())
        self.assertLess(box('.tl-stage')['y'], top0 - 40)
        self.page.click('[data-act=max]')
        self.page.wait_for_timeout(200)
        self.assertTrue(self.page.locator('.view-tabs').is_visible())
        self.assertEqual(self.errors, [])

    def test_analysis_panels(self):
        self.open_timeline()
        helpers.click_timeline_tool(self.page, '[data-act=an-errors]')
        self.page.wait_for_timeout(200)
        self.assertGreater(self.ev("document.querySelectorAll('.an-tbl tbody tr[data-chains]').length"), 0)
        self.page.click('.an-tbl tbody tr[data-chains]')
        self.page.wait_for_timeout(200)
        self.assertIn('occurrence 1', self.ev("document.querySelector('.tl-toast').textContent"))
        helpers.click_timeline_tool(self.page, '[data-act=an-lat]')
        self.page.wait_for_timeout(200)
        self.assertIn('Scheduling latency', self.ev("document.querySelector('.tl-detail-title').textContent"))
        self.page.keyboard.press('e')
        self.page.wait_for_timeout(200)
        self.page.keyboard.press('h')
        self.page.wait_for_timeout(300)
        self.assertTrue(self.ev("!!document.querySelector('.an-hist')"))
        self.assertGreater(self.ev("document.querySelectorAll('.an-list tr').length"), 0)

    def open_call_navigation(self, mode='abs'):
        self.open_range_data(['root_a', 'deep_first', 'grandchild', 'shallow_second', 'deep_last',
                              'root_b', 'other_parent_child', 'root_c', 'other_task', 'other_task_child'],
                             mode=mode, starts=[0, 1000, 1200, 10000, 20000, 200000, 201000, 300000, 0, 1000],
                             durations=[100000, 5000, 1000, 1000, 1000, 50000, 1000, 1000, 100000, 1000],
                             parents=[-1, 0, 1, 0, 0, -1, 5, -1, -1, 8],
                             columns={'depth': [2, 5, 6, 3, 5, 0, 3, 4, 0, 5], 'task': [0] * 8 + [1, 1],
                                      'start': [0, 1000, 200, 8800, 10000, 180000, 1000, 99000, 0, 1000]},
                             extra={'tasks': [dict(pid=1, comm='first', kind=0, cpu=0),
                                              dict(pid=2, comm='second', kind=0, cpu=1)]})
        self.ev("fgView('timeline').probe().select(0)")

    def test_recorded_navigation_real_trace_does_not_cross_parent(self):
        self.open_timeline()
        target = self.ev("""() => {
            const probe = fgView('timeline').probe();
            for (const task of probe.TRACKS) for (const row of task.rowsT) {
                for (let position = 1; position < row.length; position++) {
                    const before = row[position - 1], after = row[position];
                    if (before >= probe.N || after >= probe.N || probe.sParent[before] < 0 ||
                        probe.sParent[before] === probe.sParent[after]) continue;
                    const siblings = [];
                    for (let index = 0; index < probe.N; index++) {
                        if (probe.sTask[index] === probe.sTask[before] && probe.sParent[index] === probe.sParent[before]) siblings.push(index);
                    }
                    const next = siblings[siblings.indexOf(before) + 1];
                    return [before, next === undefined ? before : next];
                }
            }
        }""")
        self.assertIsNotNone(target)
        self.ev("index => fgView('timeline').probe().select(index)", target[0])
        self.page.locator('.tl-canvas').focus()
        self.page.keyboard.press(']')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), target[1])

    def test_recorded_navigation_follows_parent_links_across_depths_and_roots(self):
        for mode in ['abs', 'rel', 'compressed', 'count']:
            with self.subTest(mode=mode):
                self.ev("fgSwitchView('text')")
                self.page.reload()
                self.open_call_navigation(mode)
                if mode in ['abs', 'rel']:
                    self.assertIn('4 traced calls inside', self.page.locator('.an-bd-legend').inner_text())
                self.page.locator('.tl-canvas').focus()
                for key, expected in [('ArrowDown', 1), (']', 3), (']', 4), (']', 4), ('[', 3),
                                      ('ArrowUp', 0), (']', 5), (']', 7), (']', 7), ('[', 5),
                                      ('ArrowDown', 6), (']', 6), ('ArrowUp', 5), ('[', 0), ('[', 0)]:
                    self.page.keyboard.press(key)
                    self.assertEqual(self.ev("fgView('timeline').probe().sel()"), expected, key)
                self.ev("fgView('timeline').probe().select(9)")
                for key in ['[', ']']:
                    self.page.keyboard.press(key)
                    self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 9)

    def test_recorded_navigation_native_controls_focus_filters_and_layout(self):
        self.open_call_navigation()
        self.assertTrue(self.page.locator('[data-call-nav=parent]').is_disabled())
        self.assertTrue(self.page.locator('[data-call-nav=previous]').is_disabled())
        self.page.locator('[data-call-nav=child]').press('Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 1)
        self.assertTrue(self.page.locator('[data-call-nav=child]').evaluate('(node) => node === document.activeElement'))
        self.page.locator('[data-call-nav=next]').press('Space')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 3)
        self.assertTrue(self.page.locator('[data-call-nav=next]').evaluate('(node) => node === document.activeElement'))
        self.page.locator('[data-call-nav=next]').press('Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 4)
        self.assertTrue(self.page.locator('[data-call-nav=next]').is_disabled())
        self.assertTrue(self.page.locator('.tl-canvas').evaluate('(node) => node === document.activeElement'))
        self.ev("fgView('timeline').probe().select(0)")
        self.ev("fgNotifyFilter('root only', new Set([1]))")
        self.page.locator('[data-call-nav=child]').click()
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 1)
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        self.ev("fgView('timeline').probe().inline(true)")
        self.page.locator('[data-call-nav=next]').click()
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 3)
        for theme in ['light', 'dark']:
            self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
            for width, height in [(1500, 900), (800, 420), (390, 844), (320, 700)]:
                with self.subTest(theme=theme, width=width):
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.page.wait_for_timeout(60)
                    self.ev("document.querySelector('.tl-detail').scrollTop = 0")
                    bounds = self.ev("""() => {
                        const panel = document.querySelector('.tl-detail').getBoundingClientRect();
                        const nav = document.querySelector('.tl-call-nav').getBoundingClientRect();
                        return [nav.left - panel.left, nav.right - panel.right,
                            document.documentElement.scrollWidth, nav.height];
                    }""")
                    self.assertGreaterEqual(bounds[0], 0)
                    self.assertLessEqual(bounds[1], 1)
                    self.assertLessEqual(bounds[2], width)
                    self.assertLessEqual(bounds[3], 100)

    def test_recorded_navigation_zero_duration_ties_and_safe_target_names(self):
        unsafe = '<img src=x onerror=alert(1)>'
        self.open_range_data(['root', unsafe, 'second', 'last'], starts=[0, 0, 0, 1000],
                             durations=[10000, 0, 0, 1000], parents=[-1, 0, 0, 0], columns={'depth': [0, 3, 1, 2]})
        self.ev("fgView('timeline').probe().select(0)")
        self.assertIn(unsafe, self.page.locator('[data-call-nav=child]').get_attribute('title'))
        self.assertEqual(self.page.locator('.tl-call-nav img').count(), 0)
        self.page.locator('.tl-canvas').focus()
        for key, expected in [('ArrowDown', 1), (']', 2), (']', 3), ('[', 2), ('[', 1)]:
            self.page.keyboard.press(key)
            self.assertEqual(self.ev("fgView('timeline').probe().sel()"), expected)

    def test_recorded_navigation_reuses_links_without_reading_visual_rows(self):
        count = 10000
        self.open_range_data(['root'] + ['zero_call'] * count, starts=[0] * (count + 1),
                             durations=[10000] + [0] * count, parents=[-1] + [0] * count,
                             columns={'depth': [0] + [1] * count})
        self.ev("index => fgView('timeline').probe().select(index)", count - 2)
        result = self.ev("""() => {
            const probe = fgView('timeline').probe(), saved = probe.TRACKS.map(task => task.rowsT);
            let reads = 0;
            for (const task of probe.TRACKS) task.rowsT = new Proxy(task.rowsT, {
                get(target, key) { if (typeof key === 'string' && /^[0-9]+$/.test(key)) reads++; return Reflect.get(target, key); }
            });
            try {
                const canvas = document.querySelector('.tl-canvas');
                for (let iteration = 0; iteration < 30; iteration++) {
                    canvas.dispatchEvent(new KeyboardEvent('keydown', {key: ']', bubbles: true}));
                    canvas.dispatchEvent(new KeyboardEvent('keydown', {key: '[', bubbles: true}));
                }
                return [reads, probe.sel()];
            } finally { probe.TRACKS.forEach((task, index) => { task.rowsT = saved[index]; }); }
        }""")
        self.assertEqual(result, [0, count - 2])

    def test_recorded_navigation_synthetic_frames_reach_actual_recorded_calls(self):
        self.open_range_data(['root', 'child'], starts=[0, 100], durations=[10000, 1000], parents=[-1, 0],
                             callers=[None, 'root+0x1/0x20'], columns={'depth': [3, 6]},
                             extra={'inline': {'2': [['root', 'a.c', 10], ['inline_one', 'b.c', 20], ['inline_two', 'c.c', 30]]}})
        synthetic = self.ev("() => { const probe = fgView('timeline').probe(); return Array.from({length: probe.NA - probe.N}, (_, index) => probe.N + index); }")
        self.assertEqual(len(synthetic), 4)
        self.ev("fgView('timeline').probe().inline(true)")
        for index in synthetic:
            with self.subTest(index=index):
                self.ev("index => fgView('timeline').probe().select(index)", index)
                self.assertTrue(self.page.locator('[data-call-nav=previous]').is_disabled())
                self.assertTrue(self.page.locator('[data-call-nav=next]').is_disabled())
                self.page.locator('[data-call-nav=child]').press('Enter')
                self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 1)
                self.ev("index => fgView('timeline').probe().select(index)", index)
                self.page.locator('.tl-canvas').focus()
                self.page.keyboard.press('ArrowDown')
                self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 1)
                self.ev("index => fgView('timeline').probe().select(index)", index)
                self.page.keyboard.press('ArrowUp')
                self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 0)

    def open_detail_stack(self, count=320, errors=False, mode='abs', prefix='frame_'):
        self.ev("fgSwitchView('text')")
        self.page.reload()
        names = [prefix + str(index).zfill(4) for index in range(count)]
        extra = {'strings': names + ['-12'], 'errnames': {'12': 'ENOMEM'}}
        if errors:
            extra.update(errs=[[index, 12, 0 if index == count - 1 else 1, 2, 1, 0] for index in range(count)],
                         chains=[[count - 1, 0, 12, 2, count, 1, 0]])
        self.open_range_data(names, mode=mode, starts=list(range(count)), durations=[10000 - index * 2 for index in range(count)],
                             parents=[-1] + list(range(count - 1)), columns={'depth': list(range(count)), 'ret': [count] * count}, extra=extra)
        self.ev('index => fgView("timeline").probe().select(index)', count - 1)

    def open_child_groups(self, count=225, mode='abs', prefix='child_', parent_name='parent'):
        self.ev("fgSwitchView('text')")
        self.page.reload()
        names, starts, durations, parents, depths = [parent_name], [0], [count * 10000 + 1000], [-1], [3]
        for group in range(count):
            first = len(names)
            names += [prefix + str(group).zfill(4), 'grandchild', prefix + str(group).zfill(4), 'grandchild']
            starts += [group * 10000 + 100, group * 10000 + 200, group * 10000 + 2000, group * 10000 + 2100]
            durations += [1000, 300, 4000, 500]
            parents += [0, first, 0, first + 2]
            depths += [5, 6, 5, 6]
        self.open_range_data(names, starts=starts, durations=durations, parents=parents, mode=mode,
                             columns={'depth': depths}, extra={'overhead': {'child': 10, 'self': 5, 'source': 'test'}})
        self.ev("fgView('timeline').probe().select(0)")

    def test_child_explorer_real_trace_reaches_functions_beyond_top_eight(self):
        self.open_timeline()
        parent = self.ev("""() => { const probe = fgView('timeline').probe();
            for (let index = 0; index < probe.N; index++) if (probe.nameOf(index) === 'path_openat') return index;
        }""")
        self.ev("index => fgView('timeline').probe().select(index)", parent)
        self.assertIn('top 8 of 10 functions', self.page.locator('.tl-detail').text_content())
        self.page.locator('[data-child-open]').press('Enter')
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 10)
        groups = self.download_csv('[data-child-csv=groups]')
        calls = self.download_csv('[data-child-csv=calls]')
        self.assertEqual(len(groups), 10)
        self.assertEqual(len(calls), 10)
        self.assertEqual({int(row['parent_slice']) for row in calls}, {parent})

    def test_child_explorer_complete_groups_calls_and_longest_navigation(self):
        self.open_child_groups()
        self.assertIn('top 8 of 225 functions', self.page.locator('.tl-detail').text_content())
        self.assertEqual(self.page.locator('.tl-child-preview').first.get_attribute('data-slice'), '3')
        self.page.locator('[data-child-open]').press('Space')
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 100)
        groups = self.download_csv('[data-child-csv=groups]')
        calls = self.download_csv('[data-child-csv=calls]')
        self.assertEqual(len(groups), 225)
        self.assertEqual(len(calls), 450)
        self.assertEqual(groups[0]['calls'], '2')
        self.assertEqual(float(groups[0]['inclusive_ns']), 5000)
        self.assertEqual(float(groups[0]['self_ns']), 4200)
        self.assertEqual(float(groups[0]['mean_ns']), 2500)
        self.assertEqual(float(groups[0]['max_ns']), 4000)
        self.assertEqual(groups[0]['longest_slice'], '3')
        self.assertEqual({row['function'] for row in calls}, {row['function'] for row in groups})
        self.assertTrue(all(row['depth_gap'] == '1' for row in calls))
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 100)
        self.ev("window.firstChildExplorerRow = document.querySelector('.an-child-table tbody tr')")
        self.page.locator('[data-child-page="1"]').press('Enter')
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 100)
        self.assertFalse(self.ev("window.firstChildExplorerRow === document.querySelector('.an-child-table tbody tr')"))
        self.assertTrue(self.ev("document.activeElement.hasAttribute('data-child-page')"))
        self.page.locator('[data-child-page="-1"]').click()
        self.page.locator('[data-child-show="3"]').press('Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 3)
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 100)
        self.assertIn('Direct recorded children', self.page.locator('.tl-detail-title').inner_text())

    def test_child_paging_complete_reversible_groups_and_calls(self):
        self.open_child_groups()
        self.page.click('[data-child-open]')
        self.assertEqual(self.page.locator('[data-child-page="1"]').count(), 1)
        for mode, column in [('groups', 'function'), ('calls', 'slice_index')]:
            self.page.click('[data-child-mode=' + mode + ']')
            expected = self.download_csv('[data-child-csv=' + mode + ']')
            actual, pages = [], []
            while True:
                rows = self.page.locator('.an-child-table tbody tr')
                self.assertLessEqual(rows.count(), 100)
                values = rows.locator('td:first-child').all_text_contents() if mode == 'groups' else rows.evaluate_all('rows => rows.map(row => row.dataset.childCall)')
                actual.extend(values)
                pages.append(values)
                if self.page.locator('[data-child-page="1"]').is_disabled():
                    break
                self.page.locator('[data-child-page="1"]').click()
            self.assertEqual(actual, [row[column] for row in expected])
            for values in reversed(pages[:-1]):
                self.page.locator('[data-child-page="-1"]').click()
                rows = self.page.locator('.an-child-table tbody tr')
                actual_page = rows.locator('td:first-child').all_text_contents() if mode == 'groups' else rows.evaluate_all('rows => rows.map(row => row.dataset.childCall)')
                self.assertEqual(actual_page, values)
            self.assertTrue(self.page.locator('[data-child-page="-1"]').is_disabled())

    def test_child_paging_modes_export_query_scope_and_sort_have_independent_positions(self):
        self.open_child_groups()
        self.page.click('[data-child-open]')
        self.page.click('[data-child-page="1"]')
        group_page = self.page.locator('.an-child-table tbody tr').all_text_contents()
        self.page.click('[data-child-mode=calls]')
        self.page.click('[data-child-page="1"]')
        self.page.click('[data-child-page="1"]')
        call_page = self.page.locator('[data-child-call]').evaluate_all('rows => rows.map(row => row.dataset.childCall)')
        self.assertEqual(len(self.download_csv('[data-child-csv=groups]')), 225)
        self.assertEqual(len(self.download_csv('[data-child-csv=calls]')), 450)
        self.assertEqual(self.page.locator('[data-child-call]').evaluate_all('rows => rows.map(row => row.dataset.childCall)'), call_page)
        self.page.click('[data-child-mode=groups]')
        self.assertEqual(self.page.locator('.an-child-table tbody tr').all_text_contents(), group_page)
        self.page.click('[data-child-sort=name]')
        self.assertEqual(self.page.locator('.an-child-footer [role=status]').inner_text(), 'Showing 1–100 of 225 functions')
        self.page.click('[data-child-mode=calls]')
        self.assertEqual(self.page.locator('[data-child-call]').evaluate_all('rows => rows.map(row => row.dataset.childCall)'), call_page)
        self.page.fill('[data-child-query]', 'child_000')
        self.assertEqual(self.page.locator('.an-child-footer [role=status]').inner_text(), 'Showing 1–20 of 20 calls')
        self.page.fill('[data-child-query]', '')
        self.assertEqual(self.page.locator('.an-child-footer [role=status]').inner_text(), 'Showing 1–100 of 450 calls')
        self.page.click('[data-child-mode=groups]')
        self.page.click('[data-child-page="1"]')
        self.page.locator('[data-child-function]').first.click()
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 2)
        self.assertTrue(self.page.locator('.an-child-pages').is_hidden())
        self.page.click('[data-child-reset]')
        self.page.click('[data-child-mode=groups]')
        self.assertEqual(self.page.locator('.an-child-footer [role=status]').inner_text(), 'Showing 1–100 of 225 functions')

    def test_child_paging_overhead_details_and_text_roundtrip_keep_pages(self):
        self.open_child_groups()
        self.page.click('[data-child-open]')
        self.page.click('[data-child-page="1"]')
        self.page.click('[data-child-mode=calls]')
        self.page.click('[data-child-page="1"]')
        expected = self.page.locator('[data-child-call]').evaluate_all('rows => rows.map(row => row.dataset.childCall)')
        selected = int(expected[0])
        self.page.locator('[data-child-show]').first.click()
        for unused in range(3):
            helpers.click_timeline_tool(self.page, '[data-act=adj]')
            self.assertEqual(self.page.locator('[data-child-call]').evaluate_all('rows => rows.map(row => row.dataset.childCall)'), expected)
            self.assertEqual(self.ev("fgView('timeline').probe().sel()"), selected)
        self.ev("async () => { await fgSwitchView('text'); await fgSwitchView('timeline'); }")
        self.assertEqual(self.page.locator('[data-child-call]').evaluate_all('rows => rows.map(row => row.dataset.childCall)'), expected)
        self.page.locator('[data-child-details]').first.click()
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), selected)
        self.page.locator('.tl-canvas').focus()
        self.page.keyboard.press('ArrowUp')
        self.page.click('[data-child-open]')
        self.assertEqual(self.page.locator('[data-child-call]').evaluate_all('rows => rows.map(row => row.dataset.childCall)'), expected)
        self.page.click('[data-child-mode=groups]')
        self.assertEqual(self.page.locator('.an-child-footer [role=status]').inner_text(), 'Showing 101–200 of 225 functions')

    def test_child_paging_shared_membership_changes_reset_both_modes_not_equivalent_sets(self):
        self.open_child_groups()
        self.page.click('[data-child-open]')
        self.page.click('[data-child-page="1"]')
        self.page.click('[data-child-mode=calls]')
        self.page.click('[data-child-page="1"]')
        self.page.click('[data-child-page="1"]')
        self.ev("""() => {
            const probe = fgView('timeline').probe();
            const lines = JSON.parse(document.getElementById('tlData').textContent).cols.line;
            window.childScopeLines = Array.from({length: probe.N}, (_, index) => index)
                .filter(index => probe.sParent[index] === 0).map(index => lines[index]);
            fgNotifyFilter('all direct children', new Set(childScopeLines));
        }""")
        self.assertEqual(self.page.locator('.an-child-footer [role=status]').inner_text(), 'Showing 201–300 of 450 calls')
        self.page.click('[data-child-mode=groups]')
        self.assertEqual(self.page.locator('.an-child-footer [role=status]').inner_text(), 'Showing 101–200 of 225 functions')
        self.ev("fgNotifyFilter('first 210 direct children', new Set(childScopeLines.slice(0, 210)))")
        self.assertEqual(self.page.locator('.an-child-footer [role=status]').inner_text(), 'Showing 1–100 of 105 functions')
        self.page.click('[data-child-page="1"]')
        self.ev("fgNotifyFilter('equivalent direct children', new Set(childScopeLines.slice(0, 210)))")
        self.assertEqual(self.page.locator('.an-child-footer [role=status]').inner_text(), 'Showing 101–105 of 105 functions')
        self.page.click('[data-child-mode=calls]')
        self.assertEqual(self.page.locator('.an-child-footer [role=status]').inner_text(), 'Showing 1–100 of 210 calls')
        self.assertEqual(len(self.download_csv('[data-child-csv=groups]')), 105)
        self.assertEqual(len(self.download_csv('[data-child-csv=calls]')), 210)
        self.ev("fgNotifyFilter('', null)")
        self.page.click('[data-child-mode=groups]')
        self.assertEqual(self.page.locator('.an-child-footer [role=status]').inner_text(), 'Showing 1–100 of 225 functions')

    def test_child_paging_exact_boundaries_empty_scope_and_new_parent_reset(self):
        for count in (1, 50, 100, 101, 200, 201):
            with self.subTest(count=count):
                self.open_child_groups(count=count)
                self.page.click('[data-child-open]')
                for mode, total in [('groups', count), ('calls', count * 2)]:
                    self.page.click('[data-child-mode=' + mode + ']')
                    self.assertEqual(self.page.locator('.an-child-pages').is_hidden(), total <= 100)
                    self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), min(total, 100))
                    while not self.page.locator('[data-child-page="1"]').is_disabled():
                        self.page.click('[data-child-page="1"]')
                        self.assertLessEqual(self.page.locator('.an-child-table tbody tr').count(), 100)
                    self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), total % 100 or 100)
        self.page.fill('[data-child-query]', 'no matching child')
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 0)
        self.assertEqual(self.page.locator('.an-child-footer [role=status]').inner_text(), 'No matching calls')
        self.assertTrue(self.page.locator('.an-child-pages').is_hidden())
        self.page.click('[data-child-reset]')
        self.page.locator('[data-child-details]').first.click()
        self.page.click('[data-child-open]')
        self.assertEqual(self.page.locator('[data-child-mode=groups]').get_attribute('aria-pressed'), 'true')
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 1)
        self.assertTrue(self.page.locator('.an-child-pages').is_hidden())

    def test_child_paging_touch_controls_and_endpoint_focus_both_themes(self):
        self.page.close()
        context = self.browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True)
        self.addCleanup(context.close)
        self.page = context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)
        self.open_child_groups(count=101)
        self.page.click('[data-child-open]')
        for theme in ('light', 'dark'):
            for width, height in ((390, 844), (320, 700), (844, 390)):
                with self.subTest(theme=theme, width=width):
                    if not self.page.locator('[data-child-page="-1"]').is_disabled():
                        self.page.locator('[data-child-page="-1"]').click()
                    self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.page.wait_for_function("() => document.getElementById('timelineView').classList.contains('tl-compact')")
                    self.page.locator('.an-child-pages').scroll_into_view_if_needed()
                    next_page = self.page.locator('[data-child-page="1"]')
                    next_page.tap()
                    self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 1)
                    self.assertTrue(next_page.is_disabled())
                    self.assertEqual(self.ev('document.activeElement.dataset.childPage'), '-1')
                    self.ev('document.querySelector(".tl-detail").scrollLeft = 1e6')
                    for selector in ('[data-child-page="-1"]', '[data-child-page="1"]', '[data-child-csv=groups]'):
                        bounds = self.page.locator(selector).bounding_box()
                        self.assertGreaterEqual(bounds['height'], 44)
                        self.assertGreaterEqual(bounds['x'], 0)
                        self.assertLessEqual(bounds['x'] + bounds['width'], width)
                    self.page.locator('[data-child-page="-1"]').press('Enter')
                    self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 100)
                    self.assertEqual(self.ev('document.activeElement.dataset.childPage'), '1')
                    self.assertFalse(self.ev('document.documentElement.scrollWidth > innerWidth'))

    def test_child_explorer_scope_query_sort_and_filter_children_not_parent(self):
        self.open_child_groups(count=12)
        self.page.click('[data-child-open]')
        self.page.locator('[data-child-function="1"]').press('Enter')
        self.assertEqual(self.ev("document.activeElement.dataset.childMode"), 'calls')
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 2)
        self.assertEqual(len(self.download_csv('[data-child-csv=calls]')), 2)
        self.page.locator('[data-child-sort=start]').press('Enter')
        self.assertEqual(self.ev("document.activeElement.dataset.childSort"), 'start')
        self.assertEqual([row['slice_index'] for row in self.download_csv('[data-child-csv=calls]')], ['1', '3'])
        self.page.click('[data-child-reset]')
        self.page.fill('[data-child-query]', 'child_0009')
        self.assertEqual(len(self.download_csv('[data-child-csv=groups]')), 1)
        self.assertEqual(len(self.download_csv('[data-child-csv=calls]')), 2)
        self.page.fill('[data-child-query]', '[')
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 0)
        self.page.click('[data-child-reset]')
        self.ev("fgNotifyFilter('only one direct child', new Set([2]))")
        rows = self.download_csv('[data-child-csv=groups]')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['calls'], '1')
        self.assertEqual(float(rows[0]['inclusive_ns']), 1000)
        self.assertEqual(float(rows[0]['self_ns']), 700)
        self.ev("fgNotifyFilter('only grandchild', new Set([3]))")
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 0)
        self.assertIn('No direct recorded children', self.page.locator('.tl-detail').inner_text())
        self.assertTrue(self.page.locator('[data-child-reset]').is_visible())
        self.ev("fgNotifyFilter('', null)")
        self.assertEqual(len(self.download_csv('[data-child-csv=calls]')), 24)

    def test_child_explorer_adjustment_cache_and_loaded_pages(self):
        self.open_child_groups()
        self.page.click('[data-child-open]')
        self.page.click('[data-child-page="1"]')
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 100)
        self.assertEqual(self.page.locator('.an-child-footer [role=status]').inner_text(), 'Showing 101–200 of 225 functions')
        rows = self.download_csv('[data-child-csv=groups]')
        self.assertEqual(float(rows[0]['inclusive_ns']), 2500)
        self.assertEqual(float(rows[0]['self_ns']), 2100)
        self.assertEqual(float(rows[0]['max_ns']), 2000)
        self.assertEqual(rows[0]['duration_basis'], 'adjusted_estimate')
        writes = self.ev("""() => {
            const original = Map.prototype.set; let writes = 0;
            Map.prototype.set = function(key, value) {
                if (value && typeof value.inclusive === 'number' && Array.isArray(value.ids)) writes++;
                return original.call(this, key, value);
            };
            document.querySelector('[data-child-sort=name]').click();
            document.querySelector('[data-child-mode=calls]').click();
            document.querySelector('[data-child-mode=groups]').click();
            Map.prototype.set = original; return writes;
        }""")
        self.assertEqual(writes, 0)

    def test_child_explorer_count_basis_safe_labels_and_compact_layout(self):
        for mode in ['count', 'compressed']:
            with self.subTest(mode=mode):
                self.open_child_groups(count=2, mode=mode, prefix='=unsafe<script>')
                self.page.click('[data-child-open]')
                rows = self.download_csv('[data-child-csv=groups]')
                self.assertTrue(rows[0]['function'].startswith("'=unsafe<script>"))
                suffix = 'units' if mode == 'count' else 'ns'
                self.assertEqual(float(rows[0]['inclusive_' + suffix]), 5 if mode == 'count' else 5000)
                self.assertEqual(rows[0]['position_basis'], 'call_order' if mode == 'count' else 'packed_duration')
                self.assertEqual(self.page.locator('.an-child-table script').count(), 0)
                self.assertIn('not a partition', self.page.locator('.tl-detail').inner_text())
        for theme in ['light', 'dark']:
            for width, height in [(320, 700), (390, 844), (800, 420), (1500, 900)]:
                with self.subTest(theme=theme, width=width):
                    self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.page.wait_for_timeout(60)
                    self.ev("document.querySelector('.tl-detail').scrollLeft = 5000")
                    offsets = self.ev("""() => {
                        const panel = document.querySelector('.tl-detail').getBoundingClientRect();
                        return ['.an-child-head', '.an-child-tools', '.an-child-footer'].map(selector =>
                            document.querySelector(selector).getBoundingClientRect().left - panel.left);
                    }""")
                    self.assertTrue(all(abs(offset) <= 2 for offset in offsets))
                    self.assertLessEqual(self.ev("document.documentElement.scrollWidth"), width)
                    if width <= 640 or height <= 600:
                        self.assertEqual(self.ev("getComputedStyle(document.querySelector('.an-child-head')).top"), 'auto')
                        self.assertEqual(self.ev("document.querySelector('.an-child-table th').style.top"), '0px')

    def test_child_explorer_long_parent_and_function_scope_stay_compact(self):
        label = '<b>very_long_function_name</b>' * 30
        self.open_child_groups(count=2, prefix=label, parent_name=label + '_parent')
        self.page.click('[data-child-open]')
        self.page.click('[data-child-function="1"]')
        for width, height in [(320, 700), (390, 844), (800, 420), (1500, 900)]:
            with self.subTest(width=width, height=height):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.page.wait_for_timeout(60)
                bounds = self.ev("""() => {
                    const panel = document.querySelector('.tl-detail').getBoundingClientRect();
                    const parent = document.querySelector('.an-child-parent').getBoundingClientRect();
                    const scope = document.querySelector('.an-child-scope');
                    return [parent.left - panel.left, parent.right - panel.right, scope.offsetHeight,
                        scope.title, document.querySelector('.an-child-name').offsetHeight];
                }""")
                self.assertGreaterEqual(bounds[0], 0)
                self.assertLessEqual(bounds[1], 0)
                self.assertLessEqual(bounds[2], 40)
                self.assertEqual(bounds[3], label + '0000')
                self.assertLessEqual(bounds[4], 40)
        self.assertEqual(self.page.locator('.an-child-parent b').count(), 0)

    def test_detail_stack_pages_exports_and_preserves_expansion_on_refresh(self):
        self.open_detail_stack()
        section = self.page.locator('[data-context-list=stack]')
        self.assertEqual(section.locator('[data-slice]').count(), 100)
        self.assertEqual(section.locator('[data-slice]').first.get_attribute('data-slice'), '0')
        self.assertNotIn('outside trace window', section.inner_text())
        first = section.locator('[data-slice]').first.element_handle()
        rows = self.download_csv('[data-context-export=stack]')
        self.assertEqual(len(rows), 320)
        self.assertEqual([int(row['recorded_slice_index']) for row in rows], list(range(320)))
        self.assertTrue(all(row['path_status'] == 'complete' and row['selected_slice_index'] == '319' for row in rows))
        self.assertEqual(section.locator('[data-slice]').count(), 100)
        self.page.locator('[data-context-more=stack]').press('Enter')
        self.assertEqual(section.locator('[data-slice]').count(), 200)
        self.assertTrue(first.evaluate('(node) => node.isConnected'))
        self.assertTrue(self.page.locator('[data-context-more=stack]').evaluate('(node) => node === document.activeElement'))
        walks = self.ev("""() => {
            const original = Array.prototype.reverse;
            let walks = 0;
            Array.prototype.reverse = function() { if (this.length === 320) walks++; return original.call(this); };
            try {
                fgNotifyFilter('leaf only', new Set([320]));
                document.querySelector('[data-act=adj]').click();
                fgView('timeline').probe().inline(true);
            } finally { Array.prototype.reverse = original; }
            return walks;
        }""")
        self.assertEqual(walks, 0)
        self.assertEqual(section.locator('[data-slice]').count(), 200)
        adjusted = self.download_csv('[data-context-export=stack]')
        self.assertEqual(float(adjusted[0]['duration_ns']), float(rows[0]['duration_ns']) / 2)
        self.assertEqual([row['start_ns'] for row in adjusted], [row['start_ns'] for row in rows])
        self.assertTrue(all(row['duration_basis'] == 'overhead_subtracted_estimate' for row in adjusted))
        self.page.locator('[data-context-more=stack]').press('Space')
        self.page.locator('[data-context-more=stack]').press('Enter')
        self.assertEqual(section.locator('[data-slice]').count(), 320)
        self.assertTrue(self.page.locator('[data-context-more=stack]').is_disabled())

    def test_detail_error_chain_is_complete_beyond_sixty_four_frames(self):
        self.open_detail_stack(errors=True)
        section = self.page.locator('[data-context-list=error]')
        self.assertIn('320 recorded frames', section.locator('.tl-sec').text_content())
        self.assertIn('ENOMEM', section.inner_text())
        self.assertEqual(section.locator('[data-slice]').count(), 100)
        rows = self.download_csv('[data-context-export=error]')
        self.assertEqual(len(rows), 320)
        self.assertEqual([int(row['recorded_slice_index']) for row in rows], list(range(320)))
        self.assertTrue(all(row['chain_index'] == '0' and row['errno'] == '12' and row['fate'] == 'unknown' for row in rows))
        self.assertTrue(all(row['return'] == "'-12" for row in rows))
        self.assertEqual(section.locator('[data-slice]').count(), 100)
        for expected in [200, 300, 320]:
            self.page.locator('[data-context-more=error]').press('Enter')
            self.assertEqual(section.locator('[data-slice]').count(), expected)

    def test_detail_stack_exports_typed_inline_gaps_and_actual_window_boundary(self):
        self.open_range_data(['root', 'child'], starts=[0, 100], durations=[10000, 1000], parents=[-1, 0],
                             callers=[None, 'root+0x1/0x20'], columns={'depth': [3, 6]},
                             extra={'inline': {'2': [['root', 'a.c', 10], ['inline_one', 'b.c', 20], ['inline_two', 'c.c', 30]]}})
        self.ev("fgView('timeline').probe().select(1)")
        rows = self.download_csv('[data-context-export=stack]')
        self.assertEqual([row['kind'] for row in rows], ['outside', 'traced', 'untraced', 'untraced', 'inline', 'inline', 'traced'])
        self.assertEqual(rows[0]['outside_frame_count'], '3')
        self.assertEqual([row['depth'] for row in rows if row['kind'] == 'untraced'], ['4', '5'])
        self.assertEqual([row['source_file'] for row in rows if row['kind'] == 'inline'], ['b.c', 'c.c'])
        for row in rows:
            if row['kind'] != 'traced':
                self.assertTrue(all(row[field] == '' for field in ['recorded_slice_index', 'trace_line', 'duration_ns', 'self_ns', 'start_ns', 'end_ns', 'duration_basis']))
        before = self.page.locator('[data-context-list=stack]').inner_text()
        self.ev("fgView('timeline').probe().inline(true)")
        self.assertEqual(self.page.locator('[data-context-list=stack]').inner_text(), before)

    def open_detail_children(self, count=225, inline=False):
        self.ev("fgSwitchView('text')")
        self.page.reload()
        extra = {'inline': {'2': [['root', 'a.c', 1], ['inline_frame', 'b.c', 2]]}} if inline else None
        self.open_range_data(['root'] + ['child'] * count, starts=[0] + [1000 + index * 1000 for index in range(count)],
                             durations=[(count + 2) * 1000] + [500] * count, parents=[-1] + [0] * count,
                             callers=[None] + ['root+0x1/0x20'] * count if inline else None,
                             columns={'depth': [0] + [1 if inline else 2] * count}, extra=extra)
        self.ev("fgView('timeline').probe().select(fgView('timeline').probe().N)")

    def test_detail_ghost_children_are_complete_cached_and_incremental(self):
        self.open_detail_children()
        section = self.page.locator('[data-context-list=children]')
        self.assertEqual(section.locator('[data-slice]').count(), 100)
        rows = self.download_csv('[data-context-export=children]')
        self.assertEqual(len(rows), 225)
        self.assertEqual([int(row['recorded_slice_index']) for row in rows], list(range(1, 226)))
        self.page.locator('[data-context-more=children]').press('Enter')
        self.assertEqual(section.locator('[data-slice]').count(), 200)
        scans = self.ev("""() => {
            const probe = fgView('timeline').probe(), kids = probe.SYN[probe.NA - probe.N - 1].kids;
            const original = Array.prototype[Symbol.iterator];
            let scans = 0;
            Array.prototype[Symbol.iterator] = function() { if (this === kids) scans++; return original.call(this); };
            try {
                fgNotifyFilter('one child only', new Set([2]));
                document.querySelector('[data-act=adj]').click();
            } finally { Array.prototype[Symbol.iterator] = original; }
            return scans;
        }""")
        self.assertEqual(scans, 0)
        self.assertEqual(section.locator('[data-slice]').count(), 200)
        adjusted = self.download_csv('[data-context-export=children]')
        self.assertEqual(float(adjusted[0]['duration_ns']), float(rows[0]['duration_ns']) / 2)
        self.assertEqual([row['start_ns'] for row in adjusted], [row['start_ns'] for row in rows])
        self.page.locator('[data-context-more=children]').press('Space')
        self.assertEqual(section.locator('[data-slice]').count(), 225)
        self.ev("fgNotifyFilter('no matching children', new Set())")
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), -1)
        self.assertFalse(self.page.locator('.tl-detail').is_visible())

    def test_detail_inline_exports_callsite_chain_without_fabricated_timings(self):
        self.open_detail_children(inline=True)
        rows = self.download_csv('[data-context-export=inline]')
        self.assertEqual([row['kind'] for row in rows], ['call_site', 'inline'])
        self.assertEqual([row['source_file'] for row in rows], ['a.c', 'b.c'])
        self.assertTrue(all(row['recorded_slice_index'] == '' and row['duration_ns'] == '' for row in rows))
        self.assertEqual(len(self.download_csv('[data-context-export=children]')), 225)
        self.assertEqual(self.page.locator('[data-context-list=children] [data-slice]').count(), 100)
        self.page.locator('[data-context-more=children]').press('Enter')
        self.page.locator('[data-context-list=children] [data-slice="150"]').press('Space')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 150)
        self.assertEqual(self.page.locator('[data-context-list=stack] [data-context-kind=inline]').count(), 1)

    def test_detail_large_synthetic_fanout_only_renders_requested_rows(self):
        self.open_detail_children(count=150000)
        self.assertEqual(self.page.locator('[data-context-list=children] [data-slice]').count(), 100)
        self.assertIn('of 150000 context rows', self.page.locator('.tl-context-footer').inner_text())
        self.page.locator('[data-context-more=children]').press('Enter')
        self.assertEqual(self.page.locator('[data-context-list=children] [data-slice]').count(), 200)

    def test_detail_long_inline_chain_is_lazy_and_fully_exportable(self):
        chain = [['root', 'root.c', 1]] + [['inline_' + str(index), 'inline.c', index + 2] for index in range(1000)]
        self.open_range_data(['root', 'child'], starts=[0, 100], durations=[10000, 1000], parents=[-1, 0],
                             callers=[None, 'root+0x1/0x20'], columns={'depth': [0, 1]}, extra={'inline': {'2': chain}})
        self.ev("fgView('timeline').probe().select(fgView('timeline').probe().N)")
        section = self.page.locator('[data-context-list=inline]')
        self.assertEqual(section.locator('.tl-context-row').count(), 100)
        self.assertIn('of 1001 context rows', section.inner_text())
        rows = self.download_csv('[data-context-export=inline]')
        self.assertEqual(len(rows), 1001)
        self.assertEqual(rows[-1]['function'], 'inline_999')
        self.assertEqual(rows[-1]['source_line'], '1001')
        self.assertTrue(all(row['duration_ns'] == '' and row['recorded_slice_index'] == '' for row in rows))
        self.assertEqual(section.locator('.tl-context-row').count(), 100)
        self.page.locator('[data-context-more=inline]').press('Enter')
        self.assertEqual(section.locator('.tl-context-row').count(), 200)

    def test_detail_context_native_navigation(self):
        self.open_detail_stack(count=3, errors=True)
        self.page.locator('[data-context-list=error] [data-slice="1"]').press('Space')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 1)
        self.assertEqual(self.page.locator('[data-context-list=error] .cur').get_attribute('data-slice'), '1')
        self.page.locator('[data-context-list=stack] [data-slice="0"]').press('Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 0)
        self.assertEqual(self.page.locator('[data-context-list=stack] [data-slice]').count(), 1)

    def test_detail_csv_units_and_formula_protection(self):
        for mode in ['abs', 'dur', 'count']:
            with self.subTest(mode=mode):
                self.open_detail_stack(count=3, mode=mode, prefix='=SUM(1,2)')
                rows = self.download_csv('[data-context-export=stack]')
                self.assertEqual(rows[0]['function'], "'=SUM(1,2)0000")
                unit = 'units' if mode == 'count' else 'ns'
                self.assertEqual(float(rows[0]['duration_' + unit]), 10 if mode == 'count' else 10000)
                self.assertEqual(rows[0]['position_basis'], 'relative_timestamp_span' if mode == 'abs' else 'packed_geometry' if mode == 'dur' else 'call_order_geometry')
                self.assertEqual(rows[0]['time_mode'], mode)
                if mode == 'count':
                    self.assertFalse(any(column.endswith('_ns') for column in rows[0]))

    def test_detail_context_reports_inconsistent_chain_metadata(self):
        self.open_range_data(['root', 'parent', 'origin', 'unrelated'], starts=[0, 1, 2, 20000], durations=[10000, 9000, 1000, 1000],
                             parents=[-1, 0, 1, -1], columns={'depth': [0, 1, 2, 0]},
                             extra={'errs': [[2, 12, 0, 2, 0, 0]], 'chains': [[2, 3, 12, 2, 999, 0, 0]]})
        self.ev("fgView('timeline').probe().select(2)")
        section = self.page.locator('[data-context-list=error]')
        self.assertIn('missing chain top', section.inner_text())
        self.assertIn('3 recorded frames', section.locator('.tl-sec').text_content())
        rows = self.download_csv('[data-context-export=error]')
        self.assertEqual([row['recorded_slice_index'] for row in rows], ['0', '1', '2'])
        self.assertTrue(all(row['path_status'] == 'missing_chain_top' for row in rows))
        self.assertTrue(all(row['chain_top_slice'] == '3' and row['chain_reported_frames'] == '999' for row in rows))

    def test_detail_context_detects_parent_cycles_without_a_depth_guard(self):
        self.open_range_data(['root', 'parent', 'leaf'], starts=[0, 1, 2], durations=[10000, 9000, 1000],
                             parents=[-1, 0, 1], columns={'depth': [0, 1, 2]})
        self.ev("() => { const probe = fgView('timeline').probe(); probe.sParent[0] = 2; probe.select(2); }")
        self.assertIn('cycle', self.page.locator('[data-context-list=stack]').inner_text())
        rows = self.download_csv('[data-context-export=stack]')
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row['path_status'] == 'cycle' for row in rows))

    def test_detail_context_long_names_fit_compact_windows(self):
        self.open_detail_stack(count=160, prefix='<b>long_function_name</b>' * 15)
        for theme in ['light', 'dark']:
            for width, height in [(1500, 900), (320, 700), (390, 844), (800, 420)]:
                with self.subTest(theme=theme, width=width):
                    self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.ev("document.querySelector('.tl-detail').scrollTop = 0")
                    self.page.wait_for_timeout(100)
                    close = self.page.locator('.tl-slice-head .tl-close').bounding_box()
                    self.assertGreaterEqual(close['x'], 0)
                    self.assertLessEqual(close['x'] + close['width'], width)
                    self.assertEqual(self.ev("getComputedStyle(document.querySelector('.tl-slice-head')).top"), 'auto' if width <= 640 or height <= 600 else '0px')
                    self.assertEqual(self.page.locator('.tl-context-text b').count(), 0)
                    self.assertLessEqual(self.page.locator('.tl-context-select').first.evaluate('(node) => node.offsetHeight'), 40)
                    self.assertLessEqual(self.page.locator('.tl-context-row').first.evaluate('(node) => node.offsetHeight'), 64)
                    self.assertLessEqual(self.ev('document.documentElement.scrollWidth'), width)
                    self.assertTrue(self.page.locator('[data-context-export=stack]').is_enabled())

    def test_detail_breakdown_uses_real_parent_links_across_depth_gaps(self):
        self.open_range_data(['parent', 'real_child'], starts=[0, 100], durations=[10000, 1000], parents=[-1, 0],
                             columns={'depth': [0, 3]})
        self.ev("fgView('timeline').probe().select(0)")
        legend = self.page.locator('.an-bd-legend').inner_text()
        self.assertIn('traced callees', legend)
        self.assertIn('1 traced call inside', legend)
        self.assertNotIn('NaN', self.page.locator('.tl-detail').inner_text())
        self.assertIn('real_child', self.page.locator('.tl-detail').inner_text())
        self.assertEqual(self.page.locator('.tl-detail .tl-crumb[data-slice="1"]').count(), 1)

    def test_detail_breakdown_keeps_descendants_beyond_256_levels(self):
        count = 320
        self.open_range_data(['deep_call'] * count, starts=list(range(count)),
                             durations=[10000 - index * 2 for index in range(count)],
                             parents=[-1] + list(range(count - 1)), columns={'depth': list(range(count))})
        self.ev("fgView('timeline').probe().select(0)")
        self.assertIn('319 traced calls inside', self.page.locator('.an-bd-legend').inner_text())

    def test_detail_breakdown_discloses_overlapping_recordings_instead_of_160_percent(self):
        self.open_range_data(['parent', 'first', 'overlapping'], starts=[0, 0, 100], durations=[1000, 800, 800],
                             parents=[-1, 0, 0], columns={'depth': [0, 1, 1]})
        self.ev("fgView('timeline').probe().select(0)")
        self.assertIn('parts exceed the recorded interval by 600 ns', self.page.locator('.an-bd-legend').inner_text())
        self.assertNotIn('160%', self.page.locator('.an-bd-legend').inner_text())
        self.assertAlmostEqual(self.ev("Array.from(document.querySelectorAll('.an-bd span')).reduce((total, part) => total + parseFloat(part.style.width), 0)"), 100)

    def open_slowfast_data(self, functions=225, depth=1, extra=None, root_sites=None, child_prefix='child_'):
        self.ev("fgSwitchView('text')")
        self.page.reload()
        names, starts, durations, parents, depths, callers = [], [], [], [], [], []
        for root in range(10):
            parent = len(names)
            names.append('target')
            starts.append(root * 1000000)
            durations.append(800000 if root == 9 else 600000 + root * 1000)
            parents.append(-1)
            depths.append(0)
            callers.append(root_sites[root] if root_sites else None)
            for child in range(functions):
                names.append(child_prefix + str(child).zfill(4))
                starts.append(root * 1000000 + child * 1000)
                durations.append(200 + child if root == 9 else 100)
                parents.append(parent)
                depths.append(depth)
                callers.append(None)
        self.open_range_data(names, starts=starts, durations=durations, parents=parents, columns={'depth': depths}, extra=extra,
                             callers=callers if root_sites else None)
        self.ev("fgView('timeline').probe().dist(0)")
        self.page.locator('[data-act=slowfast]').press('Enter')

    def test_detail_breakdown_partitions_irq_and_waits_without_synthetic_calls(self):
        self.open_range_data(['root', 'helper', 'irq', 'irq_child', 'child'], starts=[0, 100, 200, 300, 700],
                             durations=[10000, 1000, 400, 100, 300], parents=[-1, 0, 1, 2, 1],
                             callers=[None, 'caller_helper', None, None, None],
                             columns={'depth': [0, 3, 5, 6, 4], 'flags': [0, 0, 2, 2, 0]},
                             extra={'offcpu': [[0, 3000, 5000, 1, -1, -1, 0, 0, 0, -1],
                                               [0, 1010, 1060, 4, -1, -1, 0, 0, 1, -1]],
                                    'inline': {'5': [['caller_helper', 'a.c', 1], ['inlined', 'a.c', 2]]}})
        self.ev("fgView('timeline').probe().select(0)")
        values = self.page.locator('.an-bd span').evaluate_all('(parts) => parts.map(part => part.title)')
        self.assertEqual(values, ['own code 7.000 us', 'traced callees 550 ns', 'interrupts / softirq 400 ns', 'blocked 2.000 us', 'preempted 50 ns'])
        self.assertIn('4 traced calls inside', self.page.locator('.an-bd-legend').inner_text())
        writes = self.ev("""() => {
            const original = Map.prototype.set;
            let writes = 0;
            Map.prototype.set = function(key, value) { if (value && typeof value.wall === 'number' && typeof value.nCalls === 'number') writes++; return original.call(this, key, value); };
            document.querySelector('[data-act=adj]').click();
            fgNotifyFilter('child only', new Set([5]));
            fgView('timeline').probe().inline(true);
            Map.prototype.set = original;
            return writes;
        }""")
        self.assertEqual(writes, 0)
        self.assertEqual(self.page.locator('.an-bd span').evaluate_all('(parts) => parts.map(part => part.title)'), values)

    def test_slowfast_refresh_adjustment_bucket_scope_and_example_navigation(self):
        self.open_slowfast_data(functions=2, depth=3,
                                extra={'offcpu': [[0, 9700000, 9710000, 1, -1, -1, 0, 0, 0, -1]]})
        raw = self.download_csv('[data-act=slowfast-csv]')
        child = next(row for row in raw if row['function'] == 'child_0001')
        self.assertEqual(float(child['slow_self_per_root_ns']), 201)
        blocked = next(row for row in raw if row['kind'] == 'blocked')
        self.assertEqual(float(blocked['slow_self_per_root_ns']), 10000)
        self.assertTrue(all(row['name_index'] == '' or int(row['name_index']) < 3 for row in raw))
        self.page.locator('[data-sf-example]').first.press('Enter')
        self.assertEqual(self.page.locator('.tl-detail-title').inner_text(), 'target')
        self.assertGreater(self.ev("fgView('timeline').probe().sel()"), 0)
        helpers.click_timeline_tool(self.page, '[data-act=adj]')
        adjusted = self.download_csv('[data-act=slowfast-csv]')
        child = next(row for row in adjusted if row['function'] == 'child_0001')
        self.assertEqual(float(child['slow_self_per_root_ns']), 100.5)
        self.assertEqual(child['value_basis'], 'overhead_subtracted_estimate')
        blocked_adjusted = next(row for row in adjusted if row['kind'] == 'blocked')
        self.assertEqual(blocked_adjusted['slow_self_per_root_ns'], blocked['slow_self_per_root_ns'])
        self.assertEqual(blocked_adjusted['value_basis'], 'recorded_scheduling_geometry')
        option = self.page.locator('[data-act=dist-bucket] option').nth(1).get_attribute('value')
        self.page.locator('[data-act=dist-bucket]').select_option(option)
        self.assertEqual(self.download_csv('[data-act=slowfast-csv]'), adjusted)
        self.ev("fgNotifyFilter('roots only', new Set([1,4,7,10,13,16,19,22,25,28]))")
        self.assertEqual(self.page.locator('.an-slowfast-table').count(), 1)
        self.assertEqual(len(self.download_csv('[data-act=slowfast-csv]')), 6)
        self.ev("fgNotifyFilter('no calls', new Set())")
        self.assertIn('at least 6 matching calls', self.page.locator('.an-slowfast').inner_text())
        self.ev("fgNotifyFilter('', null)")
        self.assertEqual(len(self.download_csv('[data-act=slowfast-csv]')), 6)

    def test_slowfast_cache_survives_sort_export_collapse_and_site_changes(self):
        self.open_slowfast_data(functions=30, root_sites=['site_a'] * 7 + ['site_b'] * 3)
        writes = self.ev("""() => {
            const original = Map.prototype.set;
            let writes = 0;
            Map.prototype.set = function(key, value) { if (value && typeof value.calls === 'number' && typeof value.self === 'number' && 'example' in value) writes++; return original.call(this, key, value); };
            document.querySelector('[data-sf-sort=name] button').click();
            document.querySelector('[data-act=slowfast]').click();
            document.querySelector('[data-act=slowfast]').click();
            Map.prototype.set = original;
            return writes;
        }""")
        self.assertEqual(writes, 0)
        self.assertEqual(len(self.download_csv('[data-act=slowfast-csv]')), 34)
        self.page.locator('tr[data-site=site_a] button').press('Enter')
        rows = self.download_csv('[data-act=slowfast-csv]')
        self.assertTrue(all(row['population_calls'] == '7' and row['call_site_filter'] == 'site_a' for row in rows))
        self.page.locator('[data-act=dist-reset-site]').press('Enter')
        self.assertTrue(all(row['population_calls'] == '10' for row in self.download_csv('[data-act=slowfast-csv]')))

    def test_slowfast_ties_recursion_and_zero_time_count_differences(self):
        self.open_range_data(['target'] * 10, starts=[0] * 10, durations=[1000] * 10,
                             parents=[-1] + list(range(9)), columns={'depth': list(range(10))})
        self.ev("fgView('timeline').probe().dist(0)")
        self.page.locator('[data-act=slowfast]').press('Enter')
        cohorts = self.download_csv('[data-act=slowfast-cohorts-csv]')
        fast = {int(row['slice_index']) for row in cohorts if row['cohort'] == 'fast'}
        slow = {int(row['slice_index']) for row in cohorts if row['cohort'] == 'slow'}
        self.assertEqual(fast, {5, 6, 7, 8, 9})
        self.assertEqual(slow, {0})
        self.assertFalse(fast & slow)
        function = next(row for row in self.download_csv('[data-act=slowfast-csv]') if row['kind'] == 'function')
        self.assertEqual(float(function['fast_calls_per_root']), 2)
        self.assertEqual(float(function['slow_calls_per_root']), 9)
        self.assertEqual(float(function['fast_self_per_root_ns']), 800)
        self.assertEqual(float(function['slow_self_per_root_ns']), 1000)
        self.ev("fgSwitchView('text')")
        self.page.reload()
        self.open_range_data(['target', 'zero_time'] + ['target'] * 9, starts=[0, 0] + list(range(1000, 10000, 1000)),
                             durations=[1000, 0] + [1000] * 9, parents=[-1, 0] + [-1] * 9,
                             columns={'depth': [0, 1] + [0] * 9})
        self.ev("fgView('timeline').probe().dist(0)")
        self.page.locator('[data-act=slowfast]').press('Enter')
        row = next(row for row in self.download_csv('[data-act=slowfast-csv]') if row['kind'] == 'function')
        self.assertEqual(row['function'], 'zero_time')
        self.assertEqual(float(row['delta_self_per_root_ns']), 0)
        self.assertEqual(float(row['slow_calls_per_root']), 1)

    def test_slowfast_export_formula_protection_and_units(self):
        for mode in ['abs', 'count', 'dur']:
            with self.subTest(mode=mode):
                self.ev("fgSwitchView('text')")
                self.page.reload()
                self.open_range_data(['target', '=SUM(1,2)'] * 10, mode=mode, starts=[index * 3000 for index in range(20)],
                                     durations=[6000, 1000] * 10, parents=[parent for index in range(10) for parent in [-1, index * 2]],
                                     columns={'depth': [0, 1] * 10}, extra={'tasks': [dict(pid=1, comm='@worker', kind=0, cpu=0)]})
                self.ev("fgView('timeline').probe().dist(0)")
                self.page.locator('[data-act=slowfast]').press('Enter')
                rows = self.download_csv('[data-act=slowfast-csv]')
                row = next(row for row in rows if row['kind'] == 'function')
                self.assertEqual(row['function'], "'=SUM(1,2)")
                unit = 'units' if mode == 'count' else 'ns'
                self.assertEqual(float(row['fast_self_per_root_' + unit]), 1 if mode == 'count' else 1000)
                cohort = self.download_csv('[data-act=slowfast-cohorts-csv]')[0]
                self.assertEqual(cohort['comm'], "'@worker")
                self.assertEqual(cohort['time_mode'], mode)
                if mode == 'count':
                    self.assertFalse(any(column.endswith('_ns') for column in row))
                elif mode == 'dur':
                    self.assertEqual(next(row for row in rows if row['kind'] == 'irq')['value_basis'], 'packed_geometry')

    def test_slowfast_compact_layout_sorts_and_examples_remain_reachable(self):
        self.open_slowfast_data(functions=5, child_prefix='very_long_function_' * 20)
        for theme in ['light', 'dark']:
            for width, height in [(1500, 900), (320, 700), (800, 420)]:
                with self.subTest(theme=theme, width=width):
                    self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                    self.page.set_viewport_size({'width': width, 'height': height})
                    self.ev("() => { const panel = document.querySelector('.tl-detail'); panel.scrollTop = 0; panel.scrollLeft = panel.scrollWidth; }")
                    self.page.wait_for_timeout(100)
                    close = self.page.locator('.an-dist-head .tl-close').bounding_box()
                    self.assertGreaterEqual(close['x'], 0)
                    self.assertLessEqual(close['x'] + close['width'], width)
                    for selector in ['.an-slowfast-tools', '.an-slowfast-footer', '.an-slowfast-hint']:
                        bounds = self.page.locator(selector).bounding_box()
                        self.assertGreaterEqual(bounds['x'], 0)
                        self.assertLessEqual(bounds['x'] + bounds['width'], width)
                    self.page.locator('[data-sf-sort=name] button').press('Enter')
                    self.page.locator('[data-sf-example]').first.press('Space')
                    self.assertEqual(self.page.locator('.tl-detail-title').inner_text(), 'target')
                    self.assertLessEqual(self.ev('document.documentElement.scrollWidth'), width)
                    self.assertEqual(self.ev("getComputedStyle(document.querySelector('.an-dist-head')).top"),
                                     'auto' if width <= 640 or height <= 600 else '0px')
                    self.assertEqual(self.ev("document.querySelector('.an-slowfast-table th').style.top"),
                                     '0px' if width <= 640 or height <= 600 else str(self.ev("document.querySelector('.an-dist-head').offsetHeight")) + 'px')
                    self.assertEqual(len(self.download_csv('[data-act=slowfast-csv]')), 9)
                    self.assertLessEqual(self.page.locator('.an-slowfast-table tr[data-sf-kind=function]').first.evaluate('(node) => node.offsetHeight'), 48)

    def test_slowfast_large_fanout_paginates_without_spreading_arguments(self):
        count = 150000
        self.open_range_data(['target'] * 6 + ['child_' + str(index) for index in range(count)],
                             starts=[index * 2000 for index in range(6)] + [11000 + index * 1000 for index in range(count)],
                             durations=[1000] * 5 + [(count + 1) * 1000] + [500] * count,
                             parents=[-1] * 6 + [5] * count, columns={'depth': [0] * 6 + [1] * count})
        self.ev("fgView('timeline').probe().dist(0)")
        self.page.locator('[data-act=slowfast]').press('Enter')
        self.assertEqual(self.page.locator('.an-slowfast-table tbody tr').count(), 100)
        self.assertIn('of 150004 profile rows', self.page.locator('.an-slowfast-footer').inner_text())
        self.page.locator('[data-act=slowfast-more]').press('Enter')
        self.assertEqual(self.page.locator('.an-slowfast-table tbody tr').count(), 200)

    def test_slowfast_paginates_sorts_exports_and_retains_population(self):
        self.open_slowfast_data()
        self.assertEqual(self.page.locator('.an-slowfast-table tbody tr').count(), 100)
        first = self.page.locator('.an-slowfast-table tbody tr').first.element_handle()
        rows = self.download_csv('[data-act=slowfast-csv]')
        self.assertEqual(len(rows), 229)
        child = next(row for row in rows if row['function'] == 'child_0224')
        self.assertEqual(float(child['fast_calls_per_root']), 1)
        self.assertEqual(float(child['slow_calls_per_root']), 1)
        self.assertEqual(float(child['fast_self_per_root_ns']), 100)
        self.assertEqual(float(child['slow_self_per_root_ns']), 424)
        self.assertEqual(float(child['delta_self_per_root_ns']), 324)
        self.page.locator('[data-act=slowfast-more]').press('Enter')
        self.assertEqual(self.page.locator('.an-slowfast-table tbody tr').count(), 200)
        self.assertTrue(first.evaluate('(node) => node.isConnected'))
        self.assertTrue(self.page.locator('[data-act=slowfast-more]').evaluate('(node) => node === document.activeElement'))
        self.page.locator('[data-act=slowfast-more]').press('Enter')
        self.assertEqual(self.page.locator('.an-slowfast-table tbody tr').count(), 229)
        cohorts = self.download_csv('[data-act=slowfast-cohorts-csv]')
        self.assertEqual(len(cohorts), 6)
        self.assertEqual(sum(row['cohort'] == 'fast' for row in cohorts), 5)
        self.assertEqual(sum(row['cohort'] == 'slow' for row in cohorts), 1)
        self.page.locator('.an-slowfast-table th[data-sf-sort=name] button').press('Enter')
        self.assertEqual(self.page.locator('.an-slowfast-table tbody tr').count(), 100)
        self.assertEqual(self.page.locator('.an-slowfast-table th[data-sf-sort=name]').get_attribute('aria-sort'), 'ascending')
        self.page.locator('[data-act=slowfast]').press('Enter')
        self.assertEqual(self.page.locator('.an-slowfast-table').count(), 0)
        self.page.locator('[data-act=slowfast]').press('Enter')
        self.assertEqual(len(self.download_csv('[data-act=slowfast-csv]')), 229)

    def test_slowfast_numeric_sorts_export_complete_profiles(self):
        for key, field in [('fastCalls', 'fast_calls_per_root'), ('slowCalls', 'slow_calls_per_root'),
                           ('fastSelf', 'fast_self_per_root_ns'), ('slowSelf', 'slow_self_per_root_ns'),
                           ('delta', 'delta_self_per_root_ns')]:
            with self.subTest(sort=key):
                self.open_slowfast_data()
                self.page.locator('.an-slowfast-table th[data-sf-sort=name] button').press('Enter')
                button = self.page.locator(f'.an-slowfast-table th[data-sf-sort={key}] button')
                button.press('Enter')
                values = [float(row[field] or 0) for row in self.download_csv('[data-act=slowfast-csv]')]
                self.assertEqual(len(values), 229)
                self.assertEqual(values, sorted(values, reverse=True))
                button.press('Enter')
                values = [float(row[field] or 0) for row in self.download_csv('[data-act=slowfast-csv]')]
                self.assertEqual(len(values), 229)
                self.assertEqual(values, sorted(values))

    def test_breakdown_sections_slowfast(self):
        self.open_timeline()
        self.page.fill('.tl-search', '^__arm64_sys_openat$')
        self.page.press('.tl-search', 'Enter')
        self.page.wait_for_timeout(200)
        self.ev('document.activeElement.blur()')
        self.assertIn('own code', self.ev("document.querySelector('.an-bd-legend').innerText"))
        self.page.keyboard.press('h')
        self.page.wait_for_timeout(200)
        self.page.click('[data-act=slowfast]')
        self.page.wait_for_timeout(300)
        self.assertRegex(self.ev("document.querySelector('.tl-detail').innerText"), r'Slow vs fast')
        helpers.click_timeline_tool(self.page, '[data-act=an-sec]')
        self.page.wait_for_timeout(200)
        self.assertGreater(self.ev("document.querySelectorAll('tr[data-sec]').length"), 0)

    def test_source_block_modes(self):
        lid = self.ev("(() => { const c = Array.from(document.querySelectorAll('.expanded-content[data-src]')); return c[0].id; })()")
        line_id = lid[:-len('_content')]
        self.page.query_selector(f'[data-line-id="{line_id}"] .line-content').click()
        self.page.wait_for_timeout(200)
        self.assertEqual(self.ev("document.getElementById('srcModeBtn').textContent"), 'Source: chain')
        self.page.click('#srcModeBtn')
        self.page.wait_for_timeout(200)
        self.assertEqual(self.ev("document.getElementById('srcModeBtn').textContent"), 'Source: flat')
        self.assertEqual(self.ev(f"document.querySelectorAll('#{lid} details').length"), 0)

    def test_source_modes_visit_only_filled_panels_without_document_selector_scans(self):
        facts = self.ev('''() => {
            const panels = Array.from(document.querySelectorAll('.expanded-content[data-src]')).slice(0, 2);
            const originalSource = fgSourceHtml;
            fgSourceHtml = () => '<a class="location-link">inner at inner.c:2</a><div class="source-line">inner();</div>' +
                '<a class="location-link">outer at outer.c:4</a><div class="source-line">outer();</div>';
            panels.forEach(panel => fgFill(panel));
            const unopened = document.createElement('div');
            unopened.hidden = true;
            for (let index = 0; index < 1200; index++) {
                const panel = document.createElement('div');
                panel.className = 'expanded-content'; panel.dataset.src = panels[0].dataset.src;
                unopened.appendChild(panel);
            }
            document.body.appendChild(unopened);
            const control = document.createElement('button');
            control.dataset.fgSourceMode = '';
            document.body.appendChild(control);
            fgTrackSource(control);
            const query = document.querySelectorAll;
            const collection = document.getElementsByClassName;
            const fill = fgFill;
            const visited = [];
            let result;
            document.querySelectorAll = selector => { throw new Error('Unexpected document scan: ' + selector); };
            document.getElementsByClassName = name => { throw new Error('Unexpected document collection: ' + name); };
            fgFill = (panel, force) => { visited.push(panel.id); fill(panel, force); };
            try {
                fgToggleSrcMode();
                const flat = panels.map(panel => panel.querySelectorAll('.src-frame').length);
                fgToggleSrcMode();
                result = {visited, flat, chain:panels.map(panel => panel.querySelectorAll('.src-frame').length),
                    controls:[document.getElementById('srcModeBtn').textContent, control.textContent],
                    untouched:Array.from(unopened.children).every(panel => !panel.firstChild && panel.className === 'expanded-content'),
                    expected:panels.map(panel => panel.id)};
            } finally {
                document.querySelectorAll = query; document.getElementsByClassName = collection;
                fgFill = fill; fgSourceHtml = originalSource;
            }
            return result;
        }''')
        self.assertEqual(facts['visited'], facts['expected'] * 2)
        self.assertEqual(facts['flat'], [0, 0])
        self.assertEqual(facts['chain'], [2, 2])
        self.assertEqual(facts['controls'], ['Source: chain', 'Source: chain'])
        self.assertTrue(facts['untouched'])

    def test_source_mode_membership_follows_reordering_and_dynamic_panel_removal(self):
        facts = self.ev('''() => {
            const panel = document.querySelector('#content .expanded-content[data-src]');
            fgFill(panel);
            fgSetTextOrder('task');
            const dynamic = document.createElement('div');
            dynamic.dataset.src = panel.dataset.src;
            dynamic.className = 'expanded-content';
            document.body.appendChild(dynamic);
            fgFill(dynamic);
            const detached = document.createElement('div');
            detached.dataset.src = panel.dataset.src;
            detached.className = 'expanded-content';
            fgFill(detached);
            const fill = fgFill;
            const visited = [];
            fgFill = (source, force) => { visited.push(source); fill(source, force); };
            try {
                fgToggleSrcMode();
                const withDynamic = visited.length;
                dynamic.remove(); visited.length = 0;
                fgSetTextOrder('time');
                fgToggleSrcMode();
                const result = {withDynamic, remaining:visited.length, same:visited[0] === panel,
                    connected:visited.every(source => source.isConnected),
                    mode:fgSourceMode(), textMode:document.getElementById('srcModeBtn').textContent};
                visited.length = 0;
                document.body.appendChild(detached);
                fgToggleSrcMode();
                result.reattached = visited.length === 2 && visited.includes(detached);
                return result;
            } finally { fgFill = fill; }
        }''')
        self.assertEqual(facts, dict(withDynamic=2, remaining=1, same=True, connected=True, reattached=True,
                                     mode='chain', textMode='Source: chain'))

    def test_source_mode_tracking_does_not_retain_removed_panels(self):
        self.ev('''() => {
            const source = document.createElement('div');
            source.dataset.src = document.querySelector('.expanded-content[data-src]').dataset.src;
            source.className = 'expanded-content';
            document.body.appendChild(source);
            fgFill(source);
            window.removedSource = new WeakRef(source);
            source.remove();
        }''')
        session = self.page.context.new_cdp_session(self.page)
        try:
            session.send('HeapProfiler.collectGarbage')
            self.assertTrue(self.ev('removedSource.deref() === undefined'))
            self.ev('fgToggleSrcMode()')
            self.assertEqual(self.ev('fgSourceNodes.size'), 0)
        finally:
            session.detach()

    def test_source_mode_without_filled_panels_keeps_the_source_pool_unparsed(self):
        self.page.add_init_script('''
            const parse = JSON.parse;
            window.sourcePoolParses = 0;
            JSON.parse = (value, ...options) => {
                const pool = document.getElementById('fgSrc');
                if (pool && value === pool.textContent) window.sourcePoolParses++;
                return parse(value, ...options);
            };
        ''')
        self.page.reload(wait_until='load')
        self.assertEqual(self.ev('sourcePoolParses'), 0)
        facts = self.ev('''() => {
            const query = document.querySelectorAll;
            const read = fgData.read;
            document.querySelectorAll = selector => { throw new Error('Unexpected document scan: ' + selector); };
            fgData.read = name => { throw new Error('Unexpected data read: ' + name); };
            try {
                fgToggleSrcMode(); fgToggleSrcMode();
                return {parses:sourcePoolParses, tracked:fgSourceNodes.size,
                    label:document.getElementById('srcModeBtn').textContent};
            } finally { document.querySelectorAll = query; fgData.read = read; }
        }''')
        self.assertEqual(facts, dict(parses=0, tracked=0, label='Source: chain'))

    def test_source_modes_keep_working_without_weak_reference_support(self):
        self.page.add_init_script('window.WeakRef = undefined; window.FinalizationRegistry = undefined;')
        self.page.reload(wait_until='load')
        self.assertIsNone(self.ev('fgSourceNodes'))
        panel = self.page.locator('.expanded-content[data-src]').first
        panel.evaluate('element => fgFill(element)')
        self.ev('''() => {
            const control = document.createElement('button');
            control.id = 'legacySourceMode'; control.dataset.fgSourceMode = '';
            document.body.appendChild(control); fgTrackSource(control);
        }''')
        self.page.locator('#srcModeBtn').click()
        self.assertEqual(self.page.locator('#srcModeBtn').inner_text(), 'Source: flat')
        self.assertEqual(self.page.locator('#legacySourceMode').inner_text(), 'Source: flat')
        self.assertTrue(panel.evaluate('element => !!element.firstChild'))
        self.page.locator('#srcModeBtn').click()
        self.assertEqual(self.page.locator('#srcModeBtn').inner_text(), 'Source: chain')
        self.assertEqual(self.page.locator('#legacySourceMode').inner_text(), 'Source: chain')

    def test_inline_toggle_hidden_without_data(self):
        self.open_timeline()      # fake faddr2line has no inline chains
        self.assertEqual(self.ev("getComputedStyle(document.querySelector('[data-act=inl]')).display"), 'none')
        self.assertEqual(self.page.locator('.tl-options [data-act=inl]').count(), 0)

    def test_inline_toggle_is_next_to_fit_without_opening_tools(self):
        self.open_range_data(['outer', 'child'], starts=[0, 100], durations=[10000, 1000], parents=[-1, 0],
                             columns={'depth': [0, 1], 'adj': None},
                             extra={'inline': {'1': [['inlined', 'inline.h', 20], ['outer', 'outer.c', 10]]}})
        button = self.page.locator('[data-act=inl]')
        self.assertEqual(button.count(), 1)
        self.assertTrue(button.is_visible())
        self.assertEqual(button.inner_text(), 'Inline: hidden')
        self.assertEqual(self.page.locator('.tl-options [data-act=inl]').count(), 0)
        self.assertTrue(button.evaluate('''node => node.parentElement.classList.contains('tl-quick') &&
            node.previousElementSibling.lastElementChild.dataset.act === 'fit' '''))
        fit = self.page.locator('[data-act=fit]').bounding_box()
        bounds = button.bounding_box()
        self.assertAlmostEqual(bounds['y'], fit['y'])
        self.assertGreaterEqual(bounds['x'], fit['x'] + fit['width'])
        self.assertFalse(self.page.get_by_role('group', name='Timeline display', exact=True).is_visible())
        original = self.page.locator('#tlData').text_content()
        button.press('Space')
        self.assertEqual(button.inner_text(), 'Inline: shown')
        self.assertTrue(button.evaluate("node => node.classList.contains('on')"))
        self.page.locator('.tl-canvas').focus()
        self.page.keyboard.press('i')
        self.assertEqual(button.inner_text(), 'Inline: hidden')
        button.press('Enter')
        self.assertEqual(button.inner_text(), 'Inline: shown')
        self.assertEqual(self.ev("lsGet('tlInline')"), '1')
        self.assertEqual(self.page.locator('#tlData').text_content(), original)
        self.assertEqual(self.page.locator('[data-act=options]').get_attribute('aria-expanded'), 'false')

    def test_inline_toggle_stays_reachable_in_compact_and_fullscreen_layouts(self):
        self.page.close()
        self.page = self.browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('console', lambda message: self.errors.append(message.text) if message.type == 'error' else None)
        self.page.goto(self.url, wait_until='load')
        self.ev('localStorage.clear()')
        self.open_range_data(['outer', 'child'], starts=[0, 100], durations=[10000, 1000], parents=[-1, 0],
                             columns={'depth': [0, 1]},
                             extra={'inline': {'1': [['inlined', 'inline.h', 20], ['outer', 'outer.c', 10]]}})
        self.ev('Element.prototype.requestFullscreen = undefined')
        for theme, size, fullscreen in product(('light', 'dark'), ((390, 844), (320, 700), (844, 390)), (False, True)):
            with self.subTest(theme=theme, size=size, fullscreen=fullscreen):
                self.page.set_viewport_size(dict(zip(('width', 'height'), size)))
                self.page.wait_for_function('document.getElementById("timelineView").classList.contains("tl-compact")')
                self.ev('theme => document.documentElement.setAttribute("data-theme", theme)', theme)
                if self.ev('document.getElementById("timelineView").classList.contains("tl-fullscreen")') != fullscreen:
                    self.page.locator('[data-act=max]').click()
                self.page.wait_for_function('''fullscreen =>
                    document.getElementById('timelineView').classList.contains('tl-fullscreen') === fullscreen &&
                    !document.querySelector('[data-act=max]').disabled''', arg=fullscreen)
                for selector in ('[data-act=fit]', '[data-act=inl]', '[data-act=options]'):
                    bounds = self.page.locator(selector).bounding_box()
                    self.assertIsNotNone(bounds)
                    self.assertGreaterEqual(bounds['height'], 44)
                    self.assertGreaterEqual(bounds['x'], 0)
                    self.assertLessEqual(bounds['x'] + bounds['width'], size[0])
                button = self.page.locator('[data-act=inl]')
                button.click()
                self.assertEqual(button.inner_text(), 'Inline: shown')
                button.click()
                self.assertEqual(button.inner_text(), 'Inline: hidden')
                self.assertEqual(self.page.locator('[data-act=options]').get_attribute('aria-expanded'), 'false')

    def test_text_order_by_task(self):
        self.page.click('#textOrderBtn')
        self.page.wait_for_timeout(300)
        runs, groups, gaps = self.ev("""() => {
            const L = Array.from(document.querySelectorAll('#content .line-container')).filter(l => l.dataset.pid && !l.classList.contains('grp-hidden'));
            let runs = 0, prev = null;
            for (const l of L) { const k = l.dataset.pid === '0' ? 'i' + l.dataset.cpu : l.dataset.pid; if (k !== prev) { runs++; prev = k; } }
            return [runs, document.querySelectorAll('.task-group-head').length, document.querySelectorAll('.task-gap').length]; }""")
        self.assertEqual(runs, groups)                     # every task's lines are contiguous
        self.assertGreater(gaps, 0)                        # context switches summarized
        self.page.query_selector('.fold-entry .fold-icon').click()
        self.page.wait_for_timeout(100)
        self.page.click('#textOrderBtn')
        self.page.wait_for_timeout(300)
        self.assertTrue(self.ev("""(() => { const n = Array.from(document.querySelectorAll('#content .line-container')).map(l => +l.dataset.lineNumber);
            return n.every((x, i) => i === 0 || x > n[i - 1]) && !document.querySelector('.task-gap, .task-group-head'); })()"""))

    def test_task_order_projects_cached_records_without_reading_live_trace_text(self):
        facts = self.ev('''() => {
            const root = document.getElementById('content');
            const model = fgGetTextModel(root.querySelectorAll('.line-container'));
            const plan = fgTextModel.order(model, 'task');
            const attribute = Element.prototype.getAttribute;
            const text = Object.getOwnPropertyDescriptor(Node.prototype, 'textContent');
            Element.prototype.getAttribute = function(name) {
                if (this.classList.contains('line-container') && ['data-cpu','data-pid','data-comm'].includes(name))
                    throw new Error('Live task metadata read: ' + name);
                return attribute.call(this, name);
            };
            Object.defineProperty(Node.prototype, 'textContent', {...text, get() {
                if (this.nodeType === 1 && this.classList.contains('line-content')) throw new Error('Live trace text read');
                return text.get.call(this);
            }});
            try { fgSetTextOrder('task'); fgSetTextOrder('time'); fgSetTextOrder('task'); }
            finally {
                Element.prototype.getAttribute = attribute;
                Object.defineProperty(Node.prototype, 'textContent', text);
            }
            return {rows:Array.from(root.querySelectorAll('.line-container'), row => +row.dataset.lineNumber),
                expected:plan.order, groups:Array.from(root.querySelectorAll('.task-group-head, .task-gap'), node => node.textContent),
                expectedGroups:plan.boundaries.map(boundary => boundary.text),
                navigation:fgTextNavigation.order, same:fgTextModel.order(model, 'task') === plan};
        }''')
        self.assertEqual(facts['rows'], facts['expected'])
        self.assertEqual(facts['navigation'], facts['expected'])
        self.assertEqual(facts['groups'], facts['expectedGroups'])
        self.assertTrue(facts['same'])

    def test_incomplete_order_projection_fails_without_discarding_other_rows(self):
        result = self.ev('''() => {
            const root = document.getElementById('content');
            fgGetTextModel(root.querySelectorAll('.line-container'));
            root.lastElementChild.remove();
            const before = root.innerHTML, order = fgTextOrder;
            let message = '';
            try { fgSetTextOrder('task'); } catch (error) { message = error.message; }
            return {message, same:root.innerHTML === before, connected:root.isConnected, order:fgTextOrder === order};
        }''')
        self.assertEqual(result['message'], 'Text order does not cover mounted rows')
        self.assertTrue(result['same'])
        self.assertTrue(result['connected'])
        self.assertTrue(result['order'])

    def test_text_order_keeps_chunks_bounded_and_source_pairs_intact(self):
        snapshot = """() => {
            const content = document.getElementById('content');
            const chunks = Array.from(content.querySelectorAll('.text-chunk'));
            const rows = Array.from(content.querySelectorAll('.line-container'));
            return {pairs:rows.map(row => [row.id, row.nextElementSibling?.classList.contains('expanded-content') ? row.nextElementSibling.id : null])
                    .sort((left, right) => Number(left[0].slice(1)) - Number(right[0].slice(1))),
                sizes:chunks.map(chunk => chunk.querySelectorAll('.line-container').length),
                limits:chunks.map(chunk => Number(chunk.dataset.chunkSize)),
                nested:chunks.some(chunk => chunk.parentElement !== content),
                unpaired:content.querySelectorAll('.expanded-content').length - rows.filter(row => row.nextElementSibling?.classList.contains('expanded-content')).length,
                headers:Array.from(content.querySelectorAll('.task-group-head, .task-gap'), head =>
                    [head.parentElement === content, head.classList.contains('task-group-head'), getComputedStyle(head).position]),
                looseRows:rows.filter(row => !row.parentElement.classList.contains('text-chunk')).length};
        }"""
        before = self.ev(snapshot)
        self.assertGreater(len(before['sizes']), 1)
        self.assertEqual(len(set(pair[0] for pair in before['pairs'])), len(before['pairs']))
        for mode in ['task', 'time', 'desc', 'asc'] * 3:
            with self.subTest(mode=mode):
                if mode in ['task', 'time']:
                    self.page.fill('#filterInput', '')
                    self.ev('applyFilter()')
                    self.ev('mode => fgSetTextOrder(mode)', mode)
                else:
                    self.page.fill('#filterInput', 'duration:>0 sort:' + mode)
                    self.ev("""mode => {
                        const original = parseFilterExpr;
                        parseFilterExpr = text => ({...original(text.replace(/ sort:(asc|desc)$/, '')), sortDir:mode});
                        try { applyFilter(); } finally { parseFilterExpr = original; }
                    }""", mode)
                    durations = self.ev("Array.from(document.querySelectorAll('#content .line-container')).filter(row => row.style.display !== 'none').map(row => Number(row.dataset.sortDuration || row.dataset.duration || 0))")
                    self.assertGreater(len(durations), 512)
                    self.assertEqual(durations, sorted(durations, reverse=mode == 'desc'))
                    self.assertEqual(self.page.locator('#textOrderBtn').inner_text(), 'Order: duration ' + ('↓' if mode == 'desc' else '↑'))
                after = self.ev(snapshot)
                self.assertEqual(after['pairs'], before['pairs'])
                self.assertTrue(all(0 < size <= 512 for size in after['sizes']))
                self.assertEqual(set(after['limits']), {512})
                self.assertFalse(after['nested'])
                self.assertEqual(after['looseRows'], 0)
                self.assertEqual(after['unpaired'], 0)
                self.assertTrue(all(header[0] for header in after['headers']))
                if mode != 'task':
                    self.assertEqual(after['sizes'], before['sizes'])
                    self.assertEqual(after['headers'], [])
                else:
                    self.assertGreater(len(after['headers']), 0)
                    self.assertTrue(all(position == 'sticky' for _, is_head, position in after['headers'] if is_head))

    def open_dense_pixels(self, depth=1, inline=False, label='dense_child', mode='abs'):
        self.open_range_data(['root'] + [label] * 6, starts=[0, 100000, 100001, 100002, 100800, 100801, 100802],
                             durations=[1000000] + [1] * 6, parents=[-1] + [0] * 6, mode=mode,
                             callers=[None] + ['root+0x1/0x20'] * 6 if inline else None,
                             columns={'depth': [0] + [depth] * 6},
                             extra={'inline': {'2': [['root', 'a.c', 1], ['inline_frame', 'b.c', 2]]}} if inline else None)

    def canvas_pixel(self, index, at_start=False):
        return self.ev("""({index, at_start}) => {
            const probe = fgView('timeline').probe(), canvas = document.querySelector('.tl-canvas');
            const bounds = canvas.getBoundingClientRect(), point = probe.xyOf(index), ratio = window.devicePixelRatio || 1;
            if (at_start) { const view = probe.view(); point[0] = bounds.left + (probe.sStart[index] - view[0]) * bounds.width / (view[1] - view[0]); }
            const localX = Math.floor(point[0] - bounds.left) + 0.5, localY = point[1] - bounds.top;
            return {color: [...canvas.getContext('2d').getImageData(Math.floor(localX * ratio), Math.floor(localY * ratio), 1, 1).data],
                point: [bounds.left + localX, bounds.top + localY]};
        }""", {'index': index, 'at_start': at_start})

    def test_dense_pixels_keep_matching_members_bright_without_brightening_neighbors(self):
        self.open_dense_pixels()
        self.page.wait_for_timeout(80)
        bright = [self.canvas_pixel(index)['color'] for index in [1, 4]]
        self.ev("fgNotifyFilter('no calls', new Set())")
        self.page.wait_for_timeout(80)
        dimmed = [self.canvas_pixel(index)['color'] for index in [1, 4]]
        self.assertNotEqual(bright, dimmed)
        for line in [6, 5]:
            self.ev("line => fgNotifyFilter('one member', new Set([line]))", line)
            self.page.wait_for_timeout(80)
            self.assertEqual(self.canvas_pixel(1)['color'], dimmed[0])
            self.assertEqual(self.canvas_pixel(4)['color'], bright[1])
        self.page.fill('.tl-search', '^root$')
        self.page.wait_for_timeout(220)
        self.assertEqual(self.canvas_pixel(4)['color'], dimmed[1])
        self.page.fill('.tl-search', '')
        self.page.wait_for_timeout(80)
        self.assertEqual(self.canvas_pixel(4)['color'], bright[1])

    def test_dense_pixel_hover_and_click_explain_and_select_matching_representative(self):
        label = '<img src=x onerror=alert(1)>'
        self.open_dense_pixels(label=label)
        self.ev("fgNotifyFilter('middle member', new Set([3]))")
        self.page.wait_for_timeout(80)
        point = self.canvas_pixel(1)['point']
        self.page.mouse.move(*point)
        self.assertIn('3 frames', self.page.locator('.tl-tip').inner_text())
        self.assertIn('1 match', self.page.locator('.tl-tip').inner_text())
        self.assertIn(label, self.page.locator('.tl-tip .t-name').inner_text())
        self.assertEqual(self.page.locator('.tl-tip img').count(), 0)
        self.page.mouse.click(*point)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 2)

    def test_dense_pixel_keeps_exact_wide_hits_and_zero_duration_boundary_calls(self):
        self.open_range_data(['root', 'thin', 'wanted', 'wide'], starts=[0, 100000, 100001, 100002],
                             durations=[1000000, 1, 1, 200000], parents=[-1, 0, 0, 0], columns={'depth': [0, 1, 1, 1]})
        self.ev("fgNotifyFilter('thin match', new Set([3]))")
        point = self.canvas_pixel(1)['point']
        self.ev("""point => {
            const event = new MouseEvent('mousemove', {clientX: point[0], clientY: point[1], bubbles: true});
            Object.defineProperty(event, 'clientX', {value: point[0]});
            document.querySelector('.tl-canvas').dispatchEvent(event);
        }""", point)
        self.assertEqual(self.page.locator('.tl-tip .t-name').inner_text(), 'wide')
        self.assertNotIn('Dense pixel', self.page.locator('.tl-tip').inner_text())
        self.page.mouse.click(point[0] + 1, point[1])
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 3)
        self.ev("fgSwitchView('text')")
        self.page.reload()
        self.open_range_data(['root', 'zero', 'wanted'], starts=[0, 0, 0], durations=[1000000, 0, 0],
                             parents=[-1, 0, 0], columns={'depth': [0, 1, 1]})
        self.ev("fgNotifyFilter('zero match', new Set([3]))")
        self.page.wait_for_timeout(80)
        point = self.canvas_pixel(1)['point']
        self.page.mouse.move(*point)
        self.assertIn('2 frames', self.page.locator('.tl-tip').inner_text())
        self.assertIn('1 match', self.page.locator('.tl-tip').inner_text())
        self.page.mouse.click(*point)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 2)

    def test_dense_synthetic_frames_match_any_child_with_search_filter_intersection(self):
        for inline in [False, True]:
            with self.subTest(inline=inline):
                self.ev("fgSwitchView('text')")
                self.page.reload()
                self.open_dense_pixels(depth=1 if inline else 3, inline=inline)
                if inline:
                    self.ev("fgView('timeline').probe().inline(true)")
                synthetic = self.ev("fgView('timeline').probe().N")
                self.ev("fgNotifyFilter('all children', new Set([2, 3, 4, 5, 6, 7]))")
                self.page.wait_for_timeout(120)
                bright = self.canvas_pixel(synthetic, at_start=True)['color']
                self.ev("fgNotifyFilter('later child', new Set([6]))")
                self.page.wait_for_timeout(80)
                self.assertEqual(self.canvas_pixel(synthetic, at_start=True)['color'], bright)
                self.page.fill('.tl-search', '^root$')
                self.page.wait_for_timeout(220)
                self.assertNotEqual(self.canvas_pixel(synthetic, at_start=True)['color'], bright)
                self.page.fill('.tl-search', '')
                self.page.wait_for_timeout(80)
                self.assertEqual(self.canvas_pixel(synthetic, at_start=True)['color'], bright)
                helpers.click_timeline_tool(self.page, '[data-act=adj]')
                self.page.wait_for_timeout(80)
                self.assertEqual(self.canvas_pixel(synthetic, at_start=True)['color'], bright)

    def test_dense_synthetic_hover_and_filter_facts_are_cached_across_repaints(self):
        for inline in [False, True]:
            with self.subTest(inline=inline):
                self.open_detail_children(count=30000, inline=inline)
                if inline:
                    self.ev("fgView('timeline').probe().inline(true)")
                synthetic = self.ev("fgView('timeline').probe().N")
                self.ev("fgView('timeline').probe().select(-1)")
                self.ev("""() => {
                    const kids = fgView('timeline').probe().SYN[0].kids;
                    const iterate = kids[Symbol.iterator], some = kids.some;
                    window.syntheticGeometryWalks = 0; window.syntheticFilterWalks = 0;
                    kids[Symbol.iterator] = function() { window.syntheticGeometryWalks++; return iterate.call(this); };
                    kids.some = function(...args) { window.syntheticFilterWalks++; return some.apply(this, args); };
                    fgNotifyFilter('last child', new Set([30001]));
                }""")
                self.page.wait_for_timeout(80)
                for step in range(6):
                    point = self.canvas_pixel(synthetic)['point']
                    self.page.mouse.move(point[0] + step, point[1])
                helpers.click_timeline_tool(self.page, '[data-act=adj]')
                self.page.set_viewport_size({'width': 1400, 'height': 850})
                self.page.wait_for_timeout(80)
                point = self.canvas_pixel(synthetic)['point']
                self.page.mouse.move(*point)
                self.assertEqual(self.ev('window.syntheticGeometryWalks'), 0)
                self.assertEqual(self.ev('window.syntheticFilterWalks'), 1)
                self.page.fill('.tl-search', '^child$')
                self.page.wait_for_timeout(220)
                self.page.mouse.move(point[0] + 1, point[1])
                self.assertEqual(self.ev('window.syntheticGeometryWalks'), 0)
                self.assertEqual(self.ev('window.syntheticFilterWalks'), 2)

    def test_dense_matching_prefix_reuses_row_facts_for_hover_and_paint(self):
        count = 30000
        self.open_range_data(['root'] + ['child'] * count, starts=[0] + [100000] * count,
                             durations=[1000000] + [0] * count, parents=[-1] + [0] * count,
                             columns={'depth': [0] + [1] * count})
        self.ev("fgNotifyFilter('last child', new Set([30001]))")
        self.page.wait_for_timeout(100)
        self.ev("""() => {
            const probe = fgView('timeline').probe(), row = probe.TRACKS[0].rows[1];
            window.denseRowReads = 0;
            probe.TRACKS[0].rows[1] = new Proxy(row, {
                get(target, key) {
                    if (typeof key === 'string' && /^[0-9]+$/.test(key)) window.denseRowReads++;
                    return Reflect.get(target, key, target);
                }
            });
        }""")
        point = self.canvas_pixel(1)['point']
        self.page.mouse.move(*point)
        self.assertIn('30000 frames', self.page.locator('.tl-tip').inner_text())
        self.assertIn('1 match', self.page.locator('.tl-tip').inner_text())
        self.ev('window.denseRowReads = 0')
        for offset in [0.1, 0.2, 0.3, 0.4]:
            self.page.mouse.move(point[0] + offset, point[1])
            self.page.wait_for_timeout(25)
        self.assertLess(self.ev('window.denseRowReads'), 1000)
        self.page.mouse.click(*point)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), count)

    def test_dense_pixel_highlights_survive_time_modes_themes_and_device_scale(self):
        for mode in ['abs', 'rel', 'compressed', 'count']:
            with self.subTest(mode=mode):
                self.ev("fgSwitchView('text')")
                self.page.reload()
                self.open_dense_pixels(mode=mode)
                self.page.fill('.tl-search', '^dense_child$')
                self.page.wait_for_timeout(220)
                for theme in ['light', 'dark']:
                    self.ev("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                    self.page.wait_for_timeout(80)
                    self.ev("fgNotifyFilter('all children', new Set([2, 3, 4, 5, 6, 7]))")
                    self.page.wait_for_timeout(80)
                    bright = self.canvas_pixel(4)['color']
                    self.ev("fgNotifyFilter('last child', new Set([7]))")
                    self.page.wait_for_timeout(80)
                    self.assertEqual(self.canvas_pixel(4)['color'], bright)
        context = self.browser.new_context(viewport={'width': 800, 'height': 600}, device_scale_factor=2)
        original = self.page
        try:
            self.page = context.new_page()
            self.page.on('pageerror', lambda error: self.errors.append(str(error)))
            self.page.goto(self.url)
            self.open_dense_pixels()
            self.ev("fgNotifyFilter('last child', new Set([4]))")
            self.page.wait_for_timeout(100)
            point = self.canvas_pixel(1)['point']
            self.page.mouse.move(*point)
            self.assertIn('3 frames', self.page.locator('.tl-tip').inner_text())
            self.assertIn('1 match', self.page.locator('.tl-tip').inner_text())
            self.page.mouse.click(*point)
            self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 3)
        finally:
            context.close()
            self.page = original

    def test_wide_slice_after_thin_one_is_drawn(self):
        # compressed time: a 1 ns call, then a 100 us call starting in the same pixel at full view
        trace = os.path.join(self.k.dir, 'thin_wide.txt')
        with open(trace, 'w') as f:
            f.write(' 0)   a-1     |   0.001 us    |  thin();\n'
                    ' 0)   a-1     | ! 100.000 us  |  wide();\n')
        self.k.run(trace, '--timeline', '--no-text-view', out='thin_wide.html')
        self.page.goto('file://' + self.k.dir + '/thin_wide.html', wait_until='load')
        self.page.wait_for_timeout(500)
        # middle of the first call row (track header 20 px + half a 17 px row)
        # compared with an empty spot of the same canvas (a deeper row without calls)
        painted = self.ev("""(() => { const cv = document.querySelector('.tl-canvas'), d = window.devicePixelRatio || 1;
            const g = cv.getContext('2d'), x = Math.round(cv.width / 2);
            const a = g.getImageData(x, Math.round(28 * d), 1, 1).data, e = g.getImageData(x, Math.round(80 * d), 1, 1).data;
            return Math.abs(a[0] - e[0]) + Math.abs(a[1] - e[1]) + Math.abs(a[2] - e[2]) > 30; })()""")
        self.assertTrue(painted, 'wide slice not drawn at full view')

    def test_wakeup_overlay_toggle(self):
        self.open_timeline()
        self.assertFalse(self.ev("document.querySelector('[data-ov=wk]').classList.contains('na')"))
        helpers.click_timeline_tool(self.page, '[data-ov=wk]')
        helpers.click_timeline_tool(self.page, '[data-ov=wk]')

    def test_scheduling_overlays_keep_boundary_geometry_and_first_hit(self):
        for ordered in (True, False):
            with self.subTest(ordered=ordered):
                self.ev('localStorage.clear()')
                self.page.goto(self.url)
                self.assertEqual(self.ev('fgCurrentView()'), 'text')
                outside = [(index * 20 + 2000, index * 20 + 2005, 0) for index in range(256)]
                edge, crossing, overlap, right = (9000, 10000, 0), (9200, 12000, 1), (10500, 12500, 2), (21600, 21600, 0)
                intervals = (outside + [edge, crossing, overlap, right] if ordered
                             else [overlap] + outside + [edge, right, crossing])
                waits = [[0, start, end, 0, name, -1, 0, 0, 0, -1] for start, end, name in intervals]
                waits.append([0, 31000, None, 0, 2, -1, 0, -1, 0, -1])
                self.open_range_data(['work', 'first_wait', 'second_wait'], starts=[0, 1000, 39900],
                                     durations=[1, 1, 100], extra=dict(offcpu=waits,
                                         resched=[[0, start, end, 1, 0, 0] for start, end, _ in intervals],
                                         irqoff=[[0, start, end] for start, end, _ in intervals]))
                self.ev('''() => {
                    window.schedulingGeometry = async () => {
                        const api = fgView('timeline'), probe = api.probe();
                        const canvas = document.querySelector('.tl-canvas'), ctx = canvas.getContext('2d');
                        const top = probe.TRACKS[0].y - document.querySelector('.tl-scroll').scrollTop;
                        const rects = {resched: [], irq: [], waits: []}, original = ctx.fillRect;
                        ctx.fillRect = function (rectX, rectY, width, height) {
                            const name = rectY === top + 16 && height === 3 ? 'resched' :
                                rectY === top + 18 && height === 2 ? 'irq' : rectY === top + 35 && height === 1 ? 'waits' : null;
                            if (name) rects[name].push([Math.round(rectX), Math.round(width)]);
                            return original.apply(this, arguments);
                        };
                        try {
                            probe.zoomTo(10800, 20800, -1);
                            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                            return {rects, width: canvas.clientWidth, view: probe.view()};
                        } finally { ctx.fillRect = original; }
                    };
                }''')
                result = self.ev('schedulingGeometry()')
                self.assertEqual(result['view'], [10000, 21600])
                width = result['width']
                positions = [0, 0, round(width * 500 / 11600), width]
                if not ordered:
                    positions = [positions[index] for index in (2, 0, 3, 1)]
                for rects in result['rects'].values():
                    self.assertEqual([max(0, rect[0]) for rect in rects], positions)
                    self.assertEqual(sum(rect[0] <= 0 and rect[1] > 2 for rect in rects), 1)
                point = self.ev('''() => {
                    const probe = fgView('timeline').probe();
                    return probe.offXY([0, 11000, 11000, 0]);
                }''')
                self.page.mouse.click(*point)
                self.assertIn('in first_wait' if ordered else 'in second_wait',
                              self.page.locator('.an-wait-head').inner_text())
                self.page.click('.an-wait-head .tl-close')
                for overlay in ('off', 'rs', 'irq'):
                    helpers.click_timeline_tool(self.page, f'[data-ov={overlay}]')
                self.assertEqual(self.ev('schedulingGeometry()')['rects'], dict(resched=[], irq=[], waits=[]))
                self.page.mouse.click(*point)
                self.assertFalse(self.page.locator('.an-wait-head').is_visible())
                helpers.click_timeline_tool(self.page, '[data-ov=off]')
                open_point = self.ev('''() => {
                    const probe = fgView('timeline').probe();
                    probe.zoomTo(35000, 38000, -1);
                    return probe.offXY(probe.TRACKS[0].off.at(-1));
                }''')
                self.page.mouse.click(*open_point)
                self.assertIn('blocked ≥', self.page.locator('.an-wait-head').inner_text())

    def test_wakeup_overlay_indexes_intersections_without_reordering(self):
        def wake(time, off=-1):
            return [0, time, 1, 0, -1, off]

        ends = [25000, 9200, None]
        offcpu = [[1, 0, end, 0, 0, 1, 0, 0, 0, -1] for end in ends]
        wakes = [wake(50, 0)] + [wake(35000)] * 127 + [wake(1000, 1), wake(20800), wake(3000, 2), wake(12000)]
        wakes += [wake(35000)] * 124 + [wake(31000 + index * 10) for index in range(16)]
        self.open_range_data(['waker', 'sleeper'], starts=[0, 0], durations=[40000, 40000],
                             columns={'task': [0, 1]}, extra=dict(wakeups=wakes, offcpu=offcpu,
                                 tasks=[dict(pid=1, comm='waker', kind=0, cpu=0),
                                        dict(pid=2, comm='sleeper', kind=0, cpu=0)]))
        self.ev('''() => {
            window.measureWakeups = async (start, end) => {
                const model = fgView('timeline').probe(), canvas = document.querySelector('.tl-canvas');
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                const ctx = canvas.getContext('2d'), curves = [], labels = [];
                const curve = ctx.bezierCurveTo, text = ctx.fillText;
                ctx.bezierCurveTo = function (...args) { curves.push(args[4]); return curve.apply(this, args); };
                ctx.fillText = function (...args) {
                    if (args[0].includes('wakeups bunched')) labels.push(args);
                    return text.apply(this, args);
                };
                try {
                    model.zoomTo(start, end, -1);
                    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    const [left, right] = model.view(), width = canvas.clientWidth;
                    return {ends: curves.map(px => Math.round(left + px * (right - left) / width)), labels, width};
                } finally { ctx.bezierCurveTo = curve; ctx.fillText = text; }
            };
        }''')
        result = self.ev('measureWakeups(10000, 20000)')
        self.assertEqual(result['ends'], [25000, 9200, 20800, 40000, 12000])
        self.assertEqual(result['labels'], [])
        self.page.set_viewport_size({'width': 350, 'height': 680})
        result = self.ev('measureWakeups(31000, 31150)')
        self.assertEqual(result['ends'], [])
        label, x, _y, width = result['labels'][0]
        self.assertEqual(label, '17 of 17 wakeups bunched')
        self.assertGreaterEqual(x, 0)
        self.assertLessEqual(x + width, result['width'])
        self.ev("fgView('timeline').probe().select(0)")
        result = self.ev('measureWakeups(31000, 31150)')
        self.assertEqual(result['ends'], [40000] + [31000 + index * 10 for index in range(16)])

    def test_dense_wakeups_keep_track_direction_and_hover_past_curve_limit(self):
        wakes = [[0, 20000, 1, 0, -1, -1]] * 405 + [[0, 22000, 1, 0, -1, -1]]
        wakes += [[2, 22000, 1, 0, -1, -1]] * 25 + [[2, 22000, 0, 0, -1, -1]] * 25
        wakes += [[0, 83000, 1, 0, -1, 0]]
        self.open_range_data(['first', 'middle', 'last'], starts=[0, 0, 0], durations=[100000, 6000, 4000],
                             columns={'task': [0, 1, 2]}, extra=dict(wakeups=wakes,
                                 offcpu=[[1, 82000, 86000, 0, 0, 1, 0, 0, 0, -1]],
                                 tasks=[dict(pid=index + 1, comm=name, kind=0, cpu=0)
                                        for index, name in enumerate(['first', 'middle', 'last'])]))
        self.ev('''() => {
            const ctx = document.querySelector('.tl-canvas').getContext('2d');
            const begin = ctx.beginPath, move = ctx.moveTo, line = ctx.lineTo;
            const curve = ctx.bezierCurveTo, text = ctx.fillText;
            ctx.beginPath = function () {
                if (this.globalAlpha > 0.5 && this.globalAlpha < 0.6) window.wakePaint.batches++;
                return begin.apply(this, arguments);
            };
            ctx.moveTo = function (x, y) {
                if (this.globalAlpha > 0.5 && this.globalAlpha < 0.6) window.wakePaint.heads.push([x, y]);
                return move.apply(this, arguments);
            };
            ctx.lineTo = function (x, y) {
                if (this.globalAlpha > 0.5 && this.globalAlpha < 0.6) {
                    const head = window.wakePaint.heads.at(-1);
                    if (head.length === 2) head.push(Math.sign(head[1] - y));
                }
                return line.apply(this, arguments);
            };
            ctx.bezierCurveTo = function (...args) {
                window.wakePaint.curves.push({x: args[4], y: args[5], color: this.strokeStyle});
                return curve.apply(this, args);
            };
            ctx.fillText = function (label, ...args) {
                if (label.includes('wakeups bunched')) window.wakePaint.label = label;
                return text.call(this, label, ...args);
            };
            window.wakePaint = {heads: [], batches: 0, curves: [], label: ''};
            window.readWakePaint = async () => {
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                const width = document.querySelector('.tl-canvas').clientWidth;
                window.wakePaint = {heads: [], batches: 0, curves: [], label: '', width};
                fgView('timeline').draw();
                await new Promise(requestAnimationFrame);
                return window.wakePaint;
            };
        }''')
        paint = self.ev('readWakePaint()')
        self.assertEqual(paint['curves'], [])
        self.assertEqual(paint['batches'], 1)
        self.assertEqual(len(paint['heads']), 5)
        self.assertIn(f'{len(wakes)} of {len(wakes)} wakeups bunched', paint['label'])
        _, shared_x, _ = sorted({head[0] for head in paint['heads']})
        shared = [head for head in paint['heads'] if head[0] == shared_x]
        self.assertEqual(len(shared), 3)
        top_row, middle_row = sorted({head[1] for head in shared})
        self.assertEqual({head[2] for head in shared if head[1] == top_row}, {-1})
        self.assertEqual({head[2] for head in shared if head[1] == middle_row}, {-1, 1})

        self.ev("fgView('timeline').probe().select(0)")
        paint = self.ev('readWakePaint()')
        self.assertEqual(len(paint['curves']), 400)
        head_positions = {head[0] for head in paint['heads']}
        self.assertEqual(len(head_positions), 3)
        for fraction, position in zip([0.20, 0.22, 0.86], sorted(head_positions)):
            self.assertAlmostEqual(position, fraction * paint['width'], delta=2)
        self.assertIn('400 full arrows', paint['label'])
        point = self.ev("(() => { const model = fgView('timeline').probe(); return model.offXY(model.TRACKS[1].off[0]); })()")
        self.page.mouse.move(*point)
        paint = self.ev('readWakePaint()')
        self.assertEqual(len(paint['curves']), 400)
        self.assertEqual(paint['curves'][-1]['color'], '#0b7a6b')
        self.assertAlmostEqual(paint['curves'][-1]['x'], 0.86 * paint['width'], delta=1)
        self.assertFalse(any(abs(head[0] - 0.86 * paint['width']) <= 2 for head in paint['heads']))

    def test_range_summary_and_theme(self):
        self.open_timeline()
        box = self.page.query_selector('.tl-canvas').bounding_box()
        self.page.keyboard.down('Shift')
        self.page.keyboard.down('Alt')
        self.page.mouse.move(box['x'] + box['width'] * 0.1, box['y'] + 30)
        self.page.mouse.down()
        self.page.mouse.move(box['x'] + box['width'] * 0.9, box['y'] + 30, steps=4)
        self.page.mouse.up()
        self.page.keyboard.up('Alt')
        self.page.keyboard.up('Shift')
        self.page.wait_for_timeout(300)
        self.assertGreater(self.ev("document.querySelectorAll('table.tl-sum tbody tr').length"), 5)
        self.ev('toggleTheme()')
        for mode in ('name', 'total', 'heat', 'cpu', 'ctx'):
            helpers.open_timeline_tools(self.page)
            self.page.get_by_label('Color', exact=True).select_option(mode)
            self.assertEqual(self.page.get_by_label('Color', exact=True).input_value(), mode)
            self.assertGreater(self.ev("document.querySelectorAll('table.tl-sum tbody tr').length"), 5)
        self.page.wait_for_timeout(200)


if __name__ == '__main__':
    unittest.main()
