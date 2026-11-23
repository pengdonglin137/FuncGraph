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
class SourceUrlCacheTest(unittest.TestCase):
    def test_caches_only_bounded_resolutions_for_the_current_base(self):
        panels = (Path(helpers.ROOT) / 'assets' / 'timeline' / 'panels.js').read_text()
        script = panels[panels.index('const DETAIL_SOURCE_URL_LIMIT'):panels.index('const CALL_NAVIGATION')]
        script += '''
            const assert = require('node:assert/strict');
            const document = {baseURI:'file:///reports/ping/index.html'};
            const NativeURL = globalThis.URL;
            let parses = 0;
            globalThis.URL = class extends NativeURL {
                constructor(...args) { super(...args); parses++; }
            };
            for (let repeat = 0; repeat < 2; repeat++) {
                assert.equal(safeSourceUrl('../a.c'), 'file:///reports/a.c');
                assert.equal(safeSourceUrl('  JAVASCRIPT:alert(1)'), '');
                assert.equal(safeSourceUrl('vscode://file/tmp/a.c:4'), 'vscode://file/tmp/a.c:4');
                assert.equal(safeSourceUrl(''), '');
            }
            assert.equal(parses, 3);
            document.baseURI = 'https://example.test/sub/report.html';
            assert.equal(safeSourceUrl('../a.c'), 'https://example.test/a.c');
            assert.equal(safeSourceUrl('http://['), '');
            assert.equal(safeSourceUrl('data:text/plain,a'), '');
            const long = 'x'.repeat(DETAIL_SOURCE_URL_UNITS + 1);
            const before = parses;
            assert.equal(safeSourceUrl(long), 'https://example.test/sub/' + long);
            assert.equal(safeSourceUrl(long), 'https://example.test/sub/' + long);
            assert.equal(parses - before, 2);
            for (let index = 0; index < DETAIL_SOURCE_URL_LIMIT; index++) safeSourceUrl('test' + index);
            assert.equal(detailSourceUrls.size, DETAIL_SOURCE_URL_LIMIT);
            assert.equal(detailSourceUrls.has('../a.c'), false);
        '''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)


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

    def test_simple_delimiters_keep_empty_fields_and_whitespace(self):
        value = ', flags=0x12,\t , count=-4,\n'
        parsed = self.render(dict(value=value))[0]
        self.assertEqual(parsed.units, [',', 'flags=0x12,', ',', 'count=-4,'])

    def test_recorded_names_with_commas_quotes_and_markup_stay_one_unit(self):
        name = 'count, "quoted"=<script>window.argumentExecuted=1</script>'
        value = name + '=3, slot2=0xffffffffffffffff'
        parsed = self.render(dict(value=value, names=[name, None]))[0]
        self.assertEqual(parsed.units, [name + '=3,', 'slot2=0xffffffffffffffff'])

    def test_unbalanced_or_unclosed_values_are_kept_as_one_safe_unit(self):
        values = ['first=(1, 2, second=3', 'first="a,b, second=3', 'first=[1, 2), second=3']
        values.extend('first=' + closer + ', second=3' for closer in (')', ']', '}', "'"))
        for value, parsed in zip(values, self.render(*(dict(value=value) for value in values))):
            self.assertEqual(parsed.units, [value])

    def test_incompatible_optional_names_do_not_reconstruct_or_change_values(self):
        value = 'count=3, flags=4'
        for parsed in self.render(*(dict(value=value, names=names)
                                    for names in ([], ['other', 'flags'], ['count'], [False, 'flags']))):
            self.assertEqual(parsed.units, ['count=3,', 'flags=4'])

    def test_argument_formatting_reuses_short_records_without_retaining_large_records(self):
        script = self.script + r'''
            const assert = require('node:assert/strict');
            const split = splitDetailArguments;
            let scans = 0;
            splitDetailArguments = value => { scans++; return split(value); };
            const raw = `count=3, path="<b>&, nested"`;
            const first = detailRecordedArgumentsHtml(raw, 1);
            assert.equal(detailRecordedArgumentsHtml(raw, 1), first);
            assert.equal(scans, 1);
            assert.match(first, /&lt;b&gt;&amp;/);

            const withComma = 'count,name=3, flags=4';
            const plain = detailRecordedArgumentsHtml(withComma, 2);
            const named = detailArgumentsHtml(withComma, ['count,name', 'flags']);
            assert.notEqual(named, plain);
            assert.equal(detailRecordedArgumentsHtml(withComma, 2), plain);
            assert.equal(scans, 2);

            const large = 'payload=' + 'x'.repeat(4096);
            assert.equal(detailRecordedArgumentsHtml(large, 3), detailRecordedArgumentsHtml(large, 3));
            assert.equal(scans, 4);
            const expands = '<'.repeat(510);
            assert.equal(detailRecordedArgumentsHtml(expands, 4), detailRecordedArgumentsHtml(expands, 4));
            assert.equal(scans, 6);

            const collidingId = 1 + DETAIL_ARGUMENT_CACHE_LIMIT;
            assert.notEqual(detailRecordedArgumentsHtml('value=99', collidingId), first);
            assert.equal(scans, 7);
            assert.equal(detailRecordedArgumentsHtml(raw, 1), first);
            assert.equal(scans, 8);
        '''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_btf_argument_cache_keeps_type_context_and_raw_payload_separate(self):
        model = (Path(helpers.ROOT) / 'assets' / 'timeline' / 'model.js').read_text()
        script = self.script + model[model.index('function scalarValue('):model.index('function rawRetOf(')] + r'''
            const assert = require('node:assert/strict');
            const D = {btf_detail: {enums: [
                {bits: 32, values: {'1': ['FAST', 'QUICK']}}, {bits: 32, values: {'1': ['ACTIVE']}}
            ]}};
            const sArgs = [1, 1, 129, 2, 3], sLine = [10, 11, 12, 13, 14];
            const first = {args: {0: ['mode', 0]}}, second = {args: {0: ['mode', 1]}};
            const split = splitDetailArguments;
            let scans = 0;
            splitDetailArguments = value => { scans++; return split(value); };
            const raw = 'mode=1 [QUICK]';
            const parsed = btfEnumArguments(0, raw, first);
            assert.strictEqual(btfEnumArguments(1, raw, first), parsed);
            assert.equal(parsed.parts[0].match.name, 'QUICK');
            assert.equal(scans, 1);
            const other = btfEnumArguments(0, raw, second).parts[0].match;
            assert.equal(other.name, 'ACTIVE');
            assert.equal(other.labelMismatch, true);
            assert.equal(btfEnumArguments(2, 'mode=2', first).parts[0].match.value, '2');
            assert.deepEqual(btfEnumArguments(0, raw, first), parsed);
            assert.equal(scans, 4);

            for (let repeat = 0; repeat < 2; repeat++) assert.equal(btfEnumArguments(3, 'mode=(', first), null);
            assert.equal(scans, 5);
            const long = raw + ', label="' + 'x'.repeat(512) + '"';
            for (let repeat = 0; repeat < 2; repeat++) assert.equal(btfEnumArguments(4, long, first).parts[0].match.name, 'QUICK');
            assert.equal(scans, 7);

            D.uftrace = {kernel_details: {10: {raw_payload: true}}};
            assert.equal(btfEnumArguments(0, raw, first), null);
            assert.equal(scans, 7);
        '''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)


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

    def test_compact_call_facts_give_values_room_in_portrait_and_landscape(self):
        self.context.close()
        for mode, viewport in (('single', (280, 480)), ('sidecar', (667, 375))):
            with self.subTest(mode=mode, viewport=viewport):
                with self.browser.new_context(viewport=dict(zip(('width', 'height'), viewport)),
                                              is_mobile=True, has_touch=True) as context:
                    self.page = context.new_page()
                    self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                    self.open('dark', mode, name='inner')
                    dimensions = self.page.locator('.tl-call-facts').evaluate('''grid => {
                        const pairs = Array.from(grid.querySelectorAll(':scope > .k, :scope > .tl-recorded-field > .k'), label => {
                            const value = label.nextElementSibling;
                            return [label.textContent, label.clientWidth, value.clientWidth, value.scrollWidth];
                        });
                        const pane = grid.closest('.tl-detail');
                        return {pairs, gridFits:grid.scrollWidth <= grid.clientWidth + 1,
                                paneFits:pane.scrollWidth <= pane.clientWidth + 1};
                    }''')
                    self.assertTrue(dimensions['gridFits'] and dimensions['paneFits'], dimensions)
                    self.assertTrue({'duration', 'start', 'args (BTF)', 'trace lines'}.issubset(
                        pair[0] for pair in dimensions['pairs']), dimensions['pairs'])
                    for field, label, value, content in dimensions['pairs']:
                        with self.subTest(field=field):
                            self.assertGreaterEqual(value, label * 2, dimensions['pairs'])
                            self.assertLessEqual(content, value + 1, dimensions['pairs'])

    def test_start_keeps_timestamp_and_unit_together_without_losing_precision(self):
        self.context.close()
        for mode, width, touch in (('single', 320, True), ('sidecar', 320, True), ('single', 1440, False)):
            with self.subTest(mode=mode, width=width):
                with self.browser.new_context(viewport={'width': width, 'height': 844},
                                              is_mobile=touch, has_touch=touch) as context:
                    self.page = context.new_page()
                    self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                    self.open(mode=mode)
                    facts = self.value('start').evaluate('''element => {
                        const selection = getSelection(), range = document.createRange();
                        range.selectNodeContents(element);
                        selection.removeAllRanges(); selection.addRange(range);
                        const copied = selection.toString().replace(/\\s+/g, ' ');
                        selection.removeAllRanges();
                        const right = element.getBoundingClientRect().right;
                        const tokens = Array.from(element.querySelectorAll('.tl-time-value'), token => {
                            range.selectNodeContents(token);
                            const bounds = token.getBoundingClientRect();
                            return {text:token.textContent, top:bounds.top, fits:bounds.right <= right + 1,
                                    lines:range.getClientRects().length};
                        });
                        return {copied, tokens};
                    }''')
                    self.assertEqual(facts['copied'], '+100 ns 18446744000.000000200 s')
                    self.assertEqual([token['text'] for token in facts['tokens']], ['+100 ns', '18446744000.000000200 s'])
                    self.assertTrue(all(token['fits'] and token['lines'] == 1 for token in facts['tokens']), facts)
                    self.assertEqual(facts['tokens'][1]['top'] > facts['tokens'][0]['top'], touch, facts)

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
                    self.assertEqual(self.page.locator('[data-trace-metadata], .tl-metadata').count(), 0)
                    for label in ('task', 'depth', 'recorded address', 'trace lines'):
                        self.assertTrue(self.value(label).is_visible(), label)
                    self.assertTrue(self.page.evaluate('''() => {
                        const root = document.querySelector('#timelineView');
                        return root.scrollWidth <= root.clientWidth + 1;
                    }'''))

    def test_return_explanation_wraps_between_words_and_keeps_original_text(self):
        self.page.set_viewport_size({'width': 320, 'height': 844})
        self.open(name='inner')
        facts = self.value('return (BTF)').evaluate('''value => {
            const text = Array.from(value.childNodes).find(node =>
                node.nodeType === Node.TEXT_NODE && node.textContent.includes(' · '));
            const before = value.textContent;
            value.style.width = '13.5ch';
            const words = [...text.textContent.matchAll(/\\S+/g)].map(word => {
                const range = document.createRange();
                range.setStart(text, word.index);
                range.setEnd(text, word.index + word[0].length);
                return [word[0], range.getClientRects().length];
            });
            return {words, same: before === value.textContent, fits: value.scrollWidth <= value.clientWidth + 1};
        }''')
        self.assertGreater(len(facts['words']), 4)
        self.assertTrue(all(lines == 1 for word, lines in facts['words']), facts)
        self.assertTrue(facts['same'] and facts['fits'], facts)

    def test_raw_evidence_has_full_touch_targets_and_readable_records(self):
        self.context.close()
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                with self.browser.new_context(viewport={'width': 320, 'height': 844},
                                              is_mobile=True, has_touch=True) as context:
                    self.page = context.new_page()
                    self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                    self.open(mode=mode, name='inner')
                    self.page.get_by_role('button', name='Expand details into the chart area').tap()
                    for label, column in (('args (BTF)', 'args'), ('return (BTF)', 'ret')):
                        evidence = self.value(label).locator(':scope > details')
                        summary = evidence.locator(':scope > summary')
                        self.assertFalse(evidence.evaluate('node => node.open'))
                        hit = summary.evaluate('''summary => {
                            summary.scrollIntoView({block:'center', inline:'nearest'});
                            const bounds = summary.getBoundingClientRect();
                            const column = summary.closest('.v').getBoundingClientRect();
                            const tapX = column.right - 4, tapY = bounds.top + bounds.height / 2;
                            return {tapX, tapY, height: bounds.height, hit: summary.contains(document.elementFromPoint(tapX, tapY))};
                        }''')
                        self.assertGreaterEqual(hit['height'], 44, hit)
                        self.assertTrue(hit['hit'], hit)
                        self.page.touchscreen.tap(hit['tapX'], hit['tapY'])
                        self.assertTrue(evidence.evaluate('node => node.open'))
                        record = evidence.locator('.tl-code-raw')
                        expected = self.page.evaluate('''column => {
                            const data = fgData.read('tlData'), index = fgView('timeline').probe().sel();
                            return data.strings[data.cols[column][index]];
                        }''', column)
                        self.assertEqual(record.text_content(), expected)
                        self.assertTrue(record.evaluate('''record => {
                            const body = record.closest('.tl-value-evidence-body');
                            const grid = body.closest('.tl-call-facts').getBoundingClientRect();
                            const bounds = body.getBoundingClientRect();
                            return Math.abs(bounds.left - grid.left) < 1 && Math.abs(bounds.right - grid.right) < 1 &&
                                body.scrollWidth <= body.clientWidth + 1;
                        }'''))
                        summary.press('Space')
                        self.assertFalse(evidence.evaluate('node => node.open'))
                        self.assertTrue(summary.evaluate('node => node === document.activeElement'))

    def test_primary_facts_prioritize_calls_values_and_visible_errors(self):
        for theme, mode in product(('light', 'dark'), ('single', 'sidecar')):
            with self.subTest(theme=theme, mode=mode):
                self.open(theme, mode)
                main = self.page.locator('.tl-left > .tl-call-facts')
                self.assertEqual(main.locator('.k').all_text_contents(),
                                 ['duration', 'self', 'start', 'task', 'caller site', 'user args',
                                  'user return', 'depth', 'recorded address', 'trace lines'])
                self.assertEqual(self.page.locator('[data-trace-metadata], .tl-metadata').count(), 0)
                self.assertEqual(self.page.get_by_text('Trace metadata', exact=True).count(), 0)
                self.assertTrue(self.value('task').is_visible())
                self.assertTrue(self.value('depth').is_visible())
                address = self.value('recorded address')
                self.assertTrue(address.is_visible())
                self.assertIn('Function entry address, not the call site.', address.text_content())
                self.assertEqual(self.value('caller site').locator('.tl-code-offset').inner_text(), '+0x33/0x80')
                self.assertNotIn('Names from recorded user DWARF', self.value('user args').inner_text())
                self.select('inner')
                self.assertEqual(main.locator('.k').all_text_contents(),
                                 ['duration', 'self', 'start', 'task', 'args (BTF)', 'return (BTF)',
                                  'depth', 'trace lines'])
                prototype = self.page.locator('.tl-recorded-call-head + .tl-btf-prototype')
                opener = self.page.get_by_role('button', name='Function prototype: inner', exact=True)
                self.assertIsNone(prototype.get_attribute('open'))
                self.assertFalse(prototype.locator('summary').is_visible())
                self.assertEqual(opener.get_attribute('aria-expanded'), 'false')
                self.assertEqual(opener.get_attribute('aria-controls'), prototype.get_attribute('id'))
                opener.press('Space')
                self.assertTrue(prototype.locator('code').is_visible())
                self.assertEqual(opener.get_attribute('aria-expanded'), 'true')
                opener.press('Enter')
                self.assertFalse(prototype.is_visible())
                self.page.get_by_role('button', name='Selected call actions').click()
                self.page.get_by_role('menuitem', name='Show recorded symbol').click()
                symbol = self.page.get_by_role('region', name='Recorded symbol')
                self.assertEqual(symbol.locator('code').text_content(), 'inner')
                self.assertEqual(opener.get_attribute('aria-expanded'), 'false')
                symbol.get_by_role('button', name='Hide symbol', exact=True).click()
                self.assertTrue(self.value('return (BTF)').is_visible())
                self.assertTrue(self.page.locator('.tl-pill.err').is_visible())
                self.assertTrue(main.locator('.v.err').is_visible())
                evidence = self.value('return (BTF)').locator('.tl-return-evidence')
                self.assertEqual(main.locator('.k:text-is("raw return")').count(), 0)
                self.assertIsNone(evidence.get_attribute('open'))
                self.assertFalse(evidence.locator('.tl-code-raw').is_visible())
                evidence.locator('summary').click()
                self.assertTrue(evidence.locator('.tl-code-raw').is_visible())
                self.assertTrue(self.page.locator('.tl-context-row[data-space-boundary]').is_visible())

    def test_direct_fields_preserve_argument_disclosure_and_trace_navigation(self):
        for theme, mode in product(('light', 'dark'), ('single', 'sidecar')):
            with self.subTest(theme=theme, mode=mode):
                self.open(theme, mode)
                before = self.page.evaluate('''() => {
                    const model = fgView('timeline').probe();
                    return [JSON.stringify(fgData.read('tlData')), model.sel(), model.view()];
                }''')
                self.assertEqual(self.page.locator('[data-trace-metadata], .tl-metadata').count(), 0)
                self.assertEqual(self.page.get_by_text('Trace metadata', exact=True).count(), 0)
                self.assertTrue(self.value('recorded address').is_visible())
                self.assertEqual(self.value('recorded address').locator('.tl-code-raw').inner_text(), '0x2')
                link = self.value('trace lines').locator('button[data-line]').first
                self.assertTrue(link.is_visible())
                provenance = self.value('user args').locator('details')
                provenance.locator('summary').focus()
                self.page.keyboard.press('Enter')
                self.assertTrue(provenance.evaluate('element => element.open'))
                self.assertEqual(provenance.locator('.tl-code-raw').inner_text(), '03000000ffffffffffffffff01')
                self.page.keyboard.press('Space')
                self.assertFalse(provenance.evaluate('element => element.open'))
                after = self.page.evaluate('''() => {
                    const model = fgView('timeline').probe();
                    return [JSON.stringify(fgData.read('tlData')), model.sel(), model.view()];
                }''')
                self.assertEqual(after, before)
                link.click()
                self.page.wait_for_function("() => fgCurrentView() === 'text' && !fgTextDeferred()")
                self.assertTrue(self.page.locator('.view-tab[data-view=text]').evaluate(
                    'element => element === document.activeElement'))
                self.assertEqual(self.page.evaluate('JSON.stringify(fgData.read("tlData"))'), before[0])

    def test_direct_fields_survive_navigation_analysis_and_reload_without_data_changes(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open(mode=mode)
                original = self.page.evaluate('JSON.stringify(fgData.read("tlData"))')
                initial_fields = self.page.locator('.tl-left > .tl-call-facts').inner_text()
                self.select('inner')
                for label in ('task', 'depth', 'return (BTF)', 'trace lines'):
                    self.assertTrue(self.value(label).is_visible(), label)
                self.assertFalse(self.value('return (BTF)').locator('.tl-return-evidence .tl-code-raw').is_visible())
                self.page.evaluate('''() => {
                    const model = fgView('timeline').probe();
                    model.range(model.sStart[0], model.sEnd[0]);
                }''')
                self.assertEqual(self.page.locator('.tl-left > .tl-call-facts').count(), 0)
                self.select('legacy')
                self.assertTrue(self.value('raw argument bytes').is_visible())
                legacy_fields = self.page.locator('.tl-left > .tl-call-facts').inner_text()
                self.assertEqual(self.page.evaluate('JSON.stringify(fgData.read("tlData"))'), original)
                self.page.reload()
                self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
                self.select('work<int>')
                self.assertEqual(self.page.locator('.tl-left > .tl-call-facts').inner_text(), initial_fields)
                self.select('legacy')
                self.assertEqual(self.page.locator('.tl-left > .tl-call-facts').inner_text(), legacy_fields)
                self.assertEqual(self.value('raw argument bytes').inner_text(), 'deadbeef12345678')
                self.page.reload()
                self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
                self.select('legacy')
                self.assertEqual(self.page.locator('.tl-left > .tl-call-facts').inner_text(), legacy_fields)
                self.assertEqual(self.page.locator('[data-trace-metadata], .tl-metadata').count(), 0)
                self.assertEqual(self.page.evaluate('JSON.stringify(fgData.read("tlData"))'), original)

    def test_old_metadata_preferences_cannot_hide_fields_and_are_no_longer_accessed(self):
        self.page.add_init_script('''(() => {
            localStorage.setItem('fgTl.metadataOpen', '0');
            localStorage.setItem('fgTl.metadataCollapsed', '1');
            window.metadataPreferenceAccesses = [];
            for (const method of ['getItem', 'setItem']) {
                const original = Storage.prototype[method];
                Storage.prototype[method] = function(key, ...values) {
                    if (key === 'fgTl.metadataOpen' || key === 'fgTl.metadataCollapsed') {
                        window.metadataPreferenceAccesses.push([method, key]);
                    }
                    return original.call(this, key, ...values);
                };
            }
        })();''')
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open(mode=mode)
                self.assertEqual(self.page.locator('[data-trace-metadata], .tl-metadata').count(), 0)
                for label in ['task', 'depth', 'recorded address', 'trace lines']:
                    self.assertTrue(self.value(label).is_visible(), label)
                self.select('inner')
                self.select('work<int>')
                self.page.reload()
                self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
                self.select('work<int>')
                for label in ['task', 'depth', 'recorded address', 'trace lines']:
                    self.assertTrue(self.value(label).is_visible(), label)
                self.assertEqual(self.page.evaluate('window.metadataPreferenceAccesses'), [])

    def test_kernel_flags_follow_task_and_raw_only_calls_stay_readable(self):
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                self.open(mode=mode, kind='ftrace', name='ksys_dup3')
                original = self.page.evaluate('JSON.stringify(fgData.read("tlData"))')
                self.assertTrue(self.value('flags').is_visible())
                self.assertEqual(self.page.locator('[data-trace-metadata], .tl-metadata').count(), 0)
                self.assertEqual(self.page.locator('.tl-left > .tl-call-facts .k').all_text_contents(),
                                 ['duration', 'self', 'start', 'task', 'flags', 'caller site',
                                  'args', 'return', 'depth', 'trace lines'])
                self.assertTrue(self.value('task').is_visible())
                self.assertTrue(self.value('trace lines').is_visible())
                self.assertTrue(self.value('args').is_visible())
                self.assertTrue(self.value('return').is_visible())
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
