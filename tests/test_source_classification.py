"""Source candidates, DWARF inlining and static tail jumps stay distinct."""

from itertools import product
from pathlib import Path
import shutil
import struct
import threading
import unittest

import helpers
from fglib.viewer import create_server
from test_tailcalls import TRACE, write_elf


class SourceClassificationBrowserTest(unittest.TestCase):
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
        cls.kernel = helpers.FakeKernel(inline_source=True)
        cls.addClassCleanup(cls.kernel.close)
        root = Path(cls.kernel.dir)
        trace = root / 'tail.txt'
        trace.write_text(TRACE)
        write_elf(cls.kernel.vmlinux, [('parent', 0x401000, b'\xe9' + struct.pack('<i', 0x100 - 5)),
                                      ('child', 0x401100, b'\xc3'), ('root', 0x401200, b'\x90' * 32)])
        tail_options = [False]
        try:
            import elftools
        except ImportError:
            pass
        else:
            if shutil.which('objdump'):
                tail_options.append(True)
        cls.reports = {}
        for inlined, with_tail, mode in product((False, True), tail_options, ('single', 'sidecar')):
            resolver = helpers.FAKE_INLINE_FADDR2LINE.replace('traced_outer', 'root')
            if not inlined:
                resolver = resolver.replace(' (inlined by) root', 'alternative')
            Path(cls.kernel.ksrc, 'scripts/faddr2line').write_text(resolver)
            destination = f'{inlined}-{with_tail}-{mode}' + ('.html' if mode == 'single' else '')
            extra = ['--tail-calls'] if with_tail else []
            cls.kernel.run(str(trace), '--no-config', '--no-btf', '--timeline', '--enable-fold',
                           '--report-mode', mode, '--base-url', 'https://source.invalid/src',
                           *extra, out=destination)
            path = root / destination
            if mode == 'sidecar':
                server = create_server(path)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                cls.addClassCleanup(server.server_close)
                cls.addClassCleanup(server.shutdown)
                url = server.url
            else:
                url = path.as_uri()
            cls.reports[inlined, with_tail, mode] = url

    def test_inline_evidence_is_independent_of_tail_analysis_and_source_delivery(self):
        for (inlined, with_tail, mode), url in self.reports.items():
            for legacy in (False, True):
                with self.subTest(inlined=inlined, with_tail=with_tail, mode=mode, legacy=legacy):
                    page = self.browser.new_page(viewport={'width': 1500, 'height': 900})
                    self.addCleanup(page.close)
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(url)
                    if legacy:
                        page.evaluate("async () => { await fgData.load('tlData'); delete fgData.read('tlData').sources; }")
                    page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().select(1); }")
                    source = page.locator('.tl-cs-src')
                    source.locator('.source-line').first.wait_for(state='attached')
                    self.assertEqual(source.locator('.src-frame').count(), 2 if inlined else 0)
                    self.assertEqual('(inlined)' in page.locator('.tl-cs-at').inner_text(), inlined)
                    inline_rows = page.locator('[data-context-list=stack] [data-context-kind=inline]').count()
                    self.assertEqual(inline_rows > 0, inlined)
                    if inlined:
                        self.assertEqual(source.locator('.src-tag.traced').inner_text(), 'function')
                    pills = page.locator('.tl-detail-head .tl-pill').all_text_contents()
                    label = 'tail jump (static)' if with_tail else 'caller mismatch'
                    self.assertIn(label, pills)
                    note = page.locator('.tl-detail-head .tl-pill', has_text=label).get_attribute('title')
                    self.assertIn('Static evidence' if with_tail else 'Cause unresolved', note)
                    self.assertEqual(page.evaluate("!!fgData.read('tlData').tail_calls"), with_tail)
                    self.assertEqual(page.evaluate("Object.keys(fgData.read('tlData').inline).length > 0"), inlined)
                    before = page.evaluate('''() => {
                        const model = fgView('timeline').probe();
                        return [JSON.stringify(fgData.read('tlData')), model.sel(), model.view()];
                    }''')
                    source.locator('.source-line').first.wait_for(state='attached')
                    page.locator('[data-fg-source-mode]').click()
                    self.assertEqual(source.locator('.src-frame').count(), 0)
                    page.locator('[data-fg-source-mode]').click()
                    self.assertEqual(source.locator('.src-frame').count(), 2 if inlined else 0)
                    after = page.evaluate('''() => {
                        const model = fgView('timeline').probe();
                        return [JSON.stringify(fgData.read('tlData')), model.sel(), model.view()];
                    }''')
                    self.assertEqual(after, before)
                    page.evaluate("async () => { await fgSwitchView('text'); }")
                    row = page.locator('.line-container[data-line-number="2"]')
                    text_source = page.locator('#' + row.get_attribute('data-line-id') + '_content')
                    row.click()
                    text_source.locator('.source-line').first.wait_for(state='attached')
                    self.assertEqual(text_source.locator('.src-frame').count(), 2 if inlined else 0)
                    self.assertEqual(text_source.locator('.source-line').count(), 5)
                    self.assertEqual(errors, [])
                    page.close()

    def test_multiple_candidates_keep_separate_chains_headers_links_and_source_lines(self):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.goto(next(iter(self.reports.values())))
        result = page.evaluate('''() => {
            const source = document.createElement('div');
            source.innerHTML = '<div class="location-link"><strong>candidate one:</strong></div>' +
                '<a class="location-link" href="https://source.invalid/one#L2">inner_one at one.c:2</a>' +
                '<div class="source-line">code one</div>' +
                '<a class="location-link" href="https://source.invalid/one#L3"> (inlined by) outer_one at one.c:3</a>' +
                '<div class="source-line">code two</div>' +
                '<div class="location-link"><strong>candidate two:</strong></div>' +
                '<a class="location-link" href="https://source.invalid/two#L4">inner_two at two.c:4</a>' +
                '<div class="source-line">code three</div>' +
                '<a class="location-link" href="https://source.invalid/two#L5"> (inlined by) outer_two at two.c:5</a>' +
                '<div class="source-line">code four</div>' +
                '<div class="location-link">independent at other.c:6</div>' +
                '<div class="source-line">code five</div><p>diagnostic retained</p>';
            fgChainView(source);
            const first = source.innerHTML;
            fgChainView(source);
            return {
                headers: Array.from(source.querySelectorAll('strong'), element => element.textContent),
                names: Array.from(source.querySelectorAll('.src-frame summary b'), element => element.textContent),
                indents: Array.from(source.querySelectorAll('.src-frame'), element => element.style.marginLeft),
                tags: Array.from(source.querySelectorAll('.src-tag'), element => element.textContent),
                lines: Array.from(source.querySelectorAll('.source-line'), element => element.textContent).sort(),
                links: Array.from(source.querySelectorAll('a'), element => element.href).sort(),
                other: source.querySelector('p').textContent,
                independent: Array.from(source.querySelectorAll(':scope > .location-link'), element => element.textContent),
                idempotent: source.innerHTML === first
            };
        }''')
        self.assertEqual(result['headers'], ['candidate one:', 'candidate two:'])
        self.assertEqual(result['names'], ['outer_one', 'inner_one', 'outer_two', 'inner_two'])
        self.assertEqual(result['indents'], ['0px', '14px', '0px', '14px'])
        self.assertEqual(result['tags'], ['function', 'inline', 'function', 'inline'])
        self.assertEqual(result['lines'], sorted(['code one', 'code two', 'code three', 'code four', 'code five']))
        self.assertEqual(result['links'], ['https://source.invalid/one#L2', 'https://source.invalid/one#L3',
                                           'https://source.invalid/two#L4', 'https://source.invalid/two#L5'])
        self.assertEqual(result['independent'], ['candidate one:', 'candidate two:', 'independent at other.c:6'])
        self.assertEqual(result['other'], 'diagnostic retained')
        self.assertTrue(result['idempotent'])

    def test_text_roundtrip_preserves_flat_candidate_order_and_bounds_node_reuse(self):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.goto(self.reports[True, False, 'single'])
        facts = page.evaluate('''() => {
            const panel = document.querySelector('#content .expanded-content[data-src]');
            panel.style.display = 'block';
            const chain = name => `<div class="location-link"><strong>${name}:</strong></div>\n` +
                `<a class="location-link" href="#${name}">${name}_inner at a.c:2</a>\n` +
                `<div class="source-line">${name} inner</div>\n` +
                `<a class="location-link" href="#${name}_outer">(inlined by) ${name}_outer at a.c:4</a>\n` +
                `<div class="source-line">${name} outer</div>\n`;
            const small = chain('first') + chain('second') + '<p>diagnostic</p>';
            const large = small + '<div class="source-line">more code</div>'.repeat(300);
            let html = small;
            let reads = 0;
            const sourceHtml = fgSourceHtml;
            fgSourceHtml = () => { reads++; return html; };
            try {
                fgToggleSrcMode();
                fgFill(panel, true);
                const original = [...panel.childNodes];
                const flatMarkup = panel.innerHTML;
                fgToggleSrcMode();
                const chainNames = [...panel.querySelectorAll('.src-frame summary b')].map(node => node.textContent);
                fgToggleSrcMode();
                const restored = panel.innerHTML === flatMarkup && panel.childNodes.length === original.length &&
                    original.every((node, index) => node === panel.childNodes[index]);
                const smallReads = reads;
                html = large;
                fgFill(panel, true);
                fgToggleSrcMode();
                const largeFrames = panel.querySelectorAll('.src-frame').length;
                fgToggleSrcMode();
                return {chainNames, restored, smallReads, largeFrames, largeReads: reads - smallReads,
                    largeLines: panel.querySelectorAll('.source-line').length};
            } finally { fgSourceHtml = sourceHtml; }
        }''')
        self.assertEqual(facts, dict(chainNames=['first_outer', 'first_inner', 'second_outer', 'second_inner'],
                                     restored=True, smallReads=1, largeFrames=4, largeReads=3, largeLines=304))

    def test_unmarked_positions_and_isolated_inline_markers_keep_the_original_markup(self):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.goto(next(iter(self.reports.values())))
        cases = [
            '<div class="location-link">only at file.c:1</div><div class="source-line">first</div>',
            '<div class="location-link">one at file.c:1</div><div class="source-line">first</div>'
            '<div class="location-link">two at other.c:2</div><div class="source-line">second</div>',
            '<div class="location-link">one at file.c:1</div><div class="source-line">first</div>'
            '<div class="location-link"><strong>another candidate:</strong></div>'
            '<div class="location-link">(inlined by) isolated at other.c:2</div>',
        ]
        for html in cases:
            with self.subTest(html=html):
                self.assertTrue(page.evaluate('''html => {
                    const source = document.createElement('div');
                    source.innerHTML = html;
                    fgChainView(source);
                    return source.innerHTML === html && !source.classList.contains('src-chain');
                }''', html))


if __name__ == '__main__':
    unittest.main()
