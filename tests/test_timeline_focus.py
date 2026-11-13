"""Selected-call filter drafts, exact symbols and one-step focus undo."""

from itertools import product
from pathlib import Path
import shutil
import subprocess
import threading
import unittest

import helpers
from fglib.viewer import create_server
from test_uftrace_import import call, capture, stream

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


ROOT = Path(helpers.ROOT)
SYMBOL = 'operator()(int, const char*) [clone .isra.0] AND "q"\\path'


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class LiteralFunctionFilterTest(unittest.TestCase):
    def run_script(self, assertions):
        source = (ROOT / 'assets/report/report.js').read_text()
        source = source[source.index('        function tokenizeFilter'):source.index('        // ====== Filter Tags Management')]
        setup = '''
            const assert = require('node:assert/strict');
            const _graphFoldIds = {};
            const record = (name, space='user', pid='42', raw=null) => ({getAttribute:key =>
                ({'data-func':name,'data-space':space,'data-pid':pid,'data-raw-func':raw})[key] ?? null});
        '''
        result = subprocess.run(['node', '-e', setup + source + assertions], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_literal_names_are_case_sensitive_complete_and_round_trip(self):
        self.run_script('''
            for (const name of ['inode_has_perm.isra.0', 'operator()(int, const char*)',
                'AND OR NOT sort:desc', 'quote" and back\\\\slash', '中文🙂\u0020symbol',
                'line\\nname', '[x]+.*?^${}|', '<img src=x onerror=alert(1)>']) {
                const text = 'func:=' + JSON.stringify(name);
                const ast = parseFilterExpr(text).ast;
                assert.equal(astToText(ast), text);
                assert.equal(evalExpr(record(name), ast), true);
                assert.equal(evalExpr(record(name + 'suffix'), ast), false);
                assert.equal(evalExpr(record('prefix' + name), ast), false);
            }
            const ast = parseFilterExpr('func:="Worker.isra.0"').ast;
            assert.equal(evalExpr(record('worker.isra.0'), ast), false);
            assert.equal(evalExpr(record('WorkerXisraY0'), ast), false);
            assert.equal(evalExpr(record('Worker.isra.1'), ast), false);
            const compiled = record('worker', 'kernel', '42', 'worker.isra.0');
            assert.equal(evalExpr(compiled, parseFilterExpr('func:="worker.isra.0"').ast), true);
            assert.equal(evalExpr(compiled, parseFilterExpr('func:="worker"').ast), false);
            assert.equal(evalExpr(compiled, parseFilterExpr('func:^worker$').ast), true);
        ''')

    def test_boolean_groups_sort_and_legacy_regex_semantics_remain_intact(self):
        self.run_script('''
            const text = '(pid:^42$ OR pid:^142$) AND NOT (space:kernel OR func:="AND OR") sort:desc';
            const {ast, sortDir} = parseFilterExpr(text);
            const rebuilt = parseFilterExpr(astToText(ast)).ast;
            assert.deepEqual(rebuilt, ast); assert.equal(sortDir, 'desc');
            assert.equal(evalExpr(record('anything'), rebuilt), true);
            assert.equal(evalExpr(record('anything', 'kernel'), rebuilt), false);
            assert.equal(evalExpr(record('AND OR'), rebuilt), false);
            assert.equal(evalExpr(record('anything', 'user', '242'), rebuilt), false);
            assert.equal(evalExpr(record('Worker.isra.0'), parseFilterExpr('func:worker').ast), true);
            assert.equal(evalExpr(record('Worker.isra.0'), parseFilterExpr('func:=worker').ast), true);
            for (const text of ['func:="unclosed', 'func:="bad\\\\q"', 'func:=""'])
                assert.equal(evalExpr(record('anything'), parseFilterExpr(text).ast), false);
        ''')


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class TimelineFocusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel(inline_source=True)
        cls.addClassCleanup(cls.kernel.close)
        records = capture()
        names = [SYMBOL, SYMBOL + 'suffix', SYMBOL.lower(), 'inode_has_perm.isra.0',
                 'inode_has_permXisraY0', 'inode_has_perm.isra.1',
                 '<img src=x onerror="window.focusInjected=1"> AND OR']
        for index, name in enumerate(names):
            timestamp = 1000 + 100 * index
            records.extend([call('entry', 'user', timestamp, 0, name, 20 + index),
                            call('exit', 'user', timestamp + 50, 0, name, 20 + index)])
        records.extend([call('entry', 'user', 2000, 0, SYMBOL, 40, tid=142, comm='other'),
                        call('exit', 'user', 2050, 0, SYMBOL, 40, tid=142)])
        trace = Path(cls.kernel.dir, 'focus.jsonl')
        trace.write_text(stream(records))
        common = ['--no-config', '--timeline', '--enable-fold', '--filter']
        cls.kernel.run(str(trace), *common, out='single.html')
        cls.kernel.run(str(trace), *common, '--report-mode', 'sidecar', out='sidecar')
        cls.kernel.run(str(trace), *common, '--no-text-view', '--report-mode', 'sidecar', out='timeline-only')
        window_records = records[:]
        for index in range(2200):
            timestamp = 3000 + index * 100
            window_records.extend([call('entry', 'user', timestamp, 0, 'filler', 99),
                                   call('exit', 'user', timestamp + 50, 0, 'filler', 99)])
        window_trace = Path(cls.kernel.dir, 'window.jsonl')
        window_trace.write_text(stream(window_records))
        cls.kernel.run(str(window_trace), *common, '--report-mode', 'sidecar', out='window')
        cls.kernel.run(str(trace), '--no-config', '--timeline', out='no-filter.html')
        cls.kernel.run(helpers.fixture('pi4_noproc'), *common, out='no-task.html')
        inferred_trace = Path(cls.kernel.dir, 'inferred-task.txt')
        inferred_trace.write_text('# tracer: function_graph\n 0) old-1 => inferred-42\n'
                                  ' 0)   1.000 us    |  leaf();\n')
        cls.kernel.run(str(inferred_trace), *common, out='inferred-task.html')
        cls.kernel.run(helpers.fixture('pi4_sched'), *common, out='kernel.html')
        clone_trace = Path(cls.kernel.dir, 'kernel-clones.txt')
        clone_trace.write_text('# tracer: function_graph\n' + ''.join(
            ' 0)   1.000 us    |  ' + name + '();\n'
            for name in ['cloned', 'cloned.isra.0', 'cloned.isra.1'] + ['filler'] * 4200))
        cls.kernel.run(str(clone_trace), *common, out='kernel-clones.html')
        cls.kernel.run(str(clone_trace), *common, '--report-mode', 'sidecar', out='kernel-clones-sidecar')
        Path(cls.kernel.dir, 'index.html').write_text('<!doctype html><title>Focus test reports</title>')
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

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 960})
        self.page = self.context.new_page()
        self.page.set_default_timeout(8000)
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def open(self, mode='single.html'):
        url = self.server.url.rsplit('/', 1)[0] + '/' + mode + '/index.html'
        self.page.goto(url if mode in ('sidecar', 'timeline-only') else Path(self.kernel.dir, mode).as_uri())
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")

    def select(self, name='work<int>', space='user'):
        self.page.evaluate('''([name, space]) => {
            const probe = fgView('timeline').probe(), data = fgData.read('tlData');
            const index = Array.from({length:probe.N}, (_, index) => index).find(index =>
                probe.nameOf(index) === name && !!(data.cols.flags[index] & (1 << 25)) === (space === 'user'));
            if (index === undefined) throw new Error('Missing selected call');
            probe.select(index);
        }''', [name, space])

    def focus(self, action='function'):
        self.page.locator('[data-call-actions]').click()
        self.page.locator('[data-call-action=focus-' + action + ']').click()
        self.assertTrue(self.page.locator('.tl-ftext').evaluate('element => element === document.activeElement'))
        return self.page.locator('.tl-ftext').input_value()

    def apply(self):
        query = self.page.locator('.tl-ftext').input_value().strip()
        self.page.locator('[data-act=fapply]').click()
        self.page.wait_for_function('query => fgFilterState.text === query && !fgFilterProgress', arg=query)
        return query

    def matching_calls(self):
        return self.page.evaluate('''() => {
            const probe=fgView('timeline').probe(), data=fgData.read('tlData'), lines=fgFilterState.lines;
            return Array.from({length:probe.N}, (_, index)=>index).filter(index=>
                !lines || lines.has(data.cols.line[index]) || lines.has(data.cols.eline?.[index])).map(index=>
                ({name:probe.nameOf(index),user:!!(data.cols.flags[index] & (1 << 25)),
                    pid:data.tasks[data.cols.task[index]].pid}));
        }''')

    def test_function_action_stages_exact_space_qualified_filter_without_changing_model(self):
        for mode in ('single.html', 'sidecar'):
            with self.subTest(mode=mode):
                self.open(mode)
                self.select()
                self.page.evaluate('''() => {
                    const probe=fgView('timeline').probe();
                    window.focusOriginal={data:JSON.stringify(fgData.read('tlData')),starts:probe.sStart,
                        view:probe.view(),selected:probe.sel(),filter:fgFilterState};
                }''')
                query = self.focus()
                self.assertEqual(query, 'func:="work<int>" AND space:user')
                self.assertTrue(self.page.evaluate('fgFilterState === focusOriginal.filter'))
                self.assertTrue(self.page.locator('.tl-fdraft').is_visible())
                self.assertFalse(self.page.locator('[data-act=fundo]').is_visible())
                if mode != 'single.html':
                    self.assertEqual(self.page.locator('#content .line-container').count(), 0)
                self.apply()
                self.assertEqual(self.matching_calls(), [{'name': 'work<int>', 'user': True, 'pid': 42}])
                self.assertTrue(self.page.locator('[data-act=fundo]').is_visible())
                self.assertTrue(self.page.evaluate('''() => {
                    const probe=fgView('timeline').probe(); return probe.sStart===focusOriginal.starts &&
                        JSON.stringify(fgData.read('tlData'))===focusOriginal.data && probe.sel()===focusOriginal.selected &&
                        JSON.stringify(probe.view())===JSON.stringify(focusOriginal.view);
                }'''))
                if mode != 'single.html':
                    self.assertEqual(self.page.locator('#content .line-container').count(), 0)

    def test_exact_symbols_preserve_punctuation_spaces_case_and_compiler_suffixes(self):
        for name, space in product((SYMBOL, 'inode_has_perm.isra.0', 'work<int>'), ('user', 'kernel')):
            if space == 'kernel' and name != 'work<int>':
                continue
            with self.subTest(name=name, space=space):
                self.open()
                self.select(name, space)
                self.focus()
                self.apply()
                matches = self.matching_calls()
                self.assertTrue(matches)
                self.assertTrue(all(call['name'] == name and call['user'] == (space == 'user') for call in matches))

    def test_task_filter_uses_exact_pid_and_covers_both_spaces(self):
        self.open()
        self.select()
        self.assertEqual(self.focus('task'), 'pid:^42$')
        self.apply()
        matches = self.matching_calls()
        self.assertTrue(all(call['pid'] == 42 for call in matches))
        self.assertEqual({call['user'] for call in matches}, {False, True})

    def test_kernel_compiler_suffixes_remain_exact_in_dom_sidecar_and_windowed_text(self):
        base = self.server.url.rsplit('/', 1)[0]
        urls = [Path(self.kernel.dir, 'kernel-clones.html').as_uri(),
                base + '/kernel-clones-sidecar/index.html',
                base + '/kernel-clones-sidecar/index.html?text=windowed']
        for url in urls:
            with self.subTest(url=url):
                self.page.goto(url)
                if 'windowed' in url:
                    self.page.evaluate("async () => { await fgSwitchView('text'); }")
                    self.page.wait_for_function('() => !!fgTextWindow')
                    self.assertEqual(self.page.evaluate('fgTextWindow.model.version'), 5)
                self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
                self.select('cloned.isra.0', 'kernel')
                self.assertEqual(self.focus(), 'func:="cloned.isra.0" AND space:kernel')
                self.apply()
                self.assertEqual([call['name'] for call in self.matching_calls()], ['cloned.isra.0'])
                self.page.evaluate("async () => { await fgSwitchView('text'); }")
                row = self.page.locator('#content [data-raw-func="cloned.isra.0"]').first
                self.assertEqual(row.get_attribute('data-func'), 'cloned')
                self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
                helpers.open_timeline_tools(self.page)
                self.page.locator('[data-act=fundo]').click()
                self.page.wait_for_function('() => !fgFilterState.text && !fgFilterProgress')
                self.page.locator('.tl-ftext').fill('func:^cloned$')
                self.apply()
                self.assertEqual({call['name'] for call in self.matching_calls()},
                                 {'cloned', 'cloned.isra.0', 'cloned.isra.1'})

    def test_existing_applied_filter_and_newer_draft_are_preserved_until_apply_and_undo(self):
        self.open()
        helpers.open_timeline_tools(self.page)
        self.page.locator('.tl-ftext').fill('pid:^42$ OR pid:^142$ sort:desc')
        before = self.apply()
        self.page.locator('.tl-ftext').fill('(pid:^42$ OR pid:^142$) AND space:user sort:desc')
        draft = self.page.locator('.tl-ftext').input_value()
        self.page.locator('[data-act=options]').click()
        self.select(SYMBOL)
        query = self.focus()
        self.assertTrue(query.startswith('(' + draft + ') AND ('))
        self.assertEqual(self.page.evaluate('fgFilterState.text'), before)
        self.apply()
        self.assertEqual(len(self.matching_calls()), 2)
        self.page.locator('[data-act=fundo]').click()
        self.page.wait_for_function('text => fgFilterState.text === text && !fgFilterProgress', arg=before)
        self.assertEqual(self.page.locator('.tl-ftext').input_value(), before)
        self.assertFalse(self.page.locator('[data-act=fundo]').is_visible())

    def test_undo_is_one_step_protects_new_drafts_and_is_invalidated_by_external_changes(self):
        self.open()
        self.select()
        self.focus()
        self.apply()
        self.page.locator('.tl-ftext').fill('duration:>10')
        self.assertTrue(self.page.locator('[data-act=fundo]').is_disabled())
        self.page.locator('.tl-ftext').press('Escape')
        self.assertTrue(self.page.locator('[data-act=fundo]').is_enabled())
        helpers.open_timeline_tools(self.page)
        self.page.locator('[data-act=fundo]').click()
        self.assertEqual(self.page.evaluate('fgFilterState.text'), '')
        self.page.locator('[data-act=options]').click()
        self.select()
        self.focus()
        self.apply()
        self.page.evaluate("fgNotifyFilter('space:kernel', new Set())")
        self.assertFalse(self.page.locator('[data-act=fundo]').is_visible())

    def test_cancelled_draft_and_manual_edits_do_not_install_focus_undo(self):
        self.open()
        self.select()
        self.focus()
        self.page.locator('.tl-ftext').press('Escape')
        self.assertEqual(self.page.evaluate('fgFilterState.text'), '')
        self.assertEqual(self.page.locator('.tl-ftext').input_value(), '')
        self.page.locator('[data-act=options]').click()
        self.focus()
        self.page.locator('.tl-ftext').fill('space:user')
        self.apply()
        self.assertFalse(self.page.locator('[data-act=fundo]').is_visible())

    def test_failed_sidecar_request_retries_and_cancellation_never_applies_late_results(self):
        self.open('sidecar')
        self.select()
        query = self.focus()
        self.page.route('**/text-index.json', lambda route: route.abort())
        self.page.locator('[data-act=fapply]').click()
        self.page.wait_for_function('() => fgFilterProgress?.error')
        self.assertEqual(self.page.evaluate('fgFilterState.text'), '')
        self.assertFalse(self.page.locator('[data-act=fundo]').is_visible())
        self.page.unroute('**/text-index.json')
        self.page.locator('[data-act=fretry]').click()
        self.page.wait_for_function('query => fgFilterState.text === query && !fgFilterProgress', arg=query)
        self.assertTrue(self.page.locator('[data-act=fundo]').is_visible())
        self.page.locator('[data-act=fundo]').click()
        self.assertEqual(self.page.evaluate('fgFilterState.text'), '')
        self.open('sidecar')
        self.select()
        self.focus()
        pending = []
        self.page.route('**/text-index.json', lambda route: pending.append(route))
        self.page.locator('[data-act=fapply]').click()
        self.page.wait_for_function('() => !!fgFilterProgress')
        self.page.locator('.tl-ftext').press('Escape')
        for route in pending:
            route.abort()
        self.assertEqual(self.page.evaluate('fgFilterState.text'), '')
        self.assertFalse(self.page.locator('[data-act=fundo]').is_visible())
        self.assertEqual(self.page.locator('#content .line-container').count(), 0)

    def test_unavailable_task_and_filter_controls_do_not_offer_misleading_actions(self):
        for mode in ('no-filter.html', 'timeline-only'):
            self.open(mode)
            self.select()
            self.page.locator('[data-call-actions]').click()
            self.assertEqual(self.page.locator('[data-call-action^=focus-]').count(), 0)
        self.open('no-task.html')
        self.page.evaluate("fgView('timeline').probe().select(0)")
        self.page.locator('[data-call-actions]').click()
        self.assertTrue(self.page.locator('[data-call-action=focus-task]').is_disabled())
        self.assertIn('No task identity available to the shared filter',
                      self.page.locator('[data-call-action=focus-task]').get_attribute('title'))
        self.assertTrue(self.page.locator('[data-call-action=focus-function]').is_enabled())
        self.open('inferred-task.html')
        self.page.evaluate("fgView('timeline').probe().select(0)")
        self.assertTrue(self.page.evaluate("fgData.read('tlData').tasks.some(task=>task.pid===42)"))
        self.page.locator('[data-call-actions]').click()
        self.assertTrue(self.page.locator('[data-call-action=focus-task]').is_disabled())

    def test_idle_task_is_cpu_qualified_without_guessing_identity(self):
        self.open('kernel.html')
        identity = self.page.evaluate('''() => {
            const probe=fgView('timeline').probe(), data=fgData.read('tlData');
            const index=data.cols.task.findIndex(task=>data.tasks[task].kind===1);
            if (index < 0) throw new Error('Fixture has no idle call');
            probe.select(index); return data.tasks[data.cols.task[index]];
        }''')
        self.assertEqual(self.focus('task'), 'pid:^0$ AND cpu:^' + str(identity['cpu']) + '$')
        self.apply()
        self.assertTrue(self.matching_calls())
        self.assertTrue(all(call['pid'] == 0 for call in self.matching_calls()))

    def test_two_successive_focuses_undo_only_the_latest_and_cancel_pending_menu_actions(self):
        self.open()
        self.select()
        task_query = self.focus('task')
        self.apply()
        self.page.locator('[data-act=options]').click()
        self.focus()
        self.apply()
        self.page.locator('[data-act=fundo]').click()
        self.assertEqual(self.page.evaluate('fgFilterState.text'), task_query)
        self.assertFalse(self.page.locator('[data-act=fundo]').is_visible())
        self.page.locator('[data-act=options]').click()
        self.page.evaluate("fgFilterProgress={text:'func:pending',message:'Filtering…',error:false}")
        self.page.locator('[data-call-actions]').click()
        for action in ('focus-task', 'focus-function'):
            self.assertTrue(self.page.locator('[data-call-action=' + action + ']').is_disabled())
        self.assertTrue(self.page.locator('[data-call-action=distribution]').is_enabled())

    def test_windowed_text_uses_the_same_literal_and_restores_the_complete_filter_scope(self):
        url = self.server.url.rsplit('/', 1)[0] + '/window/index.html?text=windowed'
        self.page.goto(url)
        self.page.evaluate("async () => { await fgSwitchView('text'); }")
        self.page.wait_for_function('() => !!fgTextWindow')
        total = self.page.evaluate('fgTextWindow.model.records.length')
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        self.select(SYMBOL)
        self.focus()
        query = self.apply()
        self.assertEqual(len(self.matching_calls()), 2)
        self.page.evaluate("async () => { await fgSwitchView('text'); }")
        self.page.wait_for_function('query => fgFilterState.text === query && !fgFilterProgress', arg=query)
        self.assertEqual(self.page.locator('#filterInput').input_value(), query)
        self.assertEqual(self.page.evaluate('fgTextWindow.model.records.length'), total)
        self.page.evaluate("async () => { await fgSwitchView('timeline'); }")
        helpers.open_timeline_tools(self.page)
        self.page.locator('[data-act=fundo]').click()
        self.page.wait_for_function('() => !fgFilterState.text && !fgFilterProgress')
        self.assertEqual(self.page.evaluate('fgTextWindow.model.records.length'), total)

    def test_literal_tags_are_safe_and_editing_tags_preserves_grouping_names_and_sort(self):
        self.open()
        name = '<img src=x onerror="window.focusInjected=1"> AND OR'
        self.select(name)
        self.focus()
        self.apply()
        self.assertEqual(self.page.locator('#filterTags img').count(), 0)
        self.assertIsNone(self.page.evaluate('window.focusInjected'))
        self.page.evaluate('''name => {
            const input=document.getElementById('filterInput');
            input.value='(pid:^42$ OR pid:^142$) AND (func:=' + JSON.stringify(name) + ' AND space:user) sort:desc';
            applyFilter();
        }''', name)
        self.page.locator('[data-act=options]').click()
        self.page.evaluate("fgSwitchView('text')")
        self.page.locator('#filterTags .filter-tag-logic').first.click()
        self.assertTrue(self.page.evaluate('name => fgFilterState.text.includes(JSON.stringify(name))', name))
        self.assertIn('sort:desc', self.page.evaluate('fgFilterState.text'))
        self.page.locator('#filterTags .filter-tag').filter(has_text='space:user').locator('.tag-remove').click()
        self.assertNotIn('space:user', self.page.evaluate('fgFilterState.text'))
        self.assertTrue(self.page.evaluate('name => fgFilterState.text.includes(JSON.stringify(name))', name))
        self.assertIsNone(self.page.evaluate('window.focusInjected'))

    def test_keyboard_mobile_and_fullscreen_keep_focus_inside_existing_controls(self):
        self.context.close()
        for theme in ('light', 'dark'):
            with self.subTest(theme=theme):
                self.context = self.browser.new_context(viewport={'width': 390, 'height': 740}, has_touch=True, is_mobile=True)
                self.page = self.context.new_page()
                self.page.set_default_timeout(8000)
                self.page.on('pageerror', lambda error: self.errors.append(str(error)))
                self.open()
                self.page.evaluate('theme => document.documentElement.dataset.theme=theme', theme)
                self.select()
                self.page.locator('[data-act=max]').click()
                self.page.locator('[data-call-actions]').press('ArrowUp')
                self.assertEqual(self.page.evaluate('document.activeElement.dataset.callAction'), 'focus-function')
                self.page.keyboard.press('Enter')
                self.assertTrue(self.page.locator('.tl-ftext').evaluate('element => element === document.activeElement'))
                self.assertLessEqual(self.page.locator('.tl-options').evaluate('element => element.scrollWidth-element.clientWidth'), 1)
                self.page.locator('.tl-ftext').press('Enter')
                self.assertTrue(self.page.locator('[data-act=fundo]').is_visible())
                self.assertGreaterEqual(self.page.locator('[data-act=fundo]').bounding_box()['height'], 44)
                self.page.locator('[data-act=fundo]').click()
                self.assertEqual(self.page.evaluate('fgFilterState.text'), '')
                self.assertTrue(self.page.locator('#timelineView').evaluate('element=>element.classList.contains("tl-fullscreen")'))
                self.context.close()
