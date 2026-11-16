"""Dropdown choices match complete recorded values without treating data as syntax."""
import json
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import threading
import unittest
from urllib.parse import urljoin

import helpers
from fglib.viewer import create_server
from test_uftrace_import import call, stream

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


NAMES = ['worker[0]', 'worker0', 'Worker[0]', 'a,b', '(worker)', '<worker>',
         'name@host', ' leading ', 'OR NOT', 'quote"\\path',
         '中文🙂', 'a&amp;b', 'toString', 'constructor', '__proto__',
         'unit" data-probe="yes', '<img src=x onerror="window.fieldInjected=1">']


def records():
    result = []
    for index, name in enumerate(NAMES):
        cpu = [1, 10, 11][index] if index < 3 else 100 + index
        tid = [7, 70, 700][index] if index < 3 else 1000 + index
        timestamp = index * 100
        result.extend([call('entry', 'user', timestamp, 0, 'main', index + 1, tid=tid, comm=name),
                       call('entry', 'kernel', timestamp + 10, 0, 'work', index + 100, tid=tid, cpu=cpu,
                            common_flags=index % 2, common_preempt_count=1),
                       call('exit', 'kernel', timestamp + 20, 0, 'work', index + 100, tid=tid, cpu=cpu,
                            retval='0xfffffffffffffff5' if index % 2 else '0xffffffffffffffff'),
                       call('exit', 'user', timestamp + 30, 0, 'main', index + 1, tid=tid)])
    return result


class FieldValueEncodingTest(unittest.TestCase):
    def fields(self, content):
        class FieldParser(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.fields = {}

            def handle_starttag(self, tag, attributes):
                attributes = dict(attributes)
                if tag == 'button' and 'field-btn' in attributes.get('class', '').split():
                    self.fields[attributes['data-field']] = attributes

        parser = FieldParser()
        parser.feed(content)
        return parser.fields

    def test_json_attributes_preserve_complete_names_without_extra_markup_attributes(self):
        kernel = helpers.FakeKernel()
        self.addCleanup(kernel.close)
        trace = Path(kernel.dir, 'fields.jsonl')
        trace.write_text(stream(records()))
        for mode in ('single', 'sidecar'):
            with self.subTest(mode=mode):
                content, _ = kernel.run(str(trace), '--no-config', '--timeline', '--filter',
                                         '--report-mode', mode, out=mode if mode == 'sidecar' else mode + '.html')
                fields = self.fields(content)
                self.assertEqual(json.loads(fields['comm']['data-values']), sorted(NAMES))
                for field in fields.values():
                    self.assertLessEqual(set(field), {'class', 'data-field', 'data-values', 'data-hints'})
                    if field['data-values']:
                        values = json.loads(field['data-values'])
                        self.assertIsInstance(values, list)
                        self.assertTrue(all(isinstance(value, str) for value in values))
                self.assertEqual(json.loads(fields['cpu']['data-values'])[:3], ['1', '10', '11'])

    def test_error_options_keep_all_and_complete_error_numbers(self):
        kernel = helpers.FakeKernel()
        self.addCleanup(kernel.close)
        trace = Path(kernel.dir, 'ret.txt')
        trace.write_text('0) a-7 | first(); /* ret=-1 */\n0) b-70 | second(); /* ret=-11 */\n')
        content, _ = kernel.run(str(trace), '--no-config', '--filter')
        self.assertEqual(json.loads(self.fields(content)['ret']['data-values']), ['all', '-11', '-1'])


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class LiteralFieldFilterTest(unittest.TestCase):
    def run_script(self, assertions):
        source = (Path(helpers.ROOT) / 'assets/report/report.js').read_text()
        source = source[source.index('        function tokenizeFilter'):source.index('        // ====== Filter Tags Management')]
        setup = '''const assert = require('node:assert/strict'), _graphFoldIds = {};
            const record = attributes => ({getAttribute:name => attributes[name] ?? null, querySelector:() => null});
        '''
        result = subprocess.run(['node', '-e', setup + source + assertions], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_enumerated_literals_are_complete_and_case_sensitive(self):
        self.run_script('''
            for (const [field, value, others] of [
                ['cpu','1',['10','11','21']], ['pid','7',['70','700','17']],
                ['comm','worker[0]',['worker0','Worker[0]','worker[0]suffix']],
                ['ret','-1',['-11','-12']], ['irqs','.',['d']],
                ['resched','N',['n','B','.']], ['hirq','H',['h','s','.']],
                ['preempt','1',['10','11']]
            ]) {
                const text = field + ':=' + JSON.stringify(value), ast = parseFilterExpr(text).ast;
                assert.equal(astToText(ast), text);
                assert.equal(evalExpr(record({['data-'+field]:value}), ast), true);
                for (const other of [...others, '', null])
                    assert.equal(evalExpr(record({['data-'+field]:other}), ast), false, text+' / '+other);
            }
            const literal=parseFilterExpr('cpu:="1"').ast, regex=parseFilterExpr('cpu:^1$').ast;
            const original=RegExp, row=record({'data-cpu':'1'});
            let compiled=0;
            try {
                globalThis.RegExp=function(...parameters) { compiled++; return new original(...parameters); };
                for (let index=0; index<1000; index++) assert.equal(evalExpr(row, literal), true);
                assert.equal(compiled, 0);
                for (let index=0; index<1000; index++) assert.equal(evalExpr(row, regex), true);
                assert.equal(compiled, 1);
            } finally { globalThis.RegExp=original; }
        ''')

    def test_special_task_names_and_boolean_groups_round_trip(self):
        self.run_script(r'''
            for (const name of ['a,b','(worker)','<worker>','name@host',' leading ',
                'AND OR NOT sort:desc', 'quote" and back\\slash', '中文🙂', '[x]+.*?^${}|',
                '<img src=x onerror=alert(1)>', 'line\nname']) {
                const text = 'comm:=' + JSON.stringify(name), ast = parseFilterExpr(text).ast;
                assert.equal(astToText(ast), text);
                assert.equal(evalExpr(record({'data-comm':name}), ast), true);
                assert.equal(evalExpr(record({'data-comm':name+'suffix'}), ast), false);
                const grouped = '(pid:="7" OR pid:="70") AND NOT (' + text + ' OR irqs:="d") sort:desc';
                const parsed = parseFilterExpr(grouped), rebuilt = parseFilterExpr(astToText(parsed.ast));
                assert.equal(parsed.sortDir, 'desc');
                assert.deepEqual(rebuilt.ast, parsed.ast);
            }
        ''')

    def test_manual_regex_numeric_comparison_and_ret_all_keep_their_semantics(self):
        self.run_script('''
            for (const cpu of ['1','10','11'])
                assert.equal(evalExpr(record({'data-cpu':cpu}), parseFilterExpr('cpu:1').ast), true);
            for (const flag of ['.','d'])
                assert.equal(evalExpr(record({'data-irqs':flag}), parseFilterExpr('irqs:.').ast), true);
            assert.equal(evalExpr(record({'data-comm':'Worker0'}), parseFilterExpr('comm:worker.').ast), true);
            assert.equal(evalExpr(record({'data-ret':'-11'}), parseFilterExpr('ret:-1').ast), true);
            assert.equal(evalExpr(record({'data-ret':'-11'}), parseFilterExpr('ret:all').ast), true);
            assert.equal(evalExpr(record({'data-ret':'-11'}), parseFilterExpr('ret:="all"').ast), false);
            assert.equal(evalExpr(record({'data-duration':'12'}), parseFilterExpr('duration:>10').ast), true);
            assert.equal(evalExpr(record({'data-preempt':'2'}), parseFilterExpr('preempt:>=2').ast), true);
            assert.equal(evalExpr(record({'data-func':'worker','data-raw-func':'worker.isra.0'}),
                parseFilterExpr('func:="worker.isra.0"').ast), true);
        ''')

    def test_malformed_or_empty_literals_do_not_become_match_all(self):
        self.run_script(r'''
            for (const field of ['cpu','pid','comm','ret','irqs','resched','hirq','preempt'])
                for (const value of ['"unclosed','"bad\q"','""','"value"trailing']) {
                    const ast = parseFilterExpr(field+':='+value).ast;
                    assert.equal(evalExpr(record({['data-'+field]:'anything'}), ast), false);
                    assert.equal(evalExpr(record({}), ast), false);
                }
        ''')


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class ExactFieldBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel()
        cls.addClassCleanup(cls.kernel.close)
        trace = Path(cls.kernel.dir, 'fields.jsonl')
        trace.write_text(stream(records()))
        common = ['--no-config', '--enable-fold', '--filter']
        cls.kernel.run(str(trace), *common, '--timeline', out='index.html')
        cls.kernel.run(str(trace), *common, '--timeline', '--report-mode', 'sidecar', out='sidecar')
        cls.kernel.run(str(trace), *common, out='plain.html')
        ret_trace = Path(cls.kernel.dir, 'ret.txt')
        ret_trace.write_text('# tracer: function_graph\n'
                             ' 1) a-7 | 1.000 us | first(); /* ret=-1 */\n'
                             '10) b-70 | 2.000 us | second(); /* ret=-11 */\n')
        cls.kernel.run(str(ret_trace), *common, '--timeline', out='ret.html')
        cls.server = create_server(Path(cls.kernel.dir))
        cls.addClassCleanup(cls.server.server_close)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
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

    def open_report(self, filename='index.html', touch=False, theme='light', view='text'):
        context = self.browser.new_context(viewport=dict(width=390 if touch else 1400, height=900),
                                           has_touch=touch, is_mobile=touch)
        self.addCleanup(context.close)
        context.add_init_script('localStorage.clear()')
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        self.addCleanup(self.assertEqual, errors, [])
        page.goto(urljoin(self.server.url, filename), wait_until='load')
        page.evaluate('''async ({theme, view}) => {
            document.documentElement.dataset.theme = theme;
            if (typeof fgSwitchView === 'function') await fgSwitchView(view);
        }''', dict(theme=theme, view=view))
        if view == 'text' and not page.locator('#filterBox').is_visible():
            page.locator('#textFilterToggle').press('Enter')
        return page

    def choose(self, page, field, values):
        page.locator('.field-btn[data-field=' + field + ']').press('Enter')
        checkboxes = page.locator('#textFieldDropdown input[type=checkbox]')
        options = checkboxes.evaluate_all('inputs => inputs.map(input => input.value)')
        for value in values:
            checkboxes.nth(options.index(value)).check()
        page.get_by_role('button', name='OK', exact=True).press('Enter')
        page.wait_for_function('() => !fgFilterProgress')
        return page.locator('#filterInput').input_value()

    def visible_values(self, page, field):
        return page.locator('#content .line-container:visible').evaluate_all('''(rows, field) =>
            [...new Set(rows.map(row => row.getAttribute('data-' + field)).filter(value => value))].sort()''', field)

    def test_dropdown_numeric_and_flag_choices_match_only_selected_values(self):
        for field, value in [('cpu','1'), ('pid','7'), ('ret','-1'), ('irqs','.')]:
            with self.subTest(field=field):
                page = self.open_report('ret.html' if field == 'ret' else 'index.html')
                rows = page.locator('#content .line-container').count()
                self.assertEqual(self.choose(page, field, [value]), field + ':=' + json.dumps(value))
                self.assertEqual(self.visible_values(page, field), [value])
                self.assertEqual(page.locator('#content .line-container').count(), rows)

    def test_task_names_keep_commas_wrappers_quotes_spaces_and_markup_as_data(self):
        page = self.open_report()
        button = page.locator('.field-btn[data-field=comm]')
        self.assertIsNone(button.get_attribute('data-probe'))
        self.assertEqual(json.loads(button.get_attribute('data-values')), sorted(NAMES))
        for name in NAMES:
            with self.subTest(name=name):
                page.evaluate('clearFilter()')
                query = self.choose(page, 'comm', [name])
                self.assertEqual(query, 'comm:=' + json.dumps(name, ensure_ascii=False))
                self.assertEqual(self.visible_values(page, 'comm'), [name])
                self.assertEqual(page.locator('#textFieldDropdown img, #filterTags img').count(), 0)
                self.assertIsNone(page.evaluate('window.fieldInjected'))

    def test_multiple_values_ret_all_and_manual_regex_remain_distinct(self):
        page = self.open_report()
        query = self.choose(page, 'cpu', ['1', '11'])
        self.assertEqual(query, '(cpu:="1" OR cpu:="11")')
        self.assertEqual(self.visible_values(page, 'cpu'), ['1', '11'])
        page = self.open_report('ret.html')
        self.assertEqual(self.choose(page, 'ret', ['all']), 'ret:all')
        self.assertEqual(self.visible_values(page, 'ret'), ['-1', '-11'])
        page = self.open_report()
        page.evaluate('''() => { document.getElementById('filterInput').value='cpu:1'; applyFilter(); }''')
        self.assertIn('10', self.visible_values(page, 'cpu'))
        page.evaluate('''() => { document.getElementById('filterInput').value='irqs:.'; applyFilter(); }''')
        self.assertEqual(self.visible_values(page, 'irqs'), ['.', 'd'])

    def test_tag_edits_keep_exact_names_groups_and_sort(self):
        page = self.open_report()
        name = NAMES[-1]
        page.evaluate('''name => {
            document.getElementById('filterInput').value='(pid:="7" OR comm:='+JSON.stringify(name)+') AND irqs:="." sort:desc';
            applyFilter();
        }''', name)
        page.locator('#filterTags .filter-tag').filter(has_text='irqs:').locator('.tag-remove').click()
        query = page.locator('#filterInput').input_value()
        self.assertIn('comm:=' + json.dumps(name), query)
        self.assertIn('pid:="7"', query)
        self.assertIn('sort:desc', query)
        self.assertEqual(self.visible_values(page, 'comm'), sorted([NAMES[0], name]))
        self.assertEqual(page.locator('#filterTags img').count(), 0)
        self.assertIsNone(page.evaluate('window.fieldInjected'))

    def test_sidecar_windowed_and_timeline_share_the_exact_pid_scope(self):
        for filename in ('index.html', 'sidecar/index.html', 'sidecar/index.html?text=windowed'):
            with self.subTest(filename=filename):
                page = self.open_report(filename)
                before = page.evaluate("async () => JSON.stringify(await fgData.load('tlData'))")
                self.choose(page, 'pid', ['7'])
                self.assertEqual(self.visible_values(page, 'pid'), ['7'])
                page.evaluate("async () => { await fgSwitchView('timeline'); }")
                pids = page.evaluate('''() => {
                    const data=fgData.read('tlData'), lines=fgFilterState.lines;
                    return data.cols.line.flatMap((number, index) =>
                        lines.has(number) || lines.has(data.cols.eline[index]) ? [data.tasks[data.cols.task[index]].pid] : []);
                }''')
                self.assertTrue(pids)
                self.assertEqual(set(pids), {7})
                self.assertEqual(page.evaluate("JSON.stringify(fgData.read('tlData'))"), before)

    def test_regex_reuse_keeps_complete_results_across_edits_and_text_modes(self):
        for filename in ('plain.html', 'index.html', 'sidecar/index.html',
                         'sidecar/index.html?text=windowed'):
            with self.subTest(filename=filename):
                page = self.open_report(filename)
                original = page.evaluate('''() => Array.from(document.querySelectorAll(
                    '#content .line-container'), row => [row.dataset.lineNumber, row.textContent])''')
                for query, expected in [('comm:^worker', sorted(NAMES[:3])),
                                        ('comm:[', []), ('comm:worker', sorted(NAMES[:3] + NAMES[4:6])),
                                        ('comm:^中文', ['中文🙂'])]:
                    page.evaluate('''query => {
                        const NativeRegExp = RegExp;
                        window.fieldRegexCompilations = [];
                        window.RegExp = new Proxy(NativeRegExp, {
                            construct(target, parameters) {
                                fieldRegexCompilations.push(parameters[0]);
                                return Reflect.construct(target, parameters);
                            }
                        });
                        window.restoreFieldRegExp = () => { window.RegExp = NativeRegExp; };
                        document.getElementById('filterInput').value = query;
                        applyFilter();
                    }''', query)
                    page.wait_for_function('query => fgFilterState.text === query && !fgFilterProgress', arg=query)
                    self.assertEqual(self.visible_values(page, 'comm'), expected)
                    counts = page.evaluate('''pattern => fieldRegexCompilations.filter(value => value === pattern).length''',
                                           query.removeprefix('comm:'))
                    self.assertEqual(counts, 1)
                    page.evaluate('restoreFieldRegExp()')
                page.evaluate('clearFilter()')
                page.wait_for_function('() => !fgFilterState.text && !fgFilterProgress')
                self.assertEqual(page.evaluate('''() => Array.from(document.querySelectorAll(
                    '#content .line-container'), row => [row.dataset.lineNumber, row.textContent])'''), original)

    def test_deferred_timeline_filter_reuses_compilation_without_mounting_text(self):
        page = self.open_report('sidecar/index.html', view='timeline')
        self.assertEqual(page.locator('#content .line-container').count(), 0)
        original = page.evaluate("async () => JSON.stringify(await fgData.load('tlData'))")
        page.evaluate('''() => {
            const NativeRegExp = RegExp;
            window.fieldRegexCompilations = [];
            window.RegExp = new Proxy(NativeRegExp, {
                construct(target, parameters) {
                    fieldRegexCompilations.push(parameters[0]);
                    return Reflect.construct(target, parameters);
                }
            });
            window.restoreFieldRegExp = () => { window.RegExp = NativeRegExp; };
            document.getElementById('filterInput').value = 'comm:^worker';
            applyFilter();
        }''')
        page.wait_for_function("() => fgFilterState.text === 'comm:^worker' && !fgFilterProgress")
        self.assertEqual(page.evaluate("fieldRegexCompilations.filter(pattern => pattern === '^worker').length"), 1)
        page.evaluate('restoreFieldRegExp()')
        self.assertEqual(page.locator('#content .line-container').count(), 0)
        lines = page.evaluate('Array.from(fgFilterState.lines)')
        self.assertTrue(lines)
        page.evaluate("async () => { await fgSwitchView('text'); }")
        self.assertEqual(self.visible_values(page, 'comm'), sorted(NAMES[:3]))
        self.assertEqual(page.evaluate('Array.from(fgFilterState.lines)'), lines)
        self.assertEqual(page.evaluate("JSON.stringify(fgData.read('tlData'))"), original)

    def test_plain_and_touch_themes_use_the_same_literal_choice(self):
        for filename, touch, theme in [('plain.html',False,'light'), ('index.html',True,'light'), ('index.html',True,'dark')]:
            with self.subTest(filename=filename, theme=theme):
                page = self.open_report(filename, touch=touch, theme=theme)
                self.choose(page, 'comm', ['a,b'])
                self.assertEqual(self.visible_values(page, 'comm'), ['a,b'])
                self.assertEqual(page.locator('.field-btn[data-field=comm]').get_attribute('aria-expanded'), 'false')

    def test_legacy_csv_values_keep_safe_labels_and_exact_selection(self):
        page = self.open_report()
        page.locator('.field-btn[data-field=comm]').evaluate('button => { button.dataset.values="worker[0],worker0"; }')
        self.assertEqual(self.choose(page, 'comm', ['worker[0]']), 'comm:="worker[0]"')
        self.assertEqual(self.visible_values(page, 'comm'), ['worker[0]'])

    def test_popup_label_building_never_parses_value_or_hint_markup(self):
        page = self.open_report()
        page.locator('.field-btn[data-field=comm]').evaluate('''(button, names) => {
            button.dataset.values=JSON.stringify(names);
            button.dataset.hints='worker[0]=<svg onload="window.fieldInjected=1">';
        }''', NAMES)
        page.locator('.field-btn[data-field=comm]').press('Enter')
        self.assertEqual(page.locator('#textFieldDropdown input[type=checkbox]').count(), len(NAMES))
        self.assertEqual(page.locator('#textFieldDropdown img, #textFieldDropdown svg').count(), 0)
        self.assertEqual(page.locator('#textFieldDropdown .hint-text').count(), 1)
        self.assertIn('<svg', page.locator('#textFieldDropdown .hint-text').inner_text())
        self.assertIsNone(page.evaluate('window.fieldInjected'))
