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

    def open_report(self, width=1500, theme='light', filename='index.html', unsupported=None):
        context = self.browser.new_context(viewport={'width': width, 'height': 900})
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

    def test_only_large_supported_inline_reports_enable_native_layout(self):
        for count in (4095, 4096):
            with self.subTest(count=count):
                page = self.open_report(filename='boundary-' + str(count) + '.html')
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
                    if (selection.rangeCount) throw new Error('Hidden Text kept active native selection');
                    await fgSwitchView('text'); await settle();
                    if (selection.toString() !== before.selection) throw new Error('Lost complete cross-chunk selection');
                }
            } finally { fgMeasureTextLayout = originalMeasure; }
            return {before, after:{top:scrollY, left:viewport.scrollLeft, selection:selection.toString(),
                source:row.nextElementSibling.innerHTML, data:JSON.stringify(fgData.read('tlData'))},
                same:document.getElementById(row.id)===originalRow, measurements};
        }''')
        self.assertTrue(result['before']['selection'].strip())
        self.assertEqual(result['before'], result['after'])
        self.assertTrue(result['same'])
        self.assertEqual(result['measurements'], 0)

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
