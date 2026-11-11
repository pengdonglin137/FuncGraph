from pathlib import Path
import shutil
import subprocess
import unittest

import helpers

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class TimelinePreparationTest(unittest.TestCase):
    def run_model(self, assertions):
        asset = Path(__file__).resolve().parents[1] / 'assets' / 'timeline' / 'model.js'
        source = asset.read_text(encoding='utf-8')
        source = source[:source.index('for (const r of D.offcpu) {   // [task, start, end, depth')]
        setup = '''
            const assert = require('node:assert/strict');
            globalThis.document = {getElementById: () => null};
            globalThis.window = {fgData:{read:() => input}};
            let input;
            function data(count) {
                return {n:count, mode:'abs', strings:['root', 'child', 'caller+0x4/0x40', 'other+0x8/0x80'],
                    cols:{start:Array(count).fill(1), dur:Array(count).fill(1), depth:Array(count).fill(0),
                        task:Array(count).fill(0), flags:Array(count).fill(0), name:Array(count).fill(0),
                        parent:Array(count).fill(-1), line:Array.from({length:count}, (_, index) => index + 1),
                        caller:Array(count).fill(-1), ret:null, args:null, module:null, cpu:null},
                    tasks:[{pid:1, comm:'first', kind:0}, {pid:2, comm:'second', kind:0}],
                    errs:[], chains:[], errnames:{}, offcpu:[], resched:[], irqoff:[]};
            }
            function freeze(value) {
                if (!value || typeof value !== 'object' || Object.isFrozen(value)) return;
                for (const child of Object.values(value)) freeze(child);
                Object.freeze(value);
            }
            function drain(iterator) {
                const labels = [];
                while (true) {
                    const result = iterator.next();
                    if (result.done) return {model:result.value, labels};
                    labels.push(result.value);
                }
            }
            function snapshot(model) {
                return JSON.stringify({strings:model.S, synthetic:model.SYN, columns:[model.sStart,
                    model.sEnd, model.sDepth, model.sTask, model.sFlags, model.sName, model.sParent,
                    model.sLine, model.sLdAll, model.sDur, model.sSelf], tracks:model.TRACKS});
            }
        '''
        factory = 'function* prepare() {\n' + source + '''
            function callerOf(index) { return sCaller && sCaller[index] >= 0 ? S[sCaller[index]] : null; }
            return {N, NA, SYN, S, sStart, sEnd, sDepth, sTask, sFlags, sName, sParent,
                sLine, sLdAll, sDur, sSelf, TRACKS, sortTimelineRow, buildRows};
        }
        '''
        result = subprocess.run(['node', '-e', setup + factory + assertions],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_empty_and_small_inputs_keep_exact_columns_without_batch_delays(self):
        self.run_model('''
            for (const count of [0, 1, 7]) {
                input = data(count);
                freeze(input);
                const {model, labels} = drain(prepare());
                assert.equal(model.NA, count);
                assert.deepEqual(Array.from(model.sStart), Array.from({length:count}, (_, index) => index + 1));
                assert.deepEqual(Array.from(model.sEnd), Array.from({length:count}, (_, index) => index + 2));
                assert.deepEqual(model.SYN, []);
                assert.equal(labels.length, 5);
                assert.equal(model.TRACKS[1].lo, -1);
                assert.deepEqual(model.TRACKS[0].rowsT.map(row => Array.from(row)), count ? [Array.from({length:count}, (_, index) => index)] : []);
            }
        ''')

    def test_coalesced_ghost_extents_names_children_and_direct_child_boundaries(self):
        self.run_model('''
            input = data(6);
            Object.assign(input.cols, {start:[0, 10, 10, 10, 10, 10], dur:[100, 5, 15, 5, 5, 5],
                parent:[-1, 0, 0, 0, 0, 0], depth:[0, 3, 3, 1, 3, 2], caller:[-1, 2, 2, -1, 2, 3]});
            freeze(input);
            const {model} = drain(prepare());
            assert.equal(model.NA, 11);
            assert.deepEqual(model.SYN.map(frame => [frame.name, frame.level, frame.gap, frame.kids]),
                [['?', 1, 2, [1, 2]], ['caller', 2, 2, [1, 2]], ['?', 1, 2, [4]],
                 ['caller', 2, 2, [4]], ['other', 1, 1, [5]]]);
            assert.deepEqual(Array.from(model.sStart).slice(6), [10, 10, 40, 40, 50]);
            assert.deepEqual(Array.from(model.sEnd).slice(6), [35, 35, 45, 45, 55]);
            assert.deepEqual(Array.from(model.sDepth).slice(6), [1, 2, 1, 2, 1]);
            assert.deepEqual(Array.from(model.sParent).slice(6), [0, 0, 0, 0, 0]);
            assert.equal(snapshot(drain(prepare()).model), snapshot(model));
        ''')

    def test_inline_then_ghost_growth_preserves_recorded_and_synthetic_columns(self):
        self.run_model('''
            input = data(3);
            Object.assign(input.cols, {start:[0, 10, 10], dur:[100, 5, 5], parent:[-1, 0, 0],
                depth:[0, 3, 3], caller:[-1, 2, 2], flags:[0, 6, 6]});
            input.inline = {'2':[['root', 'root.c', 1], ['outer', 'outer.h', 2], ['inner', 'inner.h', 3]]};
            freeze(input);
            const {model} = drain(prepare());
            assert.equal(model.NA, 7);
            assert.deepEqual(Array.from(model.sStart), [0, 10, 20, 10, 10, 10, 10]);
            assert.deepEqual(Array.from(model.sEnd), [100, 15, 25, 25, 25, 25, 25]);
            assert.deepEqual(Array.from(model.sLdAll), [0, 5, 5, 3, 4, 1, 2]);
            assert.deepEqual(model.S.slice(4), ['outer', 'inner', '?', 'caller']);
            assert.deepEqual(model.SYN.map(frame => frame.kids), [[1, 2], [1, 2], [1, 2], [1, 2]]);
            assert.equal(model.sFlags[3], (1 << 23) | 2);
            assert.equal(model.sFlags[5], (1 << 24) | 2);
            assert.deepEqual(model.TRACKS[0].rowsT.map(row => Array.from(row)), [[0], [5], [6], [1, 2]]);
            assert.deepEqual(model.TRACKS[0].rowsI.map(row => Array.from(row)), [[0], [5], [6], [3], [4], [1, 2]]);
        ''')

    def test_uftrace_spaces_never_invent_ghost_frames(self):
        self.run_model('''
            for (const spaces of [['user'], ['kernel'], ['user', 'kernel']]) {
                input = data(2);
                input.cols.parent[1] = 0;
                input.cols.depth[1] = 10;
                input.uftrace = {spaces};
                freeze(input);
                const {model} = drain(prepare());
                assert.equal(model.NA, 2);
                assert.deepEqual(model.SYN, []);
                assert.equal(model.TRACKS[0].rowsT.length, 11);
            }
        ''')

    def test_large_ghost_scan_and_materialization_yield_without_losing_members(self):
        self.run_model('''
            input = data(20001);
            input.cols.parent.fill(0, 1);
            input.cols.depth.fill(3, 1);
            input.cols.caller.fill(2, 1);
            input.cols.dur[0] = 30000;
            freeze(input);
            const {model, labels} = drain(prepare());
            assert.equal(model.NA, 20003);
            assert.equal(model.SYN[0].kids.length, 20000);
            assert.equal(model.SYN[1].kids.length, 20000);
            assert.deepEqual(model.SYN[1].kids, Array.from({length:20000}, (_, index) => index + 1));
            assert.ok(labels.filter(label => label === 'Building untraced frames…').length > 10);
            assert.ok(labels.filter(label => label === 'Indexing task tracks…').length > 8);
            assert.equal(model.TRACKS[0].rowsT[3].length, 20000);
        ''')

    def test_single_very_deep_call_yields_inside_missing_levels(self):
        self.run_model('''
            input = data(2);
            input.cols.parent[1] = 0;
            input.cols.depth[1] = 20001;
            input.cols.caller[1] = 2;
            const {model, labels} = drain(prepare());
            assert.equal(model.NA, 20002);
            assert.equal(model.SYN.length, 20000);
            assert.ok(labels.filter(label => label === 'Building untraced frames…').length >= 5);
            assert.equal(model.SYN[19999].name, 'caller');
            assert.equal(model.sDepth[20001], 20000);
        ''')

    def test_single_deep_inline_chain_yields_and_preserves_all_inline_frames(self):
        self.run_model('''
            input = data(2);
            input.cols.parent[1] = 0;
            input.cols.depth[1] = 1;
            input.cols.caller[1] = 2;
            input.inline = {'2':Array.from({length:17000}, (_, index) => ['frame_' + index, 'inline.h', index + 1])};
            const {model, labels} = drain(prepare());
            assert.equal(model.NA, 17001);
            assert.ok(labels.filter(label => label === 'Building inline frames…').length >= 5);
            assert.equal(model.SYN.length, 16999);
            assert.equal(model.sLdAll[1], 17000);
            assert.equal(model.S[model.sName[17000]], 'frame_16999');
        ''')

    def test_large_unsorted_rows_match_native_order_with_ties_and_partial_runs(self):
        self.run_model('''
            for (const count of [2, 8191, 8192, 8193, 16384, 16385, 40003]) {
                input = data(count);
                const {model} = drain(prepare());
                for (let index = 0; index < count; index++) model.sStart[index] = (index * 7919) % 127;
                const row = Int32Array.from({length:count}, (_, index) => count - index - 1);
                const expected = row.slice().sort((left, right) => model.sStart[left] - model.sStart[right] || left - right);
                const {labels} = drain(model.sortTimelineRow(row));
                assert.deepEqual(row, expected);
                if (count > 8192) assert.ok(labels.length > 1);
            }
        ''')

    def test_already_ordered_rows_yield_without_native_sort_or_merge(self):
        self.run_model('''
            input = data(20000);
            const {model} = drain(prepare());
            const row = Int32Array.from({length:20000}, (_, index) => index);
            row.subarray = () => { throw new Error('Sorted row should not be rewritten'); };
            const {labels} = drain(model.sortTimelineRow(row));
            assert.equal(labels.length, 2);
            assert.equal(row[19999], 19999);
        ''')

    def test_multiple_task_rows_preserve_negative_deltas_and_equal_start_identity(self):
        self.run_model('''
            input = data(40006);
            input.cols.task.fill(1, 20003);
            input.cols.start = Array.from({length:input.n}, (_, index) => index % 3 ? -1 : 2);
            input.cols.depth = Array.from({length:input.n}, (_, index) => index % 2);
            freeze(input);
            const {model} = drain(prepare());
            for (const track of model.TRACKS) {
                assert.equal(track.hi - track.lo, 20003);
                const ids = Array.from({length:20003}, (_, index) => track.lo + index);
                for (let depth = 0; depth < 2; depth++) {
                    const expected = ids.filter(index => model.sDepth[index] === depth)
                        .sort((left, right) => model.sStart[left] - model.sStart[right] || left - right);
                    assert.deepEqual(Array.from(track.rowsT[depth]), expected);
                }
                assert.equal(track.rowsT, track.rowsI);
            }
        ''')

    def test_cancellation_in_all_preparation_phases_keeps_frozen_input_reusable(self):
        self.run_model('''
            input = data(18000);
            for (let index = 1; index < input.n; index += 2) {
                input.cols.parent[index] = index - 1;
                input.cols.depth[index] = 3;
                input.cols.caller[index] = 2;
            }
            input.inline = {'2':[['root', 'root.c', 1], ['outer', 'outer.h', 2], ['inner', 'inner.h', 3]]};
            const original = JSON.stringify(input);
            freeze(input);
            const expected = snapshot(drain(prepare()).model);
            for (const phase of ['Building inline frames…', 'Building untraced frames…', 'Indexing task tracks…']) {
                for (const occurrence of [1, 2, 5]) {
                    const steps = prepare();
                    let seen = 0, result;
                    do {
                        result = steps.next();
                        if (result.value === phase) seen++;
                    } while (!result.done && seen < occurrence);
                    assert.equal(result.done, false, phase + ': missing yield ' + occurrence);
                    assert.equal(steps.return().done, true);
                    assert.equal(JSON.stringify(input), original);
                    assert.equal(snapshot(drain(prepare()).model), expected);
                }
            }
        ''')


@unittest.skipIf(sync_playwright is None, 'playwright not installed')
class TimelinePreparationBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from benchmark_preparation import fixture

        cls.kernel = helpers.FakeKernel()
        cls.kernel.run(helpers.fixture('pi4_sched'), '--timeline', out='base.html')
        root = Path(cls.kernel.dir)
        for scenario in ('gaps', 'inline', 'interleaved'):
            fixture(root / 'base.html', root / f'{scenario}.html', scenario, 12000)
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as error:
            cls.playwright.stop()
            cls.kernel.close()
            raise unittest.SkipTest(f'chromium not available: {error}')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.kernel.close()

    def setUp(self):
        self.errors = []
        self.context = self.browser.new_context(viewport={'width':1500, 'height':900})
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('console', lambda message: self.errors.append(message.text) if message.type == 'error' else None)

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def gate(self, scenario, phase, occurrence):
        self.page.goto((Path(self.kernel.dir) / f'{scenario}.html').as_uri(), wait_until='load')
        self.page.evaluate('''({phase, occurrence}) => {
            window.beforePreparation = JSON.stringify(fgData.read('tlData'));
            window.originalSteps = fgViews.timeline.steps;
            fgViews.timeline.staged = true;
            fgViews.timeline.steps = function* () {
                const steps = originalSteps();
                let seen = 0;
                try {
                    while (true) {
                        const result = steps.next();
                        if (result.done) return result.value;
                        if (result.value === phase && ++seen === occurrence) {
                            window.preparationGated = true;
                            yield {label:phase, load:() => new Promise(resolve => {window.releasePreparation = resolve;})};
                        } else yield result.value;
                    }
                } finally { steps.return(); window.preparationClosed = true; }
            };
            window.preparing = fgSwitchView('timeline');
        }''', dict(phase=phase, occurrence=occurrence))
        self.page.wait_for_function('window.preparationGated === true && typeof releasePreparation === "function"')
        self.assertEqual(self.page.locator('#timelineView').get_attribute('aria-busy'), 'true')
        self.assertEqual(self.page.locator('.tl-canvas').count(), 0)

    def test_cancel_during_each_large_phase_reopens_without_mutating_cached_input(self):
        for scenario, phase in (('inline', 'Building inline frames…'), ('gaps', 'Building untraced frames…'),
                                ('gaps', 'Indexing task tracks…')):
            with self.subTest(scenario=scenario, phase=phase):
                self.gate(scenario, phase, 5)
                self.page.locator('.fg-loading button').click()
                self.assertTrue(self.page.evaluate('async () => await preparing === null'))
                self.assertTrue(self.page.evaluate('preparationClosed'))
                self.assertEqual(self.page.evaluate('fgCurrentView()'), 'text')
                self.assertTrue(self.page.evaluate('JSON.stringify(fgData.read("tlData")) === beforePreparation'))
                self.page.evaluate('''() => {
                    fgViews.timeline.steps = originalSteps;
                    releasePreparation();
                }''')
                facts = self.page.evaluate('''async () => {
                    const api = await fgSwitchView('timeline'), model = api.probe();
                    return {calls:model.N, frames:model.NA,
                        unchanged:JSON.stringify(fgData.read('tlData')) === beforePreparation,
                        synthetic:model.SYN.length, busy:document.getElementById('timelineView').getAttribute('aria-busy')};
                }''')
                self.assertEqual(facts, dict(calls=24000, frames=60000, unchanged=True, synthetic=36000, busy=None))
                self.page.evaluate("fgSwitchView('text'); localStorage.clear()")

    def test_escape_during_row_indexing_keeps_no_partial_canvas_and_can_retry(self):
        self.gate('interleaved', 'Indexing task tracks…', 30)
        self.page.keyboard.press('Escape')
        self.assertTrue(self.page.evaluate('async () => await preparing === null'))
        self.assertEqual(self.page.locator('.tl-canvas').count(), 0)
        self.assertTrue(self.page.evaluate('JSON.stringify(fgData.read("tlData")) === beforePreparation'))
        self.page.evaluate('fgViews.timeline.steps = originalSteps; releasePreparation()')
        facts = self.page.evaluate('''async () => {
            const model = (await fgSwitchView('timeline')).probe();
            return {calls:model.N, frames:model.NA, rows:model.TRACKS[0].rowsI.reduce((sum, row) => sum + row.length, 0)};
        }''')
        self.assertEqual(facts, dict(calls=24000, frames=42000, rows=42000))


if __name__ == '__main__':
    unittest.main()
