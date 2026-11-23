"""Synthetic inline selections reuse recorded source without inventing events."""

from itertools import product
from pathlib import Path
import threading
import unittest

import helpers
from fglib.viewer import create_server
from test_tailcalls import TRACE


class InlineSourceBrowserTest(unittest.TestCase):
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
        cls.reports = {}
        for listing, mode, text in product((False, True), ('single', 'sidecar'), (False, True)):
            kernel = helpers.FakeKernel(inline_source=True, listing=listing)
            cls.addClassCleanup(kernel.close)
            resolver = helpers.FAKE_INLINE_FADDR2LINE.replace(
                "       printf ' (inlined by) traced_outer",
                "       printf ' (inlined by) inline_middle at %s/%s:1\\n' \"$FAKE_SRC\" \"$FAKE_SOURCE_FILE\"\n"
                "       if \"$listed\"; then printf '>1<\\tint a;\\n 2\\tint b;\\n'; fi\n"
                "       printf ' (inlined by) traced_outer")
            Path(kernel.ksrc, 'scripts/faddr2line').write_text(resolver)
            trace = Path(kernel.dir, 'trace.txt')
            trace.write_text(TRACE.replace('parent() { /* <-root+0x1/0x20 */', 'parent() {'))
            extra = [] if text else ['--no-text-view', '--keep-source']
            destination = 'report.html' if mode == 'single' else 'report'
            kernel.run(str(trace), '--no-config', '--no-btf', '--timeline', '--enable-fold',
                       '--report-mode', mode, '--base-url', 'https://source.invalid/src',
                       *extra, out=destination)
            path = Path(kernel.dir, destination)
            if mode == 'sidecar':
                server = create_server(path)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                cls.addClassCleanup(server.server_close)
                cls.addClassCleanup(server.shutdown)
                url = server.url
            else:
                url = path.as_uri()
            cls.reports[listing, mode, text] = url

    def open_report(self, listing=True, mode='single', text=True, *, viewport=None, touch=False):
        page = self.browser.new_page(viewport=viewport or {'width': 1500, 'height': 1000},
                                     is_mobile=touch, has_touch=touch)
        self.addCleanup(page.close)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(self.assertEqual, errors, [])
        page.goto(self.reports[listing, mode, text])
        page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().inline(true); }")
        return page

    def select_inline(self, page, level=1, mouse=False):
        index = page.evaluate('''level => {
            const probe = fgView('timeline').probe();
            return probe.N + probe.SYN.findIndex(frame => frame.k === level && frame.kids.includes(1));
        }''', level)
        self.assertGreaterEqual(index, page.evaluate("fgView('timeline').probe().N"))
        if mouse:
            page.evaluate("index => fgView('timeline').probe().select(index)", index)
            page.evaluate("fgView('timeline').probe().select(-1)")
            point = page.evaluate("index => fgView('timeline').probe().xyOf(index)", index)
            page.mouse.click(*point)
        else:
            page.evaluate("index => fgView('timeline').probe().select(index)", index)
        self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), index)
        return index

    def test_mouse_click_opens_selected_inline_source_with_and_without_text_or_sidecars(self):
        for mode, text in product(('single', 'sidecar'), (False, True)):
            with self.subTest(mode=mode, text=text):
                page = self.open_report(mode=mode, text=text)
                snapshot = page.evaluate("JSON.stringify(fgData.read('tlData'))")
                for level, name, line in [(1, 'inline_middle', 1), (2, 'inline_helper', 2)]:
                    selected = self.select_inline(page, level, mouse=True)
                    page.locator('.tl-cs .source-line').first.wait_for(state='attached')
                    self.assertEqual(page.locator('.tl-detail-title').inner_text(), name)
                    self.assertIn('first traced call', page.locator('.tl-cs').inner_text())
                    self.assertEqual(page.locator('.tl-cs .src-frame[open] summary b').all_text_contents(), [name])
                    self.assertTrue(page.locator('.tl-cs .src-frame[open] .source-line').first.is_visible())
                    self.assertEqual(page.locator('.tl-cs .src-frame[open] summary a').get_attribute('href'),
                                     f'https://source.invalid/src/kernel/fake.c#L{line}')
                    self.assertEqual(page.locator('.tl-cs .source-line').count(), 7)
                    self.assertEqual(page.locator('[data-call-actions]').count(), 0)
                    page.locator('.tl-cs [data-fg-source-mode]').click()
                    self.assertEqual(page.locator('.tl-cs .src-frame').count(), 0)
                    self.assertEqual(page.locator('.tl-cs .source-line').count(), 7)
                    page.locator('.tl-cs [data-fg-source-mode]').click()
                    self.assertEqual(page.locator('.tl-cs .src-frame[open] summary b').all_text_contents(), [name])
                    self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), selected)
                self.assertEqual(page.evaluate("JSON.stringify(fgData.read('tlData'))"), snapshot)
                page.close()

    def test_plain_positions_keep_links_and_explain_missing_excerpts(self):
        for mode, text in product(('single', 'sidecar'), (False, True)):
            with self.subTest(mode=mode, text=text):
                page = self.open_report(listing=False, mode=mode, text=text)
                self.select_inline(page)
                page.wait_for_function("document.querySelector('.tl-cs-src')?.textContent.includes('Source excerpt not included')")
                self.assertEqual(page.locator('.tl-cs .source-line').count(), 0)
                self.assertEqual(page.locator('[data-context-list=inline] .tl-context-source a').count(), 3)
                self.assertEqual(page.locator('.tl-cs [data-fg-source-mode]').count(), 0)
                page.close()

    def test_missing_source_block_is_explicit_without_changing_inline_selection(self):
        page = self.open_report()
        page.evaluate("fgSourceForLine = () => null")
        selected = self.select_inline(page)
        self.assertIn('Source excerpt not included', page.locator('.tl-cs-src').inner_text())
        self.assertEqual(page.locator('[data-context-list=inline] .tl-context-source a').count(), 3)
        self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), selected)

    def test_source_layout_roundtrip_restores_the_expanded_inline_frames(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                page = self.open_report(mode=mode)
                selected = self.select_inline(page)
                source = page.locator('.tl-cs-src')
                toggle = page.locator('.tl-cs [data-fg-source-mode]')
                frames = source.locator('.src-frame')
                frames.first.wait_for()
                self.assertEqual(frames.count(), 3)
                open_frames = source.locator('.src-frame[open] summary b')
                self.assertEqual(open_frames.all_text_contents(), ['inline_middle'])
                for expected in ([True, False, True], [False, False, False]):
                    names = frames.evaluate_all('''(frames, flags) => frames.flatMap((frame, index) => {
                        frame.open = flags[index];
                        return frame.open ? [frame.querySelector('summary b').textContent] : [];
                    })''', expected)
                    toggle.click()
                    self.assertEqual(frames.count(), 0)
                    toggle.click()
                    self.assertEqual(frames.count(), 3)
                    self.assertEqual(open_frames.all_text_contents(), names)
                    self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), selected)
                self.select_inline(page, level=2)
                frames.first.wait_for()
                self.assertEqual(open_frames.all_text_contents(), ['inline_helper'])

    def test_layout_switch_keeps_horizontal_source_position_without_rereading(self):
        page = self.open_report(viewport={'width': 320, 'height': 770}, touch=True)
        page.evaluate('''() => {
            const read = fgSourceHtml;
            window.layoutSourceReads = 0;
            fgSourceHtml = source => {
                layoutSourceReads++;
                return read(source).replace(/(<div class="source-line[^"]*">)/g, '$1' + 'long_source_'.repeat(30));
            };
        }''')
        selected = self.select_inline(page)
        source = page.locator('.tl-cs-src')
        code = source.locator('.src-frame[open] .tl-source-code')
        code.wait_for()
        position = code.evaluate('''code => {
            window.pannedSourceCode = code;
            window.beforeLayoutReads = layoutSourceReads;
            code.scrollLeft = 160;
            return code.scrollLeft;
        }''')
        self.assertEqual(position, 160)
        toggle = page.locator('.tl-cs [data-fg-source-mode]')
        for mode in ('flat', 'chain'):
            with self.subTest(mode=mode):
                toggle.tap()
                self.assertEqual(toggle.get_attribute('aria-label'), f'Source: {mode}')
                state = source.evaluate('''source => ({
                    offset: pannedSourceCode.scrollLeft, reused: source.contains(pannedSourceCode),
                    reloaded: layoutSourceReads !== beforeLayoutReads
                })''')
                self.assertEqual(state, {'offset': position, 'reused': True, 'reloaded': False})
                self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), selected)

    def test_shared_source_keeps_each_frame_position_across_layouts(self):
        for mode, oversized in (('single', False), ('sidecar', True)):
            with self.subTest(mode=mode, oversized=oversized):
                page = self.open_report(mode=mode, viewport={'width': 320, 'height': 770}, touch=True)
                page.set_default_timeout(5000)
                page.evaluate('''async oversized => {
                    const lines = fgData.read('tlData').cols.line;
                    const key = fgSourceForLine(lines[1]);
                    await fgData.loadSource(key);
                    const lookup = fgSourceForLine;
                    fgSourceForLine = line => line === lines[2] ? key : lookup(line);
                    const read = fgSourceHtml;
                    window.sharedLayoutReads = 0;
                    fgSourceHtml = source => {
                        const html = read(source);
                        if (source !== key) return html;
                        sharedLayoutReads++;
                        const longLines = html.replace(/(<div class="source-line[^"]*">)/g, '$1' + 'long_source_'.repeat(30));
                        return longLines + (oversized ? '<span></span>'.repeat(257) : '');
                    };
                }''', oversized)
                source = page.locator('.tl-cs-src')
                heading = page.locator('.tl-cs > summary')
                toggle = heading.locator('[data-fg-source-mode]')
                visible_code = source.locator('.src-frame[open] .tl-source-code')
                pan = '(code, offset) => { code.scrollLeft = offset; return code.scrollLeft; }'
                for index in (1, 2):
                    page.evaluate("index => fgView('timeline').probe().select(index)", index)
                    heading.scroll_into_view_if_needed()
                    visible_code.wait_for()
                    self.assertEqual(visible_code.evaluate('code => code.scrollLeft'), 0)
                    if index == 1:
                        self.assertEqual(visible_code.evaluate(pan, 81), 81)
                        source.locator('.src-frame').first.evaluate('frame => { frame.open = true; }')
                reads = page.evaluate('sharedLayoutReads')
                self.assertEqual(reads, 1)
                self.assertEqual(visible_code.evaluate(pan, 160), 160)
                toggle.tap()
                self.assertEqual(toggle.get_attribute('aria-label'), 'Source: flat')
                flat = source.locator('.tl-source-code')
                self.assertEqual(flat.first.evaluate('code => code.scrollLeft'), 160)
                self.assertEqual(page.evaluate('sharedLayoutReads'), reads + oversized)
                self.assertEqual(flat.last.evaluate(pan, 97), 97)
                toggle.tap()
                self.assertEqual(visible_code.evaluate('code => code.scrollLeft'), 160)
                outer = source.locator('.src-frame').first
                self.assertFalse(outer.evaluate('frame => frame.open'))
                outer.locator('summary').tap()
                page.wait_for_function('() => document.querySelector(".tl-cs-src .src-frame .tl-source-code").scrollLeft === 97')

                page.locator('.view-tab[data-view=text]').tap()
                page.evaluate('fgToggleSrcMode()')
                self.assertGreater(source.locator('.src-frame').count(), 0)
                page.locator('.view-tab[data-view=timeline]').tap()
                self.assertEqual(toggle.get_attribute('aria-label'), 'Source: flat')
                self.assertEqual(flat.first.evaluate('code => code.scrollLeft'), 160)
                self.assertEqual(flat.last.evaluate('code => code.scrollLeft'), 97)
                self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), 2)

                toggle.tap()
                self.assertTrue(outer.evaluate('frame => frame.open'))
                outer.locator('summary').tap()
                self.assertFalse(outer.evaluate('frame => frame.open'))
                toggle.tap()
                self.assertEqual(flat.last.evaluate('code => code.scrollLeft'), 97)

                toggle.tap()
                heading.tap(position={'x': 12, 'y': 15})
                self.assertFalse(heading.evaluate('summary => summary.parentElement.open'))
                reads = page.evaluate('sharedLayoutReads')
                page.locator('.view-tab[data-view=text]').tap()
                page.evaluate('fgToggleSrcMode()')
                page.locator('.view-tab[data-view=timeline]').tap()
                self.assertEqual(toggle.get_attribute('aria-label'), 'Source: flat')
                self.assertTrue(outer.count())
                self.assertEqual(page.evaluate('sharedLayoutReads'), reads)
                heading.tap(position={'x': 12, 'y': 15})
                page.wait_for_function('() => document.querySelector(".tl-cs-src .tl-source-code")?.scrollLeft === 160')
                self.assertEqual(flat.first.evaluate('code => code.scrollLeft'), 160)
                self.assertEqual(flat.last.evaluate('code => code.scrollLeft'), 97)
                self.assertEqual(page.evaluate('sharedLayoutReads'), reads + oversized)

    def test_source_disclosure_preference_and_real_call_focus_remain_independent(self):
        page = self.open_report()
        self.select_inline(page)
        page.locator('.tl-cs .source-line').first.wait_for(state='attached')
        page.locator('.tl-cs > summary').click()
        page.wait_for_function("localStorage.getItem('fgTl.csOpen') === '0'")
        self.select_inline(page, level=2)
        self.assertFalse(page.locator('.tl-cs').evaluate('element => element.open'))
        page.locator('.tl-cs > summary').click()
        page.locator('.tl-cs .source-line').first.wait_for(state='attached')
        page.evaluate("fgView('timeline').probe().select(1)")
        page.locator('.tl-cs > summary').scroll_into_view_if_needed()
        open_frames = page.locator('.tl-cs .src-frame[open] summary b')
        open_frames.first.wait_for(state='attached')
        self.assertEqual(open_frames.all_text_contents(), ['inline_helper'])
        self.assertEqual(page.locator('.tl-cs > summary').text_content().split('Source:')[0].strip(), 'Call site source')

    def test_replaced_timeline_source_leaves_layout_index_without_collection(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                page = self.open_report(mode=mode)
                inline = self.select_inline(page)
                source = page.locator('.tl-cs-src')
                mode_button = page.locator('.tl-cs [data-fg-source-mode]')
                page.evaluate('window.oldTimelineSources = []')
                for index in (1, inline, 1, inline):
                    mode_button.wait_for()
                    page.evaluate('''index => {
                        oldTimelineSources.push(...document.querySelectorAll('.tl-cs-src, .tl-cs [data-fg-source-mode]'));
                        fgView('timeline').probe().select(index);
                    }''', index)
                mode_button.wait_for()
                facts = page.evaluate('''() => ({retained:oldTimelineSources.length,
                    detached:oldTimelineSources.every(element => !element.isConnected), tracked:fgSourceNodes.size})''')
                self.assertEqual(facts, dict(retained=8, detached=True, tracked=2))
                mode_button.click()
                self.assertEqual(mode_button.inner_text(), 'Source: flat')
                self.assertEqual(source.locator('.src-frame').count(), 0)
                self.assertGreater(source.locator('.source-line').count(), 0)
                page.evaluate("fgView('timeline').probe().select(-1)")
                self.assertEqual(page.evaluate('fgSourceNodes.size'), 0)

    def test_source_layout_refreshes_only_the_visible_view(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                page = self.open_report(mode=mode)
                selected = self.select_inline(page)
                timeline_source = page.locator('.tl-cs-src')
                timeline_toggle = page.locator('.tl-cs [data-fg-source-mode]')
                timeline_source.locator('.src-frame').first.wait_for()
                line = page.evaluate('''async () => {
                    await fgSwitchView('text');
                    const line = fgData.read('tlData').cols.line[1];
                    const row = await fgEnsureTextLine(line);
                    toggleExpand(row.dataset.lineId);
                    await fgFill(row.nextElementSibling);
                    return line;
                }''')
                text_source = page.locator(f'#L{line} + .expanded-content')
                text_frames = text_source.locator('.src-frame')
                self.assertEqual(text_frames.count(), 3)
                text_frames.first.evaluate('frame => { frame.open = true; window.originalTextFrame = frame; }')
                text_source.evaluate('source => { window.originalCodeLines = [...source.querySelectorAll(".source-line")]; }')
                page.locator('.view-tab[data-view=timeline]').click()
                timeline_toggle.click()
                self.assertEqual(timeline_source.locator('.src-frame').count(), 0)
                self.assertTrue(text_source.evaluate('source => source.querySelector(".src-frame") === originalTextFrame'))
                timeline_toggle.click()
                page.locator('.view-tab[data-view=text]').click()
                self.assertTrue(text_frames.first.evaluate('frame => frame === originalTextFrame && frame.open'))

                page.locator('#srcModeBtn').click()
                self.assertEqual(text_frames.count(), 0)
                self.assertEqual(timeline_source.locator('.src-frame').count(), 3)
                page.locator('.view-tab[data-view=timeline]').click()
                self.assertEqual(timeline_toggle.inner_text(), 'Source: flat')
                self.assertEqual(timeline_source.locator('.src-frame').count(), 0)
                timeline_toggle.click()
                self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), selected)
                self.assertEqual(text_frames.count(), 0)
                page.locator('.view-tab[data-view=text]').click()
                self.assertTrue(text_frames.first.evaluate('frame => frame.open'))
                self.assertTrue(text_source.evaluate('''source => {
                    const lines = [...source.querySelectorAll('.source-line')];
                    return lines.length > 0 && lines.length === originalCodeLines.length &&
                        lines.every((line, index) => line === originalCodeLines[index]);
                }'''))

    def test_collapsed_text_source_uses_latest_layout_on_reopen(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                page = self.open_report(mode=mode)
                self.select_inline(page)
                page.locator('.tl-cs [data-fg-source-mode]').wait_for()
                line = page.evaluate('''async () => {
                    await fgSwitchView('text');
                    const line = fgData.read('tlData').cols.line[1];
                    const row = await fgEnsureTextLine(line);
                    toggleExpand(row.dataset.lineId);
                    await fgFill(row.nextElementSibling);
                    const source = row.nextElementSibling;
                    window.originalCollapsedFrame = source.querySelector('.src-frame');
                    originalCollapsedFrame.open = true;
                    toggleExpand(row.dataset.lineId);
                    return line;
                }''')
                text_line = page.locator(f'#L{line}')
                text_source = page.locator(f'#L{line} + .expanded-content')
                page.locator('.view-tab[data-view=timeline]').click()
                page.locator('.tl-cs [data-fg-source-mode]').click()
                page.locator('.view-tab[data-view=text]').click()
                self.assertTrue(text_source.evaluate('''source =>
                    source.style.display === 'none' && source.querySelector('.src-frame') === originalCollapsedFrame'''))
                text_line.locator('.expand-btn').click()
                text_source.wait_for(state='visible', timeout=5000)
                self.assertEqual(text_source.locator('.src-frame').count(), 0)
                text_line.locator('.expand-btn').click()
                text_source.wait_for(state='hidden', timeout=5000)
                page.locator('#srcModeBtn').click()
                self.assertEqual(text_source.locator('.src-frame').count(), 0)
                text_line.locator('.expand-btn').click()
                text_source.wait_for(state='visible', timeout=5000)
                self.assertTrue(text_source.locator('.src-frame').first.evaluate('frame => frame.open'))

    def test_touch_source_controls_open_and_wrap_inside_the_detail_pane(self):
        for mode, width, height in (('single', 320, 770), ('sidecar', 844, 390)):
            with self.subTest(mode=mode, width=width, height=height):
                page = self.open_report(mode=mode, viewport={'width': width, 'height': height}, touch=True)
                page.evaluate("localStorage.setItem('fgTl.csOpen', '0')")
                selected = self.select_inline(page)
                pane = page.locator('.tl-detail')
                expand = pane.get_by_role('button', name='Expand details into the chart area')
                self.assertTrue(expand.evaluate('''button => {
                    const heading = button.closest('.tl-slice-head');
                    const title = heading.querySelector('.tl-detail-title').getBoundingClientRect();
                    const bounds = button.getBoundingClientRect();
                    const close = heading.querySelector('.tl-close').getBoundingClientRect();
                    return bounds.width >= 44 && bounds.height >= 44 && title.right <= bounds.left &&
                        bounds.right <= close.left && close.right <= innerWidth;
                }'''))
                source = pane.locator('.tl-cs')
                source_summary = source.locator(':scope > summary')

                def tap_lower_edge(locator):
                    area = locator.evaluate('''target => {
                        target.scrollIntoView({ block: 'center', inline: 'nearest' });
                        const bounds = target.getBoundingClientRect();
                        const tapX = bounds.left + 8, tapY = bounds.bottom - 6;
                        const hit = target.contains(document.elementFromPoint(tapX, tapY));
                        return { tapX, tapY, height: bounds.height, hit };
                    }''')
                    self.assertGreaterEqual(area['height'], 44, area)
                    self.assertTrue(area['hit'], area)
                    page.touchscreen.tap(area['tapX'], area['tapY'])

                self.assertFalse(source.evaluate('node => node.open'))
                tap_lower_edge(source_summary)
                source.locator('.source-line').first.wait_for(state='attached')
                mode_button = source.locator('[data-fg-source-mode]')
                tap_lower_edge(mode_button)
                self.assertEqual(page.evaluate('fgSourceMode()'), 'flat')
                self.assertTrue(source.evaluate('node => node.open'))
                tap_lower_edge(mode_button)
                self.assertEqual(page.evaluate('fgSourceMode()'), 'chain')

                frame = source.locator('.src-frame').first
                summary = frame.locator(':scope > summary')
                self.assertTrue(summary.evaluate('''node => {
                    const bounds = node.getBoundingClientRect(), text = document.createRange();
                    text.selectNodeContents(node);
                    return [...text.getClientRects()].every(rect => rect.right <= bounds.right + 1);
                }'''))
                self.assertFalse(frame.evaluate('node => node.open'))
                tap_lower_edge(summary)
                self.assertTrue(frame.evaluate('node => node.open'))
                self.assertEqual(summary.locator('.src-at').get_attribute('href'),
                                 'https://source.invalid/src/kernel/fake.c#L3')
                self.assertEqual(frame.locator('.source-line').first.evaluate('node => getComputedStyle(node).whiteSpace'), 'pre')
                self.assertTrue(pane.evaluate('node => node.scrollWidth === node.clientWidth'))
                self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), selected)
                page.close()

    def test_legacy_source_metadata_still_opens_the_selected_inline_level(self):
        page = self.open_report()
        page.evaluate("delete fgData.read('tlData').sources")
        self.select_inline(page)
        self.assertEqual(page.locator('.tl-cs .src-frame[open] summary b').all_text_contents(), ['inline_middle'])
        self.assertEqual(page.locator('.tl-cs .source-line').count(), 7)

    def test_code_pans_without_moving_source_links_or_timeline(self):
        page = self.open_report(viewport={'width': 320, 'height': 770}, touch=True)
        selected = self.select_inline(page)
        source = page.locator('.tl-cs-src')
        for mode in ('chain', 'flat'):
            with self.subTest(mode=mode):
                if mode == 'flat':
                    page.locator('.tl-cs [data-fg-source-mode]').tap()
                line = source.locator('.source-line:visible').first
                facts = line.evaluate('''line => {
                    const source = line.closest('.tl-cs-src');
                    const code = line.closest('.tl-source-code') || source;
                    const link = line.closest('.src-frame')?.querySelector('.src-at') || source.querySelector('a.location-link');
                    const heading = link.closest('summary') || link;
                    line.append('    tail_' + 'x'.repeat(160));
                    const left = heading.getBoundingClientRect().left;
                    const lineLeft = line.getBoundingClientRect().left;
                    code.scrollLeft = code.scrollWidth;
                    return { pan: code.scrollLeft, lineMoved: line.getBoundingClientRect().left < lineLeft,
                        headingDelta: heading.getBoundingClientRect().left - left, outerPan: source.scrollLeft };
                }''')
                self.assertGreater(facts['pan'], 0, facts)
                self.assertTrue(facts['lineMoved'], facts)
                self.assertAlmostEqual(facts['headingDelta'], 0, delta=1)
                self.assertEqual(facts['outerPan'], 0)
                code = line.locator('..')
                code.evaluate('element => { element.scrollLeft = 0; }')
                before = page.evaluate("fgView('timeline').probe().view()")
                code.focus()
                page.keyboard.press('ArrowRight')
                page.wait_for_function('document.activeElement.matches(".tl-source-code") && document.activeElement.scrollLeft > 0',
                                       timeout=3000)
                self.assertEqual(page.evaluate("fgView('timeline').probe().view()"), before)
                self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), selected)

    def test_failed_source_load_offers_retry_without_selecting_a_recorded_child(self):
        page = self.open_report(mode='sidecar')
        page.evaluate('''() => {
            const original = fgData.loadSource;
            fgData.sourceReady = () => false;
            let attempts = 0;
            fgData.loadSource = (...args) => ++attempts === 1 ? Promise.reject(new Error('test failure')) : original(...args);
        }''')
        selected = self.select_inline(page)
        page.locator('.tl-cs [role=alert]').wait_for(state='attached')
        self.assertIn('test failure', page.locator('.tl-cs [role=alert]').inner_text())
        page.get_by_role('button', name='Retry', exact=True).click()
        page.locator('.tl-cs .source-line').first.wait_for(state='attached')
        self.assertEqual(page.locator('.tl-cs .src-frame[open] summary b').all_text_contents(), ['inline_middle'])
        self.assertEqual(page.evaluate("fgView('timeline').probe().sel()"), selected)

    def test_pending_source_is_cancelled_on_selection_change_and_can_be_retried(self):
        page = self.open_report(mode='sidecar')
        page.evaluate('''() => {
            window.sourceRequests = [];
            window.originalLoadSource = fgData.loadSource;
            fgData.sourceReady = () => false;
            fgData.loadSource = (source, {signal}) => new Promise(resolve => sourceRequests.push({source, signal, resolve}));
        }''')
        self.select_inline(page)
        page.wait_for_function('sourceRequests.length === 1')
        self.assertEqual(page.locator('.tl-cs-src [role=status]').inner_text(), 'Loading source…')
        self.select_inline(page, level=2)
        page.wait_for_function('sourceRequests.length === 2')
        self.assertTrue(page.evaluate('sourceRequests[0].signal.aborted'))
        page.evaluate('''async () => {
            const text = await originalLoadSource(sourceRequests[1].source);
            sourceRequests[1].resolve(text);
            sourceRequests[0].resolve('<div class="source-line">stale source</div>');
        }''')
        page.locator('.tl-cs .source-line').first.wait_for(state='attached')
        self.assertNotIn('stale source', page.locator('.tl-cs-src').inner_text())
        self.assertEqual(page.locator('.tl-cs .src-frame[open] summary b').all_text_contents(), ['inline_helper'])

    def test_focus_matches_function_file_and_line_without_hiding_other_source_candidates(self):
        page = self.open_report()
        result = page.evaluate('''() => {
            const source = document.createElement('div');
            source.innerHTML = '<div class="location-link">same at /tree/first.c:2</div><div class="source-line">one</div>' +
                '<div class="location-link"> (inlined by) same at /tree/second.c:3</div><div class="source-line">two</div>' +
                '<div class="location-link"> (inlined by) outer at /tree/root.c:4</div><div class="source-line">three</div>' +
                '<div class="location-link">other at /tree/other.c:5</div><div class="source-line">four</div>';
            fgChainView(source, ['same', 'second.c', 3]);
            return {
                open: Array.from(source.querySelectorAll('.src-frame[open] summary .src-at'), element => element.textContent),
                lines: Array.from(source.querySelectorAll('.source-line'), element => element.textContent).sort(),
                candidates: source.querySelector(':scope > .location-link').textContent
            };
        }''')
        self.assertEqual(result['open'], ['/tree/second.c:3'])
        self.assertEqual(result['lines'], ['four', 'one', 'three', 'two'])
        self.assertEqual(result['candidates'], 'other at /tree/other.c:5')


if __name__ == '__main__':
    unittest.main()
