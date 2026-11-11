"""Semantic detail colors preserve captured text, theme contrast and layout."""

from html.parser import HTMLParser
from itertools import product
import json
from pathlib import Path
import shutil
import subprocess
import threading
import unittest

import helpers
from fglib.viewer import create_server
from test_btf import BtfBuilder, make_elf
from test_uftrace_details import arguments, callsite
from test_uftrace_import import call, capture, stream

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


class Tokens(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.text = ''
        self.spans = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        if tag != 'span':
            raise AssertionError('unexpected generated element: ' + tag)
        self.current = [dict(attributes)['class'], '']
        self.spans.append(self.current)

    def handle_endtag(self, tag):
        self.current = None

    def handle_data(self, data):
        self.text += data
        if self.current is not None:
            self.current[1] += data


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class DetailTokenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(helpers.ROOT) / 'assets' / 'timeline'
        panels = (root / 'panels.js').read_text()
        model = (root / 'model.js').read_text()
        cls.script = (model[model.index('function esc('):model.index('function nameOf(')] +
                      panels[panels.index('function detailToken('):panels.index('const PROCESS_PAGE')])

    def render(self, values, symbol=False, parameters=True):
        script = self.script + '''
            const request = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
            console.log(JSON.stringify(request.values.map(value => request.symbol ?
                detailSymbolHtml(value) : detailValueHtml(value, request.parameters))));
        '''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=20,
                                input=json.dumps(dict(values=values, symbol=symbol, parameters=parameters)))
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        for value, html in zip(values, output):
            self.assertEqual(Tokens(html).text, value)
        return [Tokens(html) for html in output]

    def test_named_values_separate_names_literals_and_keywords(self):
        tokens = self.render(['count=-3, buffer=0xffff1234, enabled=true, value=NULL'])[0]
        self.assertEqual(tokens.spans, [['tl-code-parameter', 'count'], ['tl-code-number', '-3'],
            ['tl-code-parameter', 'buffer'], ['tl-code-number', '0xffff1234'],
            ['tl-code-parameter', 'enabled'], ['tl-code-keyword', 'true'],
            ['tl-code-parameter', 'value'], ['tl-code-keyword', 'NULL']])

    def test_quoted_commas_assignments_and_escapes_stay_inside_the_string(self):
        value = r'''path="a,count=3,flag=false,\"quoted\"", letter='x', flags=0x1'''
        tokens = self.render([value])[0]
        self.assertEqual([text for kind, text in tokens.spans if kind == 'tl-code-parameter'],
                         ['path', 'letter', 'flags'])
        self.assertEqual([text for kind, text in tokens.spans if kind == 'tl-code-string'],
                         [r'''"a,count=3,flag=false,\"quoted\""''', "'x'"])

    def test_literals_are_not_reformatted_or_classified_as_errors(self):
        values = ['0xFFFFFFFFFFFFFFEA', '-22', '0', 'true', 'false', 'NULL', '1.25e-3',
                  '18446744073709551615', '0x1234(trunc)', 'no parameters', 'opaque_field7']
        for tokens in self.render(values, parameters=False):
            self.assertNotIn('tl-code-error', [kind for kind, text in tokens.spans])
        self.assertEqual(self.render(['unknown=3'], parameters=False)[0].spans, [['tl-code-number', '3']])

    def test_symbols_keep_cpp_names_and_exact_offset_text(self):
        values = ['std::vector<int>::push_back', 'operator++', 'caller+0x33/0x80 [module]',
                  '0x1234 (symbol unavailable)', 'work<int>']
        results = self.render(values, symbol=True)
        self.assertEqual(results[2].spans, [['tl-code-symbol', 'caller'], ['tl-code-offset', '+0x33/0x80']])
        self.assertEqual(results[3].spans, [['tl-code-raw', '0x1234']])
        self.assertEqual(results[4].spans, [['tl-code-symbol', 'work<int>']])

    def test_untrusted_names_strings_and_symbols_only_produce_escaped_spans(self):
        values = ['count<script>window.argNameExecuted=1</script>=3',
                  'name="<img src=x onerror=alert(1)>&"', "'onfocus=alert(1)'", '<>&"\'']
        self.render(values)
        self.render(values, symbol=True)


def contrast(first, second):
    def luminance(channels):
        linear = [channel / 255 / 12.92 if channel / 255 <= .04045 else
                  ((channel / 255 + .055) / 1.055) ** 2.4 for channel in channels]
        return sum(weight * channel for weight, channel in zip((.2126, .7152, .0722), linear))
    low, high = sorted((luminance(first), luminance(second)))
    return (high + .05) / (low + .05)


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class DetailColorBrowserTest(unittest.TestCase):
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
        builder = BtfBuilder()
        arguments(builder, 'inner', [('fd', 'int'), ('buffer', 'ptr'), ('length', 'wide'), ('enabled', 'bool')])
        Path(cls.kernel.vmlinux).write_bytes(make_elf({'.BTF': builder.blob()}))
        records = capture()
        records[1].update(user_args=['3', '0xffffffffffffffff', '1'],
                          user_arg_names=['count', None, 'enabled'], payload_hex='03000000ffffffffffffffff01')
        records[2].update(user_retval='0', payload_hex='00')
        records[5]['args'] = ['0x3', '0xffff1234', '0x200', '0x1']
        site = callsite(source={'file': 'demo.c', 'line': 5})
        site['user_callsite']['symbol'] = {'address': '0x1200', 'offset': 0x33, 'size': 0x80}
        records.insert(1, site)
        records.extend([call('entry', 'user', 1000, 0, 'legacy', 0x55, payload_hex='deadbeef12345678'),
                        call('exit', 'user', 1100, 0, 'legacy', 0x55)])
        trace = Path(cls.kernel.dir) / 'capture.jsonl'
        trace.write_text(stream(records))
        cls.urls = {}
        for mode in ('single', 'sidecar'):
            for name, source, highlighted in (('mixed', str(trace), True),
                                               ('ftrace', helpers.fixture('vm72_full'), True),
                                               ('plain', helpers.fixture('vm72_full'), False)):
                destination = name + '-' + mode + ('.html' if mode == 'single' else '')
                options = ['--no-config', '--timeline', '--enable-fold', '--report-mode', mode,
                           '--base-url', 'https://source.test/linux']
                if highlighted:
                    options.append('--highlight-code')
                cls.kernel.run(source, *options, out=destination)
                report = Path(cls.kernel.dir) / destination
                if mode == 'sidecar':
                    server = create_server(report)
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    cls.addClassCleanup(thread.join, 5)
                    cls.addClassCleanup(server.server_close)
                    cls.addClassCleanup(server.shutdown)
                    cls.urls[name, mode] = server.url
                else:
                    cls.urls[name, mode] = report.as_uri()

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 960})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open(self, theme='light', mode='single', kind='mixed', name='work<int>'):
        self.page.add_init_script(f"localStorage.setItem('theme', '{theme}'); localStorage.setItem('fgTl.detailFrac', '0.65')")
        self.page.goto(self.urls[kind, mode])
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.select(name)

    def select(self, name):
        self.page.evaluate('''name => {
            const model = fgView('timeline').probe();
            model.select(Array.from({length:model.N}, (_, index) => index).find(index => model.nameOf(index) === name));
        }''', name)

    def value(self, label):
        return self.page.locator('.tl-kv .k').filter(has_text=label).locator('xpath=following-sibling::span[1]')

    def test_user_kernel_and_raw_details_share_semantics_in_both_delivery_modes(self):
        for theme, mode in product(('light', 'dark'), ('single', 'sidecar')):
            with self.subTest(theme=theme, mode=mode):
                self.open(theme, mode)
                args = self.value('user args')
                self.assertEqual(args.locator('.tl-code-parameter').all_text_contents(), ['count', 'slot2', 'enabled'])
                self.assertEqual(args.locator('.tl-code-number').all_text_contents(), ['3', '0xffffffffffffffff', '1'])
                self.assertEqual(args.locator('.tl-code-keyword').count(), 0)
                self.assertEqual(self.value('user return').locator('.tl-code-number').inner_text(), '0')
                self.assertEqual(self.value('caller site').locator('.tl-code-symbol').inner_text(), 'main')
                self.assertEqual(self.value('caller site').locator('.tl-code-offset').inner_text(), '+0x33/0x80')
                self.assertFalse(args.locator('details').evaluate('element => element.open'))
                args.locator('summary').click()
                self.assertEqual(args.locator('.tl-code-raw').inner_text(), '03000000ffffffffffffffff01')
                self.select('inner')
                self.assertEqual(self.value('args (BTF)').locator('.tl-code-parameter').all_text_contents(),
                                 ['fd', 'buffer', 'length', 'enabled'])
                self.assertEqual(self.value('args (BTF)').locator('.tl-code-number').all_text_contents(),
                                 ['3', '0xffff1234', '0x200', '0x1'])
                self.assertEqual(self.page.locator('.tl-context-value').first.evaluate('element => getComputedStyle(element).color'),
                                 self.page.locator('.tl-code-raw').first.evaluate('element => getComputedStyle(element).color'))
                self.assertTrue(self.page.locator('.tl-context-row[data-space-boundary]').count())
                error = self.page.locator('.tl-kv .v.err')
                self.assertTrue(error.count())
                self.assertEqual(error.locator('.tl-code-number').evaluate('element => getComputedStyle(element).color'),
                                 error.evaluate('element => getComputedStyle(element).color'))
                self.select('legacy')
                raw = self.value('raw argument bytes')
                self.assertEqual(raw.locator('.tl-code-raw').inner_text(), 'deadbeef12345678')
                self.assertEqual(raw.locator('.tl-code-number, .tl-code-parameter').count(), 0)

    def test_source_palette_is_scoped_to_enabled_detail_highlighting(self):
        for theme, mode in product(('light', 'dark'), ('single', 'sidecar')):
            with self.subTest(theme=theme, mode=mode):
                self.open(theme, mode, 'ftrace', 'ksys_dup3')
                self.page.locator('.tl-cs-src .c-keyword').first.wait_for(state='attached')
                colors = self.page.evaluate('''() => {
                    const detail = document.querySelector('.tl-detail');
                    const source = detail.querySelector('.c-keyword');
                    const marker = document.createElement('span');
                    marker.className = 'c-keyword';
                    document.querySelector('#content').append(marker);
                    const colors = [getComputedStyle(source).color, getComputedStyle(marker).color,
                        getComputedStyle(detail).getPropertyValue('--tl-code-literal').trim()];
                    marker.remove();
                    return colors;
                }''')
                self.assertNotEqual(colors[0], colors[1])
                self.assertEqual(self.page.locator('.tl-cs-src summary b').first.evaluate('element => getComputedStyle(element).color'),
                                 self.page.locator('.tl-detail-title .tl-code-symbol').evaluate('element => getComputedStyle(element).color'))
                self.open(theme, mode, 'plain', 'ksys_dup3')
                self.page.locator('.tl-cs-src .source-line').first.wait_for(state='attached')
                self.assertEqual(self.page.locator('.tl-cs-src .c-keyword').count(), 0)
                self.assertEqual(self.value('args').locator('.tl-code-parameter').all_text_contents(),
                                 ['oldfd', 'newfd', 'flags'])

    def test_semantic_colors_have_readable_contrast_on_panel_hover_and_source(self):
        self.open()
        palettes = []
        for theme in ('light', 'dark'):
            self.page.evaluate('theme => document.documentElement.dataset.theme = theme', theme)
            palettes.append(self.page.evaluate('''() => {
                const detail = document.querySelector('.tl-detail');
                const rgb = color => color.match(/[\\d.]+/g).slice(0, 3).map(Number);
                const foregrounds = {};
                for (const name of ['symbol', 'parameter', 'number', 'keyword', 'string', 'offset', 'raw', 'error']) {
                    const sample = document.createElement('span');
                    sample.className = 'tl-code-' + name;
                    detail.append(sample);
                    foregrounds[name] = rgb(getComputedStyle(sample).color);
                    sample.remove();
                }
                const backgrounds = {};
                for (const name of ['--container-bg', '--container-bg-hover', '--line-highlighted']) {
                    const sample = document.createElement('div');
                    sample.style.background = 'var(' + name + ')';
                    detail.append(sample);
                    backgrounds[name] = rgb(getComputedStyle(sample).backgroundColor);
                    sample.remove();
                }
                return {foregrounds, backgrounds};
            }'''))
        for theme, palette in zip(('light', 'dark'), palettes):
            for role, background in product(palette['foregrounds'], palette['backgrounds']):
                with self.subTest(theme=theme, role=role, background=background):
                    self.assertGreaterEqual(contrast(palette['foregrounds'][role], palette['backgrounds'][background]), 4.5)
        self.assertNotEqual(palettes[0]['foregrounds'], palettes[1]['foregrounds'])

    def test_theme_changes_and_navigation_do_not_modify_trace_data(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open(mode=mode)
                before = self.page.evaluate('JSON.stringify(fgData.read("tlData"))')
                for theme in ('dark', 'light'):
                    self.page.evaluate('theme => document.documentElement.dataset.theme = theme', theme)
                    self.select('inner')
                    self.select('work<int>')
                self.assertEqual(self.page.evaluate('JSON.stringify(fgData.read("tlData"))'), before)
                self.assertEqual(self.page.locator('.tl-child-preview-name').first.evaluate('element => getComputedStyle(element).color'),
                                 self.page.locator('.tl-detail-title .tl-code-symbol').evaluate('element => getComputedStyle(element).color'))

    def test_source_links_remain_discoverable_with_hover_and_keyboard_focus(self):
        self.open(kind='ftrace', name='ksys_dup3')
        link = self.page.locator('.tl-context-source a').first
        self.assertIn('underline', link.evaluate('element => getComputedStyle(element).textDecorationLine'))
        original = link.evaluate('element => getComputedStyle(element).color')
        link.focus()
        self.assertNotEqual(link.evaluate('element => getComputedStyle(element).color'), original)
        self.assertTrue(link.evaluate('element => element.matches(":focus-visible")'))
        self.page.locator('.tl-detail-title').click()
        link.hover()
        self.assertNotEqual(link.evaluate('element => getComputedStyle(element).color'), original)

    def test_narrow_touch_details_preserve_width_text_and_selection(self):
        self.context.close()
        for theme, mode in product(('light', 'dark'), ('single', 'sidecar')):
            with self.subTest(theme=theme, mode=mode):
                self.context = self.browser.new_context(viewport={'width': 390, 'height': 844},
                                                        is_mobile=True, has_touch=True)
                self.addCleanup(self.context.close)
                self.page = self.context.new_page()
                self.page.set_default_timeout(5000)
                self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                self.open(theme, mode)
                self.assertIn('count=3, slot2=0xffffffffffffffff, enabled=1', self.value('user args').inner_text())
                self.assertEqual(self.page.locator('.tl-detail-title .tl-code-symbol').inner_text(), 'work<int>')
                self.assertTrue(self.page.evaluate('''() => {
                    const root = document.querySelector('#timelineView');
                    return root.scrollWidth <= root.clientWidth + 1;
                }'''))
                self.assertEqual(self.page.locator('.tl-detail').evaluate('element => getComputedStyle(element).userSelect'), 'text')
                self.context.close()


if __name__ == '__main__':
    unittest.main()
