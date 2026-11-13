"""Readable detail hierarchy and parameter wrapping without changing trace data."""

from html.parser import HTMLParser
from itertools import product
import json
from pathlib import Path
import shutil
import subprocess
import unittest

import helpers
import test_detail_colors as detail_colors
from test_uftrace_import import call, stream


class ArgumentHTML(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.text = ''
        self.units = []
        self.depth = 0
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        if tag != 'span':
            raise AssertionError('unexpected argument markup: ' + tag)
        if dict(attributes).get('class') == 'tl-argument':
            self.current = len(self.units)
            self.units.append('')
        self.depth += 1

    def handle_endtag(self, tag):
        self.depth -= 1
        if not self.depth:
            self.current = None

    def handle_data(self, data):
        self.text += data
        if self.current is not None:
            self.units[self.current] += data


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class ArgumentUnitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(helpers.ROOT) / 'assets' / 'timeline'
        panels = (root / 'panels.js').read_text()
        model = (root / 'model.js').read_text()
        cls.script = (model[model.index('function esc('):model.index('function nameOf(')] +
                      panels[panels.index('function detailToken('):panels.index('const PROCESS_PAGE')])

    def render(self, *cases):
        script = self.script + '''
            const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
            console.log(JSON.stringify(input.map(({value, names}) => detailArgumentsHtml(value, names))));
        '''
        result = subprocess.run(['node', '-e', script], input=json.dumps(cases),
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = [ArgumentHTML(html) for html in json.loads(result.stdout)]
        for case, arguments in zip(cases, parsed):
            self.assertEqual(arguments.text, case['value'])
        return parsed

    def test_scalar_and_named_arguments_keep_separators_and_exact_values(self):
        values = ['3, -22, 0xffffffffffffffff', 'count=3, slot2=0xffffffffffffffff, enabled=1',
                  '  first=3,\n\tsecond=4  ', '', 'no parameters']
        results = self.render(*(dict(value=value) for value in values))
        self.assertEqual(results[0].units, ['3,', '-22,', '0xffffffffffffffff'])
        self.assertEqual(results[1].units, ['count=3,', 'slot2=0xffffffffffffffff,', 'enabled=1'])
        self.assertEqual(results[2].units, ['first=3,', 'second=4'])
        self.assertEqual(results[3].units, [])

    def test_quoted_and_nested_commas_are_not_parameter_boundaries(self):
        value = r'''path="a,b,\"c\"", pair=(1, 2), array=[3, 4], object={name=5, count=6}'''
        parsed = self.render(dict(value=value))[0]
        self.assertEqual(parsed.units, [r'''path="a,b,\"c\"",''', 'pair=(1, 2),',
                                        'array=[3, 4],', 'object={name=5, count=6}'])
        spaced = 'path="a  b,\t c\n  d", count=3'
        self.assertEqual(self.render(dict(value=spaced))[0].units,
                         ['path="a  b,\t c\n  d",', 'count=3'])

    def test_recorded_names_with_commas_quotes_and_markup_stay_one_unit(self):
        name = 'count, "quoted"=<script>window.argumentExecuted=1</script>'
        value = name + '=3, slot2=0xffffffffffffffff'
        parsed = self.render(dict(value=value, names=[name, None]))[0]
        self.assertEqual(parsed.units, [name + '=3,', 'slot2=0xffffffffffffffff'])

    def test_unbalanced_or_unclosed_values_are_kept_as_one_safe_unit(self):
        values = ['first=(1, 2, second=3', 'first="a,b, second=3', 'first=[1, 2), second=3']
        for value, parsed in zip(values, self.render(*(dict(value=value) for value in values))):
            self.assertEqual(parsed.units, [value])

    def test_incompatible_optional_names_do_not_reconstruct_or_change_values(self):
        value = 'count=3, flags=4'
        for parsed in self.render(*(dict(value=value, names=names)
                                    for names in ([], ['other', 'flags'], ['count'], [False, 'flags']))):
            self.assertEqual(parsed.units, ['count=3,', 'flags=4'])


@unittest.skipIf(detail_colors.sync_playwright is None, 'playwright not installed')
class DetailLayoutBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.addClassCleanup(detail_colors.DetailColorBrowserTest.doClassCleanups)
        detail_colors.DetailColorBrowserTest.setUpClass()
        cls.browser = detail_colors.DetailColorBrowserTest.browser
        cls.urls = detail_colors.DetailColorBrowserTest.urls
        kernel = detail_colors.DetailColorBrowserTest.kernel
        trace = Path(kernel.dir) / 'named-only.jsonl'
        trace.write_text(stream([call('entry', 'user', 100, 0, 'named_only', 1,
                                      user_args=['3'], user_arg_names=['count']),
                                 call('exit', 'user', 200, 0, 'named_only', 1, user_retval='0')]))
        kernel.run(str(trace), '--no-config', '--timeline', '--report-mode', 'single', out='named-only.html')
        cls.urls['named-only', 'single'] = (Path(kernel.dir) / 'named-only.html').as_uri()

    setUp = detail_colors.DetailColorBrowserTest.setUp
    tearDown = detail_colors.DetailColorBrowserTest.tearDown
    open = detail_colors.DetailColorBrowserTest.open
    select = detail_colors.DetailColorBrowserTest.select
    value = detail_colors.DetailColorBrowserTest.value

    def test_touch_parameters_wrap_as_complete_units_and_copy_exactly(self):
        self.context.close()
        for theme, mode, width in product(('light', 'dark'), ('single', 'sidecar'), (320, 390)):
            with self.subTest(theme=theme, mode=mode, width=width):
                with self.browser.new_context(viewport={'width': width, 'height': 844},
                                             is_mobile=True, has_touch=True) as context:
                    self.page = context.new_page()
                    self.page.set_default_timeout(5000)
                    self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                    self.open(theme, mode)
                    result = self.value('user args').evaluate('''element => {
                        const units = [...element.querySelectorAll('.tl-argument')];
                        const range = document.createRange();
                        range.setStartBefore(units[0]); range.setEndAfter(units.at(-1));
                        const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
                        const copied = selection.toString(); selection.removeAllRanges();
                        const bounds = element.getBoundingClientRect();
                        return {copied, units:units.map(unit => {
                            const rect = unit.getBoundingClientRect();
                            return {text:unit.textContent, left:rect.left - bounds.left,
                                right:rect.right - bounds.right, height:rect.height,
                                numberLines:unit.querySelector('.tl-code-number').getClientRects().length};
                        })};
                    }''')
                    self.assertEqual(result['copied'], 'count=3, slot2=0xffffffffffffffff, enabled=1')
                    self.assertEqual([unit['text'] for unit in result['units']],
                                     ['count=3,', 'slot2=0xffffffffffffffff,', 'enabled=1'])
                    for unit in result['units']:
                        self.assertGreaterEqual(unit['left'], -1)
                        self.assertLessEqual(unit['right'], 1)
                        self.assertEqual(unit['numberLines'], 1)
                    summary = self.page.locator('[data-trace-metadata] > summary')
                    self.assertGreaterEqual(summary.bounding_box()['height'], 44)
                    self.assertTrue(self.value('recorded address').is_visible())
                    summary.click()
                    self.assertFalse(self.value('recorded address').is_visible())
                    summary.click()
                    self.assertTrue(self.value('recorded address').is_visible())
                    self.assertTrue(self.page.evaluate('''() => {
                        const root = document.querySelector('#timelineView');
                        return root.scrollWidth <= root.clientWidth + 1;
                    }'''))

    def test_primary_facts_prioritize_calls_values_and_visible_errors(self):
        for theme, mode in product(('light', 'dark'), ('single', 'sidecar')):
            with self.subTest(theme=theme, mode=mode):
                self.open(theme, mode)
                main = self.page.locator('.tl-left > .tl-call-facts')
                self.assertEqual(main.locator('.k').all_text_contents(),
                                 ['duration', 'self', 'start', 'caller site', 'user args', 'user return'])
                self.assertTrue(self.value('task').is_visible())
                self.assertTrue(self.value('depth').is_visible())
                address = self.value('recorded address')
                self.assertTrue(address.is_visible())
                self.assertIn('Function entry address, not the call site.', address.text_content())
                self.assertEqual(self.value('caller site').locator('.tl-code-offset').inner_text(), '+0x33/0x80')
                self.assertNotIn('Names from recorded user DWARF', self.value('user args').inner_text())
                self.select('inner')
                self.assertEqual(main.locator('.k').all_text_contents(),
                                 ['duration', 'self', 'start', 'args (BTF)', 'return (BTF)'])
                self.assertTrue(self.value('return (BTF)').is_visible())
                self.assertTrue(self.page.locator('.tl-pill.err').is_visible())
                self.assertTrue(main.locator('.v.err').is_visible())
                self.assertTrue(self.value('raw return').is_visible())
                self.assertTrue(self.page.locator('.tl-context-row[data-space-boundary]').is_visible())

    def test_metadata_uses_keyboard_disclosure_without_changing_data_or_selection(self):
        for theme, mode in product(('light', 'dark'), ('single', 'sidecar')):
            with self.subTest(theme=theme, mode=mode):
                self.open(theme, mode)
                before = self.page.evaluate('''() => {
                    const model = fgView('timeline').probe();
                    return [JSON.stringify(fgData.read('tlData')), model.sel(), model.view()];
                }''')
                summary = self.page.locator('[data-trace-metadata] > summary')
                summary.focus()
                self.assertEqual(summary.evaluate('element => getComputedStyle(element).outlineStyle'), 'solid')
                self.page.keyboard.press('Enter')
                self.assertFalse(self.value('recorded address').is_visible())
                self.page.keyboard.press('Space')
                self.assertTrue(self.value('recorded address').is_visible())
                self.assertEqual(self.value('recorded address').locator('.tl-code-raw').inner_text(), '0x2')
                self.assertTrue(self.value('trace lines').locator('a[data-line]').count())
                after = self.page.evaluate('''() => {
                    const model = fgView('timeline').probe();
                    return [JSON.stringify(fgData.read('tlData')), model.sel(), model.view()];
                }''')
                self.assertEqual(after, before)

    def test_metadata_preference_survives_immediate_navigation_analysis_and_reload(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open(mode=mode)
                self.assertTrue(self.page.locator('[data-trace-metadata]').evaluate('element => element.open'))
                self.page.evaluate('''() => {
                    document.querySelector('[data-trace-metadata]').open = true;
                    const model = fgView('timeline').probe();
                    model.select(Array.from({length:model.N}, (_, index) => index)
                        .find(index => model.nameOf(index) === 'inner'));
                }''')
                metadata = self.page.locator('[data-trace-metadata]')
                self.assertTrue(metadata.evaluate('element => element.open'))
                self.page.evaluate('''() => {
                    const model = fgView('timeline').probe();
                    model.range(model.sStart[0], model.sEnd[0]);
                }''')
                self.assertEqual(metadata.count(), 0)
                self.select('legacy')
                self.assertTrue(metadata.evaluate('element => element.open'))
                self.assertTrue(self.value('raw argument bytes').is_visible())
                self.page.reload()
                self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
                self.select('work<int>')
                self.assertTrue(metadata.evaluate('element => element.open'))
                self.page.evaluate('''() => {
                    document.querySelector('[data-trace-metadata]').open = false;
                    const model = fgView('timeline').probe();
                    model.select(Array.from({length:model.N}, (_, index) => index)
                        .find(index => model.nameOf(index) === 'legacy'));
                }''')
                self.assertFalse(metadata.evaluate('element => element.open'))
                self.assertEqual(self.value('raw argument bytes').inner_text(), 'deadbeef12345678')
                self.page.reload()
                self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
                self.select('legacy')
                self.assertFalse(metadata.evaluate('element => element.open'))
                self.assertEqual(self.value('raw argument bytes').inner_text(), 'deadbeef12345678')
                self.page.evaluate('''() => {
                    document.querySelector('[data-trace-metadata]').open = true;
                    const model = fgView('timeline').probe();
                    model.select(Array.from({length:model.N}, (_, index) => index)
                        .find(index => model.nameOf(index) === 'inner'));
                }''')
                self.assertTrue(metadata.evaluate('element => element.open'))

    def test_metadata_defaults_to_open_despite_the_legacy_saved_closed_default(self):
        self.page.add_init_script("localStorage.setItem('fgTl.metadataOpen', '0')")
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open(mode=mode)
                metadata = self.page.locator('[data-trace-metadata]')
                self.assertTrue(metadata.evaluate('element => element.open'))
                for label in ['task', 'depth', 'recorded address', 'trace lines']:
                    self.assertTrue(self.value(label).is_visible(), label)

    def test_kernel_flags_are_visible_outside_metadata_and_raw_only_calls_stay_readable(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open(mode=mode, kind='ftrace', name='ksys_dup3')
                original = self.page.evaluate('JSON.stringify(fgData.read("tlData"))')
                flags_text = self.value('flags').inner_text()
                self.assertTrue(self.value('flags').is_visible())
                self.assertTrue(self.page.locator('[data-trace-metadata]').evaluate('element => element.open'))
                self.assertTrue(self.value('task').is_visible())
                self.assertTrue(self.value('trace lines').is_visible())
                self.assertTrue(self.value('args').is_visible())
                self.assertTrue(self.value('return').is_visible())
                self.page.locator('[data-trace-metadata] > summary').click()
                self.assertTrue(self.value('flags').is_visible())
                self.assertEqual(self.value('flags').inner_text(), flags_text)
                self.assertFalse(self.value('task').is_visible())
                self.page.locator('[data-trace-metadata] > summary').click()
                self.assertTrue(self.value('task').is_visible())
                self.assertTrue(self.value('flags').is_visible())
                self.assertEqual(self.page.evaluate('JSON.stringify(fgData.read("tlData"))'), original)
                self.open(mode=mode)
                self.select('legacy')
                self.assertEqual(self.value('raw argument bytes').inner_text(), 'deadbeef12345678')
                self.assertEqual(self.page.locator('.tl-left > .tl-call-facts .tl-code-parameter').count(), 0)

    def test_user_only_provenance_without_payload_or_source_has_no_fabricated_bytes(self):
        self.open(kind='named-only', name='named_only')
        args = self.value('user args')
        self.assertEqual(args.locator('.tl-argument').inner_text(), 'count=3')
        self.assertEqual(self.value('user return').inner_text(), '0')
        self.assertIn('Not recorded', self.value('caller site').inner_text())
        provenance = args.get_by_text('Argument provenance', exact=True)
        self.assertFalse(provenance.locator('..').evaluate('element => element.open'))
        self.assertNotIn('Names from recorded user DWARF', args.inner_text())
        provenance.click()
        self.assertIn('Names from recorded user DWARF', args.inner_text())
        self.assertEqual(args.locator('.tl-code-raw').count(), 0)
        self.assertEqual(args.get_by_text('Original argument bytes', exact=True).count(), 0)

    def test_oversized_units_wrap_without_truncating_or_overflowing(self):
        self.page.set_viewport_size({'width': 320, 'height': 844})
        self.open(kind='ftrace', name='ksys_dup3')
        name = 'very_long_parameter_' * 20
        original = name + '=' + '0x' + 'f' * 160
        self.value('args').locator('.tl-argument').first.evaluate('''(element, text) => {
            element.textContent = text;
        }''', original)
        unit = self.value('args').locator('.tl-argument').first
        self.assertEqual(unit.text_content(), original)
        self.assertTrue(unit.evaluate('element => element.scrollWidth <= element.clientWidth + 1'))
        self.assertTrue(self.page.evaluate('''() => {
            const root = document.querySelector('#timelineView');
            return root.scrollWidth <= root.clientWidth + 1;
        }'''))

    def test_quoted_values_and_separators_keep_whitespace_when_copied(self):
        self.page.set_viewport_size({'width': 320, 'height': 844})
        self.open(kind='ftrace', name='ksys_dup3')
        first, separator, second = 'path="a  b,\t c\n  d",', '\n\t', 'count=3'
        result = self.value('args').evaluate('''(element, [first, separator, second]) => {
            const createUnit = text => {
                const unit = document.createElement('span');
                unit.className = 'tl-argument'; unit.textContent = text;
                return unit;
            };
            element.replaceChildren(createUnit(first), document.createTextNode(separator), createUnit(second));
            const range = document.createRange(); range.selectNodeContents(element);
            const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
            const copied = selection.toString(); selection.removeAllRanges();
            return {copied, fits:element.scrollWidth <= element.clientWidth + 1};
        }''', [first, separator, second])
        self.assertEqual(result['copied'], first + separator + second)
        self.assertTrue(result['fits'])


if __name__ == '__main__':
    unittest.main()
