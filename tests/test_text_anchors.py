"""Trace line links retain native navigation and safe clipboard fallback."""
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


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class TextAnchorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel(inline_source=True)
        cls.addClassCleanup(cls.kernel.close)
        trace = Path(cls.kernel.dir, 'trace.txt')
        trace.write_text('# tracer: function_graph\n'
                         ' 0)               |  root() { /* <-main+0x4/0x80 */\n' +
                         ''.join(f' 0)   1.000 us    |    leaf(arg={number}); /* <-root+0x4/0x80 */\n' +
                                 (' 0)               |    /* sched_wakeup: comm=worker pid=42 */\n'
                                  if number % 100 == 0 else '') for number in range(4500)) +
                         ' 0) 5000.000 us    |  } /* root ret=0 */\n')
        options = ['--no-config', '--enable-fold', '--filter', '--func-links']
        cls.kernel.run(str(trace), *options, out='plain.html')
        cls.kernel.run(str(trace), *options, '--timeline', out='index.html')
        cls.kernel.run(str(trace), *options, '--timeline', '--report-mode', 'sidecar', out='sidecar')
        cls.kernel.run(str(Path(helpers.ROOT, 'samples/sample_mixed.jsonl')), *options, '--timeline',
                       '--user-src', str(Path(helpers.ROOT, 'samples')), out='mixed.html')
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
        context = self.browser.new_context(viewport=dict(width=390 if mobile else 1500, height=900),
                                           is_mobile=mobile, has_touch=mobile)
        self.addCleanup(context.close)
        page = context.new_page()
        page.add_init_script('localStorage.clear()')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(self.assertEqual, errors, [])
        page.goto(urljoin(self.server.url, filename))
        page.evaluate('''async theme => {
            document.documentElement.dataset.theme = theme;
            if (typeof fgSwitchView === 'function') await fgSwitchView('text');
        }''', theme)
        return page

    def mock_clipboard(self, page):
        page.evaluate('''() => {
            window.anchorWrites = [];
            Object.defineProperty(navigator, 'clipboard', {configurable:true,
                value:{writeText:async text => { anchorWrites.push(text); }}});
        }''')

    def test_every_mounted_line_is_a_native_link_without_per_row_handlers(self):
        for filename in ('plain.html', 'index.html', 'mixed.html', 'sidecar/index.html',
                         'sidecar/index.html?text=windowed'):
            with self.subTest(filename=filename):
                page = self.open_report(filename)
                facts = page.evaluate('''() => {
                    const rows = [...document.querySelectorAll('#content .line-container')];
                    return {rows:rows.length, links:rows.every(row => {
                        const link = row.querySelector('.line-number');
                        return link.tagName === 'A' && link.getAttribute('href') === '#' + row.id &&
                            link.textContent === row.dataset.lineNumber && link.tabIndex === 0 &&
                            !link.hasAttribute('onclick') && !link.hasAttribute('onkeydown');
                    }), windowed:!!fgTextWindow};
                }''')
                self.assertTrue(facts['links'])
                self.assertEqual(facts['windowed'], 'windowed' in filename)
                self.assertGreater(facts['rows'], 0)
                if facts['windowed']:
                    self.assertLessEqual(facts['rows'], 160)

    def test_mouse_enter_and_nested_label_copy_exact_url_without_expanding_source(self):
        for filename in ('plain.html', 'index.html', 'mixed.html',
                         'sidecar/index.html?text=windowed&note=a%26b'):
            with self.subTest(filename=filename):
                page = self.open_report(filename)
                self.mock_clipboard(page)
                row = page.locator('#content .expandable').first
                identifier = row.get_attribute('id')
                link = row.locator('.line-number')
                line_id = row.get_attribute('data-line-id')
                state = page.evaluate('JSON.stringify({expanded:expandedLines,folds:foldedBlocks})')
                original = page.url.split('#')[0]
                link.click()
                page.wait_for_function('() => anchorWrites.length === 1')
                link.focus()
                page.keyboard.press('Enter')
                page.wait_for_function('() => anchorWrites.length === 2')
                link.evaluate('node => { const label=document.createElement("b"); label.textContent=node.textContent; node.replaceChildren(label); }')
                link.locator('b').click()
                page.wait_for_function('() => anchorWrites.length === 3')
                self.assertEqual(page.evaluate('anchorWrites'), [original + '#' + identifier] * 3)
                self.assertEqual(page.evaluate('location.hash'), '#' + identifier)
                self.assertEqual(page.evaluate('JSON.stringify({expanded:expandedLines,folds:foldedBlocks})'), state)
                self.assertEqual(page.locator('#' + line_id + '_content').evaluate('node => node.style.display'), 'none')

    def test_double_clicking_a_line_link_never_expands_its_source(self):
        page = self.open_report()
        self.mock_clipboard(page)
        row = page.locator('#L3')
        row.locator('.line-number').dblclick()
        page.wait_for_function('() => anchorWrites.length === 2')
        page.wait_for_timeout(350)
        line_id = row.get_attribute('data-line-id')
        self.assertEqual(page.locator('#' + line_id + '_content').evaluate('node => node.style.display'), 'none')

    def test_unavailable_denied_throwing_and_missing_method_use_safe_fallback(self):
        for mode in ('unavailable', 'denied', 'throwing', 'missing'):
            for copied in (False, True):
                with self.subTest(mode=mode, copied=copied):
                    page = self.open_report()
                    result = page.evaluate('''async options => {
                        const clipboard = options.mode === 'unavailable' ? undefined :
                            options.mode === 'missing' ? {} : {writeText:() => {
                                if (options.mode === 'throwing') throw new Error('blocked');
                                return Promise.reject(new Error('denied'));
                            }};
                        Object.defineProperty(navigator, 'clipboard', {value:clipboard});
                        const values = [];
                        document.execCommand = command => {
                            values.push({command,text:document.activeElement.value}); return options.copied;
                        };
                        const result = await updateAnchor('L3',new MouseEvent('click',{cancelable:true}));
                        return {result, values, url:location.href, hash:location.hash,
                            temporary:document.querySelectorAll('body > textarea').length};
                    }''', dict(mode=mode, copied=copied))
                    self.assertEqual(result['result'], copied)
                    self.assertEqual(result['values'], [dict(command='copy', text=result['url'])])
                    self.assertEqual(result['hash'], '#L3')
                    self.assertEqual(result['temporary'], 0)

    def test_throwing_fallback_leaves_url_usable_and_no_unhandled_error(self):
        page = self.open_report()
        page.evaluate('''() => {
            Object.defineProperty(navigator,'clipboard',{value:undefined});
            document.execCommand = () => { throw new Error('blocked'); };
        }''')
        page.locator('#L3 > .line-number').click()
        self.assertEqual(page.evaluate('location.hash'), '#L3')
        self.assertEqual(page.locator('#L3 > .line-number').get_attribute('href'), '#L3')
        self.assertEqual(page.locator('body > textarea').count(), 0)

    def test_legacy_copy_reuses_selection_focus_and_scroll_preservation(self):
        page = self.open_report()
        facts = page.evaluate('''async () => {
            const button = document.querySelector('button[onclick="copyVisibleContent(event)"]');
            button.focus();
            const selection = getSelection(), range = document.createRange();
            range.selectNodeContents(document.querySelector('#L3 .line-content'));
            selection.removeAllRanges(); selection.addRange(range);
            selection.setBaseAndExtent(selection.focusNode,selection.focusOffset,selection.anchorNode,selection.anchorOffset);
            const before = {text:selection.toString(),anchor:selection.anchorNode,anchorOffset:selection.anchorOffset,
                focus:selection.focusNode,focusOffset:selection.focusOffset};
            let preserved;
            const fallback = fallbackCopy;
            fallbackCopy = text => {
                const top=scrollY, left=document.getElementById('textViewport').scrollLeft, active=document.activeElement;
                const result=fallback(text);
                preserved = active === document.activeElement && top === scrollY &&
                    left === document.getElementById('textViewport').scrollLeft;
                return result;
            };
            Object.defineProperty(navigator,'clipboard',{value:undefined});
            document.execCommand = () => true;
            await updateAnchor('L3',new MouseEvent('click',{cancelable:true}));
            return {preserved,text:selection.toString() === before.text,
                anchor:selection.anchorNode === before.anchor && selection.anchorOffset === before.anchorOffset,
                focus:selection.focusNode === before.focus && selection.focusOffset === before.focusOffset};
        }''')
        self.assertEqual(facts, dict(preserved=True,text=True,anchor=True,focus=True))

    def test_stale_denial_does_not_copy_the_previous_anchor_over_the_new_one(self):
        page = self.open_report()
        facts = page.evaluate('''async () => {
            const writes = [], fallbackValues = [];
            Object.defineProperty(navigator,'clipboard',{value:{writeText:text =>
                new Promise((resolve,reject)=>writes.push({text,resolve,reject}))}});
            document.execCommand = () => { fallbackValues.push(document.activeElement.value); return true; };
            const first=updateAnchor('L3',new MouseEvent('click',{cancelable:true}));
            const second=updateAnchor('L4',new MouseEvent('click',{cancelable:true}));
            writes[1].resolve(); await second;
            writes[0].reject(new Error('old request denied')); await first;
            return {fallbackValues,hash:location.hash,writes:writes.map(write=>write.text)};
        }''')
        self.assertEqual(facts['fallbackValues'], [])
        self.assertEqual(facts['hash'], '#L4')
        self.assertTrue(facts['writes'][0].endswith('#L3'))
        self.assertTrue(facts['writes'][1].endswith('#L4'))

    def test_navigation_without_a_copy_also_invalidates_a_pending_fallback(self):
        page = self.open_report()
        facts = page.evaluate('''async () => {
            let deny;
            Object.defineProperty(navigator,'clipboard',{value:{writeText:() => new Promise((resolve,reject)=>{deny=reject;})}});
            let fallbacks=0; document.execCommand=()=>{fallbacks++; return true;};
            const first=updateAnchor('L3',new MouseEvent('click',{cancelable:true}));
            location.hash='L4';
            deny(new Error('denied')); await first;
            return {fallbacks,hash:location.hash};
        }''')
        self.assertEqual(facts, dict(fallbacks=0,hash='#L4'))

    def test_old_link_fallback_cannot_overwrite_a_newer_trace_copy(self):
        page = self.open_report()
        facts = page.evaluate('''async () => {
            const writes=[],fallbackValues=[];
            Object.defineProperty(navigator,'clipboard',{value:{writeText:text=>new Promise((resolve,reject)=>writes.push({text,resolve,reject}))}});
            document.execCommand=()=>{fallbackValues.push(document.activeElement.value);return true;};
            const first=updateAnchor('L3',new MouseEvent('click',{cancelable:true}));
            const second=copyVisibleContent({target:document.querySelector('button[onclick="copyVisibleContent(event)"]')});
            writes[1].resolve(); await second;
            writes[0].reject(new Error('old link denied')); await first;
            return {fallbackValues,latestWasTrace:writes[1].text.startsWith('# tracer:'),hash:location.hash};
        }''')
        self.assertEqual(facts, dict(fallbackValues=[],latestWasTrace=True,hash='#L3'))

    def test_old_trace_fallback_cannot_overwrite_a_newer_line_link(self):
        page = self.open_report()
        facts = page.evaluate('''async () => {
            const writes=[],fallbackValues=[];
            Object.defineProperty(navigator,'clipboard',{value:{writeText:text=>new Promise((resolve,reject)=>writes.push({text,resolve,reject}))}});
            document.execCommand=()=>{fallbackValues.push(document.activeElement.value);return true;};
            const button=document.querySelector('button[onclick="copyVisibleContent(event)"]');
            const first=copyVisibleContent({target:button});
            const second=updateAnchor('L3',new MouseEvent('click',{cancelable:true}));
            writes[1].resolve(); await second;
            writes[0].reject(new Error('old trace denied')); await first;
            return {fallbackValues,latestWasLink:writes[1].text===location.href,label:button.textContent};
        }''')
        self.assertEqual(facts, dict(fallbackValues=[],latestWasLink=True,label='Copy failed'))

    def test_modified_or_consumed_clicks_skip_copy_without_consuming_the_event(self):
        page = self.open_report()
        self.mock_clipboard(page)
        facts = page.evaluate('''async () => {
            const results=[];
            for (const options of [{ctrlKey:true},{metaKey:true},{altKey:true},{shiftKey:true},{button:1},{consumed:true}]) {
                const event=new MouseEvent('click',{cancelable:true,...options});
                if (options.consumed) event.preventDefault();
                const result=await updateAnchor('L3',event);
                results.push({result,prevented:event.defaultPrevented,consumed:!!options.consumed});
            }
            return {results,writes:anchorWrites,hash:location.hash};
        }''')
        self.assertEqual(facts['writes'], [])
        self.assertEqual(facts['hash'], '')
        for result in facts['results']:
            self.assertFalse(result['result'])
            self.assertEqual(result['prevented'], result['consumed'])

    def test_control_click_opens_a_native_line_link_in_a_new_tab_without_copying(self):
        page = self.open_report()
        self.mock_clipboard(page)
        with page.context.expect_page() as opened:
            page.locator('#L3 > .line-number').click(modifiers=['Control'])
        popup = opened.value
        self.addCleanup(popup.close)
        popup.wait_for_url(page.url + '#L3')
        popup.wait_for_load_state()
        self.assertEqual(popup.url, page.url + '#L3')
        self.assertEqual(page.evaluate('anchorWrites'), [])
        self.assertEqual(page.evaluate('location.hash'), '')

    def test_real_clipboard_receives_the_complete_line_url_in_modern_and_legacy_modes(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                page = self.open_report()
                page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
                page.evaluate('''legacy => {
                    window.nativeClipboard = navigator.clipboard;
                    if (legacy) Object.defineProperty(navigator,'clipboard',{value:undefined});
                }''', legacy)
                page.locator('#L3 > .line-number').click()
                page.wait_for_function('() => location.hash === "#L3"')
                self.assertEqual(page.evaluate('nativeClipboard.readText()'), page.url)

    def test_native_line_navigation_still_works_when_javascript_is_disabled(self):
        context = self.browser.new_context(java_script_enabled=False)
        self.addCleanup(context.close)
        page = context.new_page()
        page.goto(urljoin(self.server.url, 'plain.html'))
        link = page.locator('#L3 > .line-number')
        self.assertEqual(link.get_attribute('href'), '#L3')
        link.click()
        self.assertTrue(page.url.endswith('#L3'))
        self.assertTrue(page.locator('#L3').is_visible())

    def test_existing_alt_click_still_opens_timeline_instead_of_copying(self):
        page = self.open_report()
        self.mock_clipboard(page)
        page.locator('#L3 > .line-number').click(modifiers=['Alt'])
        page.wait_for_function('() => fgCurrentView() === "timeline"')
        self.assertEqual(page.evaluate('anchorWrites'), [])
        self.assertEqual(page.evaluate('location.hash'), '')

    def test_focus_ring_in_both_themes_and_mobile_stays_inside_the_line_number(self):
        for mobile in (False, True):
            for theme in ('light', 'dark'):
                with self.subTest(mobile=mobile, theme=theme):
                    page = self.open_report(theme=theme, mobile=mobile)
                    self.mock_clipboard(page)
                    link = page.locator('#L3 > .line-number')
                    link.focus()
                    page.keyboard.press('j')
                    self.assertTrue(link.evaluate('node => document.activeElement === node'))
                    facts = link.evaluate('''node => {
                        const style=getComputedStyle(node),bounds=node.getBoundingClientRect();
                        return {focus:node.matches(':focus-visible'),underline:style.textDecorationLine,
                            outline:style.outlineStyle,offset:style.outlineOffset,width:bounds.width,
                            trace:node.nextElementSibling.getBoundingClientRect().left >= bounds.right};
                    }''')
                    self.assertTrue(facts['focus'])
                    self.assertEqual(facts['underline'], 'none')
                    self.assertEqual(facts['outline'], 'solid')
                    self.assertEqual(facts['offset'], '-1px')
                    self.assertTrue(facts['trace'])

    def test_windowed_line_link_preserves_bounded_dom_and_does_not_load_timeline_or_sources(self):
        page = self.open_report('sidecar/index.html?text=windowed')
        self.mock_clipboard(page)
        self.assertTrue(page.evaluate('!!fgTextWindow'))
        requests = []
        page.on('request', lambda request: requests.append(request.url))
        page.locator('#L3 > .line-number').click()
        page.wait_for_function('() => anchorWrites.length === 1')
        self.assertLessEqual(page.locator('#content .line-container').count(), 160)
        self.assertFalse(page.evaluate("fgData.ready('tlData')"))
        self.assertFalse(page.evaluate("fgData.ready('fgSrc')"))
        self.assertEqual(requests, [])
