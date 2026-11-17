from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import helpers

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


class CpuPreparationFixtureTest(unittest.TestCase):
    def test_group_permutation_preserves_every_time_even_when_stride_shares_a_factor(self):
        from benchmark_preparation import fixture

        with tempfile.TemporaryDirectory(prefix='fg-cpu-fixture-') as directory:
            for count in (1, 2, 23756, 23757):
                destination = Path(directory) / 'report.html'
                fixture(Path(helpers.ROOT) / 'sample.html', destination, 'cpu', count)
                data = helpers.json_script(destination.read_text(), 'tlData')
                starts, current = [], 0
                for delta in data['cols']['start']:
                    current += delta
                    starts.append(current)
                groups = (count + 2) // 3
                self.assertEqual(sorted(starts[1::3]), [group * 6000 + 1000 for group in range(groups)])
                self.assertEqual(data['n'], count + 1)
                self.assertTrue(all(starts[index] + data['cols']['dur'][index] <= data['cols']['dur'][0]
                                    for index in range(1, count + 1)))


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class TimelinePreparationTest(unittest.TestCase):
    def run_model(self, assertions, cpu=False):
        asset = Path(__file__).resolve().parents[1] / 'assets' / 'timeline' / 'model.js'
        source = asset.read_text(encoding='utf-8')
        boundary = '// Idle tracks start collapsed' if cpu else 'for (const r of D.offcpu) {   // [task, start, end, depth'
        source = source[:source.index(boundary)]
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
                    errs:[], chains:[], errnames:{}, offcpu:[], resched:[], irqoff:[], caps:{}};
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
            function cpuSnapshot(model) {
                return {runs:Array.from(model.CPU_SEGS, ([cpu, runs])=>
                    [cpu,Array.from(runs.s),Array.from(runs.e),Array.from(runs.t)]),
                    irqs:Array.from(model.CPU_IRQ), cpus:model.CPU_LIST};
            }
            function referenceCpu(model) {
                const runs=new Map(), irqs=new Map();
                if (!['abs','rel'].includes(input.mode)) return {runs:[],irqs:[],cpus:[]};
                for (let task=0;task<input.tasks.length;task++) {
                    if (input.tasks[task].kind!==0) continue;
                    for (const [start,end,cpu] of input.tasks[task].runs||[]) {
                        if (cpu<0 || end<=start) continue;
                        if (!runs.has(cpu)) runs.set(cpu,[]);
                        runs.get(cpu).push([start,end,task]);
                    }
                }
                for (const records of runs.values()) {
                    records.sort((left,right)=>left[0]-right[0]);
                    for (let ordinal=1;ordinal<records.length;ordinal++)
                        if (records[ordinal][0]<records[ordinal-1][1]) records[ordinal-1][1]=records[ordinal][0];
                }
                if (input.cols.cpu) for (let ordinal=0;ordinal<model.N;ordinal++) {
                    const context=model.sFlags[ordinal]&3, parent=model.sParent[ordinal], cpu=input.cols.cpu[ordinal];
                    if (!context || parent>=0 && (model.sFlags[parent]&3)===context || cpu<0) continue;
                    if (!irqs.has(cpu)) irqs.set(cpu,[]);
                    irqs.get(cpu).push([model.sStart[ordinal],model.sEnd[ordinal],context]);
                    if (!runs.has(cpu)) runs.set(cpu,[]);
                }
                for (const records of irqs.values()) records.sort((left,right)=>left[0]-right[0]);
                return {runs:Array.from(runs,([cpu,records])=>[cpu,...[0,1,2].map(column=>records.map(record=>record[column]))]),
                    irqs:Array.from(irqs),cpus:Array.from(runs.keys()).sort((left,right)=>left-right)};
            }
        '''
        factory = 'function* prepare() {\n' + source + '''
            function callerOf(index) { return sCaller && sCaller[index] >= 0 ? S[sCaller[index]] : null; }
            return {N, NA, SYN, S, sStart, sEnd, sDepth, sTask, sFlags, sName, sParent,
                sLine, sLdAll, sDur, sSelf, TRACKS, sortTimelineRow, buildRows''' + (
                    ', CPU_SEGS, CPU_IRQ, CPU_LIST, CPU_IRQ_INDEX, ORDER' if cpu else '') + '''};
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

    def test_cpu_runs_context_roots_and_stable_ties_match_independent_reference(self):
        self.run_model('''
            for (const mode of ['abs','rel','count']) {
                input=data(6); input.mode=mode;
                input.tasks[0].runs=[[50,80,1],[0,70,1],[50,90,1],[10,10,0],[30,20,0],[0,100,-1]];
                input.tasks[1].runs=[[50,120,1],[0,50,0]];
                input.tasks.push({pid:3,comm:'idle',kind:1,runs:[[0,1000,7]]});
                Object.assign(input.cols,{start:[100,-80,0,0,5,10],task:[0,0,1,1,2,2],
                    flags:[1,2,2,2,3,1],cpu:[0,0,2,2,3,-1],parent:[-1,-1,-1,2,-1,-1],depth:[0,0,0,1,0,0]});
                freeze(input); const original=JSON.stringify(input);
                const {model}=drain(prepare());
                assert.deepEqual(cpuSnapshot(model),referenceCpu(model));
                assert.equal(JSON.stringify(input),original);
            }
        ''', cpu=True)

    def test_cpu_run_collection_yields_even_for_invalid_runs_and_empty_tracks(self):
        self.run_model('''
            for (const empty of [false,true]) {
                input=data(0);
                if (empty) input.tasks=Array.from({length:20000},(_,ordinal)=>({pid:ordinal,comm:'idle',kind:1}));
                else input.tasks[0].runs=Array.from({length:20000},()=>[0,100,-1]);
                freeze(input);
                const {model,labels}=drain(prepare());
                assert.deepEqual(cpuSnapshot(model),referenceCpu(model));
                assert.ok(labels.filter(label=>label==='Collecting CPU runs…').length>=2);
            }
        ''', cpu=True)

    def test_cpu_sort_clamp_and_fill_yield_without_changing_equal_start_ownership(self):
        self.run_model('''
            input=data(0);
            input.tasks[0].runs=Array.from({length:25003},(_,ordinal)=> {
                const start=(ordinal*7919)%10007; return [start,start+100,ordinal%2];
            });
            input.tasks[1].runs=[[5,100,0],[5,200,0],[90000,90001,2]];
            freeze(input); const original=JSON.stringify(input);
            const {model,labels}=drain(prepare());
            assert.deepEqual(cpuSnapshot(model),referenceCpu(model));
            for (const phase of ['Sorting CPU runs…','Clipping CPU runs…','Filling CPU columns…'])
                assert.ok(labels.includes(phase),phase);
            assert.equal(JSON.stringify(input),original);
        ''', cpu=True)

    def test_cpu_interrupt_scan_yields_for_nonirq_and_missing_cpu_records(self):
        self.run_model('''
            for (const flags of [0,1]) {
                input=data(20000);
                input.cols.flags.fill(flags); input.cols.cpu=Array(20000).fill(-1);
                freeze(input);
                const {model,labels}=drain(prepare());
                assert.deepEqual(cpuSnapshot(model),referenceCpu(model));
                assert.equal(model.CPU_IRQ.size,0);
                assert.ok(labels.filter(label=>label==='Collecting CPU interrupts…').length>=2);
            }
        ''', cpu=True)

    def test_cpu_array_sort_is_stable_batched_and_keeps_sorted_arrays_untouched(self):
        self.run_model('''
            input=data(0); const {model}=drain(prepare());
            for (const count of [0,1,8191,8192,8193,16385,40003]) {
                const records=Array.from({length:count},(_,ordinal)=>[(ordinal*7919)%127,ordinal%5,ordinal]);
                const expected=records.slice().sort((left,right)=>left[0]-right[0]);
                const {labels}=drain(model.sortTimelineRow(records,(left,right)=>left[0]-right[0],'CPU test sort'));
                assert.deepEqual(records,expected);
                assert.ok(records.every((record,ordinal)=>record===expected[ordinal]));
                if (count>8192) assert.ok(labels.length>1);
                records.slice=()=>{throw new Error('Already sorted CPU records must not be copied');};
                drain(model.sortTimelineRow(records,(left,right)=>left[0]-right[0],'CPU test sort'));
            }
        ''', cpu=True)

    def test_cpu_preparation_can_cancel_every_phase_and_rebuild_exact_frozen_evidence(self):
        self.run_model('''
            input=data(25000);
            input.tasks[0].runs=Array.from({length:25000},(_,ordinal)=> {
                const start=(ordinal*7919)%10007; return [start,start+100,0];
            });
            input.cols.cpu=Array(25000).fill(0);
            for (let ordinal=0,previous=0;ordinal<input.n;ordinal++) {
                const start=(ordinal*7919)%10007;
                input.cols.start[ordinal]=start-previous; previous=start;
                input.cols.flags[ordinal]=ordinal%3+1;
            }
            freeze(input); const original=JSON.stringify(input), expected=cpuSnapshot(drain(prepare()).model);
            for (const phase of ['Collecting CPU runs…','Sorting CPU runs…','Clipping CPU runs…',
                    'Filling CPU columns…','Collecting CPU interrupts…','Sorting CPU interrupts…']) {
                const iterator=prepare(); let found=false;
                for (let step=iterator.next();!step.done;step=iterator.next()) if (step.value===phase) {
                    assert.equal(iterator.return().done,true); found=true; break;
                }
                assert.ok(found,phase);
                assert.equal(iterator.next().done,true);
                assert.equal(JSON.stringify(input),original);
                const rebuilt=drain(prepare()).model;
                assert.deepEqual(cpuSnapshot(rebuilt),expected);
                assert.deepEqual(cpuSnapshot(rebuilt),referenceCpu(rebuilt));
            }
        ''', cpu=True)

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

    def test_row_ranges_and_synthetic_ids_match_native_grouping_at_batch_boundaries(self):
        self.run_model('''
            input = data(20020);
            freeze(input);
            const {model} = drain(prepare());
            for (let index = 0; index < model.N; index++) model.sStart[index] = (index * 7919) % 127;
            for (const count of [0, 1, 8191, 8192, 8193, 16384, 16385]) {
                for (const synthetic of [[], [19999], [0, 19999, 1, 0],
                        Array.from({length:8193}, (value, index) => 19999 - index)]) {
                    Object.freeze(synthetic);
                    const expectedIds = Array.from({length:count}, (value, index) => index + 7).concat(synthetic);
                    let visited = 0, labels = 0;
                    const iterator = model.buildRows(7, 7 + count, synthetic, index => { visited++; return index % 5; });
                    let result;
                    do {
                        visited = 0;
                        result = iterator.next();
                        assert.ok(visited <= 8208, 'Depth scans must remain bounded');
                        if (!result.done) labels++;
                    } while (!result.done);
                    const [rows, maximum] = result.value;
                    assert.equal(maximum, expectedIds.length ? Math.max(...expectedIds.map(index => index % 5)) : -1);
                    assert.equal(rows.length, maximum + 1);
                    for (let depth = 0; depth <= maximum; depth++) {
                        const expected = expectedIds.filter(index => index % 5 === depth)
                            .sort((left, right) => model.sStart[left] - model.sStart[right] || left - right);
                        assert.deepEqual(Array.from(rows[depth]), expected);
                    }
                    if (count > 8192) assert.ok(labels >= 3);
                }
            }
        ''')

    def test_track_indexing_never_joins_history_sized_recorded_identity_arrays(self):
        self.run_model('''
            for (const inline of [false, true]) {
                input = data(20004);
                input.cols.task.fill(1, 10002);
                for (let index = 1; index < input.n; index += 2) {
                    input.cols.parent[index] = index - 1;
                    input.cols.depth[index] = 3;
                    input.cols.caller[index] = 2;
                }
                if (inline) input.inline = {'2':[['root', 'root.c', 1], ['outer', 'outer.h', 2]]};
                freeze(input);
                const concat = Array.prototype.concat;
                let copied = 0, model;
                try {
                    Array.prototype.concat = function(...extra) {
                        if (this.length >= 8192 && this.every(Number.isInteger)) copied += this.length;
                        return Reflect.apply(concat, this, extra);
                    };
                    model = drain(prepare()).model;
                } finally { Array.prototype.concat = concat; }
                assert.equal(copied, 0, 'Traverse recorded ID ranges without copying them into view arrays');
                for (const track of model.TRACKS) {
                    const ids = Array.from({length:model.NA}, (value, index) => index)
                        .filter(index => model.sTask[index] === track.id);
                    for (const [rows, depthOf, visible] of [
                            [track.rowsT, index => model.sDepth[index], index => index < model.N || !!(model.sFlags[index] & (1 << 24))],
                            [track.rowsI, index => model.sLdAll[index], () => true]]) {
                        for (let depth = 0; depth < rows.length; depth++) {
                            const expected = ids.filter(index => visible(index) && depthOf(index) === depth)
                                .sort((left, right) => model.sStart[left] - model.sStart[right] || left - right);
                            assert.deepEqual(Array.from(rows[depth]), expected);
                        }
                    }
                    if (!inline) assert.equal(track.rowsT, track.rowsI);
                }
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
        fixture(root / 'base.html', root / 'cpu.html', 'cpu', 25000)
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

    def test_cancel_cpu_collection_sort_and_fill_never_publishes_partial_tracks(self):
        for phase in ('Collecting CPU runs…', 'Sorting CPU runs…', 'Clipping CPU runs…',
                      'Filling CPU columns…', 'Collecting CPU interrupts…', 'Sorting CPU interrupts…'):
            with self.subTest(phase=phase):
                self.gate('cpu', phase, 1)
                self.page.locator('.fg-loading button').click()
                self.assertTrue(self.page.evaluate('async () => await preparing === null'))
                self.assertTrue(self.page.evaluate('preparationClosed'))
                self.assertEqual(self.page.evaluate('fgCurrentView()'), 'text')
                self.assertEqual(self.page.locator('.tl-canvas').count(), 0)
                self.assertTrue(self.page.evaluate('JSON.stringify(fgData.read("tlData")) === beforePreparation'))
                self.page.evaluate('fgViews.timeline.steps = originalSteps; releasePreparation()')
                facts = self.page.evaluate('''async () => {
                    const model=(await fgSwitchView('timeline')).probe();
                    return {calls:model.N,irqs:model.CPU_IRQ.get(0).length,runs:model.CPU_SEGS.get(0).s.length,
                        unchanged:JSON.stringify(fgData.read('tlData'))===beforePreparation};
                }''')
                self.assertEqual(facts, dict(calls=25001, irqs=25000, runs=8335, unchanged=True))
                self.page.evaluate("fgSwitchView('text'); localStorage.clear()")

    def test_escape_during_cpu_collection_keeps_the_input_and_can_reopen(self):
        self.gate('cpu', 'Collecting CPU interrupts…', 1)
        self.page.keyboard.press('Escape')
        self.assertTrue(self.page.evaluate('async () => await preparing === null'))
        self.assertTrue(self.page.evaluate('preparationClosed'))
        self.assertEqual(self.page.locator('.tl-canvas').count(), 0)
        self.assertTrue(self.page.evaluate('JSON.stringify(fgData.read("tlData")) === beforePreparation'))
        self.page.evaluate('fgViews.timeline.steps = originalSteps; releasePreparation()')
        facts = self.page.evaluate('''async () => {
            const model=(await fgSwitchView('timeline')).probe();
            return {calls:model.N,irqs:model.CPU_IRQ.get(0).length};
        }''')
        self.assertEqual(facts, dict(calls=25001, irqs=25000))


if __name__ == '__main__':
    unittest.main()
