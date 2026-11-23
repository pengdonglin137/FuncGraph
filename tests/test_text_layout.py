"""Large inline Text keeps complete native content while skipping offscreen paint."""
import json
from pathlib import Path
import threading
import unittest
from urllib.parse import urljoin

import helpers
from fglib import uftrace
from fglib.viewer import create_server

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class InlineTextLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel(inline_source=True)
        cls.addClassCleanup(cls.kernel.close)
        cls.argument = '0123456789abcdef' * 200
        leaves = [' 3)   0.050 us    |    leaf(); /* <-root+0x4/0x80 */\n'] * 4200
        leaves[3000] = ' 3)   0.050 us    |    leaf(value=' + cls.argument + '); /* <-root+0x4/0x80 */\n'
        trace = Path(cls.kernel.dir) / 'layout.txt'
        trace.write_text('# tracer: function_graph\n'
                         ' 3)               |  root() { /* <-main+0x4/0x80 */\n' + ''.join(leaves) +
                         ' 3) 300.000 us    |  } /* root */\n')
        for timeline in (True, False):
            cls.kernel.run(str(trace), '--enable-fold', '--filter', '--func-links',
                           *(['--timeline'] if timeline else []),
                           out='index.html' if timeline else 'text-only.html')
        for count in (4095, 4096):
            boundary = Path(cls.kernel.dir) / ('boundary-' + str(count) + '.txt')
            boundary.write_text('# tracer: function_graph\n' + leaves[0] * (count - 1))
            cls.kernel.run(str(boundary), '--timeline', out='boundary-' + str(count) + '.html')
        cls.kernel.run(str(trace), '--timeline', '--enable-fold', '--filter', '--func-links',
                       '--report-mode', 'sidecar', out='sidecar')
        for filename in ('index.html', 'text-only.html'):
            original = Path(cls.kernel.dir, filename).read_text()
            marker = '<div class="text-chunk"'
            boundary = original.index(marker, original.index(marker) + len(marker))
            paused = original[:boundary] + '<script src="startup-pause.js"></script>' + original[boundary:]
            Path(cls.kernel.dir, 'paused-' + filename).write_text(paused)
        original = Path(cls.kernel.dir, 'index.html').read_text()
        original = original.replace('function fgInitText(mounted = false, model = null, navigation = null, renderer = null) {',
                                    'function fgInitText(mounted = false, model = null, navigation = null, renderer = null) { return;')
        Path(cls.kernel.dir, 'startup-fallback.html').write_text(original)
        original = original.replace('renderer = null) { return;',
                                    'renderer = null) { throw new Error("startup fixture failure");')
        Path(cls.kernel.dir, 'startup-error.html').write_text(original)
        event_rows = [
            ' 3)               |    /* sched_wakeup: pid=42 payload=' + ('宽🙂\t' * 400 if index == 1800 else str(index)) + ' */\n'
            if index % 3 == 0 else (' 3)   0.050 us    |    ' + ('wide_leaf(value=' + cls.argument if index == 3001
                else 'leaf(value=' + str(index)) + '); /* <-root+0x4/0x80 */\n')
            for index in range(4200)
        ]
        event_trace = Path(cls.kernel.dir, 'event-widths.txt')
        event_trace.write_text('# tracer: function_graph\n 3)               |  root() { /* <-main+0x4/0x80 */\n' + ''.join(event_rows) + ' 3) 300.000 us    |  } /* root */\n')
        cls.kernel.run(str(event_trace), '--timeline', '--enable-fold', '--filter', '--func-links', out='event-widths.html')
        cls.server = create_server(Path(cls.kernel.dir))
        cls.addClassCleanup(cls.server.server_close)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.thread.join, 5)
        cls.addClassCleanup(cls.server.shutdown)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        try:
            cls.browser = cls.playwright.chromium.launch(ignore_default_args=['--hide-scrollbars'])
        except Exception as error:
            raise unittest.SkipTest(str(error))
        cls.addClassCleanup(cls.browser.close)

    def open_report(self, width=1500, theme='light', filename='index.html', unsupported=None, touch=False):
        context = self.browser.new_context(viewport={'width': width, 'height': 900}, has_touch=touch, is_mobile=touch)
        self.addCleanup(context.close)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(self.assertEqual, errors, [])
        page.add_init_script('localStorage.setItem("theme", ' + repr(theme) + ')')
        if unsupported:
            page.add_init_script('''{
                const original = CSS.supports.bind(CSS);
                CSS.supports = (...parameters) => parameters[0] === ''' + repr(unsupported) + ''' ? false : original(...parameters);
            }''')
        page.goto(urljoin(self.server.url, filename), wait_until='load')
        return page

    def compare_layout(self, page, label):
        result = page.evaluate('''async () => {
            const root = document.getElementById('content');
            const viewport = document.getElementById('textViewport');
            const settle = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const measure = () => ({height:root.getBoundingClientRect().height,
                width:root.getBoundingClientRect().width, extent:viewport.scrollWidth});
            await settle();
            const optimized = measure();
            const baseline = document.createElement('style');
            baseline.textContent = '#content > .text-chunk {content-visibility:visible !important}';
            document.head.appendChild(baseline);
            try { await settle(); return {optimized, full:measure()}; }
            finally { baseline.remove(); }
        }''')
        for dimension in ('height', 'width', 'extent'):
            self.assertAlmostEqual(result['optimized'][dimension], result['full'][dimension], delta=1,
                                   msg=label + ': ' + dimension)

    def test_large_inline_startup_defers_partial_paint_then_restores_every_row(self):
        for filename, theme, width in (('index.html', 'light', 1500), ('text-only.html', 'dark', 390)):
            with self.subTest(filename=filename, theme=theme):
                context = self.browser.new_context(viewport={'width': width, 'height': 900})
                self.addCleanup(context.close)
                page = context.new_page()
                errors, pending = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.add_init_script('localStorage.setItem("theme", ' + repr(theme) + ')')
                page.route('**/startup-pause.js', lambda route: pending.append(route))
                page.goto(urljoin(self.server.url, 'paused-' + filename), wait_until='commit')
                page.wait_for_function('document.querySelectorAll("#content .line-container").length === 512')
                self.assertEqual(len(pending), 1)
                self.assertTrue(page.locator('#textStartupStatus').is_visible())
                self.assertEqual(page.locator('#textViewport').get_attribute('aria-busy'), 'true')
                self.assertEqual(page.evaluate('getComputedStyle(document.getElementById("content")).contentVisibility'), 'hidden')
                self.assertIn('root', page.locator('#content .line-container').nth(1).text_content())
                pending[0].fulfill(status=200, body='')
                page.wait_for_load_state('load')
                self.assertEqual(page.locator('#textStartupStatus').count(), 0)
                self.assertIsNone(page.locator('#textViewport').get_attribute('aria-busy'))
                self.assertEqual(page.evaluate('typeof fgInlineTextStartup'), 'undefined')
                self.assertEqual(page.locator('#content .line-container').count(), 4203)
                self.assertIn(self.argument, page.locator('#L3003').text_content())
                self.assertIn('root', page.locator('#L4203').text_content())
                self.compare_layout(page, 'startup complete ' + filename)
                self.assertEqual(errors, [])
                context.close()

    def test_startup_fallback_reveals_text_if_initialization_returns_early(self):
        page = self.open_report(filename='startup-fallback.html')
        page.wait_for_function('typeof fgInlineTextStartup === "undefined"')
        self.assertEqual(page.locator('#textStartupStatus').count(), 0)
        self.assertIsNone(page.locator('#textViewport').get_attribute('aria-busy'))
        self.assertEqual(page.evaluate('getComputedStyle(document.getElementById("content")).contentVisibility'), 'visible')
        self.assertEqual(page.locator('#content .line-container').count(), 4203)
        self.assertTrue(page.locator('#L2').is_visible())
        self.assertFalse(page.evaluate('fgTextInitialized'))

    def test_startup_keeps_no_javascript_and_unsupported_browser_text_readable(self):
        context = self.browser.new_context(java_script_enabled=False)
        self.addCleanup(context.close)
        page = context.new_page()
        page.goto(urljoin(self.server.url, 'index.html'), wait_until='load')
        self.assertFalse(page.locator('#textStartupStatus').is_visible())
        self.assertTrue(page.locator('#L2').is_visible())
        self.assertEqual(page.locator('#content .line-container').count(), 4203)
        page = self.open_report(unsupported='content-visibility: hidden')
        self.assertEqual(page.evaluate('typeof fgInlineTextStartup'), 'undefined')
        self.assertFalse(page.locator('#textStartupStatus').is_visible())
        self.assertTrue(page.locator('#L2').is_visible())

    def test_startup_fallback_reveals_text_after_initializer_error(self):
        context = self.browser.new_context()
        self.addCleanup(context.close)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(urljoin(self.server.url, 'startup-error.html'), wait_until='load')
        page.wait_for_function('typeof fgInlineTextStartup === "undefined"')
        self.assertEqual(errors, ['startup fixture failure'])
        self.assertEqual(page.locator('#textStartupStatus').count(), 0)
        self.assertIsNone(page.locator('#textViewport').get_attribute('aria-busy'))
        self.assertEqual(page.locator('#content .line-container').count(), 4203)
        self.assertTrue(page.locator('#L2').is_visible())

    def test_deferred_sidecar_startup_does_not_activate_inline_staging(self):
        page = self.open_report(filename='sidecar/index.html')
        self.assertEqual(page.evaluate('typeof fgInlineTextStartup'), 'undefined')
        self.assertFalse(page.locator('#textStartupStatus').is_visible())
        page.evaluate('async () => { await fgSwitchView("text"); }')
        self.assertEqual(page.locator('#content .line-container').count(), 4203)
        self.assertTrue(page.locator('#L2').is_visible())
        self.assertEqual(page.evaluate('fgDomTextModels.get(document.getElementById("content")).records.length'), 4203)

    def test_initial_far_anchor_uses_final_geometry_after_startup_staging(self):
        page = self.open_report(filename='index.html#L3003')
        page.wait_for_function('highlightedLine?.id === "L3003"')
        self.assertEqual(page.evaluate('typeof fgInlineTextStartup'), 'undefined')
        self.assertEqual(page.locator('#textStartupStatus').count(), 0)
        self.assertTrue(page.evaluate('''() => {
            const row = document.getElementById('L3003').getBoundingClientRect();
            return row.top >= document.querySelector('.controls').getBoundingClientRect().bottom && row.bottom <= innerHeight;
        }'''))
        self.assertIn(self.argument, page.locator('#L3003').text_content())

    def test_large_inline_text_skips_paint_without_dropping_dom_or_model(self):
        for filename in ('index.html', 'text-only.html'):
            with self.subTest(filename=filename):
                page = self.open_report(filename=filename)
                page.wait_for_timeout(100)
                result = page.evaluate('''() => {
                    const root = document.getElementById('content');
                    return {enabled:root.classList.contains('text-layout-auto'),
                        measured:root.classList.contains('text-layout-measured'),
                        skipped:Array.from(root.children).filter(chunk =>
                            !chunk.firstElementChild.checkVisibility({contentVisibilityAuto:true})).length,
                        rows:root.querySelectorAll('.line-container').length,
                        records:fgGetTextFolding().records.length,
                        last:document.getElementById('L4203').textContent,
                        argument:document.getElementById('L3003').textContent};
                }''')
                self.assertTrue(result['enabled'])
                self.assertTrue(result['measured'])
                self.assertGreater(result['skipped'], 0)
                self.assertEqual(result['rows'], 4203)
                self.assertEqual(result['records'], 4203)
                self.assertIn('root', result['last'])
                self.assertIn(self.argument, result['argument'])
                page.close()

    def check_event_widths(self, page):
        facts = page.evaluate('''() => {
            const chunks = Array.from(document.querySelectorAll('#content > .text-chunk'));
            const cached = chunks.map(chunk => parseFloat(chunk.style.getPropertyValue('--fg-text-inline-size')));
            for (const chunk of chunks) chunk.classList.add('text-layout-measure');
            try { return chunks.map((chunk, index) => ({cached:cached[index],native:chunk.getBoundingClientRect().width})); }
            finally { for (const chunk of chunks) chunk.classList.remove('text-layout-measure'); }
        }''')
        self.assertTrue(facts)
        for fact in facts:
            self.assertAlmostEqual(fact['cached'], fact['native'], delta=1 / 64)
        self.compare_layout(page, 'event width and complete extent')

    def observe_width_reads(self, page):
        page.evaluate('''() => {
            window.measuredChunkReads = 0;
            const original = Element.prototype.getBoundingClientRect;
            Element.prototype.getBoundingClientRect = function(...parameters) {
                if (this.classList.contains('text-layout-measure')) measuredChunkReads++;
                return original.apply(this, parameters);
            };
        }''')

    def test_event_toggles_reuse_exact_native_widths_without_preparing_a_model(self):
        for width, theme, touch in ((1500, 'light', False), (390, 'dark', True)):
            with self.subTest(width=width, theme=theme):
                page = self.open_report(width, theme, 'event-widths.html', touch=touch)
                page.evaluate('toggleExpand(document.getElementById("L4").dataset.lineId)')
                page.wait_for_function('document.getElementById("L4").nextElementSibling.querySelector(".source-line")')
                page.evaluate('''() => {
                    window.eventSourceNode = document.getElementById('L4').nextElementSibling;
                    window.eventSourceChild = eventSourceNode.firstElementChild;
                    window.eventExpandedState = JSON.stringify(expandedLines);
                }''')
                self.observe_width_reads(page)
                original = page.evaluate('''JSON.stringify(Array.from(document.querySelectorAll('#content .line-container'),
                    row => [row.id,row.querySelector('.line-content').textContent,row.dataset.traceEvent]))''')
                for visible in (False, True, False, True):
                    page.evaluate('''visible => { measuredChunkReads=0; fgSetTextEventsVisible(visible); }''', visible)
                    self.assertEqual(page.evaluate('measuredChunkReads'), 0)
                    self.assertEqual(page.locator('[data-trace-event="1"]').count(), 1400)
                    self.assertFalse(page.evaluate('fgDomTextModels.has(document.getElementById("content"))'))
                    self.check_event_widths(page)
                    self.assertTrue(page.evaluate('''() => eventSourceNode.isConnected && eventSourceNode.firstElementChild === eventSourceChild &&
                        eventSourceNode.style.display === 'block' && JSON.stringify(expandedLines) === eventExpandedState'''))
                    self.assertEqual(page.evaluate('''JSON.stringify(Array.from(document.querySelectorAll('#content .line-container'),
                        row => [row.id,row.querySelector('.line-content').textContent,row.dataset.traceEvent]))'''), original)
                page.close()

    def test_event_widths_refresh_after_filter_fold_order_and_hidden_resize(self):
        page = self.open_report(filename='event-widths.html')
        self.observe_width_reads(page)
        page.evaluate('fgSetTextEventsVisible(false)')
        page.evaluate('''async () => { document.getElementById('filterInput').value='cpu:3'; await applyFilter();
            measuredChunkReads=0; fgSetTextEventsVisible(true); }''')
        self.assertGreater(page.evaluate('measuredChunkReads'), 0)
        self.check_event_widths(page)
        for operation in ('''const entry=document.querySelector('.fold-entry'); toggleFold(entry.dataset.foldId);''',
                          '''const entry=document.querySelector('.fold-entry'); toggleFold(entry.dataset.foldId);''',
                          'clearFilter(); fgToggleTextOrder();', 'fgToggleTextOrder();'):
            page.evaluate('() => { ' + operation + ' }')
            for visible in (False, True):
                page.evaluate('visible => fgSetTextEventsVisible(visible)', visible)
                self.check_event_widths(page)
        page.evaluate('''async () => { await fgSwitchView('timeline'); fgSetTextEventsVisible(false); }''')
        page.set_viewport_size({'width':390,'height':900})
        page.evaluate('''async () => { await fgSwitchView('text'); fgSetTextEventsVisible(true); }''')
        self.check_event_widths(page)

    def test_event_widths_fall_back_for_unknown_children_and_changed_font_context(self):
        page = self.open_report(filename='event-widths.html')
        self.observe_width_reads(page)
        page.add_style_tag(content='#content { --fg-text-font-size: 17px; }')
        page.evaluate('''() => {
            measuredChunkReads=0; fgSetTextEventsVisible(false);
        }''')
        self.assertGreater(page.evaluate('measuredChunkReads'), 0)
        self.check_event_widths(page)
        page.evaluate('''() => {
            fgSetTextEventsVisible(true);
            const chunk=document.querySelector('#content > .text-chunk');
            const note=document.createElement('div'); note.textContent='custom context '.repeat(100);
            chunk.appendChild(note); fgRefreshTextLayout([chunk]);
            measuredChunkReads=0; fgSetTextEventsVisible(false);
        }''')
        self.assertGreater(page.evaluate('measuredChunkReads'), 0)
        self.check_event_widths(page)

    def test_mounted_filter_measures_only_changed_chunks_until_layout_context_changes(self):
        page = self.open_report(filename='event-widths.html')
        self.observe_width_reads(page)
        result = page.evaluate('''() => {
            const root = document.getElementById('content');
            const input = document.getElementById('filterInput');
            input.value = 'sort:desc'; applyFilter();
            measuredChunkReads = 0; applyFilter();
            const repeatSortReads = measuredChunkReads;
            clearFilter();
            const chunks = Array.from(root.querySelectorAll('.text-chunk'));
            const rowSnapshot = () => chunks.map(chunk => Array.from(chunk.querySelectorAll('.line-container'),
                row => [row.id, row.style.display]));
            const before = rowSnapshot();
            input.value = 'NOT func:^wide_leaf$';
            measuredChunkReads = 0; applyFilter();
            const filtered = rowSnapshot();
            const changed = filtered.filter((rows, index) => JSON.stringify(rows) !== JSON.stringify(before[index])).length;
            const changedReads = measuredChunkReads;
            measuredChunkReads = 0; applyFilter();
            const repeatFilterReads = measuredChunkReads;
            const beforeFont = root.querySelector('.text-chunk').style.getPropertyValue('--fg-text-inline-size');
            root.style.setProperty('--fg-text-font-size', '17px');
            measuredChunkReads = 0; applyFilter();
            const fontChangedReads = measuredChunkReads;
            const afterFont = root.querySelector('.text-chunk').style.getPropertyValue('--fg-text-inline-size');
            return {repeatSortReads, changed, changedReads, repeatFilterReads, fontChangedReads,
                chunks:chunks.length, widthsChanged:beforeFont !== afterFont};
        }''')
        self.assertEqual(result['changed'], 1)
        self.assertGreater(result['chunks'], result['changed'])
        self.assertEqual(result['repeatSortReads'], 0)
        self.assertEqual(result['changedReads'], result['changed'])
        self.assertEqual(result['repeatFilterReads'], 0)
        self.assertEqual(result['fontChangedReads'], result['chunks'])
        self.assertTrue(result['widthsChanged'])
        self.check_event_widths(page)

    def test_filter_controls_show_status_before_work_and_respect_composition(self):
        page = self.open_report(filename='text-only.html')
        result = page.evaluate('''async () => {
            const input = document.getElementById('filterInput');
            const status = document.getElementById('textFilterProgress');
            const viewport = document.getElementById('textViewport');
            const clear = document.querySelector('.clear-btn');
            const apply = document.querySelector('.filter-btn');
            const nextFrame = () => new Promise(resolve => requestAnimationFrame(resolve));
            const state = () => [status.textContent, viewport.getAttribute('aria-busy'), fgFilterState.text];
            input.value = 'sort:desc';
            input.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', isComposing:true, bubbles:true}));
            const composed = state();
            input.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true}));
            await nextFrame();
            const pending = state();
            await nextFrame();
            const applied = state();
            input.value = 'sort:asc';
            apply.click();
            clear.click();
            await nextFrame();
            const clearing = state();
            await nextFrame();
            const cleared = state();
            input.value = 'sort:desc';
            apply.click();
            input.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}));
            await nextFrame();
            await nextFrame();
            return {composed, pending, applied, clearing, cleared, cancelled:state()};
        }''')
        self.assertEqual(result, dict(composed=['', None, ''], pending=['Applying filter…', 'true', ''],
                                      applied=['', None, 'sort:desc'], clearing=['Clearing filter…', 'true', 'sort:desc'],
                                      cleared=['', None, ''], cancelled=['', None, '']))

    def test_sparse_filters_project_only_previous_and_next_hits(self):
        page = self.open_report(filename='event-widths.html')
        result = page.evaluate('''() => {
            const input = document.getElementById('filterInput');
            const rows = Array.from(document.querySelectorAll('#content .line-container'));
            const setVisible = fgTextNavigation.setVisible;
            let visits = 0;
            fgTextNavigation.setVisible = function(...args) { visits++; return setVisible.apply(this, args); };
            const apply = query => {
                input.value = query;
                visits = 0;
                applyFilter();
                const visible = rows.filter(row => row.style.display !== 'none').map(row => +row.dataset.lineNumber);
                const navigable = fgTextNavigation.visible.map(index => fgTextNavigation.order[index]);
                return {visits, visible, match: JSON.stringify(visible) === JSON.stringify(navigable)};
            };
            const first = apply('func:^wide_leaf$');
            const root = apply('func:^root$');
            const empty = apply('func:^not_in_trace$');
            const recovered = apply('func:^wide_leaf$');
            const block = fgGetTextFolding().blocks.keys().next().value;
            fgSetFoldStates([[block, true]]);
            const folded = apply('func:^wide_leaf$');
            fgSetFoldStates([[block, false]]);
            const unfiltered = apply('');
            const again = apply('func:^wide_leaf$');
            return {first, root, empty, recovered, folded, unfiltered, again, total: rows.length};
        }''')
        self.assertTrue(all(value['match'] for key, value in result.items() if key != 'total'))
        self.assertEqual(result['first']['visits'], result['total'])
        self.assertEqual(len(result['first']['visible']), 1)
        self.assertEqual(result['root']['visits'], 1 + len(result['root']['visible']))
        self.assertEqual(result['empty']['visits'], len(result['root']['visible']))
        self.assertEqual(result['empty']['visible'], [])
        self.assertEqual(result['recovered'], dict(visits=1, visible=result['first']['visible'], match=True))
        self.assertEqual(result['folded']['visits'], result['total'])
        self.assertEqual(result['folded']['visible'], [])
        self.assertEqual(len(result['unfiltered']['visible']), result['total'])
        self.assertEqual(result['again'], result['first'])

    def test_hidden_event_widths_follow_new_filter_before_events_return(self):
        page = self.open_report(filename='event-widths.html')
        self.observe_width_reads(page)
        result = page.evaluate('''() => {
            const input = document.getElementById('filterInput');
            fgSetTextEventsVisible(false);
            input.value = 'func:^wide_leaf$'; applyFilter();
            input.value = 'NOT func:^wide_leaf$'; applyFilter();
            const events = Array.from(document.querySelectorAll('[data-trace-event="1"]'));
            const stillHidden = events.every(row => row.style.display === 'none');
            fgSetTextEventsVisible(true);
            return {stillHidden, shown:events.filter(row => row.style.display !== 'none').length};
        }''')
        self.assertEqual(result, dict(stillHidden=True, shown=1400))
        self.check_event_widths(page)

    def test_clear_refreshes_changed_chunks_and_preserves_hidden_event_context(self):
        page = self.open_report(filename='event-widths.html')
        self.observe_width_reads(page)
        result = page.evaluate('''() => {
            const root = document.getElementById('content'), input = document.getElementById('filterInput');
            const widths = () => Array.from(root.children, chunk => chunk.style.getPropertyValue('--fg-text-inline-size'));
            const before = widths();
            input.value = 'NOT func:^wide_leaf$'; applyFilter();
            measuredChunkReads = 0; clearFilter();
            const clearReads = measuredChunkReads;
            const restoredWidths = JSON.stringify(widths()) === JSON.stringify(before);
            measuredChunkReads = 0; clearFilter();
            const repeatReads = measuredChunkReads;
            root.style.setProperty('--fg-text-font-size', '17px');
            measuredChunkReads = 0; clearFilter();
            const fontReads = measuredChunkReads;
            fgSetTextEventsVisible(false);
            input.value = 'NOT func:^wide_leaf$'; applyFilter();
            measuredChunkReads = 0; clearFilter();
            const hiddenEventReads = measuredChunkReads;
            fgSetTextEventsVisible(true);
            return {clearReads, repeatReads, fontReads, hiddenEventReads, restoredWidths, chunks:before.length};
        }''')
        self.assertEqual(result['clearReads'], 1)
        self.assertEqual(result['repeatReads'], 0)
        self.assertEqual(result['fontReads'], result['chunks'])
        self.assertEqual(result['hiddenEventReads'], result['chunks'])
        self.assertTrue(result['restoredWidths'])
        self.check_event_widths(page)

    def test_filter_skips_width_measurement_of_empty_chunks_without_losing_extents(self):
        page = self.open_report(filename='event-widths.html')
        self.observe_width_reads(page)
        result = page.evaluate('''() => {
            const root = document.getElementById('content'), input = document.getElementById('filterInput');
            const chunks = () => Array.from(root.querySelectorAll('.text-chunk'));
            root.style.setProperty('--fg-text-font-size', '17px');
            input.value = 'func:^wide_leaf$ sort:desc';
            measuredChunkReads = 0; applyFilter();
            const reads = measuredChunkReads;
            const widths = chunks().map(chunk => parseFloat(chunk.style.getPropertyValue('--fg-text-inline-size')));
            const rows = Array.from(root.querySelectorAll('.line-container'));
            const first = rows.find(row => row.style.display !== 'none');
            const firstMatches = first.dataset.func === 'wide_leaf' && +first.dataset.lineNumber === fgTextNavigation.order[0];
            input.value = 'func:^not_in_trace$';
            measuredChunkReads = 0; applyFilter();
            const noMatchReads = measuredChunkReads;
            const noMatchWidths = chunks().every(chunk => chunk.style.getPropertyValue('--fg-text-inline-size') === '0px');
            const last = chunks().at(-1);
            const note = document.createElement('div');
            note.textContent = 'Additional context '.repeat(250);
            last.appendChild(note);
            measuredChunkReads = 0; fgRefreshTextLayout([last]);
            const extraReads = measuredChunkReads;
            last.classList.add('text-layout-measure');
            const nativeWidth = last.getBoundingClientRect().width;
            last.classList.remove('text-layout-measure');
            return {reads, fullCount:widths.length, positiveWidths:widths.filter(width => width > 0).length,
                firstMatches, noMatchReads, noMatchWidths, extraReads, nativeWidth,
                extraWidth:parseFloat(last.style.getPropertyValue('--fg-text-inline-size'))};
        }''')
        self.assertGreater(result['fullCount'], 1)
        self.assertEqual(result['positiveWidths'], 1)
        self.assertEqual(result['reads'], 1)
        self.assertTrue(result['firstMatches'] and result['noMatchWidths'])
        self.assertEqual(result['noMatchReads'], 0)
        self.assertEqual(result['extraReads'], 1)
        self.assertGreater(result['extraWidth'], 0)
        self.assertAlmostEqual(result['extraWidth'], result['nativeWidth'], delta=1 / 64)

    def test_duration_order_reuses_native_row_widths_until_the_context_changes(self):
        for width, touch in ((1500, False), (390, True)):
            with self.subTest(width=width, touch=touch):
                page = self.open_report(width=width, touch=touch, filename='event-widths.html')
                self.observe_width_reads(page)
                result = page.evaluate('''() => {
                    const root = document.getElementById('content'), input = document.getElementById('filterInput');
                    const chunks = () => Array.from(root.children).filter(chunk => chunk.classList.contains('text-chunk'));
                    const apply = query => { input.value = query; measuredChunkReads = 0; applyFilter(); return measuredChunkReads; };
                    const matchNative = () => {
                        const targets = chunks();
                        const widths = targets.map(chunk => parseFloat(chunk.style.getPropertyValue('--fg-text-inline-size')));
                        for (const chunk of targets) chunk.classList.add('text-layout-measure');
                        try { return targets.every((chunk, index) => Math.abs(chunk.getBoundingClientRect().width - widths[index]) <= 1 / 64); }
                        finally { for (const chunk of targets) chunk.classList.remove('text-layout-measure'); }
                    };
                    const first = apply('sort:desc'), exactFirst = matchNative();
                    const filtered = apply('func:^wide_leaf$ sort:asc'), exactFiltered = matchNative();
                    root.style.setProperty('--fg-text-font-size', '17px');
                    const changed = apply('sort:desc'), exactChanged = matchNative();
                    return {first, filtered, changed, exactFirst, exactFiltered, exactChanged, chunks:chunks().length};
                }''')
                self.assertEqual((result['first'], result['filtered']), (0, 0))
                self.assertEqual(result['changed'], result['chunks'])
                self.assertTrue(result['exactFirst'] and result['exactFiltered'] and result['exactChanged'])
                self.check_event_widths(page)
                page.close()

    def test_clearing_duration_sort_reuses_matching_order_widths_only(self):
        for mode, width in (('time', 1500), ('task', 390)):
            with self.subTest(mode=mode, width=width):
                page = self.open_report(width=width, filename='event-widths.html')
                self.observe_width_reads(page)
                result = page.evaluate('''mode => {
                    const root = document.getElementById('content'), input = document.getElementById('filterInput');
                    fgSetTextOrder(mode);
                    const styles = () => Array.from(root.querySelectorAll('.text-chunk'), chunk => chunk.style.cssText);
                    const before = styles();
                    const order = fgTextNavigation.order.slice();
                    const sorted = () => { input.value = 'duration:>0 sort:desc'; applyFilter(); };
                    sorted();
                    measuredChunkReads = 0; clearFilter();
                    const reusedReads = measuredChunkReads;
                    const sameStyles = JSON.stringify(styles()) === JSON.stringify(before);
                    const rows = Array.from(root.querySelectorAll('.line-container'), row => +row.dataset.lineNumber);
                    const sameOrder = JSON.stringify(rows) === JSON.stringify(order) &&
                        JSON.stringify(fgTextNavigation.order) === JSON.stringify(order);
                    sorted();
                    root.style.setProperty('--fg-text-font-size', '17px');
                    measuredChunkReads = 0; clearFilter();
                    return {reusedReads, fallbackReads:measuredChunkReads, count:before.length,
                        sameStyles, sameOrder, widthsChanged:JSON.stringify(styles()) !== JSON.stringify(before)};
                }''', mode)
                self.assertEqual(result['reusedReads'], 0)
                self.assertEqual(result['fallbackReads'], result['count'])
                self.assertTrue(result['sameStyles'] and result['sameOrder'] and result['widthsChanged'])
                self.check_event_widths(page)
                page.close()

    def test_only_large_supported_inline_reports_enable_native_layout(self):
        for count in (4095, 4096):
            with self.subTest(count=count):
                page = self.open_report(filename='boundary-' + str(count) + '.html')
                self.assertEqual(page.locator('script[data-inline-text="true"]').count(), int(count >= 4096))
                self.assertEqual(page.evaluate('document.getElementById("content").classList.contains("text-layout-auto")'), count >= 4096)
                self.assertEqual(page.locator('#content .line-container').count(), count)
                page.close()
        for capability in ('content-visibility: auto',
                           'contain-intrinsic-block-size: calc(2 * (max(13px * 1.5 + 4px, 21px) + 2px))',
                           'selector(:has(*))'):
            with self.subTest(capability=capability):
                page = self.open_report(unsupported=capability)
                self.assertFalse(page.evaluate('document.getElementById("content").classList.contains("text-layout-auto")'))
                self.assertEqual(page.locator('#content .line-container').count(), 4203)
                page.close()

    def test_offscreen_long_rows_keep_exact_extents_across_state_changes(self):
        for width, theme in ((390, 'light'), (1500, 'dark')):
            with self.subTest(width=width, theme=theme):
                page = self.open_report(width, theme)
                self.compare_layout(page, 'cold far widest row')
                page.evaluate('''() => {
                    window.savedRows = Array.from(document.querySelectorAll('#content .line-container'));
                    const row = document.getElementById('L4101');
                    toggleExpand(row.dataset.lineId);
                }''')
                page.wait_for_function('document.getElementById("L4101").nextElementSibling.querySelector(".source-line")')
                self.compare_layout(page, 'offscreen source open')
                page.evaluate('toggleExpand(document.getElementById("L4101").dataset.lineId)')
                self.compare_layout(page, 'source closed')
                for query in ('func:^root$', 'func:^leaf$ sort:desc', ''):
                    page.evaluate('query => { document.getElementById("filterInput").value=query; applyFilter(); }', query)
                    self.compare_layout(page, 'filter ' + query)
                for mode in ('task', 'time'):
                    page.evaluate('mode => fgSetTextOrder(mode)', mode)
                    self.compare_layout(page, 'order ' + mode)
                page.evaluate('''() => {
                    window.rootFold = Array.from(fgGetTextFolding().blocks.values()).find(block =>
                        block.entry?.getAttribute('data-func') === 'root').id;
                    toggleFold(rootFold);
                }''')
                self.compare_layout(page, 'folded')
                page.evaluate('toggleFold(rootFold)')
                self.compare_layout(page, 'unfolded')
                self.assertTrue(page.evaluate('''() => savedRows.every(row => row.isConnected && document.getElementById(row.id) === row)'''))
                page.set_viewport_size({'width': 1500 if width == 390 else 390, 'height': 900})
                self.compare_layout(page, 'responsive font')
                page.emulate_media(media='print')
                self.assertEqual(page.evaluate('Array.from(document.querySelectorAll("#content > .text-chunk"), chunk => getComputedStyle(chunk).contentVisibility)'), ['visible'] * 9)
                page.close()

    def test_roundtrip_keeps_sources_selection_positions_and_complete_timeline(self):
        page = self.open_report()
        result = page.evaluate('''async () => {
            const settle = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const content = document.getElementById('content');
            const containment = () => [getComputedStyle(content).contain, getComputedStyle(content).contentVisibility];
            const visible = containment(), states = [];
            const row = document.getElementById('L3003');
            row.scrollIntoView({block:'center', behavior:'instant'});
            toggleExpand(row.dataset.lineId);
            await fgFill(row.nextElementSibling);
            await settle();
            const viewport = document.getElementById('textViewport');
            viewport.scrollLeft = 500;
            const start = document.getElementById('L511').querySelector('.line-content').firstChild;
            const end = document.getElementById('L514').querySelector('.line-content').lastChild;
            const range = document.createRange();
            range.setStartBefore(start); range.setEndAfter(end);
            const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
            const before = {top:scrollY, left:viewport.scrollLeft, selection:selection.toString(),
                source:row.nextElementSibling.innerHTML, data:JSON.stringify(fgData.read('tlData'))};
            const originalRow = row;
            const originalMeasure = fgMeasureTextLayout;
            let measurements = 0;
            fgMeasureTextLayout = (...parameters) => { measurements++; return originalMeasure(...parameters); };
            try {
                for (let iteration=0; iteration<3; iteration++) {
                    await fgSwitchView('timeline'); await settle();
                    states.push(containment());
                    if (selection.rangeCount) throw new Error('Hidden Text kept active native selection');
                    await fgSwitchView('text'); await settle();
                    states.push(containment());
                    if (selection.toString() !== before.selection) throw new Error('Lost complete cross-chunk selection');
                }
            } finally { fgMeasureTextLayout = originalMeasure; }
            return {before, after:{top:scrollY, left:viewport.scrollLeft, selection:selection.toString(),
                source:row.nextElementSibling.innerHTML, data:JSON.stringify(fgData.read('tlData'))},
                same:document.getElementById(row.id)===originalRow, measurements, visible, states};
        }''')
        self.assertTrue(result['before']['selection'].strip())
        self.assertEqual(result['before'], result['after'])
        self.assertTrue(result['same'])
        self.assertEqual(result['measurements'], 0)
        self.assertEqual(result['visible'], ['style', 'visible'])
        self.assertEqual(result['states'], [['style', 'hidden'], ['style', 'visible']] * 3)

    def test_native_drag_selection_and_find_reveal_complete_offscreen_text(self):
        for theme in ('light', 'dark'):
            with self.subTest(theme=theme):
                page = self.open_report(theme=theme)
                selections = []
                for force_full in (False, True):
                    if force_full:
                        page.add_style_tag(content='#content > .text-chunk {content-visibility:visible !important}')
                    page.locator('#L512').evaluate('row => row.scrollIntoView({block:"center", behavior:"instant"})')
                    page.evaluate('''async () => {
                        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                        getSelection().removeAllRanges();
                    }''')
                    points = page.evaluate('''() => ['L512', 'L513'].map((identifier, index) => {
                        const walker = document.createTreeWalker(document.getElementById(identifier).querySelector('.line-content'), NodeFilter.SHOW_TEXT);
                        const nodes = [];
                        while (walker.nextNode()) nodes.push(walker.currentNode);
                        const node = index ? nodes[nodes.length - 1] : nodes[0];
                        const range = document.createRange();
                        range.setStart(node, index ? node.textContent.length : 0);
                        range.collapse(true);
                        const bounds = range.getBoundingClientRect();
                        return [bounds.x, bounds.y + bounds.height / 2];
                    })''')
                    page.mouse.move(*points[0])
                    page.mouse.down()
                    page.mouse.move(*points[1], steps=12)
                    page.mouse.up()
                    selections.append(page.evaluate('getSelection().toString()'))
                self.assertTrue(selections[0].strip())
                self.assertEqual(selections[0], selections[1])
                page.close()
                page = self.open_report(theme=theme)
                found = page.evaluate('''async needle => {
                    const found = window.find(needle);
                    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    const selection = getSelection();
                    const row = document.getElementById('L3003');
                    const bounds = row.getBoundingClientRect();
                    return {found, text:selection.toString(), contained:row.contains(selection.anchorNode),
                        visible:bounds.top < innerHeight && bounds.bottom > 0};
                }''', 'value=' + self.argument[:32])
                self.assertTrue(found['found'])
                self.assertTrue(found['contained'])
                self.assertTrue(found['visible'])
                self.assertEqual(found['text'], 'value=' + self.argument[:32])
                page.close()

    def test_saved_timeline_startup_and_hidden_resizes_keep_exact_widths(self):
        page = self.open_report()
        page.evaluate('lsSet("view", "timeline")')
        page.reload(wait_until='load')
        self.assertEqual(page.evaluate('typeof fgInlineTextStartup'), 'undefined')
        self.assertFalse(page.locator('#textStartupStatus').is_visible())
        self.assertEqual(page.evaluate('fgCurrentView()'), 'timeline')
        self.assertGreater(page.evaluate('fgTextLayoutPending.size'), 0)
        self.assertTrue(page.evaluate('''Array.from(document.querySelectorAll('#content > .text-chunk')).every(chunk =>
            chunk.style.getPropertyValue('--fg-text-inline-size') === '')'''))
        page.evaluate('async () => await fgSwitchView("text")')
        self.assertEqual(page.evaluate('fgTextLayoutPending.size'), 0)
        self.assertTrue(page.evaluate('''Array.from(document.querySelectorAll('#content > .text-chunk')).every(chunk =>
            parseFloat(chunk.style.getPropertyValue('--fg-text-inline-size')) > 0)'''))
        self.compare_layout(page, 'initial Timeline')
        for width in (390, 1500):
            page.evaluate('async () => await fgSwitchView("timeline")')
            page.set_viewport_size({'width': width, 'height': 900})
            page.wait_for_timeout(100)
            page.evaluate('async () => await fgSwitchView("text")')
            self.compare_layout(page, 'hidden resize ' + str(width))

    def test_large_mixed_inline_text_keeps_spaces_flags_sources_and_call_boundaries(self):
        with (Path(helpers.ROOT) / 'samples/sample_mixed.jsonl').open() as source:
            metadata, records = uftrace.read_export(source)
        span = records[-1]['time_ns'] - records[0]['time_ns'] + 1000000
        trace = Path(self.kernel.dir) / 'mixed-layout.jsonl'
        with trace.open('w') as destination:
            destination.write(json.dumps(metadata) + '\n')
            for repetition in range(4):
                for record in records:
                    destination.write(json.dumps(dict(record, time_ns=record['time_ns'] + repetition * span)) + '\n')
            destination.write(json.dumps(dict(type='end', complete=True)) + '\n')
        self.kernel.run(str(trace), '--timeline', '--enable-fold', '--filter',
                        '--user-src', str(Path(helpers.ROOT) / 'samples'), out='mixed-layout.html')
        page = self.open_report(filename='mixed-layout.html')
        self.compare_layout(page, 'mixed cold geometry')
        result = page.evaluate('''async () => {
            const root = document.getElementById('content');
            const rows = Array.from(root.querySelectorAll('.line-container'));
            const original = JSON.stringify(fgData.read('tlData'));
            document.getElementById('filterInput').value = 'space:kernel AND irqs:^d$';
            applyFilter();
            const visible = rows.filter(row => row.style.display !== 'none');
            const filtered = {count:visible.length, spaces:Array.from(new Set(visible.map(row => row.dataset.space))),
                irqs:Array.from(new Set(visible.map(row => row.dataset.irqs))).sort()};
            clearFilter();
            const user = root.querySelector('.line-container.expandable[data-space=user]');
            toggleExpand(user.dataset.lineId);
            await fgFill(user.nextElementSibling);
            await fgSwitchView('timeline');
            await fgSwitchView('text');
            return {filtered, enabled:root.classList.contains('text-layout-measured'), rows:rows.length,
                records:fgGetTextFolding().records.length,
                spaces:Array.from(new Set(rows.map(row => row.dataset.space).filter(Boolean))).sort(),
                boundaries:root.querySelectorAll('[data-boundary=user-kernel]').length,
                same:original===JSON.stringify(fgData.read('tlData')) && rows.every(row => row.isConnected),
                source:user.nextElementSibling.textContent, sourceLayout:getComputedStyle(user.parentElement).contentVisibility};
        }''')
        self.assertTrue(result['enabled'])
        self.assertGreater(result['rows'], 4096)
        self.assertEqual(result['rows'], result['records'])
        self.assertEqual(result['spaces'], ['kernel', 'user'])
        self.assertGreater(result['filtered']['count'], 0)
        self.assertEqual(result['filtered']['spaces'], ['kernel'])
        self.assertEqual(result['filtered']['irqs'], ['D', 'd'])
        self.assertGreater(result['boundaries'], 0)
        self.assertTrue(result['same'])
        self.assertIn('demo_mixed.c', result['source'])
        self.assertEqual(result['sourceLayout'], 'visible')
        self.compare_layout(page, 'mixed source geometry')
