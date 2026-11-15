"""Text copying reports actual outcomes without disturbing the reading state."""
from pathlib import Path
import threading
import unittest
from urllib.parse import urljoin

import helpers
from fglib.viewer import create_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


BUTTON = 'button[onclick="copyVisibleContent(event)"]'
EXPECTED = '''() => {
    const rows = fgTextWindow ? fgTextWindow.visibleNumbers.map(number =>
        fgTextModel.locate(fgTextWindow.model, number).record) :
        [...document.querySelectorAll('#content .line-container')].filter(row =>
            row.style.display !== 'none' && !row.classList.contains('hidden'));
    return rows.map(row => row.querySelector('.line-content').textContent).join('\\n');
}'''


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class TextCopyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel(inline_source=True)
        cls.addClassCleanup(cls.kernel.close)
        options = ['--no-config', '--enable-fold', '--filter', '--func-links']
        cls.kernel.run(helpers.fixture('pi4_sched'), *options, out='plain.html')
        cls.kernel.run(helpers.fixture('pi4_sched'), *options, '--timeline', out='index.html')
        cls.kernel.run(str(Path(helpers.ROOT, 'samples/sample_mixed.jsonl')), *options,
                       '--timeline', '--user-src', str(Path(helpers.ROOT, 'samples')), out='mixed.html')
        cls.kernel.run(helpers.fixture('pi4_sched'), *options, '--timeline',
                       '--report-mode', 'sidecar', out='sidecar')
        large = Path(cls.kernel.dir, 'large.txt')
        large.write_text('# tracer: function_graph\n'
                         ' 0)               |  root() { /* <-main+0x4/0x80 */\n' +
                         ''.join(f' 0)   1.000 us    |    leaf(arg={number}); /* <-root+0x4/0x80 */\n' +
                                 (' 0)               |    /* sched_wakeup: comm=worker pid=42 */\n'
                                  if number % 100 == 0 else '') for number in range(4500)) +
                         ' 0) 5000.000 us    |  } /* root ret=0 */\n')
        cls.kernel.run(str(large), *options, '--timeline', '--report-mode', 'sidecar', out='windowed')
        cls.server = create_server(Path(cls.kernel.dir))
        cls.addClassCleanup(cls.server.server_close)
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      kwargs={'poll_interval': 0.02}, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.thread.join, 5)
        cls.addClassCleanup(cls.server.shutdown)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)

    def open_report(self, filename='index.html', theme='light', mobile=False):
        context = self.browser.new_context(viewport=dict(width=360 if mobile else 1500, height=900),
                                           is_mobile=mobile, has_touch=mobile)
        self.addCleanup(context.close)
        context.add_init_script('localStorage.clear()')
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(self.assertEqual, errors, [])
        page.goto(urljoin(self.server.url, filename))
        page.evaluate('''async theme => {
            document.documentElement.dataset.theme = theme;
            if (typeof fgSwitchView === 'function') await fgSwitchView('text');
        }''', theme)
        return page

    def install_clipboard(self, page):
        page.evaluate('''() => {
            window.copiedText = [];
            Object.defineProperty(navigator, 'clipboard', {configurable:true,
                value:{writeText:async text => { copiedText.push(text); }}});
        }''')

    def copy(self, page):
        return page.locator(BUTTON).evaluate('button => copyVisibleContent({target:button})')

    def test_copied_rows_follow_event_visibility_filtering_and_folding_in_all_text_modes(self):
        for filename in ('plain.html', 'index.html', 'mixed.html', 'sidecar/index.html', 'windowed/index.html?text=windowed'):
            with self.subTest(filename=filename):
                page = self.open_report(filename)
                self.install_clipboard(page)
                self.assertEqual(page.evaluate('!!fgTextWindow'), 'windowed' in filename)
                states = []
                for action in ('shown', 'hidden', 'folded', 'filtered'):
                    if action == 'hidden':
                        page.evaluate('async () => { await fgSetTextEventsVisible(false); }')
                    elif action == 'folded':
                        page.locator('#content [data-fold-type=entry]').first.evaluate(
                            'row => _setFolded(row.dataset.foldId, true)')
                        page.evaluate('async () => { if (fgTextWindow) await fgTextWindow.rebuild(); }')
                    elif action == 'filtered':
                        page.evaluate('''() => {
                            unfoldAllBlocks();
                            const cpu = [...document.querySelectorAll('#content [data-func][data-cpu]')]
                                .map(row => row.dataset.cpu).find(value => /^\\d+$/.test(value));
                            document.getElementById('filterInput').value = 'cpu:' + cpu;
                            applyFilter();
                        }''')
                        page.wait_for_function('() => !fgFilterProgress')
                    expected = page.evaluate(EXPECTED)
                    mounted = page.locator('#content .line-container').count()
                    self.assertTrue(self.copy(page), (filename, action, expected))
                    self.assertEqual(page.evaluate('copiedText.at(-1)'), expected)
                    self.assertTrue(expected)
                    self.assertEqual(page.locator('#content .line-container').count(), mounted)
                    states.append(expected)
                self.assertNotEqual(states[0], states[1])
                self.assertNotEqual(states[1], states[2])

    def test_native_clipboard_and_native_legacy_fallback_copy_actual_text(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                page = self.open_report()
                page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
                expected = page.evaluate(EXPECTED)
                page.evaluate('''legacy => {
                    window.nativeClipboard = navigator.clipboard;
                    if (legacy) Object.defineProperty(navigator, 'clipboard', {value:undefined});
                }''', legacy)
                page.locator(BUTTON).click()
                page.wait_for_function('''() => document.querySelector('button[onclick="copyVisibleContent(event)"]')
                    .textContent.startsWith('Copied ')''')
                self.assertEqual(page.evaluate('nativeClipboard.readText()'), expected)

    def test_false_or_throwing_legacy_copy_is_not_reported_as_success(self):
        for outcome in ('false', 'throw'):
            for theme in ('light', 'dark'):
                with self.subTest(outcome=outcome, theme=theme):
                    page = self.open_report(theme=theme, mobile=True)
                    page.evaluate('''outcome => {
                        Object.defineProperty(navigator, 'clipboard', {value:undefined});
                        document.execCommand = () => {
                            if (outcome === 'throw') throw new Error('blocked');
                            return false;
                        };
                    }''', outcome)
                    self.assertFalse(self.copy(page))
                    self.assertEqual(page.locator(BUTTON).inner_text(), 'Copy failed')
                    self.assertEqual(page.locator('body > textarea').count(), 0)

    def test_modern_rejection_and_missing_method_fall_back_once(self):
        for outcome in ('rejected', 'throwing', 'missing'):
            with self.subTest(outcome=outcome):
                page = self.open_report()
                expected = page.evaluate(EXPECTED)
                page.evaluate('''outcome => {
                    window.fallbackValues = [];
                    const clipboard = outcome === 'missing' ? {} : {writeText: text => {
                        if (outcome === 'throwing') throw new Error('blocked');
                        return Promise.reject(new Error('blocked'));
                    }};
                    Object.defineProperty(navigator, 'clipboard', {value:clipboard});
                    document.execCommand = command => {
                        fallbackValues.push({command, text:document.activeElement.value});
                        return true;
                    };
                }''', outcome)
                self.assertTrue(self.copy(page))
                self.assertEqual(page.evaluate('fallbackValues'), [dict(command='copy', text=expected)])

    def test_repeated_copy_restores_the_original_label_and_inline_background(self):
        page = self.open_report()
        self.install_clipboard(page)
        original = page.locator(BUTTON).evaluate('''button => {
            button.style.background = 'rgb(17, 34, 51)';
            return {text:button.textContent, background:button.style.background};
        }''')
        for attempt in range(3):
            self.assertTrue(self.copy(page))
            self.assertTrue(page.locator(BUTTON).inner_text().startswith('Copied '))
        page.wait_for_function('''() => document.querySelector('button[onclick="copyVisibleContent(event)"]')
            .textContent === 'Copy' ''')
        self.assertEqual(page.locator(BUTTON).evaluate(
            'button => ({text:button.textContent, background:button.style.background})'), original)
        self.assertEqual(page.evaluate('copiedText.length'), 3)

    def test_stale_failed_request_cannot_fall_back_over_a_newer_copy(self):
        page = self.open_report()
        result = page.locator(BUTTON).evaluate('''async button => {
            const writes = [];
            Object.defineProperty(navigator, 'clipboard', {value:{writeText:text =>
                new Promise((resolve, reject) => writes.push({resolve, reject}))}});
            let fallbacks = 0;
            document.execCommand = () => { fallbacks++; return true; };
            const first = copyVisibleContent({target:button});
            const second = copyVisibleContent({target:button});
            writes[1].resolve();
            await second;
            const latest = button.textContent;
            writes[0].reject(new Error('old permission denial'));
            await first;
            return {fallbacks, latest, final:button.textContent};
        }''')
        self.assertEqual(result['fallbacks'], 0)
        self.assertTrue(result['latest'].startswith('Copied '))
        self.assertEqual(result['final'], result['latest'])

    def test_stale_success_cannot_overwrite_newer_failure(self):
        page = self.open_report()
        result = page.locator(BUTTON).evaluate('''async button => {
            const writes = [];
            Object.defineProperty(navigator, 'clipboard', {value:{writeText:text =>
                new Promise((resolve, reject) => writes.push({resolve, reject}))}});
            document.execCommand = () => false;
            const first = copyVisibleContent({target:button});
            const second = copyVisibleContent({target:button});
            writes[1].reject(new Error('new permission denial'));
            await second;
            const latest = button.textContent;
            writes[0].resolve();
            await first;
            return {latest, final:button.textContent};
        }''')
        self.assertEqual(result, dict(latest='Copy failed', final='Copy failed'))

    def test_expired_feedback_does_not_let_an_old_copy_replace_newer_feedback(self):
        page = self.open_report()
        result = page.locator(BUTTON).evaluate('''async button => {
            const writes = [];
            Object.defineProperty(navigator, 'clipboard', {value:{writeText:text =>
                new Promise((resolve, reject) => writes.push({resolve, reject}))}});
            let fallbacks = 0;
            document.execCommand = () => { fallbacks++; return false; };
            const first = copyVisibleContent({target:button});
            const second = copyVisibleContent({target:button});
            writes[1].resolve(); await second;
            await new Promise(resolve => setTimeout(resolve, 1100));
            const reset = button.textContent;
            const third = copyVisibleContent({target:button});
            writes[2].resolve(); await third;
            const latest = button.textContent;
            writes[0].reject(new Error('expired permission denial')); await first;
            return {fallbacks, reset, latest, final:button.textContent};
        }''')
        self.assertEqual(result['fallbacks'], 0)
        self.assertEqual(result['reset'], 'Copy')
        self.assertEqual(result['final'], result['latest'])

    def test_legacy_copy_restores_forward_backward_and_collapsed_selection_focus_and_scroll(self):
        for direction in ('forward', 'backward', 'collapsed'):
            for copied in (False, True):
                with self.subTest(direction=direction, copied=copied):
                    page = self.open_report()
                    facts = page.locator(BUTTON).evaluate('''async (button, options) => {
                        Object.defineProperty(navigator, 'clipboard', {value:undefined});
                        document.execCommand = () => options.copied;
                        button.focus();
                        const line = document.querySelectorAll('#content .line-content')[200];
                        const selection = getSelection(), range = document.createRange();
                        range.selectNodeContents(line);
                        selection.removeAllRanges(); selection.addRange(range);
                        if (options.direction === 'backward') selection.setBaseAndExtent(
                            selection.focusNode, selection.focusOffset, selection.anchorNode, selection.anchorOffset);
                        if (options.direction === 'collapsed') selection.collapseToStart();
                        window.scrollTo({top:1200, behavior:'instant'});
                        document.getElementById('textViewport').scrollLeft = 300;
                        const before = {text:selection.toString(), anchor:selection.anchorNode, anchorOffset:selection.anchorOffset,
                            focus:selection.focusNode, focusOffset:selection.focusOffset, top:scrollY,
                            left:document.getElementById('textViewport').scrollLeft};
                        const result = await copyVisibleContent({target:button});
                        return {result, text:selection.toString() === before.text,
                            anchor:selection.anchorNode === before.anchor && selection.anchorOffset === before.anchorOffset,
                            focus:selection.focusNode === before.focus && selection.focusOffset === before.focusOffset,
                            focused:document.activeElement === button, top:scrollY === before.top,
                            left:document.getElementById('textViewport').scrollLeft === before.left};
                    }''', dict(direction=direction, copied=copied))
                    self.assertEqual(facts, dict(result=copied, text=True, anchor=True, focus=True,
                                                 focused=True, top=True, left=True))
                    self.assertEqual(page.locator('body > textarea').count(), 0)

    def test_legacy_copy_preserves_native_input_selection(self):
        page = self.open_report()
        result = page.evaluate('''async () => {
            const input = document.getElementById('filterInput');
            input.closest('#filterBox').style.display = 'block';
            input.value = 'cpu:0 pid:42'; input.focus(); input.setSelectionRange(2, 8, 'backward');
            Object.defineProperty(navigator, 'clipboard', {value:undefined});
            document.execCommand = () => true;
            await copyVisibleContent({target:document.querySelector('button[onclick="copyVisibleContent(event)"]')});
            return {focus:document.activeElement === input, start:input.selectionStart,
                end:input.selectionEnd, direction:input.selectionDirection, text:input.value};
        }''')
        self.assertEqual(result, dict(focus=True, start=2, end=8, direction='backward', text='cpu:0 pid:42'))

    def test_no_matches_never_overwrite_the_clipboard_and_feedback_recovers(self):
        page = self.open_report()
        self.install_clipboard(page)
        page.evaluate("() => { document.getElementById('filterInput').value='func:^no_such_function$'; applyFilter(); }")
        page.wait_for_function('() => !fgFilterProgress')
        self.assertFalse(self.copy(page))
        self.assertEqual(page.locator(BUTTON).inner_text(), 'No visible lines')
        self.assertEqual(page.evaluate('copiedText'), [])
        page.evaluate('clearFilter()')
        page.wait_for_function('() => !fgFilterProgress')
        self.assertTrue(self.copy(page))
        page.wait_for_function('''() => document.querySelector('button[onclick="copyVisibleContent(event)"]')
            .textContent === 'Copy' ''')

    def test_current_target_owns_feedback_and_copy_never_loads_timeline_or_sources(self):
        for filename in ('index.html', 'windowed/index.html?text=windowed'):
            with self.subTest(filename=filename):
                page = self.open_report(filename)
                self.install_clipboard(page)
                before = page.evaluate("({timeline:fgData.ready('tlData'), source:fgData.ready('fgSrc')})")
                requests = []
                page.on('request', lambda request: requests.append(request.url))
                self.assertTrue(page.locator(BUTTON).evaluate('''button => {
                    const icon = document.createElement('span'); icon.textContent = 'Copy'; button.replaceChildren(icon);
                    return copyVisibleContent({currentTarget:button, target:icon});
                }'''))
                self.assertTrue(page.locator(BUTTON).inner_text().startswith('Copied '))
                self.assertEqual(page.evaluate("({timeline:fgData.ready('tlData'), source:fgData.ready('fgSrc')})"), before)
                self.assertEqual(requests, [])

    def test_copy_preserves_expanded_source_folds_event_preference_and_timeline_data(self):
        for filename in ('index.html', 'mixed.html', 'windowed/index.html?text=windowed'):
            with self.subTest(filename=filename):
                page = self.open_report(filename)
                self.install_clipboard(page)
                page.locator('#content .expandable').first.evaluate('''async row => {
                    toggleExpand(row.dataset.lineId);
                    await fgFill(row.nextElementSibling);
                    window.openSource = row.nextElementSibling;
                    window.openSourceChild = openSource.firstElementChild;
                }''')
                self.assertTrue(page.evaluate('!!openSourceChild'))
                page.evaluate("async () => { await fgData.load('tlData'); }")
                before = page.evaluate('''() => JSON.stringify({expanded:expandedLines, folds:foldedBlocks,
                    events:fgTextEventsVisible, timeline:fgData.read('tlData'), source:openSource.innerHTML,
                    rows:document.querySelectorAll('#content .line-container').length})''')
                expected = page.evaluate(EXPECTED)
                self.assertTrue(self.copy(page))
                self.assertEqual(page.evaluate('copiedText.at(-1)'), expected)
                self.assertTrue(page.evaluate('openSource.isConnected && openSource.firstElementChild === openSourceChild'))
                after = page.evaluate('''() => JSON.stringify({expanded:expandedLines, folds:foldedBlocks,
                    events:fgTextEventsVisible, timeline:fgData.read('tlData'), source:openSource.innerHTML,
                    rows:document.querySelectorAll('#content .line-container').length})''')
                self.assertEqual(before, after)
