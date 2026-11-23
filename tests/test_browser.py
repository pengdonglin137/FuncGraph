"""Headless browser smoke test of a generated report (skipped without Playwright + Chromium)."""
import base64
import csv
import io
from itertools import product
import os
import unittest
from urllib.parse import unquote, urlsplit

import helpers
from fglib.timeline import F_LEAF

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

    def assert_detail_table_headers(self):
        self.assertTrue(self.ev('''() => {
            const pane = document.querySelector('.tl-detail');
            const heading = pane.querySelector('.tl-detail-head');
            const headers = pane.querySelectorAll('table.tl-sum th');
            const short = pane.clientHeight < 140;
            const style = getComputedStyle(heading);
            const pinned = style.position === 'sticky' && style.top !== 'auto';
            const top = short ? 'auto' : (pinned ? heading.offsetHeight : 0) + 'px';
            return headers.length > 0 && Array.from(headers).every(header => {
                const computed = getComputedStyle(header);
                return computed.position === (short ? 'static' : 'sticky') && computed.top === top;
            });
        }'''))

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

    def test_inactive_views_keep_layout_without_focus_or_scroll_footprint(self):
        facts = self.ev('''async () => {
            const controls = {timeline: '.tl-search', compare: '.cmp-export'};
            const result = {};
            for (const [view, selector] of Object.entries(controls)) {
                await fgSwitchView(view);
                const root = document.getElementById(view + 'View');
                const target = root.querySelector(selector);
                const visible = getComputedStyle(root);
                const active = [visible.display, visible.contentVisibility];
                target.focus({preventScroll: true});
                const activeFocusable = target === document.activeElement;
                await fgSwitchView('text');
                const textTab = document.querySelector('.view-tab[data-view=text]');
                textTab.focus({preventScroll: true});
                target.focus({preventScroll: true});
                const hidden = getComputedStyle(root), rect = root.getBoundingClientRect();
                result[view] = {active, activeFocusable,
                    inactive: [hidden.display, hidden.visibility, hidden.contentVisibility],
                    footprint: [rect.width, rect.height], blocked: document.activeElement === textTab};
            }
            return result;
        }''')
        expected = dict(active=['flex', 'visible'], activeFocusable=True,
                        inactive=['flex', 'hidden', 'hidden'], footprint=[0, 0], blocked=True)
        self.assertEqual(facts, dict(timeline=expected, compare=expected))

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

    def test_native_fullscreen_view_is_not_collapsed_during_exit(self):
        self.page.set_viewport_size(dict(width=390, height=700))
        self.open_timeline()
        self.page.locator('[data-act=max]').click()
        self.page.wait_for_function('document.fullscreenElement?.id === "timelineView"')
        result = self.ev('''() => {
            const root = document.fullscreenElement;
            const exit = document.exitFullscreen;
            let finishNativeExit;
            document.exitFullscreen = () => new Promise((resolve, reject) => {
                finishNativeExit = () => exit.call(document).then(resolve, reject);
            });
            try {
                fgSwitchView('text');
                const inactive = !root.classList.contains('fg-view-on');
                const pending = document.fullscreenElement === root && !!finishNativeExit;
                const collapsed = getComputedStyle(root).contentVisibility === 'hidden';
                if (!collapsed) fgSwitchView('timeline');
                return {inactive, pending, collapsed, view: fgCurrentView()};
            } finally {
                document.exitFullscreen = exit;
                finishNativeExit?.();
            }
        }''')
        self.assertEqual(result, dict(inactive=True, pending=True, collapsed=False, view='timeline'))
        self.page.wait_for_function('!document.fullscreenElement')
        self.assertTrue(self.page.locator('#viewHelpBtn').is_visible())
        self.assertFalse(self.page.locator('#timelineHelpBtn').is_visible())

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

    def test_pending_timeline_help_can_be_cancelled_without_cancelling_the_view(self):
        for action in ['escape', 'repeat']:
            with self.subTest(action=action):
                self.ev('localStorage.clear()')
                self.page.goto(self.url, wait_until='load')
                self.stage_timeline(slow=True)
                facts = self.ev('''action => {
                    window.pendingHelpView = fgSwitchView('timeline');
                    const button = document.getElementById('viewHelpBtn');
                    button.click();
                    const requested = fgHelpPending;
                    if (action === 'escape') window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',cancelable:true}));
                    else button.click();
                    return {requested,pending:fgHelpPending,view:fgCurrentView()};
                }''', action)
                self.assertEqual(facts, dict(requested=True,pending=False,view='timeline'))
                self.assertTrue(self.ev('async () => !!(await pendingHelpView)'))
                self.assertFalse(self.page.locator('#timelineHelp').is_visible())
                self.assertEqual(self.page.locator('#viewHelpBtn').get_attribute('aria-expanded'), 'false')
                self.page.locator('#viewHelpBtn').click()
                self.page.locator('#timelineHelp').wait_for(state='visible')
                self.page.keyboard.press('Escape')
                self.assertEqual(self.ev('fgCurrentView()'), 'timeline')

    def test_pending_help_does_not_reopen_after_tabbing_or_clicking_elsewhere(self):
        for action in ['tab', 'pointer']:
            with self.subTest(action=action):
                self.ev('localStorage.clear()')
                self.page.goto(self.url, wait_until='load')
                self.stage_timeline(slow=True)
                facts = self.ev('''action => {
                    window.pendingFocusView = fgSwitchView('timeline');
                    const button = document.getElementById('viewHelpBtn');
                    button.focus(); button.click();
                    window.helpOtherControl = document.querySelector('.header-actions button:last-child');
                    const event = action === 'tab' ? new KeyboardEvent('keydown',{key:'Tab',cancelable:true}) :
                        new PointerEvent('pointerdown',{bubbles:true,cancelable:true});
                    (action === 'tab' ? window : helpOtherControl).dispatchEvent(event);
                    helpOtherControl.focus({preventScroll:true});
                    return {pending:fgHelpPending,prevented:event.defaultPrevented};
                }''', action)
                self.assertEqual(facts, dict(pending=False,prevented=False))
                self.assertTrue(self.ev('async () => !!(await pendingFocusView)'))
                self.assertFalse(self.page.locator('#timelineHelp').is_visible())
                self.assertTrue(self.ev('document.activeElement === helpOtherControl'))

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
                query = page.get_by_role('textbox', name='Filter trace lines')
                query_bounds = query.bounding_box()
                for name in ('Apply', 'Clear', 'Filter syntax help'):
                    action_bounds = filter_box.get_by_role('button', name=name, exact=True).bounding_box()
                    self.assertGreaterEqual(action_bounds['height'], 44)
                    self.assertLessEqual(action_bounds['x'] + action_bounds['width'], width)
                    if width <= 420:
                        self.assertGreaterEqual(action_bounds['y'], query_bounds['y'] + query_bounds['height'])
                if width <= 420:
                    self.assertGreater(query_bounds['width'], width * 0.85)
                page.locator('.field-btn[data-field=cpu]').tap()
                dropdown = page.locator('.field-dropdown')
                bounds = dropdown.bounding_box()
                self.assertLessEqual(bounds['x'] + bounds['width'], width)
                self.assertLessEqual(bounds['y'] + bounds['height'], height)
                page.keyboard.press('Escape')
                self.assertFalse(dropdown.is_visible())
                self.assertTrue(filter_box.is_visible())
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
                page.get_by_role('button', name='Tools · Filter on', exact=True).wait_for(state='visible')
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

    def test_fold_controls_continue_navigation_from_the_focused_call(self):
        parent, child, grandchild, outside = self.nested_fold_rows()
        for order, folded, selector, key in product(
                ['time', 'task'], [False, True], ['.fold-icon', '.fold-brace'],
                ['j', 'k', 'ArrowDown', 'ArrowUp']):
            direction = -1 if key in ['k', 'ArrowUp'] else 1
            with self.subTest(order=order, folded=folded, selector=selector, key=key):
                expected = self.ev('''({order,folded,child,outside,direction}) => {
                    unfoldAllBlocks(); fgSetTextOrder(order);
                    if (folded) toggleFold(child.fold);
                    setKeyboardSelectedLine(fgTextNavigation.indexOf(+outside.row.slice(1)), false);
                    const next = fgTextNavigation.next(fgTextNavigation.indexOf(+child.row.slice(1)), direction);
                    return 'L' + fgTextNavigation.order[next];
                }''', dict(order=order,folded=folded,child=child,outside=outside,direction=direction))
                self.page.locator('#' + child['row'] + ' ' + selector).press(key)
                self.assertEqual(self.ev('keyboardSelectedLine?.id'), expected)
                self.assertTrue(self.ev('document.activeElement === keyboardSelectedLine'))

    def test_fold_controls_keep_depth_keys_and_escape_without_toggling_source(self):
        parent, child, grandchild, outside = self.nested_fold_rows()
        self.expand_fold_source(grandchild)
        self.ev('''() => {
            window.foldSourceCalls = 0;
            const original = toggleExpand;
            toggleExpand = (...parameters) => { foldSourceCalls++; return original(...parameters); };
        }''')
        for selector in ['.fold-icon', '.fold-brace']:
            with self.subTest(selector=selector):
                control = self.page.locator('#' + parent['row'] + ' ' + selector)
                control.press('1')
                self.assertFalse(self.page.locator('#' + grandchild['row']).is_visible())
                control.press('0')
                self.assertTrue(self.page.locator('#' + grandchild['row']).is_visible())
                self.assertTrue(self.page.locator('#' + grandchild['line'] + '_content').is_visible())
                control.press('Escape')
                self.assertTrue(self.ev('document.activeElement === document.body'))
                self.assertIsNone(self.ev('keyboardSelectedLine'))
        self.assertEqual(self.ev('foldSourceCalls'), 0)

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

    def test_filter_syntax_help_is_a_keyboard_disclosure(self):
        panel = self.page.locator('#filterBox')
        trigger = panel.get_by_role('button', name='Filter syntax help', exact=True)
        help_panel = panel.locator('#filterSyntaxHelp')
        close = help_panel.get_by_role('button', name='Close filter syntax help')
        query = panel.locator('#filterInput')
        query.fill('func:pending')
        before = self.ev('''() => {
            scrollTo(0, 500);
            return [scrollY, document.querySelector('.controls').offsetHeight];
        }''')
        trigger.press('Enter')
        self.assertEqual(trigger.get_attribute('aria-controls'), 'filterSyntaxHelp')
        self.assertEqual(trigger.get_attribute('aria-expanded'), 'true')
        self.assertTrue(close.evaluate('button => button === document.activeElement'))
        self.assertEqual(self.ev("[scrollY, document.querySelector('.controls').offsetHeight]"), before)
        close.press('Space')
        self.assertFalse(help_panel.is_visible())
        self.assertEqual(trigger.get_attribute('aria-expanded'), 'false')
        self.assertTrue(trigger.evaluate('button => button === document.activeElement'))
        self.assertEqual(self.ev("[scrollY, document.querySelector('.controls').offsetHeight]"), before)
        trigger.press('Enter')
        self.page.locator('.view-tab[data-view=timeline]').click()
        self.page.locator('.view-tab[data-view=text]').click()
        self.assertFalse(help_panel.is_visible())
        self.assertEqual(trigger.get_attribute('aria-expanded'), 'false')
        self.assertEqual(query.input_value(), 'func:pending')
        self.assertEqual(self.ev('fgFilterState.text'), '')

    def test_compact_filter_syntax_help_scrolls_and_escapes_before_filter(self):
        with self.browser.new_context(viewport=dict(width=320, height=350), is_mobile=True, has_touch=True) as context:
            page = context.new_page()
            page.on('pageerror', lambda error: self.errors.append(str(error)))
            page.goto(self.url)
            toggle = page.locator('#textFilterToggle')
            panel = page.locator('#filterBox')
            trigger = panel.get_by_role('button', name='Filter syntax help', exact=True)
            help_panel = panel.locator('#filterSyntaxHelp')
            close = help_panel.get_by_role('button', name='Close filter syntax help')
            query = panel.locator('#filterInput')
            toggle.press('Enter')
            query.fill('func:pending')
            before = page.evaluate('scrollY')
            trigger.press('Enter')
            self.assertTrue(close.evaluate('''button => {
                const bounds = button.getBoundingClientRect();
                return button === document.activeElement && bounds.height >= 44 &&
                    button.contains(document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2));
            }'''))
            previous = panel.evaluate('box => box.scrollTop')
            page.keyboard.press('PageDown')
            page.wait_for_function('old => document.getElementById("filterBox").scrollTop > old', arg=previous)
            self.assertEqual(page.evaluate('scrollY'), before)
            page.keyboard.press('Escape')
            self.assertFalse(help_panel.is_visible())
            self.assertTrue(panel.is_visible())
            self.assertTrue(trigger.evaluate('''button => {
                const bounds = button.getBoundingClientRect();
                return button === document.activeElement &&
                    button.contains(document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2));
            }'''))
            self.assertEqual(trigger.get_attribute('aria-expanded'), 'false')
            self.assertEqual(query.input_value(), 'func:pending')
            self.assertEqual(page.evaluate('scrollY'), before)
            page.keyboard.press('Escape')
            self.assertFalse(panel.is_visible())
            self.assertTrue(toggle.evaluate('button => button === document.activeElement'))

    def test_field_dropdown_tracks_its_trigger_inside_the_compact_filter(self):
        with self.browser.new_context(viewport=dict(width=320, height=350), is_mobile=True, has_touch=True) as context:
            page = context.new_page()
            page.on('pageerror', lambda error: self.errors.append(str(error)))
            page.set_default_timeout(5000)
            page.goto(self.url)
            toggle = page.locator('#textFilterToggle')
            toggle.tap()
            panel = page.locator('#filterBox')
            menu = page.locator('#textFieldDropdown')
            query = panel.locator('#filterInput')
            query.fill('func:pending')
            scroll_position = page.evaluate('scrollY')
            owner = panel.locator('.field-btn[data-field=cpu]')
            owner.tap()
            before = [owner.bounding_box()['y'], menu.bounding_box()['y']]
            panel.evaluate('''async panel => {
                await new Promise(requestAnimationFrame);
                window.oldFitPanel = fgFitTextControlPanel;
                window.outerPanelFits = 0;
                fgFitTextControlPanel = controls => { outerPanelFits++; oldFitPanel(controls); };
                panel.scrollTop += 24;
                await new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)));
            }''')
            after = [owner.bounding_box()['y'], menu.bounding_box()['y']]
            self.assertAlmostEqual(after[0], before[0] - 24, delta=1)
            self.assertAlmostEqual(after[1], before[1] - 24, delta=1)
            self.assertEqual(page.evaluate('outerPanelFits'), 0)
            self.assertEqual(owner.get_attribute('aria-expanded'), 'true')
            page.keyboard.press('Escape')

            owner = panel.locator('.field-btn[data-field=notrace]')
            owner.tap()
            self.assertTrue(menu.locator('input').evaluate('input => document.activeElement === input'))
            panel.evaluate('panel => { panel.scrollTop = 0; }')
            menu.wait_for(state='detached')
            self.assertEqual(owner.get_attribute('aria-expanded'), 'false')
            self.assertTrue(toggle.evaluate('button => document.activeElement === button'))
            self.assertEqual(query.input_value(), 'func:pending')
            self.assertEqual(page.evaluate('scrollY'), scroll_position)
            self.assertTrue(panel.is_visible())

    def test_text_more_keyboard_focus_enters_tools_and_leaves_without_scrolling(self):
        for width, height in [(380, 600), (844, 280)]:
            with self.subTest(width=width, height=height):
                self.page.set_viewport_size(dict(width=width, height=height))
                more = self.page.locator('#textMoreToggle')
                options = self.page.locator('#textToolbarOptions')
                more.wait_for()
                more.focus()
                self.ev('window.scrollTo(0, 500)')
                before = self.ev('scrollY')
                more.press('Enter')
                self.assertTrue(options.evaluate('''panel => {
                    const first = panel.querySelector('button:not(:disabled)');
                    return first === document.activeElement && first.getBoundingClientRect().bottom <= panel.getBoundingClientRect().bottom;
                }'''))
                self.assertEqual(self.ev('scrollY'), before)
                self.page.keyboard.press('Escape')
                self.assertFalse(options.is_visible())
                self.assertTrue(more.evaluate('button => button === document.activeElement'))

                more.press('Space')
                self.page.keyboard.press('Shift+Tab')
                self.assertFalse(options.is_visible())
                self.assertTrue(self.page.locator('#textTimelineJump').evaluate('button => button === document.activeElement'))
                self.assertEqual(self.ev('scrollY'), before)

    def test_fold_guides_lead_secondary_tools_and_stay_reachable(self):
        for width in [380, 800, 1500]:
            self.page.set_viewport_size({'width':width, 'height':600})
            self.ev('window.scrollTo(0, 0)')
            more = self.page.locator('#textMoreToggle')
            more.wait_for(state='visible' if width == 380 else 'hidden')
            if width == 380:
                self.assertFalse(self.page.locator('#foldGuidesBtn').is_visible())
                more.click()
            bounds = self.page.locator('#foldGuidesBtn').bounding_box()
            self.assertIsNotNone(bounds)
            self.assertGreaterEqual(bounds['x'], 0)
            self.assertLess(bounds['x'] + bounds['width'], width)
            self.assertTrue(self.page.locator('#foldGuidesBtn').evaluate('button => button.closest("#textToolbarOptions") !== null && button === button.parentElement.firstElementChild'))
            self.assertGreaterEqual(bounds['y'], 0)
            self.assertLessEqual(bounds['y'] + bounds['height'], 600)
            self.page.locator('#foldGuidesBtn').click()
        for width, height, theme in [(320, 700, 'dark'), (390, 844, 'light'), (844, 390, 'dark')]:
            with self.subTest(width=width, height=height), self.browser.new_context(
                    viewport=dict(width=width,height=height), is_mobile=True, has_touch=True) as context:
                page = context.new_page()
                page.on('pageerror', lambda error: self.errors.append(str(error)))
                page.goto(self.url, wait_until='load')
                page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
                self.assertFalse(page.locator('#foldGuidesBtn').is_visible())
                page.locator('#textMoreToggle').press('Enter')
                button = page.locator('#foldGuidesBtn')
                sizes = page.locator('#textToolbarOptions button').evaluate_all('''buttons => buttons.map(button => {
                    const {width, height} = button.getBoundingClientRect();
                    return [width, height];
                })''')
                self.assertTrue(sizes and all(min(size) >= 44 for size in sizes), sizes)
                bounds = button.bounding_box()
                self.assertIsNotNone(bounds)
                self.assertGreaterEqual(bounds['x'], 0)
                self.assertLessEqual(bounds['x'] + bounds['width'], width)
                self.assertLessEqual(bounds['y'] + bounds['height'], height)
                self.assertTrue(button.evaluate('button => button.closest("#textToolbarOptions") !== null && button === button.parentElement.firstElementChild'))
                before = button.get_attribute('aria-pressed')
                button.press('Space')
                self.assertNotEqual(button.get_attribute('aria-pressed'), before)
                button.press('Escape')
                self.assertFalse(button.is_visible())
                self.assertTrue(page.locator('#textMoreToggle').evaluate('button => button === document.activeElement'))

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

    def test_many_task_tracks_keep_positions_through_layout_only_resizes(self):
        count, target = 4096, 4000
        self.open_range_data(['worker'] * count, starts=[0] * count,
                             durations=[10000] * count, columns={'task': list(range(count))},
                             extra={'tasks': [dict(pid=index + 1, comm=f'task_{index}', kind=0, cpu=0)
                                              for index in range(count)]})
        initial = self.ev('''async target => {
            const probe = fgView('timeline').probe(), pane = document.querySelector('.tl-scroll');
            probe.select(target);
            pane.scrollTop = probe.TRACKS[target].y - 4;
            window.settleTrackResize = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            await settleTrackResize();
            window.trackGeometryWrites = 0;
            for (const track of probe.TRACKS) for (const key of ['y', 'h']) {
                let value = track[key];
                Object.defineProperty(track, key, {get() { return value; },
                    set(next) { trackGeometryWrites++; value = next; }});
            }
            window.trackGeometryState = () => {
                const canvas = document.querySelector('.tl-canvas');
                return {taskY: probe.TRACKS[target].y, scrollTop: pane.scrollTop, maxScroll: pane.scrollHeight - pane.clientHeight,
                    paneHeight: pane.clientHeight, totalHeight: pane.scrollHeight,
                    selection: probe.sel(), view: probe.view(), point: probe.xyOf(target),
                    canvasHeight: canvas.getBoundingClientRect().height};
            };
            return trackGeometryState();
        }''', target)
        self.ev('''async () => {
            for (let index = 0; index < 12; index++) fgView('timeline').resize();
            fgSwitchView('text');
            await fgSwitchView('timeline');
            await settleTrackResize();
        }''')
        self.page.locator('.tl-resizer').press('ArrowUp')
        self.ev('settleTrackResize()')
        resized = self.ev('trackGeometryState()')
        self.assertEqual(self.ev('trackGeometryWrites'), 0, 'viewport-only resize rebuilt all track positions')
        for key in ('taskY', 'scrollTop', 'totalHeight', 'selection', 'view'):
            self.assertEqual(resized[key], initial[key], key)
        self.assertLess(resized['paneHeight'], initial['paneHeight'])
        self.assertEqual(resized['canvasHeight'], resized['paneHeight'])
        self.assertEqual(resized['maxScroll'] - initial['maxScroll'], initial['paneHeight'] - resized['paneHeight'])
        self.page.mouse.move(*resized['point'])
        self.ev('settleTrackResize()')
        self.assertIn('worker', self.page.locator('.tl-tip').inner_text())

    def test_hide_unmatched_reuses_filter_results_and_preserves_the_track_anchor(self):
        count, target = 4096, 4000
        self.open_range_data(['worker'] * count, starts=[0] * count,
                             durations=[10000] * count, columns={'task': list(range(count))},
                             extra={'tasks': [dict(pid=index + 1, comm=f'task_{index}', kind=0, cpu=0)
                                              for index in range(count)]})
        facts = self.ev('''target => {
            const probe = fgView('timeline').probe(), tracks = probe.TRACKS;
            const pane = document.querySelector('.tl-scroll'), button = document.querySelector('[data-act=fhide]');
            const matches = new Set(tracks.filter(track => track.id % 2 === 0).map(track => track.id + 1));
            let lineChecks = 0, positionWrites = 0;
            matches.has = line => { lineChecks++; return Set.prototype.has.call(matches, line); };
            for (const track of tracks) {
                let position = track.y;
                Object.defineProperty(track, 'y', {get() { return position; },
                    set(next) { positionWrites++; position = next; }});
            }
            fgNotifyFilter('alternate tasks', matches);
            const apply = {lineChecks, positionWrites};
            probe.range(0, 10000);
            const row = document.querySelector('.tl-range-table tbody tr');
            const rowText = row.textContent;
            lineChecks = 0;
            const offsets = [];
            const scope = () => document.querySelector('.tl-range-head').textContent;
            const beforeScope = scope();
            pane.scrollTop = tracks[target].y + 5;
            button.click();
            offsets.push(pane.scrollTop - tracks[target].y);
            const hiddenCount = tracks.filter(track => track.hidden).length;
            button.click();
            offsets.push(pane.scrollTop - tracks[target].y);
            pane.scrollTop = tracks[target - 1].y + 5;
            button.click();
            offsets.push(pane.scrollTop - tracks[target].y);
            return {apply, lineChecks, offsets, hiddenCount, sameRow: row.isConnected && row.textContent === rowText,
                sameScope: scope() === beforeScope, pressed: button.getAttribute('aria-pressed')};
        }''', target)
        self.assertEqual(facts['lineChecks'], 0, facts)
        self.assertEqual(facts['apply'], dict(lineChecks=count, positionWrites=0))
        self.assertEqual(facts['offsets'], [5, 5, 0])
        self.assertEqual(facts['hiddenCount'], count // 2)
        self.assertTrue(facts['sameRow'])
        self.assertTrue(facts['sameScope'])
        self.assertEqual(facts['pressed'], 'true')

    def test_hide_unmatched_only_clears_a_selection_on_a_hidden_track(self):
        self.open_range_data(['match', 'other', 'other'], columns={'task': [0, 0, 1]},
                             extra={'tasks': [dict(pid=1, comm='keep', kind=0, cpu=0),
                                              dict(pid=2, comm='hide', kind=0, cpu=0)]})
        facts = self.ev('''() => {
            const probe = fgView('timeline').probe(), button = document.querySelector('[data-act=fhide]');
            fgNotifyFilter('first call only', new Set([1]));
            probe.select(1);
            const detail = document.querySelector('.tl-detail-head');
            button.click();
            const kept = {selected: probe.sel(), sameDetail: detail.isConnected};
            button.click();
            probe.select(2);
            button.click();
            return {kept, selected: probe.sel(), hidden: probe.TRACKS.map(track => track.hidden)};
        }''')
        self.assertEqual(facts['kept'], dict(selected=1, sameDetail=True))
        self.assertEqual(facts['selected'], -1)
        self.assertEqual(facts['hidden'], [False, True])

    def test_equivalent_text_filters_preserve_timeline_results_and_export_the_new_query(self):
        self.open_range_data(['root', 'leaf', 'later', 'other', 'tail', 'last'],
                             columns={'line': [1, 10, 20, 30, 40, 60], 'eline': [10, 10, 25, 0, 0, 60],
                                      'task': [0, 0, 1, 1, 2, 2]},
                             extra={'tasks': [dict(pid=index + 1, comm=f'task_{index}', kind=0, cpu=0)
                                              for index in range(3)]})
        self.ev("fgNotifyFilter('original query', new Set([1, 10]))")
        self.page.fill('.tl-search', '.*')
        self.page.press('.tl-search', 'Enter')
        self.page.wait_for_function("() => document.querySelector('.tl-count').textContent === '1 / 2 calls'")
        selection = self.ev('''async () => {
            const probe = fgView('timeline').probe();
            probe.select(1);
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const view = probe.view();
            const heading = document.querySelector('.tl-detail-head');
            fgNotifyFilter('same calls, different lines', new Set([10, 99]));
            return {selected: probe.sel(), view: probe.view(), before: view,
                sameHeading: !!heading?.isConnected, count: document.querySelector('.tl-count').textContent,
                hits: probe.TRACKS.map(track => track.hits)};
        }''')
        self.assertEqual(selection['selected'], 1)
        self.assertEqual(selection['view'], selection['before'])
        self.assertTrue(selection['sameHeading'])
        self.assertEqual(selection['count'], '2 / 2 calls')
        self.assertEqual(selection['hits'], [2, 0, 0])

        facts = self.ev('''async () => {
            const probe = fgView('timeline').probe();
            probe.range(0, 6000);
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const row = document.querySelector('.tl-range-table tbody tr');
            const raf = window.requestAnimationFrame;
            let draws = 0;
            window.requestAnimationFrame = callback => { draws++; return raf(callback); };
            try { fgNotifyFilter('export this query', new Set([10])); }
            finally { window.requestAnimationFrame = raf; }
            return {sameRow: !!row?.isConnected, draws};
        }''')
        self.assertEqual(facts, dict(sameRow=True, draws=0))
        checks = self.ev('''() => {
            const lines = new Set([10]);
            let checks = 0;
            lines.has = number => { checks++; return Set.prototype.has.call(lines, number); };
            fgNotifyFilter('export this query', lines);
            return checks;
        }''')
        self.assertEqual(checks, 1)
        with self.page.expect_download() as downloaded:
            self.page.click('[data-act=range-csv]')
        with open(downloaded.value.path(), encoding='utf-8-sig', newline='') as exported:
            rows = list(csv.DictReader(exported))
        self.assertEqual({row['function'] for row in rows}, {'leaf', 'root'})
        self.assertEqual({row['filter'] for row in rows}, {'export this query'})

        self.ev("fgNotifyFilter('other calls, same count', new Set([25, 40]))")
        self.assertEqual(self.ev("fgView('timeline').probe().TRACKS.map(track => track.hits)"), [0, 1, 1])
        self.assertEqual(set(self.page.locator('.tl-range-table tbody tr').evaluate_all('rows => rows.map(row => row.dataset.name)')),
                         {'later', 'tail'})
        self.ev("fgNotifyFilter('no exact matches', new Set([0, 99]))")
        self.assertIn('No calls overlap', self.page.locator('.tl-range-table tbody').inner_text())
        self.ev("fgNotifyFilter('empty result', new Set())")
        self.assertIn('No calls overlap', self.page.locator('.tl-range-table tbody').inner_text())
        self.ev("fgNotifyFilter('', null)")
        self.assertEqual(self.page.locator('.tl-range-table tbody tr[data-name]').count(), 6)

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
        self.ev('''() => {
            window.taskPositionWrites = 0;
            for (const track of fgView('timeline').probe().TRACKS) {
                let position = track.y;
                Object.defineProperty(track, 'y', {get() { return position; },
                    set(next) { taskPositionWrites++; position = next; }});
            }
        }''')
        self.click_cpu_at(6000)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), -1)
        self.assertGreater(self.ev("fgView('timeline').probe().TRACKS[1].h"), 0)
        self.assertEqual(self.page.locator('.an-task-table tbody tr').count(), 1)
        self.assertIn('scheduler-only', self.page.locator('.an-task-table').inner_text())
        self.assertNotIn('early', self.page.locator('.tl-detail').inner_text())
        self.assertEqual(self.ev("fgView('timeline').probe().view()"), [0, 9000])
        self.assertEqual(self.ev('taskPositionWrites'), 2)
        self.click_cpu_at(6000)
        self.assertEqual(self.ev('taskPositionWrites'), 2)

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

    def test_tasks_indexed_call_extents_preserve_bounds_cpu_evidence_and_minor_counts(self):
        count = 129
        starts = [1000 * (index + 1) for index in range(count)]
        durations = [10] * count
        durations[120] = 30000
        tasks = [dict(pid=1, comm='indexed', kind=0, cpu=2, runs=[[400, 800, 5]]),
                 dict(pid=2, comm='events only', kind=0, cpu=-1, runs=[])]
        self.open_range_data(['work'] * count, starts=starts, durations=durations,
                             columns={'cpu': [3] * (count - 1) + [7]}, extra={
                                 'tasks': tasks,
                                 'offcpu': [[0, 250, 350, 0, 0, 1, 4, 6, 0, -1],
                                            [1, 190000, None, 0, 0, 1, 8, -1, 0, -1]],
                                 'wakeups': [[-1, 200000, 1, 0, -1, -1]],
                                 'chains': [[128, 128, 2, 0, 1, 1, 1]],
                             })
        self.assertEqual(self.ev("fgView('timeline').probe().TRACKS[0].rangeBounds.length"), 4)
        helpers.click_timeline_tool(self.page, '[data-act=an-tasks]')
        calls, scheduler = self.download_csv('[data-act=tasks-csv]')
        self.assertEqual([calls[key] for key in ('calls', 'start_ns', 'end_ns', 'cpus', 'error_origins_including_minor')],
                         ['129', '250', '151000', '2,3,4,5,6,7', '1'])
        self.assertEqual([scheduler[key] for key in ('calls', 'start_ns', 'end_ns', 'cpus', 'span_is_lower_bound')],
                         ['0', '190000', '200000', '8', '1'])

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
                            getComputedStyle(head).top];
                    }""")
                    self.assertLessEqual(metrics[0], width)
                    self.assertLessEqual(abs(metrics[1]), 2)
                    self.assertLessEqual(abs(metrics[2]), 2)
                    self.assertLessEqual(abs(metrics[3]), 2)
                    self.assertLessEqual(metrics[4], 40)
                    if width <= 640 or height <= 600:
                        self.assertEqual(metrics[5], 'auto')
                    self.assert_detail_table_headers()
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
        page_size = 100
        for action, selector, sort_attr in [('latency', 'tr[data-win]', 'lsort'), ('sections', 'tr[data-sec]', 'ssort')]:
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
                    self.assert_detail_table_headers()
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
        rows = self.page.locator('tr[data-win]')
        self.assertEqual(rows.count(), 100)
        self.assertIn('150000', self.page.locator('.tl-detail-head').inner_text())
        page_field = self.page.get_by_role('textbox', name='Page', exact=True)
        page_field.fill('1500')
        page_field.press('Enter')
        self.assertEqual(rows.count(), 100)
        self.assertEqual(rows.last.get_attribute('data-win'), '149999000,150000000,0')

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
                self.assert_detail_table_headers()

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

    def test_wait_chain_shows_recorded_blocking_site_separately_and_exports_io_wait(self):
        self.ev("fgSwitchView('text')")
        self.page.reload()
        caller = 'folio_wait+0x4/0x8 [<b>ext4</b>]'
        self.open_range_data(['stack_guess'], durations=[1000000], extra={
            'strings': ['stack_guess', caller], 'caps': {'sched_events': True, 'sched_blocked_reason': True},
            'offcpu': [[0, 100, 1000, 0, 0, -1, 0, 1, 0, -1, 1, 0]],
        })
        self.ev("() => { const probe = fgView('timeline').probe(); probe.wait(probe.TRACKS[0].off[0]); }")
        row = self.page.locator('tr[data-wait="0"] td').nth(2)
        self.assertIn('stack_guess', row.inner_text())
        self.assertIn('Recorded: ' + caller, row.inner_text())
        self.assertIn('I/O wait: no', row.inner_text())
        self.assertEqual(row.locator('b').count(), 0)
        exported, = self.download_csv('[data-act=wait-csv]')
        self.assertEqual((exported['waiting_function'], exported['blocked_function'], exported['io_wait']),
                         ('stack_guess', caller, '0'))

    def test_wait_chain_many_intervals_resolves_only_requested_pages(self):
        self.open_wait_data(count=150000)
        status = self.page.locator('.an-wait-pager [role=status]')
        waits = self.page.locator('tr[data-wait]')
        self.assertEqual(waits.count(), 100)
        self.assertIn('1–100 of 100+', status.inner_text())
        visits = self.ev('''() => {
            const original = Set.prototype.add, result = [];
            let steps = 0;
            Set.prototype.add = function(value) {
                if (Number.isInteger(value)) steps++;
                return original.call(this, value);
            };
            try {
                for (const action of ['wait-next', 'wait-prev', 'wait-next']) {
                    steps = 0;
                    document.querySelector(`[data-act=${action}]`).click();
                    result.push(steps);
                }
            } finally { Set.prototype.add = original; }
            return result;
        }''')
        self.assertEqual(visits, [100, 0, 0])
        self.assertEqual(waits.count(), 100)
        self.assertIn('101–200 of 200+', status.inner_text())
        self.assertEqual(waits.first.get_attribute('data-wait'), '100')
        self.assertEqual(waits.last.get_attribute('data-wait'), '199')

    def test_wait_chain_paginates_exports_and_refreshes_without_rewalking(self):
        self.open_wait_data(count=225)
        waits = self.page.locator('tr[data-wait]')
        status = self.page.locator('.an-wait-pager [role=status]')
        previous = self.page.locator('[data-act=wait-prev]')
        next_page = self.page.locator('[data-act=wait-next]')

        def visible_ids():
            return waits.evaluate_all('rows => rows.map(row => +row.dataset.wait)')

        self.assertEqual(visible_ids(), list(range(100)))
        first = waits.first.element_handle()
        self.assertTrue(previous.is_disabled())
        next_page.press('Enter')
        self.assertIn('101–200 of 200+', status.inner_text())
        self.assertEqual(visible_ids(), list(range(100, 200)))
        self.assertFalse(first.evaluate('node => node.isConnected'))
        self.assertTrue(next_page.evaluate('node => node === document.activeElement'))
        first_on_page = waits.first.element_handle()
        rows = self.download_csv('[data-act=wait-csv]')
        self.assertEqual(len(rows), 225)
        self.assertEqual([int(row['offcpu_index']) for row in rows], list(range(225)))
        self.assertEqual(rows[0]['link_kind'], 'selected')
        self.assertEqual(rows[1]['link_kind'], 'temporal')
        self.assertEqual(int(rows[1]['prior_resume_to_wake_ns']), 2900)
        self.assertEqual(rows[-1]['terminal_reason'], 'wakeup-not-recorded')
        self.assertIn('101–200 of 225', status.inner_text())
        self.assertEqual(visible_ids(), list(range(100, 200)))
        self.assertTrue(first_on_page.evaluate('node => node.isConnected'))
        next_page.press('Space')
        self.assertIn('201–225 of 225', status.inner_text())
        self.assertEqual(visible_ids(), list(range(200, 225)))
        self.assertTrue(next_page.is_disabled())
        self.assertTrue(previous.evaluate('node => node === document.activeElement'))
        last_page = waits.first.element_handle()
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
        self.assertEqual(visible_ids(), list(range(200, 225)))
        self.assertTrue(last_page.evaluate('node => node.isConnected'))
        self.assertTrue(previous.evaluate('node => node === document.activeElement'))
        self.assertEqual(self.download_csv('[data-act=wait-csv]'), rows)
        previous.press('Enter')
        self.assertEqual(visible_ids(), list(range(100, 200)))
        previous.press('Space')
        self.assertEqual(visible_ids(), list(range(100)))
        self.assertTrue(previous.is_disabled())
        self.assertTrue(next_page.evaluate('node => node === document.activeElement'))
        next_page.press('Enter')
        self.ev("() => { const probe = fgView('timeline').probe(); probe.wait(probe.TRACKS[1].off[0]); }")
        self.assertIn('1–100 of 100+', status.inner_text())
        self.assertEqual(visible_ids(), list(range(1, 101)))

    def test_wait_chain_large_export_pauses_and_keeps_paging_and_terminal_reason(self):
        count = 2305
        self.open_wait_data(count=count, task_names=['=SUM(1,2)', '@waker'])
        button = self.page.locator('[data-act=wait-csv]')
        status = self.page.locator('.an-wait-csv-status')
        downloads = []
        self.page.on('download', lambda download: downloads.append(download))
        self.assertEqual(button.evaluate('''button => new Promise((resolve, reject) => {
            const status = document.querySelector('.an-wait-csv-status');
            const clock = performance.now.bind(performance);
            let elapsed = 0;
            performance.now = () => clock() + (elapsed += 7);
            button.click();
            document.querySelector('[data-act=wait-next]').click();
            fgNotifyFilter('filter must not narrow wait scope', new Set());
            const observer = new MutationObserver(() => {
                if (!status.textContent.endsWith(' rows')) return;
                observer.disconnect(); clearTimeout(timeout); delete performance.now;
                fgSwitchView('text');
                resolve([button.textContent, status.textContent]);
            });
            const timeout = setTimeout(() => {
                observer.disconnect(); delete performance.now; reject(new Error('No wait CSV progress'));
            }, 5000);
            observer.observe(status, {childList: true});
        })'''), ['Cancel CSV', 'Preparing CSV… 512 rows'])
        self.ev('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)))')
        self.assertEqual(self.ev('fgCurrentView()'), 'text')
        self.assertEqual(downloads, [])
        self.assertEqual(status.text_content(), 'Preparing CSV… 512 rows')
        with self.page.expect_download() as result:
            self.ev("fgSwitchView('timeline')")
        self.assertEqual(result.value.suggested_filename, 'funcgraph-wait-chain-raw.csv')
        with open(result.value.path(), 'rb') as download:
            raw = download.read()
        rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline='')))
        self.assertEqual(len(downloads), 1)
        self.assertEqual([int(row['offcpu_index']) for row in rows], list(range(count)))
        self.assertEqual([row['terminal_reason'] for row in rows if row['terminal_reason']], ['wakeup-not-recorded'])
        self.assertEqual(rows[-1]['terminal_reason'], 'wakeup-not-recorded')
        self.assertEqual(rows[0]['comm'], "'=SUM(1,2)")
        self.assertEqual(rows[0]['waker_comm'], "'@waker")
        self.assertTrue(all(row['filter_scope'] == 'all_tasks' for row in rows))
        self.assertEqual(self.page.locator('.an-wait-pager [role=status]').inner_text(), f'101–200 of {count} waits')
        self.assertEqual(self.page.locator('tr[data-wait]').count(), 100)
        self.assertTrue(status.is_hidden())
        self.assertEqual(button.inner_text(), 'Export chain CSV')
        _, again = self.download_csv_file('[data-act=wait-csv]')
        self.assertEqual(again, raw)

    def test_wait_chain_large_export_cancel_remains_reachable_and_drops_stale_work(self):
        self.page.close()
        self.page = self.browser.new_page(viewport={'width': 320, 'height': 700}, is_mobile=True, has_touch=True)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)
        self.open_wait_data(count=2305, task_names=['wide_waiter_' * 20, '@waker'])
        button = self.page.locator('[data-act=wait-csv]')
        downloads = []
        self.page.on('download', lambda download: downloads.append(download))
        for action in (
            'button.click()',
            "document.querySelector('.an-wait-head .tl-close').click()",
            "const probe = fgView('timeline').probe(); probe.wait(probe.TRACKS[1].off[0])",
            "fgView('timeline').probe().errors()",
        ):
            with self.subTest(action=action):
                self.assertEqual(button.evaluate(f'''button => {{
                    const detail = document.querySelector('.tl-detail');
                    detail.scrollTop = 0;
                    detail.scrollLeft = detail.scrollWidth;
                    button.click();
                    const fits = node => {{
                        const bounds = node.getBoundingClientRect();
                        return bounds.left >= 0 && bounds.right <= innerWidth && bounds.width > 0;
                    }};
                    const result = [button.textContent, button.offsetHeight >= 44,
                        fits(button) && fits(detail.querySelector('.an-wait-csv-status'))];
                    {action};
                    return result;
                }}'''), ['Cancel CSV', True, True])
                self.ev('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)))')
                self.assertEqual(downloads, [])
                self.assertTrue(self.page.locator('.an-wait-csv-status').is_hidden())
                self.ev("() => { const probe = fgView('timeline').probe(); probe.wait(probe.TRACKS[0].off[0]); }")
                self.assertEqual(button.inner_text(), 'Export chain CSV')

    def test_wait_chain_keyboard_navigation_preserves_panel(self):
        self.open_wait_data(count=3)
        self.assertEqual(self.page.locator('.an-wait-pager').count(), 0)
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

    def test_short_wait_chain_can_page_at_the_far_horizontal_edge(self):
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.open_wait_data(count=125, task_names=['waiter_' * 64, 'waker_' * 64])
        self.page.get_by_role('button', name='All columns').press('Enter')
        scroll_left = self.assert_short_pane_controls_reachable(['[data-act=wait-next]'])
        next_page = self.page.locator('[data-act=wait-next]')
        bounds = next_page.bounding_box()
        self.page.mouse.click(bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2)
        self.assertIn('101–125 of 125', self.page.locator('.an-wait-pager').inner_text())
        self.assertEqual(self.page.locator('tr[data-wait]').count(), 25)
        self.assertEqual(self.ev("document.querySelector('.tl-detail').scrollLeft"), scroll_left)
        previous = self.page.locator('[data-act=wait-prev]')
        self.assertTrue(previous.evaluate('''control => {
            const bounds = control.getBoundingClientRect();
            return control.contains(document.elementFromPoint(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2));
        }'''))
        previous.press('Space')
        self.assertIn('1–100 of 125', self.page.locator('.an-wait-pager').inner_text())
        self.assertTrue(next_page.evaluate('node => node === document.activeElement'))

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
                self.assertIn('Stopped:', self.page.locator('.an-wait-stop').inner_text())
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

    def test_wait_chain_dense_lookup_is_stable_for_both_orders_and_unordered_ends(self):
        for order in ('ascending', 'descending', 'unordered'):
            with self.subTest(order=order):
                ends = [4 * index for index in range(3000)] + [2000000] * 2
                if order == 'descending':
                    ends.reverse()
                if order == 'unordered':
                    ends = ends[::2][::-1] + ends[1::2]
                ends.insert(1 if order == 'unordered' else 0, None)
                best = ends.index(2000000) + 2
                records = [[0, 1990000, 2000000, 0, 0, -1, 0, 1, 0, -1],
                           [0, 1990000, 1999999, 0, 0, -1, 0, 1, 0, -1]] + [
                    [1, 0, end, 0, 0, -1, 0, -1 if end is None else 1, 0, -1] for end in ends]
                self.open_wait_data(offcpu=records, wakeups=[[1, 2000000, 0, 0, -1, 0], [1, 1999999, 0, 0, -1, 1]])
                self.page.wait_for_function("!document.querySelector('.an-wait-table').hasAttribute('aria-busy')")
                expected = [0, best]
                self.assertEqual(self.page.locator('tr[data-wait]').evaluate_all('(rows) => rows.map(row => +row.dataset.wait)'), expected)
                self.assertEqual([int(row['offcpu_index']) for row in self.download_csv('[data-act=wait-csv]')], expected)
                self.ev("() => { const probe = fgView('timeline').probe(); probe.wait(probe.TRACKS[0].off[1]); }")
                self.page.wait_for_function("!document.querySelector('.an-wait-table').hasAttribute('aria-busy')")
                self.assertEqual(self.page.locator('tr[data-wait]').evaluate_all('(rows) => rows.map(row => +row.dataset.wait)'),
                                 [1, ends.index(11996) + 2])
                self.assertTrue(self.ev("!!fgView('timeline').probe().TRACKS[1].waitOrder"))
                self.assertEqual(self.ev("fgView('timeline').probe().TRACKS[1].off.map(record => record[2])"),
                                 [2000000 if end is None else end for end in ends])

    def test_wait_chain_cold_page_lookup_pauses_and_discards_replaced_work(self):
        count = 300
        records = [[index % 2, 90000 + (count - index) * 3000, 91000 + (count - index) * 3000,
                    0, 0, -1, 0, 1, 0, -1] for index in range(count)]
        ends = list(range(2000, 14000, 2)) + list(range(2001, 14000, 2))
        records += [[2, 0, end, 0, 0, -1, 0, 1, 0, -1] for end in ends]
        cutoff = 230
        wakes = [[(index + 1) % 2, records[index][2] - 100, index % 2, 0, -1, index]
                 for index in range(cutoff)] + [[2, records[cutoff][2] - 100, cutoff % 2, 0, -1, cutoff]]
        self.open_wait_data(offcpu=records, wakeups=wakes, task_names=['waiter', 'waker', 'dense'])
        next_button = self.page.locator('[data-act=wait-next]')
        next_button.click()
        waits = self.page.locator('tr[data-wait]')
        self.assertEqual(waits.first.get_attribute('data-wait'), '100')
        pause = '''async action => {
            const probe = fgView('timeline').probe(), dense = probe.TRACKS[2];
            document.querySelector('[data-act=wait-next]').click();
            const table = document.querySelector('.an-wait-table');
            const actual = [table.getAttribute('aria-busy'), table.tBodies[0].rows[0].dataset.wait];
            if (action === 'text') fgSwitchView('text');
            else if (action === 'close') document.querySelector('.an-wait-head .tl-close').click();
            else if (action === 'replace') probe.wait(dense.off[0]);
            else document.querySelector('[data-act=wait-prev]').click();
            await new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)));
            return [...actual, !!(dense.waitOrder || dense.waitProbe)];
        }'''
        for action in ('previous', 'close', 'replace', 'text'):
            with self.subTest(action=action):
                self.assertEqual(self.ev(pause, action), ['true', '100', False])
                if action == 'previous':
                    self.assertEqual(waits.first.get_attribute('data-wait'), '0')
                    next_button.click()
                elif action in ('close', 'replace'):
                    self.ev("() => { const probe = fgView('timeline').probe(); probe.wait(probe.TRACKS[0].off[0]); }")
                    if action == 'replace':
                        self.assertEqual(waits.first.get_attribute('data-wait'), '0')
                        next_button.click()
        self.assertIn('Preparing wait links', self.page.locator('.an-wait-stop').text_content())
        with self.page.expect_download() as download:
            self.ev('''() => {
                fgSwitchView('timeline');
                document.querySelector('[data-act=wait-csv]').click();
            }''')
        self.page.wait_for_function("!document.querySelector('.an-wait-table').hasAttribute('aria-busy')")
        best = count + ends.index(max(ends))
        self.assertEqual(waits.evaluate_all('(rows) => rows.map(row => +row.dataset.wait)'), list(range(200, cutoff + 1)) + [best])
        with open(download.value.path(), encoding='utf-8-sig', newline='') as exported:
            rows = list(csv.DictReader(exported))
        self.assertEqual([int(row['offcpu_index']) for row in rows], list(range(cutoff + 1)) + [best])
        self.assertEqual([row['terminal_reason'] for row in rows if row['terminal_reason']], ['wakeup-not-recorded'])

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
                                top: getComputedStyle(head).top,
                                pageWidth: document.documentElement.scrollWidth};
                    }""")
                    self.assertGreaterEqual(bounds['closeLeft'], bounds['left'])
                    self.assertLessEqual(bounds['closeRight'], bounds['right'])
                    self.assertLessEqual(bounds['closeBottom'], bounds['bottom'])
                    self.assertLessEqual(bounds['pageWidth'], width)
                    toggle = self.page.get_by_role('button', name='All columns')
                    if width <= 640:
                        self.assertTrue(toggle.is_visible())
                        self.assertEqual(self.page.locator('.an-wait-table th:visible').all_text_contents(),
                                         ['task', 'waited', 'waiting in', 'woken by'])
                        self.assertEqual(self.ev('tlDetail.scrollWidth'), self.ev('tlDetail.clientWidth'))
                    else:
                        self.assertFalse(toggle.is_visible())
                        self.assertLessEqual(self.ev("document.querySelector('tr[data-wait]').offsetHeight"), 48)
                        self.assertGreaterEqual(self.ev("document.querySelector('tr[data-wait] button').offsetWidth"), 140)
                    if width <= 640 or height <= 600:
                        self.assertEqual(bounds['top'], 'auto')
                    self.assert_detail_table_headers()
                    self.assertFalse(self.page.locator('.an-wait-hint details').evaluate('(node) => node.open'))
                    self.page.locator('tr[data-wait="2"] button').press('Enter')
                    self.assertTrue(self.page.locator('.tl-detail.show').is_visible())
        self.page.locator('.an-wait-head .tl-close').click()
        self.assertFalse(self.page.locator('.tl-detail').is_visible())

    def test_narrow_wait_columns_toggle_preserves_focus_page_and_export(self):
        self.page.close()
        self.page = self.browser.new_page(viewport={'width': 320, 'height': 740}, is_mobile=True, has_touch=True)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)
        self.open_wait_data(count=125, task_names=['waiter_' * 35, 'source_' * 35])
        self.page.get_by_role('button', name='Next page of waits').tap()
        toggle = self.page.get_by_role('button', name='All columns')
        status = self.page.locator('.an-wait-pager [role=status]')
        self.assertIn('101–125 of 125', status.inner_text())
        self.assertGreaterEqual(toggle.bounding_box()['height'], 44)
        self.assertEqual(toggle.get_attribute('aria-pressed'), 'false')
        compact_csv = self.download_csv_file('[data-act=wait-csv]')
        toggle.press('Space')
        self.assertEqual(toggle.get_attribute('aria-pressed'), 'true')
        self.assertEqual(self.page.locator('.an-wait-table th:visible').count(), 8)
        self.assertIn('101–125 of 125', status.inner_text())
        self.assertEqual(self.download_csv_file('[data-act=wait-csv]'), compact_csv)
        self.ev("() => { const pane = document.querySelector('#tlDetail'); pane.scrollTop = 0; pane.scrollLeft = pane.scrollWidth; }")
        toggle.press('Enter')
        self.assertTrue(toggle.evaluate('button => button === document.activeElement'))
        self.assertEqual(toggle.get_attribute('aria-pressed'), 'false')
        self.assertEqual(self.ev('tlDetail.scrollLeft'), 0)
        self.assertIn('101–125 of 125', status.inner_text())


    def test_range_pagination_sorting_and_cache(self):
        self.open_range_data([f'function_{index:04d}' for index in range(650)])
        self.ev("fgView('timeline').probe().range(0, 650000)")
        self.assertEqual(self.page.locator('.tl-range-table tbody tr[data-name]').count(), 100)
        first = self.page.locator('.tl-range-table tbody tr').first.element_handle()
        self.page.locator('[data-act=range-next]').click()
        self.assertFalse(first.evaluate('(node) => node.isConnected'))
        self.assertEqual(self.page.locator('.tl-range-table tbody tr[data-name]').count(), 100)
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
        self.assertEqual(self.page.locator('.tl-range-table tbody tr[data-name]').count(), 100)
        self.assertEqual(self.page.locator('.tl-range-table th[data-sort=name]').get_attribute('aria-sort'), 'ascending')
        self.page.locator('[data-act=range-page]').fill('7')
        self.page.locator('[data-act=range-page]').press('Enter')
        self.assertEqual(self.page.locator('.tl-range-table tbody tr[data-name]').count(), 50)
        self.assertTrue(self.page.locator('[data-act=range-next]').is_disabled())
        self.page.locator('[data-act=range-prev]').click()
        self.assertEqual(self.page.locator('.tl-range-table tbody tr').first.get_attribute('data-name'), 'function_0500')
        self.ev("fgNotifyFilter('one call', new Set([650]))")
        self.assertEqual(self.page.locator('.tl-range-table tbody tr[data-name]').count(), 1)
        self.assertEqual(self.page.locator('.tl-range-table tbody tr').first.get_attribute('data-name'), 'function_0649')
        self.ev("fgNotifyFilter('no calls', new Set())")
        self.assertIn('No calls overlap', self.page.locator('.tl-detail').inner_text())
        self.assertTrue(self.page.locator('[data-act=range-csv]').is_disabled())

    def test_range_many_distinct_functions_does_not_spread_arguments(self):
        self.open_range_data([f'function_{index}' for index in range(150000)])
        self.ev("fgView('timeline').probe().range(0, 150000000)")
        self.assertEqual(self.page.locator('.tl-range-table tbody tr[data-name]').count(), 100)
        self.assertIn('150000 functions', self.page.locator('.tl-range-footer').inner_text())

    def download_csv(self, selector):
        with self.page.expect_download() as download_info:
            self.page.click(selector)
        with open(download_info.value.path(), encoding='utf-8-sig', newline='') as source:
            return list(csv.DictReader(source))

    def download_csv_file(self, selector):
        with self.page.expect_download() as download_info:
            self.page.click(selector)
        download = download_info.value
        with open(download.path(), 'rb') as source:
            return download.suggested_filename, source.read()

    def download_range_csv(self):
        return self.download_csv('[data-act=range-csv]')

    def open_distribution(self):
        self.ev("fgView('timeline').probe().dist(0)")
        self.page.wait_for_selector('.an-dist-tools')

    def assert_short_pane_controls_reachable(self, actions):
        self.page.locator('.tl-resizer').press('Home')
        pane = self.page.locator('.tl-detail.tl-detail-short')
        pane.scroll_into_view_if_needed()
        scroll_left = pane.evaluate('node => { node.scrollLeft = node.scrollWidth; return node.scrollLeft; }')
        self.assertGreater(scroll_left, 50)
        for action in actions:
            with self.subTest(action=action):
                readable = pane.locator(action).evaluate('''control => {
                    const pane = control.closest('.tl-detail'), viewport = pane.getBoundingClientRect();
                    const before = control.getBoundingClientRect();
                    pane.scrollTop += before.top - viewport.top - (pane.clientHeight - before.height) / 2;
                    const bounds = control.getBoundingClientRect();
                    return control.contains(document.elementFromPoint(bounds.left + bounds.width / 2,
                        bounds.top + bounds.height / 2));
                }''')
                self.assertTrue(readable)
                self.assertEqual(pane.evaluate('node => node.scrollLeft'), scroll_left)
        return scroll_left

    def test_short_distribution_controls_stay_reachable_when_scrolled_horizontally(self):
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.open_range_data(['target'] * 55, callers=['wide_caller_' * 30] * 55)
        self.open_distribution()
        self.assertEqual(self.page.get_by_role('table', name='Duration calls').count(), 1)
        scroll_left = self.assert_short_pane_controls_reachable(['[data-act=dist-bucket]', '[data-act=dist-csv]', '[data-act=dist-next]'])
        bounds = self.page.locator('[data-act=dist-next]').bounding_box()
        self.page.mouse.click(bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2)
        self.assertIn('Calls 51–55 of 55', self.page.locator('.an-dist-count').inner_text())
        self.assertEqual(self.ev("document.querySelector('.tl-detail').scrollLeft"), scroll_left)

    def test_short_task_pager_stays_reachable_when_scrolled_horizontally(self):
        self.page.set_viewport_size({'width': 390, 'height': 844})
        tasks = [dict(pid=index + 1, comm=f'wide_task_{index}_' + 'x' * 200, kind=0, cpu=0) for index in range(105)]
        self.open_range_data(['target'], extra={'tasks': tasks})
        helpers.click_timeline_tool(self.page, '[data-act=an-tasks]')
        self.assert_short_pane_controls_reachable(['[data-task-page="1"]'])

    def test_distribution_call_sites_remain_reversible_and_page_without_resorting(self):
        sites = [f'caller_{index:02d}' for index in range(29)]
        self.open_range_data(['target'] * 116, callers=sites * 4)
        self.open_distribution()
        site_rows = self.page.locator('tr[data-site]')

        def current_sites():
            return site_rows.evaluate_all('rows => rows.map(row => row.dataset.site)')

        status = self.page.locator('.an-site-count')
        previous = self.page.locator('[data-act=dist-sites-prev]')
        next_page = self.page.locator('[data-act=dist-sites-next]')
        first = site_rows.first.element_handle()
        self.page.locator('[data-act=dist-next]').press('Enter')
        calls_before = self.download_csv('[data-act=dist-csv]')
        call_row = self.page.locator('.an-list tr').first.element_handle()
        call_status = self.page.locator('.an-dist-count').inner_text()
        self.ev("""() => {
            window.distributionSortLengths = [];
            const originalSort = Array.prototype.sort;
            Array.prototype.sort = function(...args) {
                if ((this.length === 116 && Number.isInteger(this[0])) ||
                    (this.length === 29 && typeof this[0]?.site === 'string' && Array.isArray(this[0].ids))) {
                    window.distributionSortLengths.push(this.length);
                }
                return originalSort.apply(this, args);
            };
        }""")
        self.assertIn('1–12 of 29', status.inner_text())
        self.assertEqual(current_sites(), sites[:12])
        self.assertTrue(previous.is_disabled())
        next_page.press('Enter')
        self.assertIn('13–24 of 29', status.inner_text())
        self.assertEqual(current_sites(), sites[12:24])
        self.assertFalse(first.evaluate('node => node.isConnected'))
        next_page.press('Space')
        self.assertIn('25–29 of 29', status.inner_text())
        self.assertEqual(current_sites(), sites[24:])
        self.assertTrue(next_page.is_disabled())
        self.assertTrue(previous.evaluate('node => node === document.activeElement'))
        self.assertTrue(call_row.evaluate('node => node.isConnected'))
        self.assertEqual(self.page.locator('.an-dist-count').inner_text(), call_status)
        self.assertEqual(self.download_csv('[data-act=dist-csv]'), calls_before)

        chosen = site_rows.last.locator('button')
        chosen.press('Enter')
        self.assertEqual(chosen.get_attribute('aria-pressed'), 'true')
        reset = self.page.locator('[data-act=dist-reset-site]')
        self.assertTrue(reset.evaluate('node => node === document.activeElement'))
        self.assertIn('1–4 of 4', self.page.locator('.an-dist-count').inner_text())
        self.assertEqual({row['caller'] for row in self.download_csv('[data-act=dist-csv]')}, {sites[-1]})
        previous.press('Space')
        self.assertEqual(current_sites(), sites[12:24])
        self.assertIn('1–4 of 4', self.page.locator('.an-dist-count').inner_text())
        next_page.press('Enter')
        self.assertEqual(chosen.get_attribute('aria-pressed'), 'true')
        chosen.press('Space')
        self.assertIn('1–50 of 116', self.page.locator('.an-dist-count').inner_text())
        histogram = self.page.locator('[data-act=dist-histogram]')
        self.assertTrue(histogram.evaluate('node => node === document.activeElement'))
        self.assertEqual(current_sites(), sites[24:])
        previous.press('Enter')
        previous.press('Space')
        self.assertTrue(previous.is_disabled())
        self.assertTrue(next_page.evaluate('node => node === document.activeElement'))
        self.assertEqual(current_sites(), sites[:12])
        self.assertEqual(self.ev('window.distributionSortLengths'), [])

    def test_distribution_filtered_call_site_stays_on_its_new_ranked_page(self):
        sites = [f'caller_{index:02d}' for index in range(29)]
        self.open_range_data(['target'] * 29, callers=sites)
        self.open_distribution()
        next_page = self.page.locator('[data-act=dist-sites-next]')
        next_page.press('Enter')
        next_page.press('Enter')
        selected = self.page.locator('tr[data-site=caller_28] button')
        selected.press('Enter')
        self.ev("fgNotifyFilter('first sixteen and last', new Set([...Array.from({length: 16}, (_, index) => index + 1), 29]))")
        self.assertIn('13–17 of 17', self.page.locator('.an-site-count').inner_text())
        self.assertEqual(selected.get_attribute('aria-pressed'), 'true')
        self.assertIn('1–1 of 1', self.page.locator('.an-dist-count').inner_text())
        self.page.locator('[data-act=dist-reset-site]').press('Space')
        self.assertIn('1–17 of 17', self.page.locator('.an-dist-count').inner_text())
        self.assertIn('13–17 of 17', self.page.locator('.an-site-count').inner_text())

    def test_short_distribution_call_site_pager_stays_at_the_horizontal_edge(self):
        self.page.set_viewport_size({'width': 390, 'height': 844})
        sites = [f'{index:02d}_' + 'wide_caller_' * 40 for index in range(14)]
        self.open_range_data(['target'] * len(sites), callers=sites)
        self.open_distribution()
        scroll_left = self.assert_short_pane_controls_reachable(['[data-act=dist-sites-next]'])
        next_page = self.page.locator('[data-act=dist-sites-next]')
        bounds = next_page.bounding_box()
        self.page.mouse.click(bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2)
        self.assertIn('13–14 of 14', self.page.locator('.an-site-count').inner_text())
        self.assertEqual(self.ev("document.querySelector('.tl-detail').scrollLeft"), scroll_left)
        previous = self.page.locator('[data-act=dist-sites-prev]')
        geometry = previous.evaluate('''control => {
            const bounds = control.getBoundingClientRect();
            const hit = document.elementFromPoint(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2);
            const pager = control.closest('.an-site-pager'), panel = control.closest('.tl-detail');
            return {visible: control.contains(hit), hit: hit?.className, control: bounds.toJSON(),
                pager: pager.getBoundingClientRect().toJSON(), panel: panel.getBoundingClientRect().toJSON()};
        }''')
        self.assertTrue(geometry['visible'], geometry)
        previous.press('Space')
        self.assertIn('1–12 of 14', self.page.locator('.an-site-count').inner_text())
        self.assertTrue(next_page.evaluate('node => node === document.activeElement'))

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
                self.assert_detail_table_headers()
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
        self.page.wait_for_function("() => document.querySelector('.tl-count').getAttribute('aria-busy') === 'false'")
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
        self.page.wait_for_function("() => document.querySelector('.tl-count').getAttribute('aria-busy') === 'false'")
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
        self.assertEqual(self.page.locator('.cmp-table tbody tr').count(), 400)
        self.assertEqual(self.page.locator('.cmp-page-status').inner_text(), 'Rows 1–400 of 605')
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

    def test_compare_paging_reuses_sorted_rows_without_retaining_old_nodes(self):
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
        self.page.click('.cmp-next')
        self.assertEqual(self.page.locator('.cmp-table tbody tr').count(), 400)
        self.assertEqual(self.ev('window.compareSortCalls'), 0)
        self.assertFalse(self.ev('window.firstCompareRow.isConnected'))
        self.assertEqual(self.page.locator('.cmp-page-status').inner_text(), 'Rows 401–800 of 1205')

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
        self.page.locator('[data-call-nav=child]').focus()
        for expected in [1, 2]:
            self.page.keyboard.press('Enter')
            self.assertEqual(self.ev("fgView('timeline').probe().sel()"), expected)
        self.assertTrue(self.page.locator('[data-call-nav=child]').is_disabled())
        self.assertTrue(self.page.locator('[data-call-nav=parent]').evaluate('node => node === document.activeElement'))
        for expected in [1, 0]:
            self.page.keyboard.press('Space')
            self.assertEqual(self.ev("fgView('timeline').probe().sel()"), expected)
        self.assertTrue(self.page.locator('[data-call-nav=parent]').is_disabled())
        self.assertTrue(self.page.locator('[data-call-nav=child]').evaluate('node => node === document.activeElement'))

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
        self.ev("fgNotifyFilter('keep selected', new Set([4]))")
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 3)
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
        for expected in ['irqs off', 'BH off', 'need-resched + preempt-resched (N)', 'preempt 3',
                         'migrate-off 2']:
            self.assertIn(expected, value)
        self.assertNotIn('baseline', value)
        self.assertNotIn('preemptible', value)
        badge = self.page.locator('.tl-slice-head .tl-pill').filter(has_text='preempt off')
        self.assertEqual(badge.inner_text(), 'preempt off ×2')
        self.assertEqual(badge.get_attribute('title'), 'Recorded preempt count 3; tracing-adjusted depth 2 (inferred)')
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

    def test_timeline_resched_badges_distinguish_all_eight_combinations(self):
        codes = '.nNplLbB'
        expected = [[], ['need-resched'], ['need-resched', 'preempt-resched'], ['preempt-resched'],
                    ['lazy-resched'], ['lazy-resched', 'preempt-resched'], ['need-resched', 'lazy-resched'],
                    ['need-resched', 'lazy-resched', 'preempt-resched']]
        flags = [(1 << 15) | (index << 4) | (1 << 7) for index in range(8)]
        self.open_range_data(['work_' + code for code in codes], columns={'flags': flags},
                             extra={'preempt_base': 1})
        original = self.ev('JSON.stringify(fgData.read("tlData"))')
        for index, (code, labels) in enumerate(zip(codes, expected)):
            with self.subTest(code=code):
                self.ev('index => fgView("timeline").probe().select(index)', index)
                badges = self.page.locator('.tl-slice-head .tl-pill.warn')
                self.assertEqual(badges.all_text_contents(), labels)
                value = self.page.locator('.tl-call-facts > .k').filter(has_text='flags').locator(
                    'xpath=following-sibling::span[1]').inner_text()
                description = ' + '.join(labels) + ' (' + code + ')' if labels else ''
                self.assertEqual(value, description + ', preempt 1' if labels else 'preempt 1')
                self.page.mouse.move(*self.ev('index => fgView("timeline").probe().xyOf(index)', index))
                self.page.locator('.tl-tip').wait_for(state='visible')
                for label in labels:
                    self.assertIn(label, self.page.locator('.tl-tip').inner_text())
                self.assertNotIn('preempt off', self.page.locator('.tl-slice-head').inner_text())
        self.assertEqual(self.ev('JSON.stringify(fgData.read("tlData"))'), original)

    def test_timeline_nested_irq_context_and_unknown_irq_state_remain_visible(self):
        cases = [('task', 0), ('softirq', 1), ('hardirq', 2), ('NMI', 3),
                 ('hardirq + softirq (H)', 2 | (1 << 27)), ('NMI + hardirq (Z)', 3 | (1 << 27)),
                 ('task', 1 << 28)]
        self.open_range_data(['work_' + str(index) for index in range(len(cases))],
                             columns={'flags': [(1 << 15) | flags for label, flags in cases]})
        for index, (label, flags) in enumerate(cases):
            with self.subTest(context=label, flags=flags):
                self.ev('index => fgView("timeline").probe().select(index)', index)
                self.assertEqual(self.page.locator('.tl-slice-head .tl-pill').first.inner_text(), label)
                if flags & (1 << 28):
                    self.assertIn('IRQ state unavailable (X)', self.page.locator('.tl-call-facts').inner_text())

    def test_text_resched_filter_keeps_case_sensitive_linux_codes_and_correct_hints(self):
        path = os.path.join(self.k.dir, 'resched-combinations.txt')
        codes = '.nNplLbB'
        with open(path, 'w') as trace:
            trace.write('\n'.join(f'1000.000{100 + index * 10:03d} |   0) task-42 |  D{code}Haf |   1.000 us    |  work_{index}();'
                                  for index, code in enumerate(codes)))
        self.k.run(path, '--no-config', '--timeline', '--filter', out='resched-combinations.html')
        self.page.goto('file://' + os.path.join(self.k.dir, 'resched-combinations.html'))
        hints = self.page.locator('[data-field=resched]').get_attribute('data-hints')
        for definition in ('n=NEED_RESCHED,', 'N=NEED_RESCHED+PREEMPT_RESCHED,',
                           'l=NEED_RESCHED_LAZY,', 'B=NEED_RESCHED+NEED_RESCHED_LAZY+PREEMPT_RESCHED,'):
            self.assertIn(definition, hints)
        for index, code in enumerate(codes):
            with self.subTest(code=code):
                self.ev('code => { document.getElementById("filterInput").value = \'resched:="\' + code + \'"\'; applyFilter(); }', code)
                rows = self.page.locator('.line-container:visible')
                self.assertEqual(rows.count(), 1)
                self.assertEqual(rows.get_attribute('data-resched'), code)
                self.assertEqual(rows.get_attribute('data-irqs'), 'D')
                self.assertEqual(rows.get_attribute('data-hirq'), 'H')
                self.assertEqual(rows.get_attribute('data-preempt'), 'a')
                self.assertIn('work_' + str(index), rows.inner_text())

    def test_timeline_preempt_correction_stays_internal_without_changing_recorded_flags(self):
        for entry_base, raw_depth, corrected_depth in ((0, 0, 0), (0, 1, 1), (1, 1, 0), (1, 3, 2)):
            with self.subTest(entry_base=entry_base, raw_depth=raw_depth):
                self.ev("fgSwitchView('text')")
                self.page.reload()
                self.open_range_data(['recorded'], columns={'flags': [(1 << 15) | (raw_depth << 7)]},
                                     extra={'preempt_base': entry_base, 'preempt_exit_base': entry_base})
                original = self.ev('JSON.stringify(fgData.read("tlData"))')
                self.ev("fgView('timeline').probe().select(0)")
                label = self.page.locator('.tl-call-facts > .k').filter(has_text='flags')
                self.assertEqual(label.locator('xpath=following-sibling::span[1]').inner_text(),
                                 'preempt ' + str(raw_depth) if raw_depth else 'none')
                facts = self.page.locator('.tl-call-facts').inner_text()
                self.assertNotIn('baseline', facts)
                self.assertNotIn('preemptible', facts)
                self.assertNotIn('baseline', ' '.join(self.page.locator('.tl-note').all_text_contents()))
                badge = self.page.locator('.tl-slice-head .tl-pill').filter(has_text='preempt off')
                self.assertEqual(badge.count(), int(corrected_depth > 0))
                if corrected_depth:
                    self.assertEqual(badge.get_attribute('title'),
                                     f'Recorded preempt count {raw_depth}; tracing-adjusted depth {corrected_depth} (inferred)')
                self.ev("fgView('timeline').probe().sections()")
                hints = ' '.join(self.page.locator('.an-hint').all_text_contents())
                self.assertNotIn('baseline', hints)
                self.assertIn('preempt depth excludes inferred tracing overhead', hints)
                self.assertEqual(self.ev('JSON.stringify(fgData.read("tlData"))'), original)

    def test_timeline_schedule_ignores_event_and_return_offsets_without_hiding_atomic_calls(self):
        path = os.path.join(self.k.dir, 'preempt-record-kinds.txt')
        text = ('1000.000100 |   0)   sh-7 |  ..... |               |  /* sched_process_exec: filename=/bin/sh pid=7 */\n'
                '1000.000102 |   0)   sh-7 |  ...1. |               |  schedule() {\n'
                '1000.000106 |   0)   sh-7 |  ..... |   4.000 us    |  } /* schedule */\n'
                '1000.000108 |   0)   sh-7 |  ...2. |   1.000 us    |  mutex_lock();\n'
                '1000.000110 |   0)   sh-7 |  ...1. |   1.000 us    |  done();\n')
        with open(path, 'w') as trace:
            trace.write(text)
        self.k.run(path, '--no-config', '--timeline', out='preempt-record-kinds.html')
        self.page.goto('file://' + os.path.join(self.k.dir, 'preempt-record-kinds.html'))
        self.open_timeline()
        data = self.ev('fgData.read("tlData")')
        self.assertEqual((data['preempt_base'], data['preempt_exit_base']), (1, 0))
        self.ev("fgView('timeline').probe().select(0)")
        self.assertIn('preempt 1', self.page.locator('.tl-call-facts').inner_text())
        self.assertNotIn('baseline', self.page.locator('.tl-call-facts').inner_text())
        self.assertNotIn('preemptible', self.page.locator('.tl-call-facts').inner_text())
        self.assertEqual(self.page.locator('.tl-call-facts > .v.err').count(), 0)
        self.assertNotIn('preempt off', self.page.locator('.tl-slice-head').inner_text())
        self.assertNotIn('baseline', ' '.join(self.page.locator('.tl-note').all_text_contents()))
        self.ev("fgView('timeline').probe().select(1)")
        self.assertIn('preempt 2', self.page.locator('.tl-call-facts').inner_text())
        self.assertIn('preempt off', self.page.locator('.tl-slice-head').inner_text())
        self.assertIn('sleeping function in atomic context', self.page.locator('.tl-call-facts').inner_text())
        self.assertEqual(self.ev('fgData.read("tlData")'), data)

    def test_timeline_orphan_preempt_flags_use_return_baseline_with_legacy_fallback(self):
        for exit_base, expected_depth in ((0, 1), (1, 0), (None, 0)):
            with self.subTest(exit_base=exit_base):
                self.page.reload()
                self.open_range_data(['entry', 'orphan'],
                                     columns={'flags': [(1 << 15) | (1 << 7), (1 << 15) | (1 << 7) | (1 << 17)]},
                                     extra={'preempt_base': 1, 'preempt_exit_base': exit_base})
                self.ev("fgView('timeline').probe().select(0)")
                self.assertIn('preempt 1', self.page.locator('.tl-call-facts').inner_text())
                self.assertNotIn('preempt off', self.page.locator('.tl-slice-head').inner_text())
                self.ev("fgView('timeline').probe().select(1)")
                self.assertIn('preempt 1', self.page.locator('.tl-call-facts').inner_text())
                self.assertNotIn('baseline', self.page.locator('.tl-call-facts').inner_text())
                self.assertNotIn('preemptible', self.page.locator('.tl-call-facts').inner_text())
                self.assertEqual('preempt off' in self.page.locator('.tl-slice-head').inner_text(), bool(expected_depth))
                self.ev('async () => { await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); }')
                self.page.mouse.move(0, 0)
                self.page.mouse.move(*self.ev("fgView('timeline').probe().xyOf(1)"))
                self.page.wait_for_selector('.tl-tip', state='visible')
                self.assertIn('preempt 1' + (' (preempt off)' if expected_depth else ''),
                              self.page.locator('.tl-tip').inner_text())
                self.assertEqual('preempt off' in self.page.locator('.tl-tip').inner_text(), bool(expected_depth))

    def test_timeline_preempt_overlay_uses_return_baseline_for_orphans(self):
        for exit_base in (0, 1):
            with self.subTest(exit_base=exit_base):
                self.ev('localStorage.clear()')
                self.page.reload()
                self.open_range_data(['entry', 'orphan'],
                                     columns={'flags': [(1 << 15) | (1 << 7), (1 << 15) | (1 << 7) | (1 << 17)]},
                                     extra={'preempt_base': 1, 'preempt_exit_base': exit_base})
                helpers.click_timeline_tool(self.page, '[data-ov=preempt]')
                bars = self.ev('''async () => {
                    const context = document.querySelector('.tl-canvas').getContext('2d');
                    const original = context.fillRect, bars = [];
                    context.fillRect = function(left, top, width, height) {
                        if (height === 3) bars.push([left, top, width, height]);
                        return original.apply(this, arguments);
                    };
                    try {
                        fgView('timeline').draw();
                        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                        return bars;
                    } finally { context.fillRect = original; }
                }''')
                self.assertEqual(len(bars), 1 if exit_base == 0 else 0, bars)

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

    def test_timeline_cursor_does_not_copy_canvas_on_same_hover(self):
        self.check_cursor_scene()

    def test_timeline_cursor_aligns_at_fractional_and_high_dpi(self):
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
            const tracks = document.querySelector('.tl-canvas'), ruler = document.querySelector('.tl-head-canvas');
            const create = document.createElement.bind(document);
            window.sceneWork = {tracks: 0, ruler: 0, trackCopies: 0, allocations: 0};
            window.resetScenePaints = () => Object.assign(sceneWork, {tracks:0, ruler:0, trackCopies:0});
            window.scenePaints = () => [sceneWork.tracks, sceneWork.ruler, sceneWork.trackCopies];
            document.createElement = function (name, ...args) {
                if (name === 'canvas') sceneWork.allocations++;
                return create(name, ...args);
            };
            const trackContext = tracks.getContext('2d'), rulerContext = ruler.getContext('2d');
            const fill = trackContext.fillRect, trackImage = trackContext.drawImage, rulerImage = rulerContext.drawImage;
            trackContext.fillRect = function (...args) {
                const {a: scaleX, d: scaleY} = this.getTransform();
                if (args[0] === 0 && args[1] === 0 && args[2] * scaleX === tracks.width && args[3] * scaleY === tracks.height) sceneWork.tracks++;
                return fill.apply(this, args);
            };
            trackContext.drawImage = function (...args) { sceneWork.trackCopies++; return trackImage.apply(this, args); };
            rulerContext.drawImage = function (...args) { sceneWork.ruler++; return rulerImage.apply(this, args); };
            window.settleCursor = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            window.scenePixels = () => [tracks.toDataURL(), ruler.toDataURL()];
        }''')
        overlay = self.page.locator('.tl-hover-cursor')
        points = self.ev('''() => {
            const probe = fgView('timeline').probe(), box = document.querySelector('.tl-canvas').getBoundingClientRect();
            return [probe.xyOf(1), probe.offXY(probe.TRACKS[0].off[0]),
                [box.left + box.width * 0.1, box.top + 28], [box.right - 4, box.bottom - 10]];
        }''')
        for target, point in zip(('slice', 'wait', 'cpu', 'empty'), points):
            with self.subTest(target=target):
                self.page.mouse.move(*point)
                self.ev('settleCursor()')
                self.ev('resetScenePaints()')
                pixels = self.ev('scenePixels()')
                first = overlay.bounding_box()
                self.page.mouse.move(point[0] + 2, point[1])
                self.ev('settleCursor()')
                self.assertEqual(self.ev('scenePaints()'), [0, 0, 0], 'same-hover cursor move repainted a scene layer')
                self.assertEqual(self.ev('scenePixels()'), pixels)
                self.assertAlmostEqual(overlay.bounding_box()['x'] - first['x'], 2, delta=1)
                self.assertTrue(overlay.evaluate('''node => {
                    const canvas = document.querySelector('.tl-canvas').getBoundingClientRect();
                    const line = node.getBoundingClientRect(), time = node.firstElementChild;
                    const label = time.getBoundingClientRect();
                    const ink = time.getContext('2d').getImageData(0, 0, time.width, time.height).data;
                    return Math.abs(line.top - canvas.top) <= 1 && Math.abs(line.bottom - canvas.bottom) <= 1 &&
                        label.left >= canvas.left && label.right <= canvas.right &&
                        time.width === Math.round(label.width * devicePixelRatio) &&
                        ink.some((value, index) => index % 4 === 3 && value > 0) &&
                        document.elementFromPoint(line.left, label.top + 2).matches('.tl-canvas');
                }'''))
                self.ev("fgView('timeline').draw()")
                self.ev('settleCursor()')
                self.assertEqual(self.ev('scenePixels()'), pixels)
        before_zoom = self.ev('''() => {
            const original = scenePixels(), model = fgView('timeline').probe();
            resetScenePaints();
            const canvas = document.querySelector('.tl-canvas'), box = canvas.getBoundingClientRect();
            canvas.dispatchEvent(new MouseEvent('mousemove', {bubbles:true, clientX:box.left + 60, clientY:box.bottom - 10}));
            model.zoomTo(model.sStart[1], model.sEnd[1], -1);
            return original;
        }''')
        self.ev('settleCursor()')
        self.assertEqual(self.ev('scenePaints()'), [1, 1, 0], 'cursor request swallowed a pending zoom redraw')
        after_zoom = self.ev('scenePixels()')
        for before, after in zip(before_zoom, after_zoom):
            self.assertNotEqual(before, after)
        self.ev("async () => { fgView('timeline').draw(); await settleCursor(); }")
        self.assertEqual(self.ev('scenePixels()'), after_zoom)
        self.ev("async () => { document.querySelector('[data-act=fit]').click(); await settleCursor(); resetScenePaints(); toggleTheme(); }")
        self.ev('settleCursor()')
        self.assertEqual(self.ev('scenePaints()'), [1, 1, 0])
        self.assertEqual(self.ev('sceneWork.allocations'), 2)
        self.ev('sceneWork.allocations = 0')
        self.page.set_viewport_size(dict(width=1100, height=760))
        self.ev('settleCursor()')
        point = self.ev("fgView('timeline').probe().xyOf(2)")
        self.page.mouse.move(*point)
        self.ev('settleCursor()')
        self.ev('resetScenePaints()')
        self.page.mouse.move(point[0] + 2, point[1])
        self.ev('settleCursor()')
        self.assertEqual(self.ev('scenePaints()'), [0, 0, 0])
        self.assertEqual(self.ev('sceneWork.allocations'), 0)
        self.page.mouse.move(0, 0)
        self.ev('settleCursor()')
        self.assertTrue(overlay.is_hidden())
        self.page.mouse.move(*point)
        self.ev('settleCursor()')
        self.assertTrue(overlay.is_visible())
        self.ev('''async () => {
            fgView('timeline').draw();
            await fgSwitchView('text');
            await settleCursor();
        }''')
        self.assertTrue(overlay.is_hidden())
        self.ev("async () => { await fgSwitchView('timeline'); await settleCursor(); }")
        self.assertTrue(overlay.is_hidden())

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
        parent = self.page.locator('.tl-detail .tl-kv .tl-link[data-slice]')
        parent_index = int(parent.get_attribute('data-slice'))
        parent.focus()
        self.assertTrue(parent.evaluate('element => element === document.activeElement'))
        self.page.keyboard.press('Space')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), parent_index)
        self.assertTrue(self.page.locator('.tl-detail .tl-close').evaluate('element => element === document.activeElement'))

    def test_ruler_aligned_with_tracks_when_scrollbar_shown(self):
        # headless Chromium hides scrollbars by default; with a real one the track area is narrower
        # than the ruler, and the ruler must not be stretched (selection marks drifted apart)
        b = self.pw.chromium.launch(ignore_default_args=['--hide-scrollbars'])
        try:
            pg = b.new_page(viewport={'width': 1200, 'height': 420})
            pg.goto(self.url, wait_until='load')
            snapshots = pg.evaluate('''async () => {
                await fgSwitchView('timeline');
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                const scroll = document.querySelector('.tl-scroll'), toggle = document.querySelector('[data-act=fhide]');
                const geometry = () => [document.querySelector('.tl-head-canvas').getBoundingClientRect().width,
                    document.querySelector('.tl-canvas').getBoundingClientRect().width, scroll.clientWidth, scroll.offsetWidth];
                const initial = geometry();
                fgNotifyFilter('no matches', new Set());
                toggle.click();
                const filtered = geometry();
                toggle.click();
                return [initial, filtered, geometry()];
            }''')
        finally:
            b.close()
        _, _, client, offset = snapshots[0]
        if offset == client:
            self.skipTest('no visible scrollbar in this environment')
        self.assertEqual(snapshots[1][2], offset)
        self.assertEqual(snapshots[2][2], client)
        for head, main, viewport, _ in snapshots:
            self.assertEqual(head, main)
            self.assertEqual(main, viewport)

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
        self.assertTrue(self.page.locator('[data-call-nav=previous]').evaluate('node => node === document.activeElement'))
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

    def test_recorded_navigation_resumes_cold_index_without_replacing_controls(self):
        count, gap = 40000, 256
        first_child = gap + 2
        self.open_range_data(['root', 'helper'] + ['other'] * gap + ['leaf'] * count,
                             durations=[1000 * (count + gap + 2), 1000 * (count + gap + 1)] + [100] * (count + gap),
                             parents=[-1, 0] + [0] * gap + [1] * count,
                             columns={'depth': [0, 1] + [1] * gap + [2] * count})
        state = self.ev('''() => {
            const model = fgView('timeline').probe(), clock = performance.now.bind(performance);
            let elapsed = 0;
            performance.now = () => clock() + (elapsed += 4);
            try {
                model.select(1);
                window.coldNav = document.querySelector('.tl-recorded-call-nav');
                window.coldParent = coldNav.querySelector('[data-call-nav=parent]');
                window.coldChild = coldNav.querySelector('[data-call-nav=child]');
                document.querySelector('.tl-canvas').dispatchEvent(new KeyboardEvent('keydown', {
                    key: 'ArrowDown', bubbles: true,
                }));
                fgSwitchView('text');
                return [model.sel(), coldNav.getAttribute('aria-busy'), coldParent.disabled, coldChild.disabled,
                    coldChild.getAttribute('aria-label')];
            } finally { delete performance.now; }
        }''')
        self.assertEqual(state, [1, 'true', False, True, 'First recorded child (↓): preparing…'])
        self.ev('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 30)))')
        self.assertEqual(self.page.locator('.tl-recorded-call-nav').get_attribute('aria-busy'), 'true')
        self.page.locator('.view-tab[data-view=timeline]').click()
        child = self.page.get_by_role('button', name='First recorded child (↓): leaf', exact=True)
        child.wait_for()
        self.assertTrue(self.ev('''() => coldNav === document.querySelector('.tl-recorded-call-nav') &&
            coldChild === coldNav.querySelector('[data-call-nav=child]') && !coldNav.hasAttribute('aria-busy')'''))
        child.press('Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), first_child)
        self.page.locator('.tl-canvas').press(']')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), first_child + 1)

    def test_cold_navigation_uses_exact_child_when_parent_links_are_out_of_order(self):
        count = 32768
        self.open_range_data(['root', 'invalid', 'early_child', 'selected', 'later_child'] + ['other'] * count,
                             durations=[6000, 100, 100, 1500, 100] + [100] * count,
                             parents=[-1, 3, 3, 0, 3] + [-1] * count,
                             columns={'depth': [0, 1, 2, 1, 2] + [0] * count})
        initial = self.ev('''() => {
            const model = fgView('timeline').probe();
            model.select(3);
            const nav = document.querySelector('.tl-recorded-call-nav');
            const child = nav.querySelector('[data-call-nav=child]');
            const state = [nav.getAttribute('aria-busy'), child.disabled, child.title];
            child.click();
            return [...state, model.sel()];
        }''')
        self.assertEqual(initial, ['true', False, 'First recorded child (↓): early_child', 2])

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

    def open_child_groups(self, count=225, mode='abs', prefix='child_', parent_name='parent', select_initial=True):
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
        if select_initial:
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

    def test_recorded_call_overhead_updates_stack_and_child_preview_in_place(self):
        self.open_range_data(['root', 'parent', 'alpha', 'beta', 'alpha'], starts=[0, 10, 20, 4000, 9100],
                             durations=[20000, 12000, 3800, 5000, 2800], parents=[-1, 0, 1, 1, 1],
                             columns={'depth': [0, 1, 2, 2, 2], 'adj': [19000, 10000, 100, 4300, 2500]},
                             extra={'overhead': {'child': 10, 'self': 5, 'source': 'test'}})
        self.ev("fgView('timeline').probe().select(1)")
        panel = self.page.locator('.tl-detail')
        snapshot = '''panel => ({
            timings: Array.from(panel.querySelectorAll('.tl-call-duration, .tl-call-self, [data-stack-duration]'), node => node.textContent),
            preview: Array.from(panel.querySelectorAll('.tl-child-preview'), node => [node.dataset.slice, node.textContent]),
            breakdown: panel.querySelector('.an-bd-legend').textContent})'''
        measured = panel.evaluate(snapshot)
        self.assertEqual([entry[0] for entry in measured['preview']], ['2', '3'])
        self.assertEqual(len(measured['timings']), 4)
        panel.locator('.tl-detail-head').evaluate('node => { window.recordedCallHead = node; }')
        alpha = panel.locator('.tl-child-preview', has_text='alpha')
        alpha.press('o')
        adjusted = panel.evaluate(snapshot)
        self.assertEqual([entry[0] for entry in adjusted['preview']], ['3', '4'])
        self.assertTrue(all(before != after for before, after in zip(measured['timings'], adjusted['timings'])))
        self.assertEqual(adjusted['breakdown'], measured['breakdown'])
        self.assertTrue(alpha.evaluate('node => node === document.activeElement'))
        self.assertTrue(panel.locator('.tl-detail-head').evaluate('node => node === window.recordedCallHead'))
        self.ev("fgView('timeline').probe().select(1)")
        self.assertEqual(panel.evaluate(snapshot), adjusted)
        alpha.press('o')
        self.assertEqual(panel.evaluate(snapshot), measured)

    def test_child_preview_matches_complete_ranking_with_ties_and_overhead(self):
        count = 73
        children = [f'child_{index:02d}' for index in range(count)] * 2
        duration = [200 + (index * 13 + index // count * 7) % 5 * 100 for index in range(len(children))]
        adjusted = [value - (index * 3 % 4) * 50 for index, value in enumerate(duration)]
        self.open_range_data(['parent', *children], starts=[0, *(index * 1000 for index in range(1, len(children) + 1))],
                             durations=[len(children) * 1000 + 1000, *duration], parents=[-1] + [0] * len(children),
                             columns={'depth': [0] + [1] * len(children), 'adj': [len(children) * 1000, *adjusted]})
        self.ev("fgView('timeline').probe().select(0)")
        for basis in (duration, adjusted):
            groups = [(name, offset, basis[offset] + basis[offset + count],
                       offset + (count if basis[offset + count] > basis[offset] else 0) + 1)
                      for offset, name in enumerate(children[:count])]
            reference = sorted(groups, key=lambda group: (-group[2], group[1]))[:8]
            preview = self.page.locator('.tl-child-preview')
            self.assertEqual(preview.locator('.tl-child-preview-name').all_text_contents(), [row[0] for row in reference])
            self.assertEqual(preview.evaluate_all('nodes => nodes.map(node => +node.dataset.slice)'), [row[3] for row in reference])
            self.page.locator('.tl-canvas').press('o')

    def test_child_explorer_mean_sort_and_csv_follow_the_current_filter(self):
        self.open_range_data(['parent', 'alpha', 'beta', 'gamma', 'alpha', 'delta'],
                             starts=[0, 10000, 20000, 30000, 40000, 50000],
                             durations=[60000, 6000, 5000, 3500, 1000, 3500],
                             parents=[-1, 0, 0, 0, 0, 0], columns={'depth': [0, 1, 1, 1, 1, 1]})
        self.ev("fgView('timeline').probe().select(0)")
        self.page.locator('[data-child-open]').click()
        cells = self.page.locator('.an-child-table tbody tr td:first-child')
        self.assertEqual(cells.all_text_contents(), ['alpha', 'beta', 'gamma', 'delta'])
        self.page.locator('[data-child-sort=mean]').press('Enter')
        self.assertEqual(cells.all_text_contents(), ['beta', 'alpha', 'gamma', 'delta'])
        self.assertEqual(self.page.locator('.an-child-table tbody tr td:nth-child(5)').all_text_contents(),
                         ['5.000 us', '3.500 us', '3.500 us', '3.500 us'])
        rows = self.download_csv('[data-child-csv=groups]')
        self.assertEqual([(row['function'], row['mean_ns']) for row in rows],
                         [('beta', '5000'), ('alpha', '3500'), ('gamma', '3500'), ('delta', '3500')])

        self.ev("fgNotifyFilter('without the shorter alpha', new Set([2, 3, 4, 6]))")
        self.assertEqual(cells.all_text_contents(), ['alpha', 'beta', 'gamma', 'delta'])
        rows = self.download_csv('[data-child-csv=groups]')
        self.assertEqual([(row['function'], row['calls'], row['mean_ns']) for row in rows],
                         [('alpha', '1', '6000'), ('beta', '1', '5000'), ('gamma', '1', '3500'), ('delta', '1', '3500')])

    def test_child_preview_revisit_is_immediate_without_reusing_the_wrong_timing_basis(self):
        count = 2048
        self.open_child_groups(count=count)
        heading = self.page.locator('[data-child-title]')
        self.page.locator('[data-child-open]:not([aria-disabled=true])').wait_for()
        self.assertEqual(heading.text_content(), f'Children ({count * 2} calls; top 8 of {count} functions)')
        result = self.ev('''() => {
            const model = fgView('timeline').probe();
            const preview = () => Array.from(document.querySelectorAll('.tl-child-preview'),
                node => [node.dataset.slice, node.textContent]);
            const measured = preview();
            model.select(1);
            const other = preview();
            const clock = performance.now.bind(performance);
            let elapsed = 0;
            performance.now = () => clock() + (elapsed += 4);
            try { model.select(0); } finally { delete performance.now; }
            const revisited = preview();
            document.querySelector('[data-act=adj]').click();
            return {measured, other, revisited};
        }''')
        self.assertEqual(len(result['other']), 1)
        self.assertEqual(len(result['measured']), 8)
        self.assertEqual(result['revisited'], result['measured'])
        self.page.locator('[data-child-open]:not([aria-disabled=true])').wait_for()
        self.assertEqual(self.page.locator('.tl-child-preview .d:first-child').all_text_contents(), ['2.500 us'] * 8)
        self.page.locator('[data-child-open]').press('Enter')
        rows = self.download_csv('[data-child-csv=groups]')
        self.assertEqual(len(rows), count)
        self.assertEqual({(row['calls'], row['inclusive_ns'], row['duration_basis']) for row in rows},
                         {('2', '2500', 'adjusted_estimate')})

    def test_large_child_preview_pauses_and_only_displays_latest_timing_basis(self):
        count = 3072
        self.open_child_groups(count=count, select_initial=False)
        self.ev('''() => {
            const model = fgView('timeline').probe(), clock = performance.now.bind(performance);
            window.withShortChildBudget = action => {
                let elapsed = 0;
                performance.now = () => clock() + (elapsed += 4);
                try { action(); } finally { delete performance.now; }
            };
            window.selectParentWithBudget = () => withShortChildBudget(() => model.select(0));
        }''')
        self.assertEqual(self.ev('''() => {
            const model = fgView('timeline').probe();
            model.select(2);
            selectParentWithBudget();
            const preview = document.querySelector('[data-child-preview]');
            const explorer = document.querySelector('[data-child-open]');
            explorer.click();
            const before = [preview.innerText, explorer.getAttribute('aria-disabled'),
                !!document.querySelector('.an-child-table')];
            model.select(2);
            return [...before, preview.isConnected, model.sel()];
        }'''), ['Preparing child summary…', 'true', False, False, 2])
        self.ev('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 30)))')
        self.assertEqual(self.page.locator('[data-child-preview]').count(), 0)

        self.ev('''() => {
            selectParentWithBudget();
            withShortChildBudget(() => document.querySelector('[data-act=adj]').click());
            fgSwitchView('text');
        }''')
        self.ev('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 30)))')
        self.assertEqual(self.page.locator('[data-child-preview] [role=status]').text_content(), 'Preparing child summary…')
        self.assertIn('Overhead: subtracted', self.page.locator('[data-act=adj]').text_content())
        self.page.locator('.view-tab[data-view=timeline]').click()
        self.page.wait_for_function("document.querySelectorAll('.tl-child-preview').length === 8")
        self.assertEqual(self.page.locator('[data-child-title]').text_content(),
                         f'Children ({count * 2} calls; top 8 of {count} functions)')
        self.assertTrue(self.page.locator('[data-child-open]').is_enabled())
        self.assertEqual(self.page.locator('.tl-child-preview').evaluate_all('nodes => nodes.map(node => +node.dataset.slice)'),
                         list(range(3, 32, 4)))
        self.assertEqual(self.page.locator('.tl-child-preview .d:first-child').all_text_contents(), ['2.500 us'] * 8)
        self.page.locator('[data-child-open]').press('Enter')
        rows = self.download_csv('[data-child-csv=groups]')
        self.assertEqual(len(rows), count)
        self.assertEqual({(row['calls'], row['inclusive_ns'], row['duration_basis']) for row in rows},
                         {('2', '2500', 'adjusted_estimate')})

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

    def test_large_child_list_waits_for_latest_query_after_timeline_roundtrip(self):
        count = 3072
        self.open_child_groups(count=count)
        self.page.locator('[data-child-open]').wait_for(state='visible')
        self.page.wait_for_function("document.querySelector('[data-child-open]:not([aria-disabled=true])')")
        self.ev('''() => {
            const clock = performance.now.bind(performance);
            window.withShortChildListBudget = action => {
                let elapsed = 0;
                performance.now = () => clock() + (elapsed += 4);
                try { action(); } finally { delete performance.now; }
            };
        }''')
        self.assertEqual(self.ev('''() => {
            withShortChildListBudget(() => document.querySelector('[data-child-open]').click());
            const status = document.querySelector('.an-child-footer [role=status]');
            const table = document.querySelector('.an-child-table');
            const csv = document.querySelector('[data-child-csv=groups]');
            return [status.textContent, table.getAttribute('aria-busy'), table.tBodies[0].rows.length,
                csv.getAttribute('aria-disabled'), document.querySelector('[data-child-page="1"]').disabled];
        }'''), ['Preparing child list…', 'true', 0, 'true', True])
        self.assertEqual(self.ev('''() => {
            const query = document.querySelector('[data-child-query]');
            const edit = text => { query.value = text; query.dispatchEvent(new Event('input', {bubbles:true})); };
            withShortChildListBudget(() => {
                const sort = document.querySelector('[data-child-sort=name]');
                sort.focus(); sort.click();
            });
            const focus = document.activeElement.dataset.childSort;
            withShortChildListBudget(() => {
                document.querySelector('[data-child-mode=calls]').click();
                query.focus(); edit('child_00'); edit(' CHILD_002 ');
            });
            const queryFocused = document.activeElement === query;
            fgSwitchView('text');
            return [focus, queryFocused];
        }'''), ['name', True])
        drains = '() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 30)))'
        self.ev(drains)
        status = self.page.locator('.an-child-footer [role=status]')
        self.assertEqual(status.text_content(), 'Preparing child list…')
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 0)
        self.page.locator('.view-tab[data-view=timeline]').click()
        self.page.wait_for_function("document.querySelector('.an-child-table').getAttribute('aria-busy') === 'false'")
        self.assertEqual(status.text_content(), 'Showing 1–20 of 20 calls')
        self.assertEqual(self.page.locator('tr[data-child-call]').evaluate_all('rows => rows.map(row => +row.dataset.childCall)'),
                         list(range(83, 120, 4)) + list(range(81, 118, 4)))
        rows = self.download_csv('[data-child-csv=calls]')
        self.assertEqual(len(rows), 20)
        self.assertEqual({row['function_query'] for row in rows}, {' CHILD_002 '})
        self.ev('''() => {
            withShortChildListBudget(() => document.querySelector('[data-child-reset]').click());
            document.querySelector('.an-child-head [data-child-parent]').click();
        }''')
        self.ev(drains)
        self.assertEqual(self.page.locator('.an-child-table').count(), 0)
        self.assertEqual(self.page.locator('.tl-detail-title').text_content(), 'parent')

    def test_large_child_csv_pauses_ordering_and_exports_all_calls_from_any_page(self):
        count, prefix = 4100, '@"child,接收_'
        self.open_child_groups(count=count, prefix=prefix)
        self.page.set_viewport_size({'width': 320, 'height': 700})
        self.page.locator('[data-child-open]').click()
        selector = '[data-child-csv=calls]'
        button = self.page.locator(selector)
        self.page.wait_for_function("document.querySelector('.an-child-table[aria-busy=false]')")
        downloads = []
        self.page.on('download', lambda download: downloads.append(download))
        self.assertEqual(button.evaluate('''button => {
            const clock = performance.now.bind(performance);
            let elapsed = 0;
            button.focus();
            performance.now = () => clock() + (elapsed += 4);
            try { button.click(); } finally { delete performance.now; }
            const status = document.querySelector('[data-child-csv-status]');
            const accessible = [button, status, document.querySelector('.an-child-head .tl-close')].every(node => {
                const bounds = node.getBoundingClientRect();
                return bounds.left >= 0 && bounds.right <= innerWidth && bounds.width > 0;
            });
            const result = [button.textContent, status.textContent, document.activeElement === button, accessible];
            document.querySelector('[data-child-page="1"]').click();
            fgSwitchView('text');
            return result;
        }'''), ['Cancel CSV', 'Ordering CSV…', True, True])
        status = self.page.locator('[data-child-csv-status]')
        self.ev('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 30)))')
        self.assertEqual(downloads, [])
        self.assertEqual(status.text_content(), 'Ordering CSV…')

        with self.page.expect_download() as download_info:
            self.page.locator('.view-tab[data-view=timeline]').click()
        download = download_info.value
        self.assertEqual(download.suggested_filename, 'funcgraph-children-calls.csv')
        with open(download.path(), 'rb') as source:
            raw = source.read()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbfparent_slice,parent_function,'))
        self.assertIn(('"\'@""child,接收_0000"').encode(), raw)
        self.assertTrue(raw.endswith(b'\r\n'))
        rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline='')))
        self.assertEqual([int(row['slice_index']) for row in rows], list(range(3, count * 4, 4)) + list(range(1, count * 4, 4)))
        self.assertEqual(rows[0]['function'], "'" + prefix + '0000')
        self.assertEqual({row['duration_basis'] for row in rows}, {'measured'})
        self.assertTrue(status.is_hidden())
        self.assertEqual(self.page.locator('.an-child-footer [role=status]').text_content(), f'Showing 101–200 of {count} functions')
        self.page.locator('[data-child-page="-1"]').click()
        self.assertEqual(self.download_csv_file(selector), ('funcgraph-children-calls.csv', raw))

    def test_large_child_csv_discards_pending_output_on_cancel_and_scope_changes(self):
        count = 4100
        self.open_child_groups(count=count)
        self.page.locator('[data-child-open]').click()
        selector = '[data-child-csv=groups]'
        button = self.page.locator(selector)
        downloads = []
        self.page.on('download', lambda download: downloads.append(download))
        for action in ("button.click()", "document.querySelector('[data-child-sort=name]').click()",
                       "document.querySelector('[data-act=adj]').click()",
                       "fgNotifyFilter('one direct call', new Set([2]))",
                       "document.querySelector('.an-child-head .tl-close').click()"):
            with self.subTest(action=action):
                self.page.wait_for_function("document.querySelector('.an-child-table[aria-busy=false]')")
                self.assertEqual(button.evaluate(f'''button => {{
                    button.click();
                    const state = [button.textContent, document.querySelector('[data-child-csv-status]').textContent];
                    {action};
                    return state;
                }}'''), ['Cancel CSV', 'Preparing CSV…'])
                self.ev('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 30)))')
                self.assertTrue(self.page.locator('[data-child-csv-status]').is_hidden())
                self.assertEqual(downloads, [])
                if 'fgNotifyFilter' in action:
                    rows = self.download_csv(selector)
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]['shared_filter'], 'one direct call')
                    self.assertEqual(rows[0]['duration_basis'], 'adjusted_estimate')
                    self.assertEqual(len(downloads), 1)
                    downloads.clear()
                    self.ev("fgNotifyFilter('', null)")

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

    def test_child_find_uses_prepared_orders_and_equivalent_input_keeps_the_page(self):
        self.open_child_groups()
        self.page.click('[data-child-open]')
        self.page.click('[data-child-sort=name]')
        self.page.click('[data-child-mode=calls]')
        self.page.click('[data-child-mode=groups]')
        original_calls = self.download_csv('[data-child-csv=calls]')
        self.ev('''() => {
            const original = Array.prototype.sort;
            window.childSorts = [];
            Array.prototype.sort = function (...args) {
                if (this.length >= 100 && (this[0]?.ids || typeof this[0] === 'number')) childSorts.push(this.length);
                return original.apply(this, args);
            };
        }''')
        query = self.page.locator('[data-child-query]')
        status = self.page.locator('.an-child-footer [role=status]')
        query.fill('child_0')
        self.page.locator('[data-child-page="1"]').press('Enter')
        self.assertEqual(status.inner_text(), 'Showing 101–200 of 225 functions')
        first = self.page.locator('.an-child-table tbody tr').first.element_handle()
        query.fill('  CHILD_0  ')
        self.assertEqual(status.inner_text(), 'Showing 101–200 of 225 functions')
        self.assertTrue(first.evaluate('row => row.isConnected'))
        self.assertTrue(query.evaluate('input => input === document.activeElement'))
        groups = self.download_csv('[data-child-csv=groups]')
        self.assertEqual([row['function'] for row in groups], [f'child_{index:04d}' for index in range(225)])
        self.assertEqual({row['function_query'] for row in groups}, {'  CHILD_0  '})
        query.fill('child_00')
        self.assertEqual(status.inner_text(), 'Showing 1–100 of 100 functions')
        self.page.click('[data-child-mode=calls]')
        self.assertEqual(status.inner_text(), 'Showing 1–100 of 200 calls')
        self.page.locator('[data-child-page="1"]').press('Enter')
        calls = self.download_csv('[data-child-csv=calls]')
        self.assertEqual([row['slice_index'] for row in calls], [row['slice_index'] for row in original_calls
                                                              if row['function'].startswith('child_00')])
        self.assertEqual(self.ev('childSorts'), [])
        query.fill(' CHILD_0001 ')
        self.assertEqual(status.inner_text(), 'Showing 1–2 of 2 calls')
        self.assertEqual(self.page.locator('tr[data-child-call]').evaluate_all('rows => rows.map(row => +row.dataset.childCall)'), [7, 5])

    def test_child_find_keeps_matching_visible_rows_but_updates_counts_paging_and_csv(self):
        self.open_child_groups(count=225)
        self.page.click('[data-child-open]')
        pane = self.page.locator('#tlDetail')
        field = pane.locator('[data-child-query]')
        count = pane.locator('.an-child-footer [role=status]')
        for mode, total in (('groups', 100), ('calls', 200)):
            with self.subTest(mode=mode):
                pane.locator(f'[data-child-mode={mode}]').click()
                field.fill('')
                if mode == 'calls':
                    pane.locator('[data-child-show]').first.click()
                    self.assertEqual(pane.locator('tr.an-child-selected').count(), 1)
                self.ev('window.previousChildPage = [...document.querySelectorAll(".an-child-table tbody tr")]')
                field.fill('child_00')
                self.assertEqual(count.inner_text(), f'Showing 1–100 of {total} ' + ('functions' if mode == 'groups' else 'calls'))
                self.assertTrue(self.ev('''() => {
                    const rows = [...document.querySelectorAll('.an-child-table tbody tr')];
                    return rows.length === 100 && rows.every((row, position) => row === previousChildPage[position]);
                }'''))
                self.assertTrue(field.evaluate('node => node === document.activeElement'))
                if mode == 'calls':
                    self.assertEqual(pane.locator('tr.an-child-selected').count(), 1)
                    pane.locator('[data-child-page="1"]').click()
                    self.assertEqual(count.inner_text(), 'Showing 101–200 of 200 calls')
                    self.assertEqual(pane.locator('tr.an-child-selected').count(), 0)
                rows = self.download_csv(f'[data-child-csv={mode}]')
                self.assertEqual(len(rows), total)
                self.assertEqual({row['function_query'] for row in rows}, {'child_00'})
                field.fill('missing')
                self.assertEqual(pane.locator('.an-child-table tbody tr').count(), 0)
                self.assertEqual(count.inner_text(), 'No matching ' + ('functions' if mode == 'groups' else 'calls'))

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

    def test_child_find_only_replaces_changed_rows_in_groups_and_calls(self):
        count, excluded = 104, {2, 52}
        names = ['parent'] + [f'{"drop" if index in excluded else "keep"}_{index:03d}' for index in range(1, count + 1)]
        self.open_range_data(names, starts=[0] + [index * 1500 for index in range(1, count + 1)],
                             durations=[200000] + [1000] * count, parents=[-1] + [0] * count,
                             columns={'depth': [0] + [1] * count})
        self.ev("fgView('timeline').probe().select(0)")
        self.page.click('[data-child-open]')
        for mode in ['groups', 'calls']:
            with self.subTest(mode=mode):
                self.page.locator(f'[data-child-mode={mode}]').click()
                self.ev('''() => {
                    const body = document.querySelector('.an-child-table tbody');
                    const show = row => row.querySelector('[data-child-show]');
                    const index = row => +show(row).dataset.childShow;
                    show(body.rows[0]).click();
                    const initial = new Map(Array.from(body.rows, row => [index(row), row]));
                    const observer = new MutationObserver(() => {});
                    observer.observe(body, {childList: true});
                    window.childRowChanges = () => {
                        const records = observer.takeRecords();
                        return {indices: Array.from(body.rows, index),
                                retained: Array.from(body.rows).filter(row => row === initial.get(index(row))).length,
                                selection: Array.from(body.querySelectorAll('[aria-current=true]'), node => +node.dataset.childShow),
                                added: records.reduce((count, record) => count + record.addedNodes.length, 0),
                                removed: records.reduce((count, record) => count + record.removedNodes.length, 0)};
                    };
                    window.applyChildQuery = value => {
                        const input = document.querySelector('[data-child-query]');
                        input.focus({preventScroll: true});
                        input.value = value;
                        input.dispatchEvent(new Event('input', {bubbles: true}));
                        return {...childRowChanges(), focused: document.activeElement === input};
                    };
                }''')
                self.assertEqual(self.ev("applyChildQuery('keep_')"), {
                    'indices': [index for index in range(1, 103) if index not in excluded],
                    'retained': 98, 'selection': [1], 'added': 2, 'removed': 2, 'focused': True,
                })
                self.assertEqual(self.ev("applyChildQuery('')"), {
                    'indices': list(range(1, 101)), 'retained': 98, 'selection': [1],
                    'added': 2, 'removed': 2, 'focused': True,
                })

    def test_child_details_return_restores_list_page_scroll_and_focus(self):
        self.page.close()
        for width, height, touch in ((320, 780, True), (844, 390, True), (1440, 960, False)):
            with self.subTest(width=width):
                context = self.browser.new_context(viewport={'width': width, 'height': height}, has_touch=touch, is_mobile=touch)
                self.addCleanup(context.close)
                self.page = context.new_page()
                self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                self.page.goto(self.url)
                self.open_child_groups(count=205)
                trigger = self.page.locator('[data-call-actions]')
                chart = self.page.locator('.tl-canvas')
                if touch:
                    self.page.get_by_role('separator', name='Resize details').press('Home')
                    trigger.tap()
                    self.page.get_by_role('menuitem', name='Explore all children', exact=True).tap()
                else:
                    self.page.locator('[data-child-open]').click()
                pane = self.page.locator('#tlDetail')
                pane.locator('[data-child-mode=calls]').click()
                columns = pane.get_by_role('button', name='All columns')
                narrow = columns.is_visible()
                if narrow:
                    (columns.tap if touch else columns.click)()
                pane.locator('[data-child-query]').fill('child_0')
                pane.locator('[data-child-sort=start]').press('Enter')
                pane.locator('[data-child-page="1"]').press('Enter')
                status = pane.locator('.an-child-footer [role=status]')
                self.assertEqual(status.inner_text(), 'Showing 101–200 of 410 calls')
                details = pane.locator('[data-child-details]').nth(12)
                details.scroll_into_view_if_needed()
                index = details.get_attribute('data-child-details')
                position = 'panel => [panel.scrollTop, panel.scrollLeft, panel.clientHeight, panel.clientWidth]'
                original = pane.evaluate(position)
                self.assertGreater(original[0], 0)
                if width == 320:
                    self.assertGreater(original[1], 0)
                opened_expanded = chart.is_hidden()
                (details.tap if touch else details.click)()
                back = pane.get_by_role('button', name='Back to child calls', exact=True)
                self.assertTrue(back.evaluate('button => button === document.activeElement'))
                self.assertEqual(self.ev("fgView('timeline').probe().sel()"), int(index))
                if touch:
                    self.assertGreaterEqual(back.bounding_box()['width'], 44)
                    self.assertGreaterEqual(back.bounding_box()['height'], 44)
                if width == 844:
                    pane.get_by_role('button', name='Restore chart and detail split', exact=True).tap()
                    self.assertTrue(chart.is_visible())
                if touch:
                    back.tap()
                else:
                    back.press('Enter')
                self.assertIs(chart.is_hidden(), opened_expanded)
                self.assertEqual(status.inner_text(), 'Showing 101–200 of 410 calls')
                self.assertEqual(pane.locator('[data-child-query]').input_value(), 'child_0')
                self.assertEqual(pane.locator('th:has([data-child-sort=start])').get_attribute('aria-sort'), 'ascending')
                if narrow:
                    self.assertEqual(columns.get_attribute('aria-pressed'), 'true')
                self.assertEqual(pane.evaluate(position), original)
                restored = pane.locator(f'[data-child-details="{index}"]')
                self.assertTrue(restored.evaluate('button => button === document.activeElement'))
                self.assertEqual(pane.locator('[aria-current=true][data-child-show]').get_attribute('data-child-show'), index)
                if touch:
                    pane.locator('[data-child-parent]').tap()
                    self.assertTrue(chart.is_visible())
                    self.assertTrue(trigger.evaluate('button => button === document.activeElement'))
                self.assertLessEqual(self.ev('document.documentElement.scrollWidth'), width)
                context.close()

    def test_child_compact_columns_keep_actions_visible_and_preserve_rows(self):
        self.page.close()
        self.page = self.browser.new_page(viewport={'width': 320, 'height': 780}, has_touch=True, is_mobile=True)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)
        self.open_child_groups(count=12, prefix='very_long_recorded_child_function_name_')
        self.page.locator('[data-child-open]').tap()
        pane = self.page.locator('#tlDetail')
        columns = pane.get_by_role('button', name='All columns')
        table = pane.locator('.an-child-table')
        hit_test = '''button => {
            const box = button.getBoundingClientRect(), pane = button.closest('#tlDetail');
            const {left, right} = pane.getBoundingClientRect();
            return pane.scrollLeft === 0 && box.left >= left && box.right <= right &&
                box.width >= 44 && box.height >= 44 &&
                button.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
        }'''
        for selector in ('[data-child-show]', '[data-child-function]'):
            action = table.locator(selector).first
            action.scroll_into_view_if_needed()
            self.assertTrue(action.evaluate(hit_test))
        table.locator('[data-child-function]').first.tap()
        for selector in ('[data-child-show]', '[data-child-details]'):
            action = table.locator(selector).first
            action.scroll_into_view_if_needed()
            self.assertTrue(action.evaluate(hit_test))
        details = table.locator('[data-child-details]').first
        details.tap()
        pane.get_by_role('button', name='Back to child calls').tap()
        self.assertTrue(details.evaluate('button => button === document.activeElement'))
        self.assertTrue(details.evaluate(hit_test))
        self.assertTrue(pane.evaluate('panel => panel.clientWidth === panel.scrollWidth'))

        start = table.locator('[data-child-sort=start]')
        self.assertTrue(start.is_hidden())
        row = table.locator('tbody tr').first.element_handle()
        columns.tap()
        self.assertTrue(start.is_visible())
        self.assertTrue(row.evaluate('row => row.isConnected'))
        columns.tap()
        self.assertTrue(start.is_hidden())
        self.page.set_viewport_size({'width': 1440, 'height': 960})
        start.wait_for(state='visible')
        self.assertTrue(columns.is_hidden())
        self.page.set_viewport_size({'width': 390, 'height': 844})
        start.wait_for(state='hidden')
        self.assertTrue(columns.is_visible())
        self.assertTrue(row.evaluate('row => row.isConnected'))

    def test_child_details_return_handles_a_filter_excluding_the_original_row(self):
        self.open_child_groups(count=9)
        self.page.click('[data-child-open]')
        self.page.click('[data-child-mode=calls]')
        self.page.click('[data-child-details="3"]')
        self.page.get_by_role('button', name='Back to child calls', exact=True).wait_for()
        canvas = self.page.locator('.tl-canvas')
        canvas.focus()
        self.ev("fgNotifyFilter('another recorded child', new Set([fgData.read('tlData').cols.line[1]]))")
        self.assertEqual(self.page.locator('[data-child-details]').count(), 1)
        self.assertEqual(self.page.locator('[data-child-details]').get_attribute('data-child-details'), '1')
        self.assertTrue(canvas.evaluate('element => element === document.activeElement'))
        self.page.click('[data-child-details="1"]')
        self.ev("fgView('timeline').probe().select(0)")
        self.assertEqual(self.page.get_by_role('button', name='Back to child calls', exact=True).count(), 0)

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

    def test_compact_actions_open_child_explorer_without_scrolling_the_call(self):
        self.page.close()
        context = self.browser.new_context(viewport={'width': 320, 'height': 780}, has_touch=True, is_mobile=True)
        self.addCleanup(context.close)
        self.page = context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)
        self.open_child_groups(count=9)
        for width, height in ((320, 780), (844, 390)):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.ev("theme => document.documentElement.dataset.theme = theme", 'light' if width == 320 else 'dark')
                self.page.get_by_role('separator', name='Resize details').press('Home')
                trigger = self.page.locator('[data-call-actions]')
                pane = self.page.locator('#tlDetail')
                inline = pane.locator('[data-child-open]')
                self.assertTrue(inline.evaluate('''button => {
                    const pane = button.closest('#tlDetail');
                    return button.getBoundingClientRect().top >= pane.getBoundingClientRect().top + pane.clientHeight;
                }'''))
                read_position = 'panel => [panel.scrollTop, panel.scrollLeft, panel.clientWidth, panel.clientHeight, scrollY]'
                position = pane.evaluate(read_position)
                trigger.tap()
                shortcut = self.page.get_by_role('menuitem', name='Explore all children', exact=True)
                self.assertGreaterEqual(shortcut.bounding_box()['height'], 44)
                self.assertEqual(pane.evaluate(read_position), position)
                shortcut.tap()
                parent = pane.get_by_role('button', name='Return to parent call: parent', exact=True)
                self.assertTrue(parent.evaluate('button => button === document.activeElement'))
                self.assertEqual(pane.locator('.an-child-table tbody tr').count(), 9)
                head = pane.locator('.an-child-head')
                self.assertLessEqual(head.bounding_box()['height'], 100 if width == 320 else 52)
                for control in [parent, *head.locator('[data-child-csv], .tl-close').all()]:
                    bounds = control.bounding_box()
                    self.assertGreaterEqual(bounds['height'], 44)
                    self.assertGreaterEqual(bounds['width'], 44)
                    self.assertTrue(control.evaluate('''button => {
                        const box = button.getBoundingClientRect();
                        return button.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
                    }'''))
                if width == 844:
                    self.assertTrue(self.page.locator('.tl-canvas').is_hidden())
                    self.assertGreater(pane.evaluate('panel => panel.clientWidth'), position[2])
                    first_result = pane.locator('[data-child-show]').first
                    first_result.tap(trial=True)
                    self.assertEqual(pane.evaluate('panel => panel.scrollTop'), 0)
                self.assertLessEqual(self.ev('document.documentElement.scrollWidth'), width)
                parent.tap()
                self.assertTrue(trigger.evaluate('button => button === document.activeElement'))
                self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 0)
                self.assertTrue(self.page.locator('.tl-canvas').is_visible())
                self.assertEqual(pane.evaluate(read_position), position)

    def test_compact_child_action_enables_in_place_when_summary_finishes(self):
        self.page.set_viewport_size({'width': 844, 'height': 390})
        count = 40000
        self.open_range_data(['root', 'child'] + ['other'] * count,
                             durations=[2000, 100] + [100] * count,
                             parents=[-1, 0] + [-1] * count,
                             columns={'depth': [0, 1] + [0] * count})
        initial = self.ev('''async () => {
            const timeout = window.setTimeout, callbacks = [];
            window.setTimeout = function(callback, ...args) {
                if (callback.name === 'runDetailWork') { callbacks.push(callback); return 0; }
                return timeout.call(window, callback, ...args);
            };
            try {
                fgView('timeline').probe().select(0);
                await new Promise(requestAnimationFrame);
                document.querySelector('[data-call-actions]').click();
                window.pendingChildMenuAction = document.querySelector('[data-call-action=children]');
                return [pendingChildMenuAction.disabled, pendingChildMenuAction.title];
            } finally {
                window.setTimeout = timeout;
                callbacks.forEach(callback => timeout(callback, 0));
            }
        }''')
        self.assertEqual(initial, [True, 'Preparing child summary…'])
        self.page.wait_for_function('''() => !pendingChildMenuAction.disabled &&
            pendingChildMenuAction === document.querySelector('[data-call-action=children]')''')
        self.page.get_by_role('menuitem', name='Explore all children', exact=True).press('Enter')
        self.assertTrue(self.page.locator('[data-child-parent]').evaluate('button => button === document.activeElement'))
        self.assertEqual(self.page.locator('.an-child-table tbody tr').count(), 1)

    def test_child_preview_touch_targets_keep_long_symbols_and_both_actions_usable(self):
        self.page.close()
        context = self.browser.new_context(viewport={'width': 320, 'height': 780}, has_touch=True, is_mobile=True)
        self.addCleanup(context.close)
        self.page = context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)
        self.open_child_groups(count=9, prefix='very_long_recorded_function_name_' * 8)
        for width, height, theme in ((320, 780, 'light'), (844, 390, 'dark'), (1024, 768, 'light')):
            with self.subTest(width=width, theme=theme):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.ev('''theme => {
                    document.documentElement.setAttribute('data-theme', theme);
                    fgView('timeline').probe().select(0);
                }''', theme)
                pane = self.page.locator('#tlDetail')
                explorer = pane.locator('[data-child-open]')
                rows = pane.locator('[data-child-preview-group]')
                self.assertEqual(rows.count(), 8)
                self.assertTrue(pane.evaluate('panel => panel.scrollWidth <= panel.clientWidth'))
                for control in [explorer, *rows.all()]:
                    box = control.bounding_box()
                    self.assertGreaterEqual(box['width'], 44)
                    self.assertGreaterEqual(box['height'], 44)
                    self.assertGreaterEqual(box['x'], 0)
                    self.assertLessEqual(box['x'] + box['width'], width)
                rows.first.tap()
                self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 3)
                self.ev("fgView('timeline').probe().select(0)")
                explorer.tap()
                self.assertEqual(pane.locator('.an-child-table tbody tr').count(), 9)

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

    def test_child_pager_stays_reachable_from_the_start_and_middle_of_a_page(self):
        self.open_child_groups(count=225)
        self.page.click('[data-child-open]')
        pane = self.page.locator('.tl-detail')
        next_page = pane.locator('[data-child-page="1"]')
        visible_center = '''button => {
            const box = button.getBoundingClientRect();
            return button.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
        }'''
        for position in (0, 480, 900):
            pane.evaluate('(panel, top) => { panel.scrollTop = top; }', position)
            self.assertTrue(next_page.evaluate(visible_center), f'Pager is hidden at scrollTop {position}')
        next_page.press('Enter')
        self.assertIn('101–200 of 225 functions', pane.locator('.an-child-footer').inner_text())
        row = pane.locator('.an-child-table tbody tr').first.bounding_box()
        pager = pane.locator('.an-child-footer').bounding_box()
        self.assertLess(row['y'] + row['height'], pager['y'])
        self.assertTrue(next_page.evaluate(visible_center))

    def test_child_pager_jumps_directly_and_rejects_invalid_pages(self):
        self.open_child_groups(count=225)
        self.page.click('[data-child-open]')
        pane = self.page.locator('#tlDetail')
        field = pane.get_by_role('textbox', name='Page', exact=True)
        go = pane.get_by_role('button', name='Go to child page')
        status = pane.locator('.an-child-footer [role=status]')
        count = pane.locator('#tlChildPageTotal')
        rows = pane.locator('.an-child-table tbody tr')
        self.assertEqual(count.inner_text(), 'of 3')
        first = rows.first.element_handle()
        for value in ('', '0', '4', '1.5', '1e0'):
            field.fill(value)
            field.press('Enter')
            self.assertEqual(field.evaluate('input => input.validationMessage'), 'Enter a page from 1 to 3.')
            self.assertEqual(status.inner_text(), 'Showing 1–100 of 225 functions')
            self.assertTrue(first.evaluate('row => row.isConnected'))
        field.fill(' 001 ')
        field.press('Enter')
        self.assertEqual(field.input_value(), '1')
        self.assertTrue(first.evaluate('row => row.isConnected'))
        field.fill('3')
        go.click()
        self.assertEqual(status.inner_text(), 'Showing 201–225 of 225 functions')
        self.assertEqual(rows.count(), 25)
        self.assertTrue(go.evaluate('button => button === document.activeElement'))
        pane.locator('[data-child-mode=calls]').click()
        self.assertEqual(count.inner_text(), 'of 5')
        self.assertEqual(field.input_value(), '1')
        field.fill('5')
        field.press('Enter')
        self.assertEqual(status.inner_text(), 'Showing 401–450 of 450 calls')
        self.assertEqual(rows.count(), 50)
        self.assertTrue(field.evaluate('input => input === document.activeElement'))
        pane.locator('[data-child-mode=groups]').click()
        self.assertEqual(field.input_value(), '3')
        self.assertEqual(count.inner_text(), 'of 3')
        pane.locator('[data-child-query]').fill('child_0210')
        self.assertEqual(status.inner_text(), 'Showing 1–1 of 1 functions')
        self.assertTrue(field.is_hidden())

    def test_child_pager_remains_before_the_table_in_a_short_touch_pane(self):
        self.page.close()
        context = self.browser.new_context(viewport={'width': 320, 'height': 700}, has_touch=True, is_mobile=True)
        self.addCleanup(context.close)
        self.page = context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)
        self.open_child_groups(count=205, prefix='very_long_recorded_child_name_')
        self.page.locator('[data-child-open]').tap()
        pane = self.page.locator('.tl-detail')
        pane.get_by_role('button', name='All columns').tap()
        next_page = pane.locator('[data-child-page="1"]')
        previous = pane.locator('[data-child-page="-1"]')
        pane.evaluate('''panel => {
            localStorage.setItem('fgTl.detailFrac', '0.1');
            fgView('timeline').resize();
            panel.scrollLeft = panel.scrollWidth;
        }''')
        self.assertGreater(pane.evaluate('panel => panel.scrollLeft'), 0)
        self.assertTrue(pane.evaluate('''panel => {
            const footer = panel.querySelector('.an-child-footer'), table = panel.querySelector('.an-child-table');
            return panel.classList.contains('tl-detail-short') && footer.getBoundingClientRect().bottom <= table.getBoundingClientRect().top;
        }'''))
        next_page.scroll_into_view_if_needed()
        visible_center = '''button => {
            const box = button.getBoundingClientRect();
            return button.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
        }'''
        scroll_left = pane.evaluate('panel => panel.scrollLeft')
        field = pane.get_by_role('textbox', name='Page', exact=True)
        self.assertTrue(field.evaluate(visible_center))
        self.assertGreaterEqual(field.bounding_box()['width'], 44)
        self.assertGreaterEqual(field.bounding_box()['height'], 44)
        for expected in ('101–200', '201–205'):
            self.assertTrue(next_page.evaluate(visible_center))
            next_page.tap()
            self.assertIn(expected + ' of 205 functions', pane.locator('.an-child-footer').inner_text())
            self.assertEqual(pane.evaluate('panel => panel.scrollLeft'), scroll_left)
        self.assertTrue(previous.evaluate(visible_center))
        self.assertTrue(previous.evaluate('button => button === document.activeElement'))
        previous.press('Space')
        self.assertIn('101–200 of 205 functions', pane.locator('.an-child-footer').inner_text())
        self.assertTrue(previous.evaluate(visible_center))
        pane.evaluate('''panel => {
            localStorage.removeItem('fgTl.detailFrac');
            fgView('timeline').resize();
            panel.scrollTop = 480;
        }''')
        self.assertFalse(pane.evaluate("panel => panel.classList.contains('tl-detail-short')"))
        self.assertTrue(previous.evaluate(visible_center))
        self.assertTrue(previous.evaluate('button => button === document.activeElement'))
        self.ev("localStorage.setItem('fgTl.detailFrac', '0.1'); fgView('timeline').resize();")
        self.assertTrue(previous.evaluate(visible_center))
        self.assertTrue(previous.evaluate('button => button === document.activeElement'))
        self.assertLessEqual(self.ev('document.documentElement.scrollWidth'), 320)

    def test_child_pager_does_not_cover_results_in_expanded_landscape(self):
        self.page.close()
        context = self.browser.new_context(viewport={'width': 844, 'height': 390}, has_touch=True, is_mobile=True)
        self.addCleanup(context.close)
        self.page = context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)
        self.open_child_groups(count=205, prefix='long_child_symbol_' * 12)
        self.ev("document.documentElement.dataset.theme = 'dark'")
        self.page.get_by_role('separator', name='Resize details').press('Home')
        self.page.locator('[data-call-actions]').tap()
        self.page.get_by_role('menuitem', name='Explore all children', exact=True).tap()
        pane = self.page.locator('#tlDetail')
        first_cell = pane.locator('.an-child-table tbody tr td:first-child').first
        pages = pane.locator('.an-child-pages')
        next_page = pages.locator('[data-child-page="1"]')
        previous = pages.locator('[data-child-page="-1"]')
        count = pane.locator('.an-child-footer [role=status]')
        visible_center = '''element => {
            const box = element.getBoundingClientRect();
            return element.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2));
        }'''
        for mode, total in (('groups', 205), ('calls', 410)):
            with self.subTest(mode=mode):
                pane.locator(f'[data-child-mode={mode}]').tap()
                self.assertIn(f'1–100 of {total}', count.inner_text())
                self.assertTrue(first_cell.evaluate(visible_center))
                self.assertTrue(next_page.evaluate(visible_center))
                self.assertTrue(pages.get_by_role('textbox', name='Page', exact=True).evaluate(visible_center))
                for button in pages.get_by_role('button').all():
                    bounds = button.bounding_box()
                    self.assertGreaterEqual(bounds['width'], 44)
                    self.assertGreaterEqual(bounds['height'], 44)
                pane.evaluate('panel => { panel.scrollTop = 900; }')
                self.assertTrue(next_page.evaluate(visible_center))
                next_page.tap()
                self.assertIn(f'101–200 of {total}', count.inner_text())
                self.assertTrue(first_cell.evaluate(visible_center))
                self.assertTrue(next_page.evaluate(visible_center))
                self.assertTrue(next_page.evaluate('button => button === document.activeElement'))
        for width, height in ((1440, 960), (844, 390)):
            self.page.set_viewport_size({'width': width, 'height': height})
            self.page.wait_for_function('''inline => document.querySelector('.an-child-controls')
                .classList.contains('an-child-pager-inline') === inline''', arg=width == 844)
            self.assertTrue(next_page.evaluate('button => button === document.activeElement'))
            self.assertTrue(next_page.evaluate(visible_center))
        previous.press('Enter')
        self.assertIn('1–100 of 410 calls', count.inner_text())
        self.assertTrue(first_cell.evaluate(visible_center))
        self.assertTrue(next_page.evaluate('button => button === document.activeElement'))

    def test_child_selection_is_visible_with_chart_hidden(self):
        self.page.close()
        context = self.browser.new_context(viewport={'width': 844, 'height': 390}, has_touch=True, is_mobile=True)
        self.addCleanup(context.close)
        self.page = context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)
        self.open_child_groups(count=9)
        self.page.get_by_role('separator', name='Resize details').press('Home')
        self.page.locator('[data-call-actions]').tap()
        self.page.get_by_role('menuitem', name='Explore all children', exact=True).tap()
        pane = self.page.locator('#tlDetail')
        actions = pane.locator('[data-child-show]')
        current = pane.locator('[data-child-show][aria-current=true]')
        self.assertTrue(self.page.locator('.tl-canvas').is_hidden())
        self.assertEqual(current.count(), 0)
        for position, theme in enumerate(('light', 'dark')):
            with self.subTest(theme=theme):
                self.ev('theme => document.documentElement.dataset.theme = theme', theme)
                action = actions.nth(position)
                if position != 0:
                    action.focus()
                scroll = pane.evaluate('panel => [panel.scrollTop, panel.scrollLeft]')
                if position == 0:
                    action.tap()
                else:
                    action.press('Enter')
                self.assertEqual(pane.evaluate('panel => [panel.scrollTop, panel.scrollLeft]'), scroll)
                self.assertEqual(current.count(), 1)
                self.assertEqual(current.get_attribute('data-child-show'), action.get_attribute('data-child-show'))
                self.assertEqual(self.ev("fgView('timeline').probe().sel()"), int(current.get_attribute('data-child-show')))
                self.assertEqual(current.evaluate('element => getComputedStyle(element).textDecorationLine'), 'underline')
                self.assertEqual(pane.locator('tr.an-child-selected').count(), 1)
                self.assertTrue(action.evaluate('element => element === document.activeElement'))
        selected = current.get_attribute('data-child-show')
        pane.locator('[data-child-mode=calls]').tap()
        self.assertEqual(current.get_attribute('data-child-show'), selected)
        pane.locator('[data-child-show="1"]').press('Enter')
        self.assertEqual(current.get_attribute('data-child-show'), '1')
        pane.locator('[data-child-mode=groups]').tap()
        self.assertEqual(current.count(), 0)
        self.assertEqual(pane.locator('tr.an-child-selected').count(), 0)
        self.assertTrue(self.page.locator('.tl-canvas').is_hidden())

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
                    self.assert_detail_table_headers()

    def test_child_explorer_long_parent_and_function_scope_stay_compact(self):
        label = '<b>very_long_function_name</b>' * 30
        self.open_child_groups(count=2, prefix=label, parent_name=label + '_parent')
        self.page.click('[data-child-open]')
        self.page.click('[data-child-function="1"]')
        self.assertEqual(self.page.locator('.an-child-parent').get_attribute('aria-label'), 'Return to parent call: ' + label + '_parent')
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
        previous = section.get_by_role('button', name='Previous context page', exact=True)
        next_page = section.get_by_role('button', name='Next context page', exact=True)
        self.assertTrue(previous.is_disabled())
        state = self.ev("() => [scrollY, document.querySelector('.tl-scroll').scrollTop, fgView('timeline').probe().view()]")
        section.get_by_role('button', name='Next context page at bottom').press('Enter')
        self.assertEqual(section.locator('[data-slice]').count(), 100)
        self.assertFalse(first.evaluate('node => node.isConnected'))
        self.assertEqual(section.locator('[data-slice]').first.get_attribute('data-slice'), '100')
        self.assertIn('101–200 of 320', section.get_by_role('status').inner_text())
        self.assertTrue(next_page.evaluate('node => node === document.activeElement'))
        self.assertEqual(self.ev("() => [scrollY, document.querySelector('.tl-scroll').scrollTop, fgView('timeline').probe().view()]"), state)
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
        self.assertEqual(section.locator('[data-slice]').count(), 100)
        self.assertEqual(section.locator('[data-slice]').first.get_attribute('data-slice'), '100')
        adjusted = self.download_csv('[data-context-export=stack]')
        self.assertEqual(float(adjusted[0]['duration_ns']), float(rows[0]['duration_ns']) / 2)
        self.assertEqual([row['start_ns'] for row in adjusted], [row['start_ns'] for row in rows])
        self.assertTrue(all(row['duration_basis'] == 'overhead_subtracted_estimate' for row in adjusted))
        next_page.press('Space')
        next_page.press('Enter')
        self.assertEqual(section.locator('[data-slice]').count(), 20)
        self.assertEqual(section.locator('[data-slice]').last.get_attribute('data-slice'), '319')
        self.assertTrue(next_page.is_disabled())
        self.assertTrue(previous.evaluate('node => node === document.activeElement'))
        previous.press('Enter')
        self.assertEqual(section.locator('[data-slice]').first.get_attribute('data-slice'), '200')

    def test_compact_detail_stack_mounts_only_on_disclosure(self):
        self.page.close()
        self.page = self.browser.new_page(viewport={'width': 844, 'height': 390}, is_mobile=True, has_touch=True)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)
        self.open_detail_stack()
        section = self.page.locator('[data-context-list=stack]')
        toggle = section.get_by_role('button', name='Call stack (320 recorded frames)')
        rows = section.locator('[data-context-kind]')
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'false')
        self.assertGreaterEqual(toggle.bounding_box()['height'], 44)
        self.assertEqual(rows.count(), 0)
        original = self.download_csv('[data-context-export=stack]')
        self.assertEqual([int(row['recorded_slice_index']) for row in original], list(range(320)))
        self.assertEqual(rows.count(), 0)
        self.ev("document.querySelector('[data-act=adj]').click()")

        toggle.press('Enter')
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'true')
        self.assertEqual(rows.count(), 100)
        self.assertTrue(toggle.evaluate('node => node === document.activeElement'))
        self.assertEqual(rows.first.locator('[data-stack-duration]').inner_text(), '5.000 us')
        next_page = section.get_by_role('button', name='Next context page', exact=True)
        self.assertGreaterEqual(next_page.bounding_box()['height'], 44)
        next_page.press('Space')
        self.assertEqual(rows.count(), 100)
        first = rows.first.element_handle()
        toggle.tap()
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'false')
        self.assertFalse(rows.first.is_visible())
        self.ev("document.querySelector('[data-act=adj]').click()")
        toggle.tap()
        self.assertTrue(first.evaluate('node => node.isConnected'))
        self.assertEqual(rows.count(), 100)
        self.assertEqual(rows.first.locator('[data-slice]').get_attribute('data-slice'), '100')
        self.assertEqual(rows.first.locator('[data-stack-duration]').inner_text(), '9.800 us')

        self.page.set_viewport_size({'width': 320, 'height': 740})
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'true')
        self.assertEqual(rows.count(), 100)
        toggle_box = toggle.bounding_box()
        export_box = section.get_by_role('button', name='Export stack CSV').bounding_box()
        self.assertAlmostEqual(toggle_box['y'] + toggle_box['height'] / 2,
                               export_box['y'] + export_box['height'] / 2, delta=1)
        self.assertLessEqual(export_box['x'] + export_box['width'], 320)
        self.ev("fgView('timeline').probe().select(318)")
        self.assertEqual(section.get_by_role('button', name='Call stack (319 recorded frames)').get_attribute('aria-expanded'), 'false')
        self.assertEqual(rows.count(), 0)
        self.assertFalse(first.evaluate('node => node.isConnected'))

    def test_compact_stack_counts_context_only_on_demand(self):
        self.page.set_viewport_size({'width': 320, 'height': 740})
        self.open_detail_stack()
        selection_reads = self.ev('''() => {
            const sources = fgData.read('tlData').sources, callers = sources.callers;
            window.stackCallerReads = 0;
            Object.defineProperty(sources, 'callers', { configurable: true, get() {
                stackCallerReads++;
                return callers;
            }});
            fgView('timeline').probe().select(318);
            return stackCallerReads;
        }''')
        self.assertLess(selection_reads, 10)
        stack = self.page.locator('[data-context-list=stack]')
        toggle = stack.get_by_role('button', name='Call stack (319 recorded frames)', exact=True)
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'false')
        rows = self.download_csv('[data-context-export=stack]')
        self.assertEqual([int(row['recorded_slice_index']) for row in rows], list(range(319)))
        self.assertGreaterEqual(self.ev('stackCallerReads'), 319)
        self.assertEqual(stack.locator('[data-context-kind]').count(), 0)
        reads_to_open = self.ev('''() => {
            const before = stackCallerReads;
            document.querySelector('[data-stack-toggle]').click();
            return stackCallerReads - before;
        }''')
        self.assertLessEqual(reads_to_open, 101)
        self.assertIn('1–100 of 319', stack.get_by_role('status').inner_text())
        stack.get_by_role('button', name='Go to current frame', exact=True).press('Enter')
        self.assertEqual(stack.locator('[data-slice]').last.get_attribute('data-slice'), '318')

    def test_stack_current_frame_jumps_to_last_page_without_changing_selection(self):
        self.open_detail_stack()
        section = self.page.locator('[data-context-list=stack]')
        state = '''() => {
            const model = fgView('timeline').probe();
            return [model.sel(), model.view(), scrollY, document.querySelector('.tl-scroll').scrollTop];
        }'''
        before = self.ev(state)
        section.get_by_role('button', name='Go to current frame at bottom', exact=True).press('Enter')
        self.assertEqual(section.locator('[data-context-kind]').count(), 20)
        self.assertIn('301–320 of 320', section.get_by_role('status').inner_text())
        current = section.locator('[data-slice="319"][aria-current=true]')
        self.assertTrue(current.evaluate('''node => {
            const rect = node.getBoundingClientRect();
            return document.activeElement === node && node.contains(document.elementFromPoint(
                rect.left + rect.width / 2, rect.top + rect.height / 2));
        }'''))
        self.assertEqual(self.ev(state), before)
        original = current.element_handle()
        section.get_by_role('button', name='Go to current frame', exact=True).press('Space')
        self.assertTrue(original.evaluate('node => node === document.activeElement'))
        self.assertEqual(self.ev(state), before)

    def test_first_stack_seek_does_not_rescan_recorded_ancestors(self):
        self.page.set_viewport_size({'width': 320, 'height': 740})
        self.open_detail_stack(count=4097, mode='count')
        stack = self.page.locator('[data-context-list=stack]')
        reads = stack.evaluate('''section => {
            const sources = fgData.read('tlData').sources, callers = sources.callers;
            window.stackSeekReads = 0;
            Object.defineProperty(sources, 'callers', { configurable: true, get() {
                stackSeekReads++;
                return callers;
            }});
            section.querySelector('[data-stack-toggle]').click();
            return stackSeekReads;
        }''')
        self.assertGreaterEqual(reads, 4097)
        self.assertLessEqual(reads, 4097 + 101)
        self.ev('stackSeekReads = 0')
        stack.get_by_role('button', name='Go to current frame', exact=True).press('Enter')
        self.assertLessEqual(self.ev('stackSeekReads'), 101 + 32)
        self.assertEqual(stack.get_by_role('status').inner_text(), 'Showing 4001–4097 of 4097 context rows')
        self.assertEqual(stack.locator('[data-slice]').first.get_attribute('data-slice'), '4000')
        current = stack.locator('[data-slice="4096"][aria-current=true]')
        self.assertTrue(current.evaluate('node => node === document.activeElement'))

        self.ev('stackSeekReads = 0')
        stack.get_by_role('button', name='Previous context page', exact=True).press('Space')
        self.assertLessEqual(self.ev('stackSeekReads'), 101 + 32)
        self.assertEqual(stack.locator('[data-slice]').first.get_attribute('data-slice'), '3900')
        self.assertEqual(stack.locator('[data-slice]').last.get_attribute('data-slice'), '3999')
        self.assertEqual(self.ev('fgView("timeline").probe().sel()'), 4096)

    def test_short_stack_pager_keeps_all_controls_visible_through_consecutive_taps(self):
        for width, height in ((280, 740), (320, 740), (568, 320)):
            with self.subTest(viewport=(width, height)):
                self.page.close()
                self.page = self.browser.new_page(viewport={'width': width, 'height': height},
                                                  is_mobile=True, has_touch=True)
                self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                self.page.goto(self.url)
                self.page.add_init_script('localStorage.setItem("fgTl.detailFrac", "0.09")')
                self.open_detail_stack()
                stack = self.page.locator('[data-context-list=stack]')
                stack.get_by_role('button', name='Call stack (320 recorded frames)').tap()
                next_from_bottom = stack.get_by_role('button', name='Next context page at bottom', exact=True)
                next_from_bottom.scroll_into_view_if_needed()
                state = '''() => {
                    const model = fgView('timeline').probe();
                    return [model.sel(), model.view(), scrollY, document.querySelector('.tl-scroll').scrollTop];
                }'''
                self.page.evaluate('() => new Promise(requestAnimationFrame)')
                before = self.ev(state)
                next_from_bottom.tap()
                pager = stack.locator('[data-context-page-top]')
                for page, first, step, focus in ((2, 100, '1', '1'), (3, 200, '1', '1'), (4, 300, '-1', '-1'),
                                                 (3, 200, '-1', '-1'), (2, 100, '-1', '-1'), (1, 0, 'current', '1')):
                    self.assertEqual(stack.locator('.tl-crumbs [data-slice]').first.get_attribute('data-slice'), str(first))
                    self.assertEqual(pager.get_by_role('status').inner_text(),
                                     f'Showing {first + 1}–{min(first + 100, 320)} of 320 context rows')
                    self.assertEqual(pager.locator('[data-context-page-count]').inner_text(), f'{page} / 4')
                    self.assertEqual(self.ev('document.activeElement.dataset.contextPage'), focus)
                    self.assertEqual(self.ev(state), before)
                    point = pager.evaluate('''(pager, step) => {
                        const pane = pager.closest('.tl-detail'), paneRect = pane.getBoundingClientRect();
                        const top = Math.max(0, paneRect.top + pane.clientTop);
                        const bottom = Math.min(innerHeight, paneRect.top + pane.clientTop + pane.clientHeight);
                        const center = rect => [rect.left + rect.width / 2, rect.top + rect.height / 2];
                        const controls = [...pager.querySelectorAll('button,[data-context-page-count]')];
                        const targets = controls.map(node => node.getBoundingClientRect());
                        if (Math.min(...targets.map(rect => rect.bottom)) <= Math.max(...targets.map(rect => rect.top))) return null;
                        if (controls.some((node, index) => {
                            const rect = targets[index];
                            return rect.top < top - 0.5 || rect.bottom > bottom + 0.5 ||
                                node.matches('button') && (rect.width < 44 || rect.height < 44) ||
                                !node.contains(document.elementFromPoint(...center(rect)));
                        })) return null;
                        const action = pager.querySelector('[data-context-page="' + step + '"]');
                        return action.disabled ? null : center(action.getBoundingClientRect());
                    }''', step)
                    self.assertIsNotNone(point, (width, height, page))
                    self.page.touchscreen.tap(*point)
                current = stack.locator('[data-slice="319"][aria-current=true]')
                self.assertTrue(current.evaluate('node => node === document.activeElement'))
                self.assertEqual(self.ev(state), before)

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
        for start, expected in [(100, 100), (200, 100), (300, 20)]:
            section.get_by_role('button', name='Next context page', exact=True).press('Enter')
            self.assertEqual(section.locator('[data-slice]').count(), expected)
            self.assertEqual(section.locator('[data-slice]').first.get_attribute('data-slice'), str(start))

    def test_stack_context_pages_seek_inside_gaps_and_inline_frames(self):
        chain = [['root', 'root.c', 1]] + [['inline_' + str(index), 'inline.c', index + 2] for index in range(135)]
        self.open_range_data(['root', 'child'], starts=[0, 100], durations=[10000, 1000], parents=[-1, 0],
                             callers=[None, 'root+0x1/0x20'], columns={'depth': [3, 135]}, extra={'inline': {'2': chain}})
        self.ev("fgView('timeline').probe().select(1)")
        section = self.page.locator('[data-context-list=stack]')
        rows = self.download_csv('[data-context-export=stack]')
        self.assertEqual(len(rows), 269)
        self.assertEqual([rows[index]['kind'] for index in (0, 100, 200, 268)], ['outside', 'untraced', 'inline', 'traced'])
        actual = section.locator('[data-context-kind]')
        for page, direction in ((0, 'Next'), (1, 'Next'), (2, 'Previous'), (1, 'Previous'), (0, None)):
            self.assertEqual(actual.evaluate_all("nodes => nodes.map(node => node.dataset.contextKind)"),
                             [row['kind'] for row in rows[page * 100:(page + 1) * 100]])
            symbols = actual.locator('.tl-code-symbol').all_text_contents()
            self.assertEqual([symbol for symbol in symbols if symbol.startswith('inline_')],
                             [row['function'] for row in rows[page * 100:(page + 1) * 100] if row['kind'] == 'inline'])
            if direction:
                section.get_by_role('button', name=f'{direction} context page', exact=True).press('Enter')

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

    def open_detail_children(self, count=225, inline=False, child_names=None):
        self.ev("fgSwitchView('text')")
        self.page.reload()
        extra = {'inline': {'2': [['root', 'a.c', 1], ['inline_frame', 'b.c', 2]]}} if inline else None
        names = (child_names + ['child'] * (count - len(child_names))) if child_names else ['child'] * count
        self.open_range_data(['root'] + names, starts=[0] + [1000 + index * 1000 for index in range(count)],
                             durations=[(count + 2) * 1000] + [500] * count, parents=[-1] + [0] * count,
                             callers=[None] + ['root+0x1/0x20'] * count if inline else None,
                             columns={'depth': [0] + [1 if inline else 2] * count}, extra=extra)
        self.ev("fgView('timeline').probe().select(fgView('timeline').probe().N)")

    def test_large_context_csv_can_be_cancelled_and_discards_stale_selections(self):
        self.open_detail_children(count=4096)
        self.page.set_viewport_size({'width': 320, 'height': 700})
        selector = '[data-context-export=children]'
        button = self.page.locator(selector)
        downloads = []
        self.page.on('download', lambda download: downloads.append(download))
        self.assertEqual(button.evaluate('''button => {
            button.focus(); button.click();
            const status = button.parentElement.querySelector('[role=status]');
            const state = [button.textContent, status.textContent, document.activeElement === button,
                [button, status].every(node => {
                    const bounds = node.getBoundingClientRect();
                    return bounds.left >= 0 && bounds.right <= innerWidth && bounds.width > 0;
                })];
            button.click();
            return state;
        }'''), ['Cancel CSV', 'Preparing CSV…', True, True])
        self.assertTrue(self.page.locator('[data-context-csv-status]').is_hidden())
        for change in ("document.querySelector('[data-context-list=children] [data-slice]').click()",
                       "document.querySelector('#tlDetail .tl-close').click()"):
            with self.subTest(change=change):
                self.assertEqual(button.evaluate(f'''button => {{
                    button.click();
                    const label = button.textContent;
                    {change};
                    return label;
                }}'''), 'Cancel CSV')
                self.ev('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 30)))')
                self.assertEqual(downloads, [])
                self.ev("fgView('timeline').probe().select(fgView('timeline').probe().N)")
                self.assertEqual(button.inner_text(), 'Export calls CSV')
        self.assertEqual(len(self.download_csv(selector)), 4096)
        self.assertEqual(len(downloads), 1)

    def test_large_context_csv_pauses_and_keeps_its_scope_and_timing_basis(self):
        count = 20000
        unsafe = ['=SUM(1,2)', 'child,"quoted"', '@child', '接收']
        self.open_detail_children(count=count, child_names=unsafe)
        selector = '[data-context-export=children]'
        button = self.page.locator(selector)
        downloads = []
        self.page.on('download', lambda download: downloads.append(download))
        before = button.evaluate('''button => new Promise((resolve, reject) => {
            const status = button.parentElement.querySelector('[role=status]');
            const timeout = setTimeout(() => { watcher.disconnect(); reject(Error('No mid-export progress')); }, 5000);
            const watcher = new MutationObserver(() => {
                if (!status.textContent.endsWith('%')) return;
                watcher.disconnect(); clearTimeout(timeout);
                fgSwitchView('text');
                resolve(status.textContent);
            });
            watcher.observe(status, {childList:true});
            button.click();
        })''')
        self.ev('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 30)))')
        self.assertEqual(downloads, [])
        self.assertEqual(self.page.locator('[data-context-csv-status]').text_content(), before)

        with self.page.expect_download() as download_info:
            self.assertEqual(self.ev('''() => {
                fgSwitchView('timeline');
                document.querySelector('[data-act=adj]').click();
                const section = document.querySelector('[data-context-list=children]');
                section.querySelector('[data-context-page="1"]').click();
                return [section.querySelector('[data-context-export]').textContent,
                    section.querySelector('[data-context-page-top] [role=status]').textContent,
                    document.querySelector('[data-act=adj]').textContent];
            }'''), ['Cancel CSV', f'Showing 101–200 of {count} context rows', 'Overhead: subtracted'])
        download = download_info.value
        self.assertEqual(download.suggested_filename, 'funcgraph-context-children-raw.csv')
        with open(download.path(), 'rb') as source:
            raw = source.read()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbflist,position,kind,function,'))
        self.assertTrue(raw.endswith(b'\r\n'))
        self.assertEqual(raw.count(b'\r\n'), count + 1)
        self.assertIn(b'"child,""quoted"""', raw)
        self.assertIn('接收'.encode(), raw)
        self.assertEqual(button.inner_text(), 'Export calls CSV')
        self.assertTrue(self.page.locator('[data-context-csv-status]').is_hidden())
        rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline='')))
        self.assertEqual([row['function'] for row in rows[:4]], ["'" + unsafe[0], unsafe[1], "'" + unsafe[2], unsafe[3]])
        self.assertEqual([int(row['recorded_slice_index']) for row in rows], list(range(1, count + 1)))
        self.assertEqual({(row['duration_basis'], row['duration_ns'], row['self_ns']) for row in rows},
                         {('measured_duration', '500', '500')})
        self.ev("document.querySelector('[data-act=adj]').click()")
        self.assertEqual(self.download_csv_file(selector), ('funcgraph-context-children-raw.csv', raw))
        self.ev("document.querySelector('[data-act=adj]').click()")
        adjusted = self.download_csv(selector)
        self.assertEqual(len(adjusted), count)
        self.assertEqual({(row['duration_basis'], row['duration_ns'], row['self_ns']) for row in adjusted},
                         {('overhead_subtracted_estimate', '250', '250')})

    def test_large_collapsed_stack_csv_does_not_mount_rows(self):
        self.page.set_viewport_size({'width': 480, 'height': 720})
        self.open_detail_stack(count=4097, mode='count')
        section = self.page.locator('[data-context-list=stack]')
        self.assertEqual(section.locator('[data-stack-toggle]').get_attribute('aria-expanded'), 'false')
        rows = self.download_csv('[data-context-export=stack]')
        self.assertEqual(len(rows), 4097)
        self.assertEqual([rows[0]['function'], rows[-1]['function']], ['frame_0000', 'frame_4096'])
        self.assertTrue(all(row['duration_basis'] == 'call_order' and 'duration_units' in row for row in rows))
        self.assertEqual(section.locator('[data-slice]').count(), 0)
        self.assertEqual(section.locator('[data-stack-toggle]').get_attribute('aria-expanded'), 'false')

    def test_compact_stack_csv_keeps_cancel_on_the_same_row(self):
        self.page.set_viewport_size({'width': 320, 'height': 740})
        self.page.add_init_script('localStorage.setItem("fgTl.detailFrac", "0.09")')
        self.open_detail_stack(count=4097, mode='count')
        self.page.locator('[data-call-actions]').click()
        self.page.get_by_role('menuitem', name='Show call stack').click()
        button = self.page.get_by_role('button', name='Export stack CSV', exact=True)
        facts = button.evaluate('''button => {
            const back = document.querySelector('[data-detail-return]');
            const title = document.querySelector('[data-stack-toggle]');
            const progress = button.parentElement.querySelector('[data-context-csv-status]');
            button.focus({preventScroll:true});
            button.click();
            const hit = node => {
                const rect = node.getBoundingClientRect();
                return node.contains(document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2));
            };
            const active = [button.innerText, button.getAttribute('aria-label'), progress.innerText,
                [back, title, button].every(hit), document.activeElement === button];
            button.click();
            return {active, restored:[button.innerText, button.getAttribute('aria-label'), progress.hidden]};
        }''')
        self.assertEqual(facts, {'active': ['Cancel', 'Cancel CSV', 'Preparing CSV…', True, True],
                                 'restored': ['CSV', 'Export stack CSV', True]})

    def test_detail_ghost_children_are_complete_cached_and_incremental(self):
        self.open_detail_children()
        section = self.page.locator('[data-context-list=children]')
        self.assertEqual(section.locator('[data-slice]').count(), 100)
        rows = self.download_csv('[data-context-export=children]')
        self.assertEqual(len(rows), 225)
        self.assertEqual([int(row['recorded_slice_index']) for row in rows], list(range(1, 226)))
        section.get_by_role('button', name='Next context page', exact=True).press('Enter')
        self.assertEqual(section.locator('[data-slice]').count(), 100)
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
        self.assertEqual(section.locator('[data-slice]').count(), 100)
        self.assertEqual(section.locator('[data-slice]').first.get_attribute('data-slice'), '101')
        adjusted = self.download_csv('[data-context-export=children]')
        self.assertEqual(float(adjusted[0]['duration_ns']), float(rows[0]['duration_ns']) / 2)
        self.assertEqual([row['start_ns'] for row in adjusted], [row['start_ns'] for row in rows])
        section.get_by_role('button', name='Next context page', exact=True).press('Space')
        self.assertEqual(section.locator('[data-slice]').count(), 25)
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
        self.page.locator('[data-context-list=children]').get_by_role('button', name='Next context page', exact=True).press('Enter')
        self.page.locator('[data-context-list=children] [data-slice="150"]').press('Space')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 150)
        self.assertEqual(self.page.locator('[data-context-list=stack] [data-context-kind=inline]').count(), 1)

    def test_detail_large_synthetic_fanout_only_renders_requested_rows(self):
        self.open_detail_children(count=150000)
        section = self.page.locator('[data-context-list=children]')
        rows = section.locator('[data-slice]')
        self.assertEqual(rows.count(), 100)
        self.assertIn('1–100 of 150000 context rows', section.get_by_role('status').inner_text())
        next_page = section.get_by_role('button', name='Next context page', exact=True)
        for start in range(101, 1002, 100):
            next_page.press('Enter')
            self.assertEqual(rows.count(), 100)
            self.assertEqual(rows.first.get_attribute('data-slice'), str(start))

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
        section.get_by_role('button', name='Next context page', exact=True).press('Enter')
        self.assertEqual(section.locator('.tl-context-row').count(), 100)
        self.assertIn('inline_99', section.locator('.tl-context-row').first.inner_text())
        self.ev('''() => {
            const model = fgView('timeline').probe();
            model.select(model.N + model.SYN.findIndex(frame => frame.k === 750));
        }''')
        selected = self.ev("fgView('timeline').probe().sel()")
        section.get_by_role('button', name='Go to current frame', exact=True).press('Enter')
        self.assertIn('701–800 of 1001', section.get_by_role('status').inner_text())
        current = section.locator('[aria-current=true]')
        self.assertIn('inline_749', current.inner_text())
        self.assertTrue(current.evaluate('node => node === document.activeElement'))
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), selected)

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

    def test_detail_start_only_shows_an_offset_without_an_absolute_clock(self):
        for mode, expected in (('rel', '+3.000 us'), ('dur', '+3.000 us'), ('count', '+3 u')):
            with self.subTest(mode=mode):
                self.ev("fgSwitchView('text')")
                self.page.reload()
                self.open_range_data(['work'], mode=mode, starts=[3000], durations=[1000])
                self.ev("fgView('timeline').probe().select(0)")
                start = self.page.locator('.tl-kv .k:text-is("start") + .v')
                self.assertEqual(start.locator('.tl-time-value').all_text_contents(), [expected])
                self.assertEqual(start.locator('.tl-absolute-time').count(), 0)

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

    def test_detail_breakdown_reuses_interval_index_without_changing_touching_or_unordered_waits(self):
        start, end, count = 3_000_010, 3_000_210, 65_536
        for ordered in (True, False):
            with self.subTest(ordered=ordered):
                self.ev("fgSwitchView('text')")
                self.page.reload()
                off = [[0, index * 100, index * 100 + 40, 1, -1, -1, 0, 0, index % 2, -1] for index in range(count)]
                off[29999][2] = start
                off[30003][1:3] = [end, end + 50]
                if not ordered:
                    off[59], off[30000] = off[30000], off[59]
                self.open_range_data(['root', 'child'], starts=[start, start + 65], durations=[end - start, 20],
                                     parents=[-1, 0], columns={'depth': [0, 1]}, extra={'offcpu': off})
                result = self.ev('''() => {
                    const probe = fgView('timeline').probe(), track = probe.TRACKS[0];
                    let reads = 0;
                    track.off = new Proxy(track.off, {get(target, key) {
                        if (typeof key === 'string' && /^[0-9]+$/.test(key)) reads++;
                        return Reflect.get(target, key, target);
                    }});
                    probe.select(0);
                    return {reads, titles: [...document.querySelectorAll('.an-bd span')].map(part => part.title)};
                }''')
                self.assertEqual(result['titles'], ['own code 100 ns', 'traced callees 20 ns', 'blocked 40 ns', 'preempted 40 ns'])
                self.assertLess(result['reads'], 320)

    def test_large_detail_breakdown_yields_and_ignores_closed_or_replaced_details(self):
        count = 12000
        self.open_range_data(['root', 'helper', 'irq'] + ['leaf'] * (count - 1),
                             starts=[0, 1000] + [1100 + index * 100 for index in range(count)],
                             durations=[2000000, 1200000] + [50] * count,
                             parents=[-1, 0] + [1] * count,
                             columns={'depth': [0, 1] + [2] * count, 'flags': [0, 0, 2] + [0] * (count - 1)},
                             extra={'offcpu': [[0, 1300000, 1300100, 1, -1, -1, 0, 0, 0, -1],
                                               [0, 3000, 3100, 2, -1, -1, 0, 0, 1, -1]]})
        self.ev('''() => {
            const clock = performance.now.bind(performance);
            window.selectWithShortBreakdownBudget = index => {
                let elapsed = 0;
                performance.now = () => clock() + (elapsed += 4);
                try { fgView('timeline').probe().select(index); }
                finally { delete performance.now; }
            };
        }''')
        state = self.ev('''() => {
            const model = fgView('timeline').probe();
            selectWithShortBreakdownBudget(0);
            const pending = document.querySelector('[data-detail-breakdown]');
            document.querySelector('#tlDetail .tl-close').click();
            selectWithShortBreakdownBudget(0);
            const replaced = document.querySelector('[data-detail-breakdown]');
            model.select(3);
            return [pending.textContent, replaced.textContent, pending.isConnected, replaced.isConnected,
                model.sel(), !!document.querySelector('.an-bd')];
        }''')
        self.assertEqual(state, ['Calculating time breakdown…', 'Calculating time breakdown…', False, False, 3, True])
        self.ev('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 30)))')
        self.assertEqual(self.page.locator('.an-bd span').evaluate_all('(bars) => bars.map(bar => bar.title)'), ['own code 50 ns'])

        self.ev("selectWithShortBreakdownBudget(0); fgSwitchView('text');")
        self.ev('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 30)))')
        self.assertEqual(self.page.locator('[data-detail-breakdown]').text_content(), 'Calculating time breakdown…')
        self.page.locator('.view-tab[data-view=timeline]').click()
        self.page.locator('.an-bd').wait_for()
        self.assertEqual(self.page.locator('[data-detail-breakdown]').count(), 0)
        self.assertEqual(self.page.locator('.an-bd span').evaluate_all('(bars) => bars.map(bar => bar.title)'),
                         ['own code 799.9 us', 'traced callees 1.200 ms', 'interrupts / softirq 50 ns', 'blocked 100 ns', 'preempted 100 ns'])
        self.assertIn(f'{count + 1} traced calls inside', self.page.locator('.an-bd-legend').inner_text())
        self.assertTrue(self.ev('''() => {
            const model = fgView('timeline').probe();
            model.select(3); model.select(0);
            return !!document.querySelector('.an-bd') && !document.querySelector('[data-detail-breakdown]');
        }'''))

    def test_first_selection_on_large_track_defers_details_until_after_paint(self):
        count = 40000
        self.open_range_data(['root', 'helper', 'first', 'second'] + ['other_root'] * count,
                             starts=[0, 1000, 1500, 1700] + [5000 + index * 1000 for index in range(count)],
                             durations=[3000, 1100, 50, 50] + [100] * count,
                             parents=[-1, 0, 1, 1] + [-1] * count,
                             columns={'depth': [0, 1, 2, 2] + [0] * count})
        state = self.ev('''() => {
            const model = fgView('timeline').probe();
            const select = index => {
                model.select(index);
                return ['[data-detail-breakdown]', '[data-child-preview] [role=status]']
                    .map(selector => document.querySelector(selector));
            };
            const now = performance.now();
            const requestFrame = window.requestAnimationFrame;
            let detailFrames = 0;
            performance.now = () => now;
            window.requestAnimationFrame = callback => {
                if (new Error().stack.includes('queueDetailWork')) detailFrames++;
                return requestFrame.call(window, callback);
            };
            try {
                const closed = select(1);
                document.querySelector('#tlDetail .tl-close').click();
                const replaced = select(2);
                for (let iteration = 0; iteration < 10; iteration++) {
                    model.select(1);
                    model.select(2);
                }
                const active = select(1);
                window.staleDetailWork = [...closed, ...replaced];
                fgSwitchView('text');
                return [model.sel(), ...active.map(node => node?.textContent),
                    document.querySelector('.tl-recorded-call-nav').getAttribute('aria-busy'),
                    document.querySelector('[data-call-nav=parent]').disabled, detailFrames];
            } finally {
                window.requestAnimationFrame = requestFrame;
                delete performance.now;
            }
        }''')
        waiting = ['Calculating time breakdown…', 'Preparing child summary…']
        self.assertEqual(state, [1, *waiting, 'true', False, 1])
        self.ev('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 30)))')
        self.assertEqual(self.page.locator('[data-detail-breakdown]').text_content(), waiting[0])
        self.assertEqual(self.page.locator('[data-child-preview] [role=status]').text_content(), waiting[1])
        self.page.locator('.view-tab[data-view=timeline]').click()
        self.page.get_by_role('button', name='First recorded child (↓): first', exact=True).wait_for()
        self.page.locator('.an-bd').wait_for()
        self.page.locator('[data-child-preview] [role=status]').wait_for(state='detached')
        self.assertEqual(self.page.locator('.an-bd span').evaluate_all('(bars) => bars.map(bar => bar.title)'),
                         ['own code 1.000 us', 'traced callees 100 ns'])
        self.assertEqual(self.ev('''() => staleDetailWork.map(node =>
            [node.textContent, node.isConnected, !!node.parentNode])'''),
                         [[message, False, True] for message in waiting * 2])
        self.assertTrue(self.ev('''() => {
            fgView('timeline').probe().select(0);
            return !!document.querySelector('.an-bd') &&
                !document.querySelector('.tl-recorded-call-nav[aria-busy]') &&
                !document.querySelector('[data-child-preview] [role=status]');
        }'''))

    def test_replacing_detail_between_frame_and_timer_waits_for_a_new_frame(self):
        count = 32768
        self.open_range_data(['first_root', 'first_child', 'second_root', 'second_child'] + ['other'] * count,
                             durations=[3000, 100, 2000, 200] + [100] * count,
                             parents=[-1, 0, -1, 2] + [-1] * count,
                             columns={'depth': [0, 1, 0, 1] + [0] * count})
        observed = self.ev('''async () => {
            const timeout = window.setTimeout, clear = window.clearTimeout;
            const timers = new Map(), scheduled = [];
            const nextFrame = () => new Promise(requestAnimationFrame);
            window.setTimeout = (callback, ...args) => {
                if (callback.name !== 'runDetailWork') return timeout(callback, ...args);
                const timer = -(scheduled.length + 1);
                scheduled.push(timer);
                timers.set(timer, callback);
                return timer;
            };
            window.clearTimeout = timer => timer < 0 ? timers.delete(timer) : clear(timer);
            try {
                const model = fgView('timeline').probe();
                model.select(0);
                await nextFrame();
                const before = Array.from(timers.keys());
                model.select(2);
                const oldTimerCancelled = before.every(timer => !timers.has(timer));
                const pendingBeforeFrame = !!document.querySelector('[data-detail-breakdown]');
                await nextFrame();
                return {before, oldTimerCancelled, pendingBeforeFrame, after: Array.from(timers.keys())};
            } finally {
                window.setTimeout = timeout;
                window.clearTimeout = clear;
                timers.forEach(callback => timeout(callback, 0));
            }
        }''')
        self.assertEqual(observed, dict(before=[-1], oldTimerCancelled=True, pendingBeforeFrame=True, after=[-2]))
        self.page.locator('.an-bd').wait_for()
        self.assertEqual(self.page.locator('.an-bd span').evaluate_all('(bars) => bars.map(bar => bar.title)'),
                         ['own code 1.800 us', 'traced callees 200 ns'])
        self.page.get_by_role('button', name='First recorded child (↓): second_child', exact=True).wait_for()

    def test_deferred_child_summary_removes_empty_leaf_controls_and_preserves_focus(self):
        count = 32768
        self.open_range_data(['parent', 'leaf'] + ['other_root'] * count,
                             starts=[0, 500] + [2000 + index * 1000 for index in range(count)],
                             durations=[1000] + [100] * (count + 1),
                             parents=[-1, 0] + [-1] * count,
                             columns={'depth': [0, 1] + [0] * count})
        state = self.ev('''() => {
            fgView('timeline').probe().select(1);
            const explorer = document.querySelector('[data-child-open]');
            explorer.focus({preventScroll: true});
            return [explorer.getAttribute('aria-disabled'), explorer === document.activeElement];
        }''')
        self.assertEqual(state, ['true', True])
        self.page.locator('.tl-call-nav:not([aria-busy])').wait_for()
        self.page.locator('[data-child-preview] [role=status]').wait_for(state='detached')
        self.assertEqual(self.page.locator('.tl-child-summary, [data-child-preview]').count(), 0)
        self.assertTrue(self.page.locator('[data-call-actions]').evaluate('button => button === document.activeElement'))
        self.assertTrue(self.page.locator('[data-call-nav=child]').is_disabled())
        self.ev("fgView('timeline').probe().select(0)")
        self.assertEqual(self.page.locator('[data-child-title]').text_content(), 'Children (1 call)')
        self.assertTrue(self.page.locator('[data-child-open]').is_enabled())

    def test_pending_detail_jobs_share_one_frame_budget_and_enable_navigation_first(self):
        count = 40000
        self.open_range_data(['root', 'child'] + ['descendant'] * count,
                             durations=[1000 * (count + 2), 1000 * (count + 1)] + [100] * count,
                             parents=[-1, 0] + [1] * count,
                             columns={'depth': [0, 1] + [2] * count})
        observed = self.ev('''async () => {
            const timeout = window.setTimeout, callbacks = [];
            const mutations = [];
            const observer = new MutationObserver(records => {
                for (const record of records) mutations.push([record.target.dataset.callNav, record.attributeName]);
            });
            const nextFrame = () => new Promise(requestAnimationFrame);
            window.setTimeout = function(callback, ...args) {
                if (new Error().stack.includes('flushDetailPaintWork')) {
                    callbacks.push(callback);
                    return 0;
                }
                return timeout.call(window, callback, ...args);
            };
            try {
                fgView('timeline').probe().select(0);
                const nav = document.querySelector('.tl-recorded-call-nav');
                const child = nav.querySelector('[data-call-nav=child]');
                observer.observe(nav, {subtree: true, attributes: true, attributeFilter: ['disabled', 'title', 'aria-label']});
                const childReady = !child.disabled && child.title === 'First recorded child (↓): child';
                child.focus({preventScroll: true});
                await nextFrame();
                const callbackCounts = [callbacks.length];
                const pending = () => [!!document.querySelector('.tl-call-nav[aria-busy]'),
                    !!document.querySelector('[data-detail-breakdown]'),
                    !!document.querySelector('[data-child-preview] [role=status]')];
                let state = pending();
                let clock = 0;
                for (let frame = 0; frame < 10 && state.every(Boolean) && callbacks.length; frame++) {
                    performance.now = () => clock += 6;
                    try { callbacks.splice(0).forEach(callback => callback()); }
                    finally { delete performance.now; }
                    state = pending();
                    await nextFrame();
                    callbackCounts.push(callbacks.length);
                }
                return {callbackCounts, state, childReady, mutations,
                    focusRetained: child === document.activeElement && child === document.querySelector('[data-call-nav=child]')};
            } finally {
                observer.disconnect();
                window.setTimeout = timeout;
                callbacks.forEach(callback => timeout(callback, 0));
            }
        }''')
        self.assertGreater(len(observed['callbackCounts']), 2)
        self.assertTrue(all(count == 1 for count in observed['callbackCounts']))
        self.assertTrue(observed['childReady'] and observed['focusRetained'], observed)
        self.assertEqual(observed['state'], [False, True, True])
        self.assertEqual(sorted(observed['mutations']), [['next', 'aria-label'], ['next', 'title']])
        self.page.get_by_role('button', name='First recorded child (↓): child', exact=True).wait_for()
        self.page.locator('.an-bd').wait_for()
        self.page.locator('[data-child-preview] [role=status]').wait_for(state='detached')
        self.assertEqual(self.page.locator('[data-child-title]').text_content(), 'Children (1 call)')

    def test_cold_nonleaf_with_complete_navigation_defers_child_summary_through_overhead_toggle(self):
        count = 40000
        self.open_range_data(['earlier'] * count + ['root', 'child'],
                             starts=list(range(0, count * 1000 + 1000, 1000)) + [count * 1000 + 100],
                             durations=[1000] * (count + 1) + [100], parents=[-1] * (count + 1) + [count],
                             columns={'depth': [0] * (count + 1) + [1], 'flags': [F_LEAF] * count + [0, F_LEAF]})
        observed = self.ev('''index => {
            fgView('timeline').probe().select(index);
            const nav = document.querySelector('.tl-call-nav');
            const status = document.querySelector('[data-child-preview] [role=status]');
            const child = document.querySelector('[data-call-nav=child]');
            const arrays = window.Int32Array;
            let allocations = 0;
            window.Int32Array = class extends arrays {
                constructor(...args) {
                    super(...args);
                    if (args[0] === index + 2) allocations++;
                }
            };
            try { document.querySelector('[data-act=adj]').click(); }
            finally { window.Int32Array = arrays; }
            return {childReady: !child.disabled && child.title === 'First recorded child (↓): child',
                navigationPending: nav.hasAttribute('aria-busy'), allocations,
                breakdownPending: !!document.querySelector('[data-detail-breakdown]'),
                sameStatus: !!status && status === document.querySelector('[data-child-preview] [role=status]')};
        }''', count)
        self.assertEqual(observed, dict(childReady=True, navigationPending=False, allocations=0,
                                        breakdownPending=True, sameStatus=True))
        self.page.locator('.an-bd').wait_for()
        preview = self.page.locator('.tl-child-preview')
        preview.wait_for()
        self.assertEqual(preview.locator('.d').text_content(), '50 ns')

    def test_recorded_leaf_shows_breakdown_and_nearby_navigation_without_index(self):
        count = 32768
        self.open_range_data(['parent', 'leaf', 'sibling'] + ['other_root'] * count,
                             starts=[0, 500, 2000] + [4000 + index * 1000 for index in range(count)],
                             durations=[3000, 1000, 100] + [100] * count,
                             parents=[-1, 0, 0] + [-1] * count,
                             columns={'depth': [0, 1, 1] + [0] * count, 'flags': [0] + [F_LEAF] * (count + 2)},
                             extra={'offcpu': [[0, 600, 750, 2, -1, -1, 0, 0, 0, -1],
                                               [0, 900, 1000, 2, -1, -1, 0, 0, 1, -1]],
                                    'overhead': {'self': 20, 'child': 30}})
        first = self.ev('''() => {
            fgView('timeline').probe().select(1);
            return {
                pending: document.querySelector('.tl-call-nav').hasAttribute('aria-busy'),
                children: !!document.querySelector('.tl-child-summary, [data-child-preview]'),
                child: document.querySelector('[data-call-nav=child]').getAttribute('aria-label'),
                bars: Array.from(document.querySelectorAll('.an-bd span'), part => part.title),
                legend: document.querySelector('.an-bd-legend')?.textContent,
            };
        }''')
        self.assertFalse(first['pending'])
        self.assertFalse(first['children'])
        self.assertEqual(first['child'], 'First recorded child (↓): none')
        self.assertEqual(first['bars'], ['own code 750 ns', 'blocked 150 ns', 'preempted 100 ns'])
        self.assertIn('≈20 ns is tracing overhead (0 traced calls inside)', first['legend'])
        next_call = self.page.get_by_role('button', name='Next recorded sibling / task root (]): sibling', exact=True)
        self.assertTrue(next_call.is_enabled())
        self.assertTrue(self.page.locator('[data-call-nav=parent]').is_enabled())
        self.assertTrue(self.page.locator('[data-call-nav=child]').is_disabled())
        self.assertFalse(self.page.locator('.tl-recorded-call-nav').get_attribute('aria-busy'))
        self.assertEqual(self.page.locator('.an-bd-legend').text_content(), first['legend'])
        self.page.locator('.tl-canvas').press(']')
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 2)
        self.assertEqual(self.page.locator('.tl-child-summary, [data-child-preview]').count(), 0)

    def test_recorded_leaf_keeps_near_sibling_available_while_far_sibling_is_indexed(self):
        descendants = 300
        target = descendants + 2
        self.open_range_data(['root', 'wide', *(['descendant'] * descendants), 'leaf', 'nearby'],
                             durations=[(descendants + 4) * 1000, (descendants + 1) * 1000] + [50] * (descendants + 2),
                             parents=[-1, 0] + [1] * descendants + [0, 0],
                             columns={'depth': [0, 1] + [2] * descendants + [1, 1], 'flags': [0, 0] + [F_LEAF] * (descendants + 2)})
        initial = self.ev('''index => {
            fgView('timeline').probe().select(index);
            const nav = document.querySelector('.tl-recorded-call-nav');
            const previous = nav.querySelector('[data-call-nav=previous]');
            const next = nav.querySelector('[data-call-nav=next]');
            next.focus({preventScroll: true});
            window.earlySiblingControl = next;
            return [nav.getAttribute('aria-busy'), previous.disabled, previous.title, next.disabled, next.title];
        }''', target)
        self.assertEqual(initial, ['true', True, 'Previous recorded sibling / task root ([): preparing…',
                                   False, 'Next recorded sibling / task root (]): nearby'])
        far = self.page.get_by_role('button', name='Previous recorded sibling / task root ([): wide', exact=True)
        far.wait_for()
        self.assertTrue(self.ev('''() => earlySiblingControl === document.activeElement &&
            earlySiblingControl === document.querySelector('[data-call-nav=next]')'''))
        far.click()
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 1)

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
            fgNotifyFilter('root and child only', new Set([1, 5]));
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
        self.open_slowfast_data(functions=105, root_sites=['site_a'] * 7 + ['site_b'] * 3)
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
        self.assertEqual(len(self.download_csv('[data-act=slowfast-csv]')), 109)
        pager = self.page.locator('.an-slowfast-pager')
        next_page = pager.locator('[data-act=slowfast-next]')
        next_page.press('Space')
        self.assertIn('101–109 of 109', pager.inner_text())
        self.page.locator('tr[data-site=site_b] button').press('Enter')
        self.assertIn('needs at least 6', self.page.locator('.an-slowfast').inner_text())
        self.page.locator('[data-act=dist-reset-site]').press('Enter')
        self.assertIn('1–100 of 109', pager.inner_text())
        next_page.press('Space')
        self.page.locator('tr[data-site=site_a] button').press('Enter')
        self.assertIn('1–100 of 109', pager.inner_text())
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
        self.assertEqual(self.page.locator('.an-slowfast-pages').count(), 0)
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
                    for selector in ['.an-slowfast-tools', '.an-slowfast-pager', '.an-slowfast-hint']:
                        bounds = self.page.locator(selector).bounding_box()
                        self.assertGreaterEqual(bounds['x'], 0)
                        self.assertLessEqual(bounds['x'] + bounds['width'], width)
                    self.page.locator('[data-sf-sort=name] button').press('Enter')
                    self.page.locator('[data-sf-example]').first.press('Space')
                    self.assertEqual(self.page.locator('.tl-detail-title').inner_text(), 'target')
                    self.assertLessEqual(self.ev('document.documentElement.scrollWidth'), width)
                    self.assertEqual(self.ev("getComputedStyle(document.querySelector('.an-dist-head')).top"),
                                     'auto' if width <= 640 or height <= 600 else '0px')
                    self.assert_detail_table_headers()
                    self.assertEqual(len(self.download_csv('[data-act=slowfast-csv]')), 9)
                    self.assertLessEqual(self.page.locator('.an-slowfast-table tr[data-sf-kind=function]').first.evaluate('(node) => node.offsetHeight'), 48)

    def test_slowfast_open_reveals_controls_below_the_detail_header(self):
        self.open_slowfast_data(functions=125)
        for width in (1500, 390):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': 900})
                pane = self.page.locator('.tl-detail')
                toggle = pane.locator('[data-act=slowfast]')
                toggle.press('Space')
                self.page.locator('.tl-resizer').press('Home' if width < 640 else 'ArrowUp')
                collapsed = toggle.get_attribute('aria-expanded')
                scroll_left = toggle.evaluate('''button => {
                    const pane = button.closest('.tl-detail');
                    if (pane.classList.contains('tl-detail-short')) pane.scrollLeft = pane.scrollWidth;
                    pane.scrollTop += button.getBoundingClientRect().bottom - pane.getBoundingClientRect().top - pane.clientHeight + 5;
                    return pane.scrollLeft;
                }''')
                chart = self.ev("fgView('timeline').probe().view()")
                toggle.click()
                self.assertTrue(toggle.evaluate('''button => {
                    const pane = button.closest('.tl-detail');
                    const top = pane.getBoundingClientRect().top + pane.clientTop;
                    const head = pane.querySelector('.tl-detail-head');
                    const pinned = getComputedStyle(head).top === 'auto' ? 0 : head.offsetHeight;
                    const bounds = button.getBoundingClientRect();
                    const toolsTop = button.parentElement.getBoundingClientRect().top;
                    return document.activeElement === button && Math.abs(toolsTop - top - pinned) <= 1 &&
                        button.contains(document.elementFromPoint(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2));
                }'''))
                self.assertEqual(collapsed, 'false')
                self.assertEqual(toggle.get_attribute('aria-expanded'), 'true')
                offset, maximum = pane.evaluate('pane => [pane.scrollLeft, pane.scrollWidth - pane.clientWidth]')
                self.assertEqual(offset, min(scroll_left, maximum))
                self.assertEqual(self.ev("fgView('timeline').probe().view()"), chart)

    def test_slowfast_short_pane_can_page_at_the_far_horizontal_edge(self):
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.open_slowfast_data(functions=125, child_prefix='wide_' * 60)
        scroll_left = self.assert_short_pane_controls_reachable(['[data-act=slowfast-next]'])
        next_page = self.page.locator('[data-act=slowfast-next]')
        bounds = next_page.bounding_box()
        self.page.mouse.click(bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2)
        self.assertIn('101–129 of 129', self.page.locator('.an-slowfast-pager').inner_text())
        offset, maximum = self.page.locator('.tl-detail').evaluate('node => [node.scrollLeft, node.scrollWidth - node.clientWidth]')
        self.assertEqual(offset, min(scroll_left, maximum))
        self.assertGreater(offset, 50)
        previous = self.page.locator('[data-act=slowfast-prev]')
        self.assertTrue(previous.evaluate('''control => {
            const bounds = control.getBoundingClientRect();
            return control.contains(document.elementFromPoint(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2));
        }'''))
        previous.press('Space')
        self.assertIn('1–100 of 129', self.page.locator('.an-slowfast-pager').inner_text())
        self.assertTrue(next_page.evaluate('node => node === document.activeElement'))

    def test_slowfast_large_fanout_paginates_without_spreading_arguments(self):
        count = 150000
        self.open_range_data(['target'] * 6 + ['child_' + str(index) for index in range(count)],
                             starts=[index * 2000 for index in range(6)] + [11000 + index * 1000 for index in range(count)],
                             durations=[1000] * 5 + [(count + 1) * 1000] + [500] * count,
                             parents=[-1] * 6 + [5] * count, columns={'depth': [0] * 6 + [1] * count})
        self.ev("fgView('timeline').probe().dist(0)")
        pending = self.page.locator('.an-slowfast-pending')
        self.assertTrue(self.ev('''() => {
            document.querySelector('[data-act=slowfast]').click();
            const preparing = !!document.querySelector('.an-slowfast-pending');
            document.querySelector('[data-act=slowfast]').click();
            return preparing;
        }'''))
        self.ev('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 25)))')
        self.assertEqual(self.page.locator('.an-slowfast-table').count(), 0)
        self.assertEqual(self.ev('''() => {
            const toggle = document.querySelector('[data-act=slowfast]');
            toggle.focus(); toggle.click();
            const result = [document.querySelector('.an-slowfast-pending')?.textContent,
                document.activeElement.textContent, !!document.querySelector('[data-act=slowfast-csv]')];
            fgSwitchView('text');
            return result;
        }'''), ['Preparing comparison…', 'Hide comparison', False])
        self.ev('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 25)))')
        self.assertEqual(pending.text_content(), 'Preparing comparison…')
        self.page.locator('.view-tab[data-view=timeline]').click()
        self.page.locator('.an-slowfast-table').wait_for()
        self.assertEqual(self.page.locator('.an-slowfast-table tbody tr').count(), 100)
        self.assertIn('of 150004 profile rows', self.page.locator('.an-slowfast-pager').inner_text())
        self.page.locator('[data-act=slowfast-next]').press('Enter')
        self.assertIn('101–200 of 150004', self.page.locator('.an-slowfast-pager').inner_text())
        self.assertEqual(self.page.locator('.an-slowfast-table tbody tr').count(), 100)
        self.assertEqual(self.ev('''() => {
            const header = document.querySelector('[data-sf-sort=name] button');
            header.focus(); header.click();
            return [document.querySelector('.an-slowfast-pending')?.textContent, document.activeElement.textContent];
        }'''), ['Ordering comparison…', 'Hide comparison'])
        self.page.locator('[data-sf-sort=name][aria-sort=ascending]').wait_for()
        self.assertEqual(self.ev('document.activeElement.closest("[data-sf-sort]")?.dataset.sfSort'), 'name')
        self.assertIn('1–100 of 150004', self.page.locator('.an-slowfast-pager').inner_text())

    def test_slowfast_large_profile_csv_pages_and_cancels_without_partial_files(self):
        count = 4100
        suffix = '@child,"接收"_'
        self.page.close()
        context = self.browser.new_context(viewport={'width': 320, 'height': 700}, has_touch=True, is_mobile=True)
        self.addCleanup(context.close)
        self.page = context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)
        self.open_range_data(['target'] * 6 + [suffix + str(index).zfill(5) for index in range(count)],
                             starts=[index * 2000 for index in range(6)] + [11000 + index * 1000 for index in range(count)],
                             durations=[1000] * 5 + [(count + 1) * 1000] + [500] * count,
                             parents=[-1] * 6 + [5] * count, columns={'depth': [0] * 6 + [1] * count})
        self.open_distribution()
        toggle = self.page.locator('[data-act=slowfast]')
        toggle.press('Enter')
        selector = '[data-act=slowfast-csv]'
        button, status = self.page.locator(selector), self.page.locator('.an-slowfast-csv-status')
        downloads = []
        self.page.on('download', lambda download: downloads.append(download))
        for action in ('button.click()', "pane.querySelector('[data-sf-sort=name] button').click()",
                       "pane.querySelector('[data-act=slowfast]').click()"):
            with self.subTest(action=action):
                actual = button.evaluate(f'''button => {{
                    const pane = button.closest('.an-slowfast');
                    button.click();
                    const status = pane.querySelector('.an-slowfast-csv-status');
                    const other = pane.querySelector('[data-act=slowfast-cohorts-csv]');
                    const fits = [button, status, other].every(node => {{
                        const rect = node.getBoundingClientRect();
                        return rect.width > 0 && rect.left >= 0 && rect.right <= innerWidth;
                    }});
                    const result = [button.textContent, status.textContent, other.disabled, fits, button.offsetHeight >= 44];
                    {action};
                    return result;
                }}''')
                if 'data-act=slowfast]' in action:
                    toggle.press('Enter')
                self.assertEqual(actual, ['Cancel CSV', 'Preparing CSV…', True, True, True])
                self.ev('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 20)))')
                self.assertEqual(downloads, [])
                self.assertTrue(status.is_hidden())
        with self.page.expect_download() as result:
            button.evaluate('''button => {
                button.click();
                document.querySelector('[data-act=slowfast-next]').click();
            }''')
        self.assertEqual(result.value.suggested_filename, 'funcgraph-slowfast-profile-raw.csv')
        with open(result.value.path(), 'rb') as source:
            raw = source.read()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbfkind,function,'))
        self.assertIn('\'@child,""接收""_00000'.encode(), raw)
        rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline='')))
        self.assertEqual(len(rows), count + 4)
        self.assertEqual([row['function'] for row in rows if row['kind'] == 'function'],
                         ["'" + suffix + str(index).zfill(5) for index in range(count)])
        self.assertIn('101–200 of 4104', self.page.locator('.an-slowfast-pager').inner_text())
        self.assertEqual(self.download_csv_file(selector), ('funcgraph-slowfast-profile-raw.csv', raw))

    def test_slowfast_large_cohorts_csv_pauses_and_retains_exact_members(self):
        count = 6900
        self.open_range_data(['target'] * count, starts=[index * 20000 for index in range(count)],
                             durations=[1000 + index for index in range(count)])
        self.open_distribution()
        self.page.locator('[data-act=slowfast]').press('Enter')
        button = self.page.locator('[data-act=slowfast-cohorts-csv]')
        status = self.page.locator('.an-slowfast-csv-status')
        downloads = []
        self.page.on('download', lambda download: downloads.append(download))
        self.assertEqual(button.evaluate('''button => {
            button.focus(); button.click();
            const result = [button.textContent, document.activeElement === button];
            fgSwitchView('text');
            return result;
        }'''), ['Cancel CSV', True])
        self.ev('() => new Promise(resolve => requestAnimationFrame(() => setTimeout(resolve, 25)))')
        self.assertEqual(status.text_content(), 'Preparing CSV…')
        self.assertEqual(downloads, [])
        with self.page.expect_download() as result:
            self.page.locator('.view-tab[data-view=timeline]').click()
        self.assertEqual(result.value.suggested_filename, 'funcgraph-slowfast-cohorts-raw.csv')
        with open(result.value.path(), encoding='utf-8-sig', newline='') as source:
            rows = list(csv.DictReader(source))
        self.assertEqual([int(row['slice_index']) for row in rows],
                         list(range(count // 2 - 1, -1, -1)) + list(range(count - 1, count - count // 10 - 1, -1)))
        self.assertEqual([row['cohort'] for row in rows], ['fast'] * (count // 2) + ['slow'] * (count // 10))
        self.assertTrue(status.is_hidden())
        self.assertTrue(self.page.locator('[data-act=slowfast-csv]').is_enabled())

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
        table = self.page.locator('.an-slowfast-table')
        status = self.page.locator('.an-slowfast-pager [role=status]')
        previous = self.page.locator('[data-act=slowfast-prev]')
        next_page = self.page.locator('[data-act=slowfast-next]')

        def names():
            return table.locator('.an-slowfast-name').all_text_contents()

        self.assertIn('1–100 of 229', status.inner_text())
        visited = names()
        self.assertTrue(previous.is_disabled())
        next_page.press('Enter')
        self.assertIn('101–200 of 229', status.inner_text())
        self.assertEqual(len(names()), 100)
        self.assertFalse(first.evaluate('(node) => node.isConnected'))
        self.assertTrue(next_page.evaluate('(node) => node === document.activeElement'))
        visited += names()
        next_page.press('Space')
        self.assertIn('201–229 of 229', status.inner_text())
        self.assertEqual(len(names()), 29)
        self.assertTrue(next_page.is_disabled())
        self.assertTrue(previous.evaluate('(node) => node === document.activeElement'))
        visited += names()
        self.assertEqual(visited, [row['function'] for row in rows])
        self.assertEqual(self.download_csv('[data-act=slowfast-csv]'), rows)
        self.page.locator('[data-act=slowfast]').press('Enter')
        self.page.locator('[data-act=slowfast]').press('Enter')
        self.assertEqual(names(), visited[200:])
        previous.press('Space')
        self.assertEqual(names(), visited[100:200])
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
                '<a class="location-link">(inlined by) outer at outer.c:4</a><div class="source-line">outer();</div>';
            panels.forEach(panel => { fgFill(panel); panel.style.display = 'block'; });
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
            const refresh = fgRefreshSourceLayout;
            const visited = [];
            let result;
            document.querySelectorAll = selector => { throw new Error('Unexpected document scan: ' + selector); };
            document.getElementsByClassName = name => { throw new Error('Unexpected document collection: ' + name); };
            fgRefreshSourceLayout = element => {
                if (element.matches('.expanded-content')) visited.push(element.id);
                refresh(element);
            };
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
                fgRefreshSourceLayout = refresh; fgSourceHtml = originalSource;
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
            const originalSource = fgSourceHtml;
            fgSourceHtml = () => '<a class="location-link">inner at inner.c:2</a><div class="source-line">inner();</div>' +
                '<a class="location-link">(inlined by) outer at outer.c:4</a><div class="source-line">outer();</div>';
            const panel = document.querySelector('#content .expanded-content[data-src]');
            fgFill(panel);
            panel.style.display = 'block';
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
            const refresh = fgRefreshSourceLayout;
            const visited = [];
            fgRefreshSourceLayout = element => {
                if (element.matches('.expanded-content')) visited.push(element);
                refresh(element);
            };
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
            } finally { fgRefreshSourceLayout = refresh; fgSourceHtml = originalSource; }
        }''')
        self.assertEqual(facts, dict(withDynamic=2, remaining=1, same=True, connected=True, reattached=True,
                                     mode='chain', textMode='Source: chain'))

    def test_source_layout_switch_preserves_plain_snippet_nodes_and_refreshes_inline_sources(self):
        facts = self.ev('''() => {
            const sourceHtml = fgSourceHtml;
            const panels = Array.from(document.querySelectorAll('.expanded-content[data-src]')).slice(0, 2);
            let reads = 0;
            const plain = '<a class="location-link" href="#L1">inner at inner.c:2</a><div class="source-line">inner();</div>';
            panels[0].dataset.src = '0';
            panels[1].dataset.src = '1';
            fgSourceHtml = source => {
                reads++;
                return source === 0 ? plain : plain + '<a class="location-link">(inlined by) outer at outer.c:4</a>';
            };
            try {
                panels.forEach(panel => { fgFill(panel); panel.style.display = 'block'; });
                const original = panels[0].firstChild;
                const originalInline = panels[1].querySelector('.source-line');
                original.focus({ preventScroll: true });
                const readBefore = reads;
                fgToggleSrcMode();
                const flat = panels[1].querySelectorAll('.src-frame').length;
                const flatStable = panels[1].querySelector('.source-line') === originalInline;
                fgToggleSrcMode();
                return { reads: reads - readBefore, plainStable:panels[0].firstChild === original,
                    focusKept:document.activeElement === original, flat, flatStable,
                    chain:panels[1].querySelectorAll('.src-frame').length,
                    chainStable:panels[1].querySelector('.source-line') === originalInline };
            } finally { fgSourceHtml = sourceHtml; }
        }''')
        self.assertEqual(facts, dict(reads=0, plainStable=True, focusKept=True, flat=0, flatStable=True,
                                     chain=2, chainStable=True))

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

    def test_inline_canvas_tag_and_neutral_style_preserve_recorded_data(self):
        self.ev('''() => {
            window.inlineLabels = [];
            const original = CanvasRenderingContext2D.prototype.fillText;
            CanvasRenderingContext2D.prototype.fillText = function(text, ...parameters) {
                if (this.canvas.matches('.tl-canvas')) inlineLabels.push({text, font:this.font});
                return original.call(this, text, ...parameters);
            };
        }''')
        self.open_range_data(['outer', 'child'], starts=[0, 1000], durations=[10000, 6000], parents=[-1, 0],
                             callers=[None, 'outer+0x1/0x20'],
                             columns={'depth': [0, 1], 'module': [0, 0], 'cpu': [0, 0]},
                             extra={'inline': {'2': [['outer', 'outer.c', 10], ['helper_inline', 'inline.h', 20]]}})
        original = self.ev('JSON.stringify(fgData.read("tlData"))')
        self.ev('''() => {
            const model = fgView('timeline').probe();
            model.inline(true);
            window.inlineIndex = Array.from({length:model.NA - model.N}, (_, offset) => model.N + offset)
                .find(index => !model.isGhost(index) && model.nameOf(index) === 'helper_inline');
            model.zoomTo(0, 10000, 0);
        }''')
        self.assertTrue(self.ev('Number.isInteger(inlineIndex)'))
        self.page.wait_for_function('inlineLabels.some(label => label.text === "inl")')
        labels = self.ev('inlineLabels')
        self.assertTrue(any(label['text'] == 'inl' and '9px' in label['font'] for label in labels))
        self.assertTrue(any(label['text'] == 'helper_inline' and '11px' in label['font'] for label in labels))
        self.assertFalse(any(label['text'].startswith('inline ') for label in labels))
        for theme, mode in product(('light', 'dark'), ('ctx', 'name', 'cpu', 'module', 'total', 'heat')):
            with self.subTest(theme=theme, mode=mode):
                self.ev('theme => document.documentElement.setAttribute("data-theme", theme)', theme)
                helpers.open_timeline_tools(self.page)
                self.page.locator('[data-role=color]').select_option(mode)
                self.page.locator('[data-act=options]').click()
                self.assertTrue(self.ev('fgView("timeline").probe().styleOf(inlineIndex)[0].startsWith("rgba(")'))
                self.assertEqual(self.ev('JSON.stringify(fgData.read("tlData"))'), original)

    def test_inline_hover_click_and_filter_keep_exact_extent_and_identity(self):
        self.open_range_data(['outer', 'child'], starts=[0, 1000], durations=[10000, 6000], parents=[-1, 0],
                             callers=[None, 'outer+0x1/0x20'], columns={'depth': [0, 1]},
                             extra={'inline': {'2': [['outer', 'outer.c', 10], ['helper_inline', 'inline.h', 20]]}})
        self.ev('''() => {
            const model = fgView('timeline').probe();
            model.inline(true);
            window.inlineIndex = Array.from({length:model.NA - model.N}, (_, offset) => model.N + offset)
                .find(index => !model.isGhost(index) && model.nameOf(index) === 'helper_inline');
            model.zoomTo(0, 10000, 0);
        }''')
        self.assertTrue(self.ev('Number.isInteger(inlineIndex)'))
        original = self.ev('''() => {
            const model = fgView('timeline').probe();
            return {start:model.sStart[inlineIndex], end:model.sEnd[inlineIndex],
                parent:model.sParent[inlineIndex], depth:model.sDepth[inlineIndex],
                raw:JSON.stringify(fgData.read('tlData'))};
        }''')
        for theme in ('light', 'dark'):
            with self.subTest(theme=theme):
                self.ev('theme => document.documentElement.setAttribute("data-theme", theme)', theme)
                self.page.mouse.move(0, 0)
                self.page.wait_for_timeout(60)
                fill = self.ev('fgView("timeline").probe().styleOf(inlineIndex)[0]')
                point = self.ev('fgView("timeline").probe().xyOf(inlineIndex)')
                self.page.mouse.move(*point)
                self.page.locator('.tl-tip').wait_for(state='visible')
                self.assertIn('helper_inline', self.page.locator('.tl-tip').inner_text())
                self.assertIn('traced extent', self.page.locator('.tl-tip').inner_text())
                self.assertNotEqual(self.ev('fgView("timeline").probe().styleOf(inlineIndex)[0]'), fill)
                self.page.mouse.click(*point)
                self.assertEqual(self.ev('fgView("timeline").probe().sel()'), self.ev('inlineIndex'))
                self.assertIn('traced extent', self.page.locator('.tl-detail').inner_text())
                self.ev("fgNotifyFilter('child only', new Set([2]))")
                self.assertEqual(self.ev('fgView("timeline").probe().sel()'), self.ev('inlineIndex'))
                self.assertEqual(self.ev('''() => {
                    const model = fgView('timeline').probe();
                    return {start:model.sStart[inlineIndex], end:model.sEnd[inlineIndex],
                        parent:model.sParent[inlineIndex], depth:model.sDepth[inlineIndex],
                        raw:JSON.stringify(fgData.read('tlData'))};
                }'''), original)
                self.ev("fgNotifyFilter('', null)")

    def test_inline_toggle_is_next_to_fit_without_opening_tools(self):
        self.open_range_data(['outer', 'child'], starts=[0, 100], durations=[10000, 1000], parents=[-1, 0],
                             columns={'depth': [0, 1], 'adj': None},
                             extra={'inline': {'1': [['inlined', 'inline.h', 20], ['outer', 'outer.c', 10]]}})
        button = self.page.locator('[data-act=inl]')
        self.assertEqual(button.count(), 1)
        self.assertTrue(button.is_visible())
        self.assertEqual(button.inner_text(), 'Inline: hidden')
        self.assertEqual(button.get_attribute('aria-label'), 'Inline frames')
        self.assertEqual(button.get_attribute('aria-pressed'), 'false')
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
        self.assertEqual(button.get_attribute('aria-label'), 'Inline frames')
        self.assertEqual(button.get_attribute('aria-pressed'), 'true')
        self.assertTrue(button.evaluate("node => node.classList.contains('on')"))
        self.page.locator('.tl-canvas').focus()
        self.page.keyboard.press('i')
        self.assertEqual(button.inner_text(), 'Inline: hidden')
        button.press('Enter')
        self.assertEqual(button.inner_text(), 'Inline: shown')
        self.assertEqual(self.ev("lsGet('tlInline')"), '1')
        self.assertEqual(self.page.locator('#tlData').text_content(), original)
        self.assertEqual(self.page.locator('[data-act=options]').get_attribute('aria-expanded'), 'false')

    def test_inline_toggle_keeps_the_scrolled_track_and_does_not_repaint_the_unchanged_ruler(self):
        task_count = 28
        self.open_range_data(['outer', 'child'] + ['worker'] * (task_count - 1),
                             starts=[0, 1000] + [20000 + index * 1000 for index in range(task_count - 1)],
                             durations=[10000, 6000] + [500] * (task_count - 1),
                             parents=[-1, 0] + [-1] * (task_count - 1),
                             callers=[None, 'outer+0x1/0x20'] + [None] * (task_count - 1),
                             columns={'depth': [0, 1] + [0] * (task_count - 1), 'task': [0, 0] + list(range(1, task_count))},
                             extra={'tasks': [dict(pid=index + 1, comm=f'task_{index}', kind=0, cpu=0)
                                              for index in range(task_count)],
                                    'inline': {'3': [['outer', 'outer.c', 10], ['helper_inline', 'inline.h', 20]]}})
        self.ev('''() => {
            const model = fgView('timeline').probe(), scroll = document.querySelector('.tl-scroll');
            const anchor = model.TRACKS[12], head = document.querySelector('.tl-head-canvas');
            const context = head.getContext('2d'), drawImage = context.drawImage;
            window.rulerPaints = 0;
            context.drawImage = function(...args) { rulerPaints++; return drawImage.apply(this, args); };
            window.inlineFramesSettled = () => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)));
            window.inlineScrollState = () => ({anchorY:anchor.y, offset:scroll.scrollTop - anchor.y,
                selection:model.sel(), view:model.view(), paints:rulerPaints});
            window.setInlineScroll = async selected => {
                model.select(selected);
                model.zoomTo(0, 2000, 0);
                scroll.scrollTop = anchor.y + 7;
                await inlineFramesSettled();
            };
        }''')
        button = self.page.get_by_role('button', name='Inline frames', exact=True)
        for selected in (-1, 0):
            self.ev('selected => setInlineScroll(selected)', selected)
            before = self.ev('inlineScrollState()')
            self.assertEqual(before['offset'], 7)
            for shown in (True, False):
                with self.subTest(selected=selected, shown=shown):
                    button.press('Enter')
                    self.ev('inlineFramesSettled()')
                    after = self.ev('inlineScrollState()')
                    self.assertEqual(button.get_attribute('aria-pressed'), str(shown).lower())
                    if shown:
                        self.assertGreater(after['anchorY'], before['anchorY'])
                    else:
                        self.assertEqual(after['anchorY'], before['anchorY'])
                    self.assertEqual({key: value for key, value in after.items() if key != 'anchorY'},
                                     {key: value for key, value in before.items() if key != 'anchorY'})

    def test_inline_toggle_does_not_open_collapsed_tracks_and_clears_hidden_inline_selection(self):
        self.open_range_data(['outer', 'child'], starts=[0, 1000], durations=[10000, 6000], parents=[-1, 0],
                             callers=[None, 'outer+0x1/0x20'], columns={'depth': [0, 1]},
                             extra={'inline': {'2': [['outer', 'outer.c', 10], ['helper_inline', 'inline.h', 20]]}})
        button = self.page.get_by_role('button', name='Inline frames', exact=True)
        self.ev('fgView("timeline").probe().select(1)')
        helpers.click_timeline_tool(self.page, '[data-act=expand]')
        button.press('Space')
        self.assertEqual(self.ev('''() => {const model = fgView('timeline').probe();
            return [model.TRACKS[0].collapsed, model.sel()];}'''), [True, 1])
        before = self.ev('''async () => {
            const model = fgView('timeline').probe();
            const index = model.SYN.findIndex(frame => !frame.ghost) + model.N;
            model.select(index);
            await new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)));
            const context = document.querySelector('.tl-head-canvas').getContext('2d'), drawImage = context.drawImage;
            window.inlineRulerRepainted = false;
            context.drawImage = function(...args) { inlineRulerRepainted = true; return drawImage.apply(this, args); };
            return {selection:model.sel(), range:model.view()};
        }''')
        self.assertEqual(before['selection'], 2)
        button.press('Enter')
        self.page.wait_for_function('inlineRulerRepainted')
        self.assertEqual(self.ev('''() => {const model = fgView('timeline').probe();
            return {selection:model.sel(), range:model.view()};}'''), {'selection': -1, 'range': before['range']})
        self.assertFalse(self.page.locator('.tl-detail').is_visible())

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
        sizes = ((390, 844), (320, 700), (844, 390), (280, 240))
        for theme, size, fullscreen in product(('light', 'dark'), sizes, (False, True)):
            with self.subTest(theme=theme, size=size, fullscreen=fullscreen):
                self.page.set_viewport_size(dict(zip(('width', 'height'), size)))
                self.page.wait_for_function('document.getElementById("timelineView").classList.contains("tl-compact")')
                self.ev('theme => document.documentElement.setAttribute("data-theme", theme)', theme)
                if self.ev('document.getElementById("timelineView").classList.contains("tl-fullscreen")') != fullscreen:
                    self.page.locator('[data-act=max]').click()
                self.page.wait_for_function('''fullscreen =>
                    document.getElementById('timelineView').classList.contains('tl-fullscreen') === fullscreen &&
                    !document.querySelector('[data-act=max]').disabled''', arg=fullscreen)
                selectors = ['[data-act=inl]', '[data-act=options]']
                if size == (280, 240):
                    self.page.locator('.tl-zoom-tools [data-act=fit]').wait_for(state='attached')
                    self.assertFalse(self.page.locator('[data-act=fit]').is_visible())
                else:
                    selectors.append('.tl-quick [data-act=fit]')
                for selector in selectors:
                    self.page.locator(selector).wait_for()
                    bounds = self.page.locator(selector).bounding_box()
                    self.assertIsNotNone(bounds)
                    self.assertGreaterEqual(bounds['height'], 44)
                    self.assertGreaterEqual(bounds['x'], 0)
                    self.assertLessEqual(bounds['x'] + bounds['width'], size[0])
                button = self.page.locator('[data-act=inl]')
                mark = button.locator('.tl-inline-mark')
                geometry = '''button => [button.offsetLeft, button.offsetTop, button.offsetWidth,
                    button.offsetHeight, button.closest('.tl-toolbar').offsetHeight]'''
                start = button.evaluate(geometry)
                self.assertEqual(button.get_attribute('aria-pressed'), 'false')
                self.assertFalse(mark.is_visible())
                for pressed in (True, False):
                    button.tap()
                    self.assertEqual(button.inner_text(), 'Inline')
                    self.assertEqual(button.get_attribute('aria-label'), 'Inline frames')
                    self.assertEqual(button.get_attribute('aria-pressed'), str(pressed).lower())
                    self.assertEqual(mark.is_visible(), pressed)
                    self.assertEqual(button.evaluate(geometry), start)
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

    def test_duration_sort_preserves_ties_visibility_focus_and_skips_identical_projection(self):
        result = self.ev('''() => {
            const root = document.getElementById('content');
            fgSetTextOrder('task');
            fgSetTextEventsVisible(false);
            const rows = Array.from(root.querySelectorAll('.line-container'));
            const hiddenRows = rows.filter(row => row.style.display === 'none');
            if (hiddenRows.length < 2) throw new Error('Expected at least two hidden event rows');
            hiddenRows[0].dataset.sortDuration = '1';
            hiddenRows[1].dataset.sortDuration = '2';
            const expected = rows.map(row => ({row, hidden:row.style.display === 'none',
                duration:parseFloat(row.dataset.sortDuration || row.dataset.duration || '0')}))
                .sort((left, right) => Number(left.hidden) - Number(right.hidden) ||
                    (left.hidden ? 0 : right.duration - left.duration));
            const row = rows.find(row => row.matches('.expandable') && row.style.display !== 'none');
            const source = row.nextElementSibling;
            row.focus({preventScroll:true});
            const input = document.getElementById('filterInput');
            input.value = 'sort:desc';
            applyFilter();
            const projected = Array.from(root.querySelectorAll('.line-container'));
            const matchesReference = projected.length === expected.length && fgTextNavigation.order.length === expected.length &&
                projected.every((line, index) => line === expected[index].row &&
                    fgTextNavigation.order[index] === +line.dataset.lineNumber &&
                    (line.style.display === 'none') === expected[index].hidden);
            const chunks = Array.from(root.children);
            applyFilter();
            const unchanged = root.children.length === chunks.length && chunks.every((chunk, index) => root.children[index] === chunk);
            const sorted = root.isConnected && document.activeElement === row && row.nextElementSibling === source &&
                fgTextOrder === 'task' && fgTextSort === 'desc';
            clearFilter();
            return {matchesReference, unchanged, sorted, restored:root.classList.contains('grouped') &&
                document.activeElement === row && row.nextElementSibling === source && fgTextSort === ''};
        }''')
        self.assertEqual(result, dict(matchesReference=True, unchanged=True, sorted=True, restored=True))

    def test_leaving_duration_sort_refreshes_layout_and_navigation_once(self):
        result = self.ev('''() => {
            const root = document.getElementById('content');
            const input = document.getElementById('filterInput');
            fgSetTextOrder('task');
            const expected = fgTextNavigation.order.slice();
            const originalRefresh = fgRefreshTextLayout;
            let layouts = 0, guides = 0;
            fgRefreshTextLayout = (...args) => { layouts++; return originalRefresh(...args); };
            root.addEventListener('fg-guides-change', () => guides++);
            const actions = [];
            try {
                for (const query of ['', 'duration:>1', 'duration:>999999 sort:desc']) {
                    input.value = 'sort:desc';
                    applyFilter();
                    layouts = 0; guides = 0;
                    input.value = query;
                    if (query) applyFilter(); else clearFilter();
                    const rows = Array.from(root.querySelectorAll('.line-container'));
                    const navigation = fgTextNavigation;
                    const matchesOrder = rows.length === expected.length && navigation.order.length === expected.length &&
                        rows.every((row, index) => +row.dataset.lineNumber === expected[index] && navigation.order[index] === expected[index]);
                    const expandable = rows.filter(row => row.classList.contains('expandable') && row.style.display !== 'none');
                    const matchesVisibility = navigation.expandable.length === expandable.length && navigation.expandable.every(
                        (position, index) => navigation.order[position] === +expandable[index].dataset.lineNumber);
                    actions.push({layouts, guides, grouped:root.classList.contains('grouped'), matchesOrder,
                        matchesVisibility, sort:fgTextSort, query:fgFilterState.text});
                }
            } finally { fgRefreshTextLayout = originalRefresh; }
            return actions;
        }''')
        self.assertEqual(result, [
            dict(layouts=1, guides=1, grouped=True, matchesOrder=True, matchesVisibility=True, sort='', query=query)
            for query in ('', 'duration:>1', 'duration:>999999 sort:desc')
        ])

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

    def test_dense_row_paint_reuses_runs_and_refreshes_filter_membership(self):
        self.open_dense_pixels()
        result = self.ev('''async () => {
            const timeline = fgView('timeline'), track = timeline.probe().TRACKS[0];
            let reads = 0;
            track.rows[1] = new Proxy(track.rows[1], {
                get(target, key) {
                    if (typeof key === 'string' && /^[0-9]+$/.test(key)) reads++;
                    return Reflect.get(target, key, target);
                }
            });
            const paint = async action => {
                reads = 0;
                action();
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                return reads;
            };
            return [await paint(timeline.draw), await paint(timeline.draw),
                await paint(() => fgNotifyFilter('one dense member', new Set([6]))), await paint(timeline.draw)];
        }''')
        self.assertGreater(result[0], 0)
        self.assertEqual(result[1], 0)
        self.assertGreater(result[2], 0)
        self.assertEqual(result[3], 0)
        self.page.mouse.click(*self.canvas_pixel(4)['point'])
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 5)

    def test_dense_paint_reuses_opacity_between_runs_and_restores_it_after_each_row(self):
        self.open_range_data(['root'] + ['dense_child'] * 6,
                             starts=[0, 100000, 100001, 100002, 108000, 108001, 108002],
                             durations=[1000000] + [1] * 6, parents=[-1] + [0] * 6)
        paints = self.ev('''async () => {
            const timeline = fgView('timeline'), context = document.querySelector('.tl-canvas').getContext('2d');
            const descriptor = Object.getOwnPropertyDescriptor(CanvasRenderingContext2D.prototype, 'globalAlpha');
            let writes = [];
            Object.defineProperty(context, 'globalAlpha', { configurable: true,
                get() { return descriptor.get.call(this); },
                set(opacity) { writes.push(opacity); descriptor.set.call(this, opacity); }
            });
            const paint = async action => {
                writes = [];
                action();
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                return {writes, end: context.globalAlpha};
            };
            try {
                return [await paint(timeline.draw), await paint(timeline.draw),
                    await paint(() => fgNotifyFilter('one dense member', new Set([6]))), await paint(timeline.draw)];
            } finally { delete context.globalAlpha; }
        }''')
        for ordinal, paint in enumerate(paints):
            with self.subTest(paint=ordinal):
                self.assertEqual(paint['writes'].count(0.85), 1, paint)
                self.assertEqual(paint['writes'].count(0.2), int(ordinal >= 2), paint)
                self.assertEqual(paint['end'], 1, paint)

    def test_dense_pixel_hover_and_click_explain_and_select_matching_representative(self):
        label = '<img src=x onerror=alert(1)>'
        self.open_dense_pixels(label=label)
        point = self.canvas_pixel(1)['point']
        self.ev("fgNotifyFilter('first two members', new Set([2, 3]))")
        dense_warning = self.page.locator('.tl-tip .t-warn').filter(has_text='Dense pixel')
        self.page.mouse.move(*point)
        dense_warning.filter(has_text='2 match').wait_for()
        self.ev("fgNotifyFilter('middle member', new Set([3]))")
        self.page.mouse.move(*point)
        dense_warning.filter(has_text='1 match').wait_for()
        self.assertIn('3 frames', self.page.locator('.tl-tip').inner_text())
        self.assertIn('1 match', self.page.locator('.tl-tip').inner_text())
        self.assertIn(label, self.page.locator('.tl-tip .t-name').inner_text())
        self.assertEqual(self.page.locator('.tl-tip img').count(), 0)
        self.page.mouse.click(*point)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 2)
        note = self.page.locator('.tl-dense-selection')
        self.assertIn('3 frames', note.inner_text())
        self.assertIn('1 matched', note.inner_text())
        button = note.get_by_role('button', name='Zoom into pixel')
        self.ev("() => window.clickedDenseHead = document.getElementById('tlDetail').firstElementChild")
        view = self.ev("fgView('timeline').probe().view()")
        self.page.mouse.click(*point)
        self.assertEqual(note.count(), 1)
        self.assertTrue(self.ev('clickedDenseHead.isConnected'))
        button.press('Enter')
        facts = self.ev('''() => {
            const model = fgView('timeline').probe(), focused = document.activeElement;
            return {view: model.view(), selection: model.sel(), focused: focused.matches('[data-dense-start]'),
                sameHead: clickedDenseHead.isConnected, time: model.sStart[model.sel()]};
        }''')
        self.assertLess(facts['view'][1] - facts['view'][0], view[1] - view[0])
        self.assertLessEqual(facts['view'][0], facts['time'])
        self.assertGreater(facts['view'][1], facts['time'])
        self.assertEqual(facts['selection'], 2)
        self.assertTrue(facts['focused'] and facts['sameHead'])
        button.press('Enter')
        self.assertEqual(self.ev("fgView('timeline').probe().view()"), facts['view'])
        self.page.locator('.tl-toast').filter(has_text='Cannot zoom further').wait_for()

        root = self.ev("fgView('timeline').probe().xyOf(0)")
        self.page.mouse.click(*root)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), 0)
        self.assertEqual(note.count(), 0)

    def test_dense_pixel_touch_explains_the_group_without_hover(self):
        self.page.close()
        self.page = self.browser.new_page(viewport={'width': 844, 'height': 390}, has_touch=True, is_mobile=True)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)
        self.ev('localStorage.clear()')
        self.open_dense_pixels()
        self.ev('''() => {
            window.touchTapStages = [];
            const canvas = document.querySelector('.tl-canvas');
            for (const type of ['pointerup', 'click']) canvas.addEventListener(type, () => {
                touchTapStages.push([type, fgView('timeline').probe().sel()]);
            });
        }''')
        point = self.canvas_pixel(1)['point']
        self.page.touchscreen.tap(*point)
        self.assertEqual(self.ev('touchTapStages'), [['pointerup', -1], ['click', 1]])
        note = self.page.locator('.tl-dense-selection')
        self.assertIn('frames shared this pixel', note.inner_text())
        button = note.get_by_role('button', name='Zoom into pixel')
        self.assertGreaterEqual(button.bounding_box()['height'], 44)
        before = self.ev("fgView('timeline').probe().view()")
        selection = self.ev("fgView('timeline').probe().sel()")
        button.tap()
        self.assertNotEqual(self.ev("fgView('timeline').probe().view()"), before)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), selection)

    def test_compact_dense_zoom_is_reachable_from_actions_without_scrolling_details(self):
        for width, height, keyboard in ((280, 390, False), (320, 480, True), (568, 320, False)):
            with self.subTest(width=width, height=height, keyboard=keyboard):
                self.page.close()
                self.page = self.browser.new_page(viewport={'width': width, 'height': height}, has_touch=True, is_mobile=True)
                self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                self.page.goto(self.url)
                self.ev('localStorage.clear()')
                self.open_dense_pixels()
                self.ev("fgNotifyFilter('middle member', new Set([3]))")
                settle = '() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))'
                self.ev(settle)
                point = self.canvas_pixel(1)['point']
                self.page.touchscreen.tap(*point)
                self.ev(settle)
                note = self.page.locator('.tl-dense-selection')
                self.assertIn('1 matched', note.inner_text())
                geometry = '''element => {
                    const rect = element.getBoundingClientRect(), x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
                    return {point:[x,y], height:rect.height, hit:element.contains(document.elementFromPoint(x,y))};
                }'''
                self.assertFalse(note.get_by_role('button', name='Zoom into pixel').evaluate(geometry)['hit'])
                trigger = self.page.get_by_role('button', name='Selected call actions')
                opener = trigger.evaluate(geometry)
                self.assertTrue(opener['hit'], opener)
                if keyboard:
                    trigger.press('ArrowDown')
                else:
                    self.page.touchscreen.tap(*opener['point'])
                option = self.page.get_by_role('menuitem', name='Zoom into pixel')
                button = option.evaluate(geometry)
                self.assertTrue(button['hit'], button)
                self.assertGreaterEqual(button['height'], 44)
                snapshot = '''() => {
                    const model = fgView('timeline').probe();
                    return {view:model.view(), selection:model.sel(), filter:fgFilterState.text, page:[scrollX,scrollY],
                        details:document.querySelector('.tl-detail').scrollTop};
                }'''
                before = self.ev(snapshot)
                self.assertEqual(before['selection'], 2)
                if keyboard:
                    for _ in range(2):
                        self.page.keyboard.press('ArrowDown')
                    self.assertTrue(option.evaluate('element => document.activeElement === element'))
                    self.page.keyboard.press('Enter')
                else:
                    self.page.touchscreen.tap(*button['point'])
                self.ev(settle)
                after = self.ev(snapshot)
                after_start, after_end = after.pop('view')
                before_start, before_end = before.pop('view')
                self.assertLess(after_end - after_start, before_end - before_start)
                self.assertEqual(after, before)
                self.assertFalse(option.is_visible())
                self.assertTrue(trigger.evaluate('element => document.activeElement === element'))
                if keyboard:
                    trigger.press('ArrowDown')
                    option.press('Enter')
                    self.page.locator('.tl-toast').filter(has_text='Cannot zoom further').wait_for(timeout=2500)
                    self.assertEqual(self.ev(snapshot)['view'], [after_start, after_end])
                    self.assertTrue(trigger.evaluate('element => document.activeElement === element'))
                    self.ev("fgView('timeline').probe().select(0)")
                    trigger.press('ArrowDown')
                    self.assertEqual(option.count(), 0)

    def test_thin_recorded_hover_updates_preview_without_repainting_tracks(self):
        self.open_range_data(['root', 'thin_a', 'thin_b', 'wide'], starts=[0, 100000, 400000, 600000],
                             durations=[1000000, 1, 1, 200000], parents=[-1, 0, 0, 0], columns={'depth': [0, 1, 1, 1]})
        result = self.ev('''async () => {
            const model = fgView('timeline').probe(), canvas = document.querySelector('.tl-canvas');
            const context = canvas.getContext('2d'), original = context.fillRect;
            const settle = () => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)));
            await settle();
            const pixels = canvas.toDataURL();
            let paints = 0;
            context.fillRect = function(x, y, width, height) {
                if (x === 0 && y === 0 && width === canvas.width / devicePixelRatio &&
                    height === canvas.height / devicePixelRatio) paints++;
                return original.call(this, x, y, width, height);
            };
            const move = async index => {
                const [clientX, clientY] = model.xyOf(index);
                canvas.dispatchEvent(new MouseEvent('mousemove', {bubbles: true, clientX, clientY}));
                await settle();
                return {name: document.querySelector('.tl-tip .t-name')?.textContent, paints,
                    unchanged: canvas.toDataURL() === pixels};
            };
            try {
                const first = await move(1), second = await move(2), wide = await move(3), back = await move(1);
                canvas.dispatchEvent(new MouseEvent('mouseleave'));
                await settle();
                return {first, second, wide, back, leavePaints: paints, selected: model.sel()};
            } finally { delete context.fillRect; }
        }''')
        self.assertEqual(result['first'], {'name': 'thin_a', 'paints': 0, 'unchanged': True})
        self.assertEqual(result['second'], {'name': 'thin_b', 'paints': 0, 'unchanged': True})
        self.assertEqual(result['wide']['name'], 'wide')
        self.assertFalse(result['wide']['unchanged'])
        self.assertGreater(result['wide']['paints'], 0)
        self.assertTrue(result['back']['unchanged'])
        self.assertGreater(result['back']['paints'], result['wide']['paints'])
        self.assertEqual(result['leavePaints'], result['back']['paints'])
        self.assertEqual(result['selected'], -1)

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

    def test_narrow_dense_filter_reads_only_nearby_row_blocks_and_keeps_exact_hits(self):
        first, size, count = 16000, 2000, 60000
        timestamp = first * 1000
        starts = [index * 1000 for index in range(count)]
        starts[first:first + size] = [timestamp] * size
        self.open_range_data(['child'] * count, starts=starts, durations=[0] * count)
        self.ev('''async timestamp => {
            const probe = fgView('timeline').probe(), row = probe.TRACKS[0].rows[0];
            probe.zoomTo(timestamp - 100, timestamp + 100);
            window.denseBlockFrame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            await denseBlockFrame();
            window.denseBlockReads = 0;
            probe.TRACKS[0].rows[0] = new Proxy(row, {
                get(target, key) {
                    if (typeof key === 'string' && /^[0-9]+$/.test(key)) denseBlockReads++;
                    return Reflect.get(target, key, target);
                }
            });
        }''', timestamp)
        target = first + 1400
        self.ev('''async targets => {
            fgNotifyFilter('late matches', new Set(targets.map(index => index + 1)));
            await denseBlockFrame();
        }''', [target, first + size - 1])
        self.assertLess(self.ev('denseBlockReads'), 4000)
        point = self.canvas_pixel(first)['point']
        self.page.mouse.move(*point)
        self.assertIn('2000 frames', self.page.locator('.tl-tip').inner_text())
        self.assertIn('2 match', self.page.locator('.tl-tip').inner_text())
        self.ev('''async () => {
            denseBlockReads = 0;
            fgView('timeline').draw();
            await denseBlockFrame();
        }''')
        self.assertLess(self.ev('denseBlockReads'), 300)
        self.page.mouse.click(*point)
        self.assertEqual(self.ev("fgView('timeline').probe().sel()"), target)

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

    def test_wakeup_status_stays_off_track_headers_and_stable_redraws_skip_the_ruler(self):
        self.page.set_viewport_size({'width': 568, 'height': 450})
        self.open_range_data(['sender', 'receiver'], starts=[0, 0], durations=[100000, 100000],
                             columns={'task': [0, 1]}, extra=dict(wakeups=[[0, 20000, 1, 0, -1, -1], [0, 20100, 1, 0, -1, -1]],
                                 tasks=[dict(pid=1, comm='sender', kind=0, cpu=0), dict(pid=2, comm='receiver', kind=0, cpu=0)]))
        facts = self.ev('''async () => {
            const api = fgView('timeline'), scroller = document.querySelector('.tl-scroll');
            const tracks = scroller.querySelector('canvas').getContext('2d');
            const ruler = document.querySelector('.tl-head-canvas').getContext('2d');
            const settle = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const capture = source => {
                const text = source.fillText;
                source.fillText = function(label, ...args) {
                    if (label.includes('wakeups bunched')) samples.push([this === ruler, label]);
                    return text.call(this, label, ...args);
                };
            };
            const samples = [];
            capture(tracks);
            capture(ruler);
            api.draw();
            await settle();
            const drawn = samples.splice(0);
            let rulerDraws = 0;
            const draw = ruler.drawImage;
            ruler.drawImage = function (...args) { rulerDraws++; return draw.apply(this, args); };
            for (let frame = 0; frame < 3; frame++) {
                scroller.dispatchEvent(new Event('scroll'));
                await settle();
            }
            const stable = rulerDraws;
            document.querySelector('[data-ov=wk]').click();
            await settle();
            return {drawn, stable, afterToggle:rulerDraws, later:samples};
        }''')
        self.assertEqual(len(facts['drawn']), 1, facts)
        self.assertTrue(facts['drawn'][0][0], facts)
        self.assertTrue(facts['drawn'][0][1].startswith('2 of 2 wakeups bunched'), facts)
        self.assertEqual((facts['stable'], facts['afterToggle'], facts['later']), (0, 1, []), facts)

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
                const ruler = document.querySelector('.tl-head-canvas').getContext('2d');
                const curve = ctx.bezierCurveTo, text = ruler.fillText;
                ctx.bezierCurveTo = function (...args) { curves.push(args[4]); return curve.apply(this, args); };
                ruler.fillText = function (label, x, y, ...args) {
                    if (y === 48) {
                        const width = this.measureText(label).width;
                        labels.push([label, this.textAlign === 'right' ? x - width : x, width]);
                    }
                    return text.call(this, label, x, y, ...args);
                };
                try {
                    model.zoomTo(start, end, -1);
                    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    const [left, right] = model.view(), width = canvas.clientWidth;
                    return {ends: curves.map(px => Math.round(left + px * (right - left) / width)), labels, width};
                } finally { ctx.bezierCurveTo = curve; ruler.fillText = text; }
            };
        }''')
        result = self.ev('measureWakeups(10000, 20000)')
        self.assertEqual(result['ends'], [25000, 9200, 20800, 40000, 12000])
        self.assertFalse(any('wakeups bunched' in label[0] for label in result['labels']))
        self.page.set_viewport_size({'width': 350, 'height': 680})
        result = self.ev('measureWakeups(31000, 31150)')
        self.assertEqual(result['ends'], [])
        labels = sorted(result['labels'], key=lambda label: label[1])
        self.assertEqual(len(labels), 2, result)
        self.assertTrue(labels[0][0].startswith('17 of 17 wakeups bunched'), labels)
        self.assertTrue(labels[1][0].startswith('span '), labels)
        self.assertGreaterEqual(labels[0][1], 0)
        self.assertLess(labels[0][1] + labels[0][2], labels[1][1])
        self.assertLessEqual(labels[1][1] + labels[1][2], result['width'])
        self.ev("fgView('timeline').probe().select(0)")
        result = self.ev('measureWakeups(31000, 31150)')
        self.assertEqual(result['ends'], [40000] + [31000 + index * 10 for index in range(16)])

    def test_wakeup_overlay_skips_vertical_blocks_but_keeps_crossing_arrows(self):
        tracks = 60
        names = [f'task_{index}' for index in range(tracks)]
        upper = [[0, 5000 + index * 100, 1, 0, -1, -1] for index in range(128)]
        lower = [[55, 25000 + index * 100, 56, 0, -1, -1] for index in range(128)]
        crossing = [[0, 70000, 58, 0, 0, -1], [58, 78000, 0, 0, 58, -1], [24, 54000, 25, 0, 24, -1]]
        self.open_range_data(names, starts=[0] * tracks, durations=[100000 - index for index in range(tracks)],
                             columns={'task': list(range(tracks))}, extra=dict(wakeups=upper + lower + crossing,
                                 tasks=[dict(pid=index + 1, comm=name, kind=0, cpu=0) for index, name in enumerate(names)]))
        results = self.ev('''async () => {
            const api = fgView('timeline'), model = api.probe(), scroller = document.querySelector('.tl-scroll');
            const canvas = document.querySelector('.tl-canvas'), ctx = canvas.getContext('2d');
            const settle = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const wakes = fgData.read('tlData').wakeups, results = [];
            let reads = 0;
            for (const wake of wakes) Object.defineProperty(wake, 5, {get() { reads++; return -1; }});
            model.zoomTo(0, 100000, -1);
            const ruler = document.querySelector('.tl-head-canvas').getContext('2d');
            const curve = ctx.bezierCurveTo, fillText = ruler.fillText;
            try {
                for (const track of [20, 0, 59, 20]) {
                    reads = 0;
                    scroller.scrollTop = model.TRACKS[track].y;
                    await settle();
                    const coldReads = reads;
                    reads = 0;
                    const curves = [], labels = [];
                    ctx.bezierCurveTo = function (...args) {
                        const [start, end] = model.view();
                        curves.push([Math.round(start + args[4] * (end - start) / canvas.clientWidth), args[1], args[5]]);
                        return curve.apply(this, args);
                    };
                    ruler.fillText = function (label, ...args) {
                        if (label.includes('wakeups bunched')) labels.push(label);
                        return fillText.call(this, label, ...args);
                    };
                    api.draw();
                    await settle();
                    results.push({coldReads, reads, curves, labels, height: canvas.clientHeight});
                    ctx.bezierCurveTo = curve;
                    ruler.fillText = fillText;
                }
            } finally { ctx.bezierCurveTo = curve; ruler.fillText = fillText; }
            return results;
        }''')
        middle, top, bottom, revisit = results
        self.assertEqual([curve[0] for curve in middle['curves']], [70000, 78000, 54000])
        self.assertLess(middle['curves'][0][1], 0)
        self.assertGreater(middle['curves'][0][2], middle['height'])
        self.assertGreater(middle['curves'][1][1], middle['height'])
        self.assertLess(middle['curves'][1][2], 0)
        self.assertEqual(len(top['labels']), 1)
        self.assertTrue(top['labels'][0].startswith('130 of 130 wakeups bunched'))
        self.assertEqual(bottom['labels'], top['labels'])
        self.assertEqual(revisit, middle)
        self.assertEqual([result['coldReads'] for result in results], [3, 131, 131, 3])
        self.assertEqual([result['reads'] for result in results], [0, 0, 0, 0])

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
            const ruler = document.querySelector('.tl-head-canvas').getContext('2d');
            const curve = ctx.bezierCurveTo, text = ruler.fillText;
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
            ruler.fillText = function (label, ...args) {
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
