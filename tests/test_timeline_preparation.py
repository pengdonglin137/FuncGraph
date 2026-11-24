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


class OverheadBenchmarkFixtureTest(unittest.TestCase):
    def test_correction_charges_only_recorded_descendants_in_plain_and_inline_fixtures(self):
        from benchmark_overhead import fixture

        with tempfile.TemporaryDirectory(prefix='fg-overhead-fixture-') as directory:
            for scenario in ('plain', 'inline'):
                destination = Path(directory) / 'report.html'
                facts = fixture(Path(helpers.ROOT) / 'sample.html', destination, scenario, 3)
                data = helpers.json_script(destination.read_text(), 'tlData')
                self.assertEqual(facts['calls'], 6)
                self.assertEqual(data['cols']['adj'], [6000, 2000] * 3)
                for index, duration in enumerate(data['cols']['dur']):
                    children = sum(parent == index for parent in data['cols']['parent'])
                    self.assertEqual(data['cols']['adj'][index], max(0, duration - children * 1000 - 2000))
                self.assertEqual(bool(data['inline']), scenario == 'inline')


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class TimelinePreparationTest(unittest.TestCase):
    def test_offcpu_depth_index_preserves_intersections_and_first_record(self):
        asset = Path(helpers.ROOT, 'assets', 'timeline', 'model.js').read_text(encoding='utf-8')
        functions = asset[asset.index('function* indexSchedulingIntervals('):
                          asset.index('for (const track of TRACKS) {\n    track.errorIndex')]
        result = subprocess.run(['node', '-e', functions + '''
            const assert = require('node:assert/strict');
            const SCHED_BLOCK_SIZE = 128, HAS_INL = true, TIMED = true, D = {wakeups: [true]};
            const OPEN_OFF = new Set();
            let inlineView = false;
            const offRow = record => inlineView ? record.ldI : record[3];
            for (const count of [31, 437]) for (const unordered of [false, true]) {
                const off = Array.from({length: count}, (_, index) => {
                    const start = Math.floor(index / 2) * 100;
                    const record = [0, start, start + 100, index < 128 ? 1 : index < 256 ? 32 : 4];
                    record.ldI = index < 128 ? 2 : index < 256 ? 54 : 8;
                    return record;
                });
                if (unordered) {
                    [off[0], off[count - 1]] = [off[count - 1], off[0]];
                    off[1][2] = count * 200;
                    OPEN_OFF.add(off[1]);
                }
                const track = {off}, snapshot = off.slice();
                const steps = indexSchedulingIntervals(off, 1, track);
                let state;
                do state = steps.next(); while (!state.done);
                track.offIndex = state.value;
                assert.equal(track.offIndex === true, count >= SCHED_BLOCK_SIZE && !unordered);
                for (inlineView of [false, true]) for (const [left, right] of [[0, count * 200], [6400, 6400], [350, 17200], [30000, 31000]]) {
                    for (const [first, last] of [[0, 100], [1, 1], [2, 2], [3, 31], [32, 32], [54, 54], [55, 75]]) {
                        const expected = off.filter(record => record[1] <= right && record[2] >= left &&
                            offRow(record) >= first && offRow(record) <= last);
                        const seen = [];
                        const stopped = visitOffIntervals(track, left, right, first, last, (record, depth) => {
                            assert.equal(depth, offRow(record));
                            seen.push(record);
                        });
                        assert.equal(stopped, null);
                        assert.deepEqual(seen, expected);
                        assert.equal(visitOffIntervals(track, left, right, first, last, () => true), expected[0] ?? null);
                    }
                }
                assert.ok(off.every((record, index) => record === snapshot[index]));
                OPEN_OFF.clear();
            }
        '''], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_call_tree_builds_only_the_selected_track_and_preserves_global_slice_ids(self):
        directory = Path(helpers.ROOT, 'assets', 'timeline')
        analysis = (directory / 'analysis.js').read_text(encoding='utf-8')
        interact = (directory / 'interact.js').read_text(encoding='utf-8')
        functions = (analysis[analysis.index('const callTreesByTrack'):analysis.index('const CHILD_PAGE')] +
                     analysis[analysis.index('function* scanCallSubtreeSteps('):analysis.index('// Partition of the measured')] +
                     interact[interact.index('function callTarget('):interact.index('function navigateCall(')])
        result = subprocess.run(['node', '-e', functions + '''
            const assert = require('node:assert/strict');
            const offset = 100000, count = 11, N = offset + count + 2, NA = N + 1;
            const TRACKS = [{id: 0, lo: 0, hi: offset}, {id: 1, lo: offset, hi: offset + count},
                {id: 2, lo: offset + count, hi: N}];
            const parents = new Int32Array(NA).fill(-1), sDepth = new Uint16Array(NA), sTask = new Int32Array(NA);
            const FL = { LEAF: 1 << 21 }, sFlags = new Uint32Array(NA);
            const SYN = [{kids: [offset + 1]}], isSyn = index => index >= N;
            sTask.fill(1, offset, offset + count); sTask.fill(2, offset + count, N); sTask[N] = 1;
            sDepth.set([0, 1, 3, 1, 0, 0, 1, 2, 3, 2, 2], offset);
            parents.set([-1, 0, 1, 0, 0, -1, -2, 6, 7, 3, 3].map(relative =>
                relative === -1 ? -1 : relative === -2 ? 0 : offset + relative), offset);
            parents[N] = offset;
            const visited = [];
            const sParent = new Proxy(parents, {get(array, key) {
                if (typeof key === 'string' && /^\\d+$/.test(key)) visited.push(Number(key));
                return array[key];
            }});
            assert.equal(callTarget(N, 'child'), offset + 1);
            assert.equal(callTarget(N, 'next'), -1);
            assert.equal(callTreesByTrack.has(TRACKS[1]), false);
            assert.equal(visited.length, 0);
            assert.equal(callTreeFor(offset), undefined);
            const tree = callTreeSteps(offset).next().value;
            assert.deepEqual(visited, Array.from({length: count}, (_, position) => offset + count - 1 - position));
            assert.equal(tree.first.length + tree.next.length + tree.previous.length, count * 3);
            assert.strictEqual(callTreeFor(offset + 10), tree);
            assert.equal(visited.length, count);
            assert.equal(callTreesByTrack.has(TRACKS[0]), false);
            assert.equal(callTreesByTrack.has(TRACKS[2]), false);
            assert.equal(callTarget(offset, 'next'), offset + 5);
            assert.equal(callTarget(offset + 5, 'previous'), offset);
            assert.equal(callTarget(offset + 3, 'previous'), offset + 1);
            assert.equal(callTarget(offset + 4, 'previous'), -1);
            assert.equal(callTarget(offset + 6, 'parent'), -1);
            assert.deepEqual(directChildren(offset).next(), {done: true, value: [offset + 1, offset + 3]});
            const visit = root => {
                const calls = [];
                for (const step of scanCallSubtreeSteps(root, index => calls.push(index), false)) {}
                return calls;
            };
            assert.deepEqual(visit(offset), [offset + 1, offset + 2, offset + 3, offset + 9, offset + 10]);
            assert.deepEqual(visit(offset + 6), [offset + 7, offset + 8]);
            const other = offset + count;
            assert.equal(callTreeSteps(other).next().done, true);
            assert.equal(callTarget(other, 'next'), other + 1);
            assert.equal(callTarget(other + 1, 'previous'), other);
            assert.notStrictEqual(callTreeFor(other), tree);
            assert.equal(callTreesByTrack.has(TRACKS[0]), false);
            visited.length = 0;
            const firstReader = callTreeSteps(0), secondReader = directChildren(0);
            assert.equal(firstReader.next().done, false);
            assert.equal(visited.length, CALL_TREE_BATCH);
            assert.equal(secondReader.next().done, false);
            assert.equal(visited.length, CALL_TREE_BATCH * 2);
            firstReader.return();
            assert.equal(callTreeFor(0), undefined);
            const resumed = callTreeSteps(1);
            while (!resumed.next().done) {}
            assert.equal(visited.length, offset);
            assert.deepEqual(secondReader.next(), {done: true, value: []});
            assert.equal(visited.length, new Set(visited).size);
            assert.equal(callTarget(1, 'previous'), 0);
            assert.equal(callTarget(0, 'previous'), -1);
            assert.equal(callTarget(0, 'next'), 1);
            assert.equal(callTarget(offset - 1, 'next'), -1);
        '''], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_child_preview_and_group_ranking_leave_member_lists_unbuilt(self):
        asset = Path(helpers.ROOT, 'assets', 'timeline', 'analysis.js').read_text(encoding='utf-8')
        functions = asset[asset.index('const callTreesByTrack'):asset.index('function* childOrderSteps(')]
        result = subprocess.run(['node', '-e', functions + '''
            const assert = require('node:assert/strict');
            const S = ['root', 'alpha', 'beta', 'gamma'], sName = [0, 1, 2, 1, 3, 1];
            const sParent = [-1, 0, 0, 0, 0, 1], sDepth = [0, 1, 1, 1, 1, 2];
            const sTask = sParent.map(() => 0);
            const TRACKS = [{id: 0, lo: 0, hi: sParent.length}];
            const measured = [100, 4, 8, 4, 1, 1], adjusted = [90, 2, 7, 3, 1, 1];
            let sDur = measured, sSelf = [0, 3, 8, 4, 1, 1];
            const complete = steps => {
                let result;
                do { result = steps.next(); } while (!result.done);
                return result.value;
            };
            const childData = (...args) => complete(childDataSteps(...args));
            const rankChildRows = (...args) => complete(rankChildRowsSteps(...args));
            const summary = data => selectChildPreview(data.groups).next().value.map(group =>
                [group.name, group.count, group.first, group.longest, group.inclusive]);

            const data = childData(0, null);
            assert.strictEqual(data.ids, data.raw.ids);
            assert.deepEqual(summary(data), [[1, 2, 1, 1, 8], [2, 1, 2, 2, 8], [3, 1, 4, 4, 1]]);
            assert.deepEqual(rankChildRows(data, 'groups', 'calls', null, '').map(group => group.name), [1, 2, 3]);
            assert.deepEqual(rankChildRows(data, 'calls', 'inclusive', null, ''), [2, 1, 3, 4]);
            assert.equal(data.members, null);
            assert.deepEqual(rankChildRows(data, 'calls', 'inclusive', 1, ''), [1, 3]);
            const members = data.members;
            assert.deepEqual(rankChildRows(data, 'calls', 'inclusive', 3, ''), [4]);
            assert.strictEqual(data.members, members);

            const filtered = childData(0, Uint8Array.of(0, 0, 1, 1, 1, 1));
            assert.deepEqual(filtered.ids, [2, 3, 4]);
            assert.deepEqual(summary(filtered), [[2, 1, 2, 2, 8], [1, 1, 3, 3, 4], [3, 1, 4, 4, 1]]);
            assert.equal(filtered.members, null);
            assert.deepEqual(rankChildRows(filtered, 'calls', 'inclusive', 1, ''), [3]);

            sDur = adjusted;
            assert.deepEqual(summary(childData(0, null)), [[2, 1, 2, 2, 7], [1, 2, 1, 3, 5], [3, 1, 4, 4, 1]]);
            sDur = measured;
            assert.strictEqual(childData(0, null), data);
        '''], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_subtree_breakdown_keeps_irq_boundaries_and_cached_cohort_order(self):
        asset = (Path(helpers.ROOT) / 'assets' / 'timeline' / 'analysis.js').read_text(encoding='utf-8')
        functions = (asset[asset.index('const callTreesByTrack'):asset.index('const CHILD_WORK_BATCH')] +
                     asset[asset.index('function* scanCallSubtreeSteps('):asset.index('function breakdownHtml(')])
        result = subprocess.run(['node', '-e', functions + '''
            const assert = require('node:assert/strict');
            const FL = {CTX: 3}, D = {overhead: {child: 5, self: 3}}, PREPARE_BATCH = 8192;
            const sParent = [-1, 0, 1, 1, 0, 4, 5, 0, 7], sTask = sParent.map(() => 0);
            const sDepth = [0, 2, 3, 5, 4, 7, 9, 1, 10];
            const sFlags = [0, 2, 3, 0, 0, 1, 0, 0, 0];
            const sStart = [0, 100, 110, 150, 300, 350, 360, 700, 720];
            const sEnd = [1000, 220, 135, 172, 480, 410, 385, 790, 795];
            const sSelfRaw = [100, 90, 20, 15, 120, 30, 18, 12, 13];
            const TRACKS = [{id: 0, lo: 0, hi: sParent.length,
                off: [[0, 12, 25, 1, 0, 0, 0, 0, false], [0, 40, 48, 2, 0, 0, 0, 0, true]]}];
            let waitScans = 0;
            function* offIntervalBlocks(track) { waitScans++; yield [0, track.off.length]; }
            const breakdown = (...args) => {
                const steps = breakdownSteps(...args);
                let result;
                do { result = steps.next(); } while (!result.done);
                return result.value;
            };
            const measured = breakdown(0);
            assert.deepEqual(measured, {wall: 1000, own: 87, callees: 137, irq: 180,
                offB: 13, offP: 8, ovh: 18, nCalls: 8});
            const cohort = [];
            assert.strictEqual(breakdown(0, index => cohort.push(index)), measured);
            assert.deepEqual(cohort, [1, 2, 3, 4, 5, 6, 7, 8]);
            assert.equal(waitScans, 1);
            const nested = [];
            assert.deepEqual(breakdown(1, index => nested.push(index)), {wall: 120, own: 90, callees: 15,
                irq: 25, offB: 0, offP: 0, ovh: 8, nCalls: 2});
            assert.deepEqual(nested, [2, 3]);
        '''], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dense_hit_cache_uses_current_row_geometry_and_match_state(self):
        render = Path(helpers.ROOT, 'assets', 'timeline', 'render.js').read_text(encoding='utf-8')
        interact = Path(helpers.ROOT, 'assets', 'timeline', 'interact.js').read_text(encoding='utf-8')
        lookup = render[render.index('const DENSE_MATCH_BLOCK'):render.index('// ---- detail panel split')]
        cache = interact[interact.index('const unfilteredDenseHit'):interact.index('let lastDenseOff')]
        result = subprocess.run(['node', '-e', lookup + cache + '''
            const assert = require('node:assert/strict');
            const DENSE_SLICE_WIDTH = 1.5, N = 7, NA = N, range = null;
            const sName = [0, 1, 2, 3, 4, 5, 6];
            const sStart = [0.05, 0.2, 0.45, 0.75, 2.85, 3.1, 0.45];
            const sEnd = [0.05, 0.2, 0.45, 2.8, 2.85, 3.1, 0.45];
            const row = [0, 1, 2, 3, 4, 5], otherRow = [6];
            let searchHits = null, textHits = null, lookups = 0;
            const calculate = densePixelAt;
            densePixelAt = (...args) => { lookups++; return calculate(...args); };
            const original = denseHitAt(row, 0, 0, 1);
            assert.deepEqual(original, {index: 0, count: 3, matches: 3});
            assert.strictEqual(denseHitAt(row, 0, 0, 1), original);
            assert.equal(lookups, 1);

            textHits = Uint8Array.of(0, 1, 1, 0, 0, 0, 0);
            const filtered = denseHitAt(row, 0, 0, 1);
            assert.deepEqual(filtered, {index: 1, count: 3, matches: 2});
            assert.strictEqual(denseHitAt(row, 0, 0, 1), filtered);
            assert.equal(lookups, 2);

            searchHits = Uint8Array.of(1, 0, 1, 0, 0, 0, 0);
            assert.deepEqual(denseHitAt(row, 0, 0, 1), {index: 2, count: 3, matches: 1});
            textHits = Uint8Array.of(0, 1, 0, 0, 0, 0, 0);
            assert.deepEqual(denseHitAt(row, 0, 0, 1), {index: 0, count: 3, matches: 0});
            textHits = searchHits = null;
            assert.strictEqual(denseHitAt(row, 0, 0, 1), original);
            assert.equal(lookups, 4);

            assert.deepEqual(denseHitAt(otherRow, 0, 0, 1), {index: 6, count: 1, matches: 1});
            assert.deepEqual(denseHitAt(row, 2, 0, 1), {index: 4, count: 1, matches: 1});
            assert.deepEqual(denseHitAt(row, 2, 1, 1), {index: 5, count: 1, matches: 1});
            assert.deepEqual(denseHitAt(row, 2, 0, 10), {index: 1, count: 1, matches: 1});
            assert.equal(denseHitAt(row, 1, 0, 1), null);
            assert.equal(denseHitAt(row, 1, 0, 1), null);
            assert.equal(lookups, 9);
        '''], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pixel_group_cache_reuses_boundaries_without_caching_filter_or_wide_slices(self):
        asset = Path(helpers.ROOT, 'assets', 'timeline', 'render.js').read_text(encoding='utf-8')
        pixel = asset[asset.index('function lbPixel('):asset.index('// ---- detail panel split')]
        cache = asset[asset.index('const DENSE_SLICE_WIDTH'):asset.index('function render()')]
        row = asset[asset.index('function drawDenseRun('):asset.index('function drawSlice(')]
        result = subprocess.run(['node', '-e', pixel + cache + row + '''
            const assert = require('node:assert/strict');
            const P = { dense: 0.65, denseFill: 'gray' }, searchHits = true, textHits = false;
            const lbEnd = () => 0, lbStart = () => 0, sliceStyle = index => [index];
            let sStart, sEnd, matches, spans, fills, wide, searches, reads;
            const originalSearch = lbPixel;
            lbPixel = (...args) => { searches++; return originalSearch(...args); };
            const ctx = {globalAlpha: 1, fillRect(left, top, width) { fills.push([left, width, this.fillStyle, this.globalAlpha]); }};
            const drawSlice = (index, start, end) => wide.push([index, start, end]);
            function denseSummary(row, first, end) {
                spans.push([first, end]);
                const match = matches.find(index => first <= index && index < end);
                return { index: row[match ?? first], count: end - first, matches: Number(match !== undefined) };
            }
            function paint(row, selected, start = 0, end = 10, scale = 1) {
                prepareDenseRowView(start, end, scale, {});
                matches = selected;
                spans = []; fills = []; wide = []; searches = 0; reads = 0;
                drawRow(row, 0, 12, start, end, scale, false);
            }
            function setup(starts, ends) {
                sStart = starts;
                sEnd = ends;
                return new Proxy(Array.from(starts.keys()), {
                    get(ids, key) {
                        if (typeof key === 'string' && /^\\d+$/.test(key)) reads++;
                        return ids[key];
                    }
                });
            }
            const singletons = setup([0, 1, 2, 3], [0, 1, 2, 3]);
            paint(singletons, [0, 2]);
            assert.equal(searches, 4);
            assert.deepEqual(spans, [[0, 1], [1, 2], [2, 3], [3, 4]]);
            assert.deepEqual(fills, [[0, 1, 0, 1], [1, 1, 1, 0.18], [2, 1, 2, 1], [3, 1, 3, 0.18]]);
            paint(singletons, [1, 3]);
            assert.equal(searches, 0);
            assert.deepEqual(fills, [[0, 1, 0, 0.18], [1, 1, 1, 1], [2, 1, 2, 0.18], [3, 1, 3, 1]]);
            const boundary = setup([0, 1 - Number.EPSILON / 2, 1], [0, 1, 1]);
            paint(boundary, [1]);
            assert.deepEqual(spans, [[0, 2], [2, 3]]);
            assert.deepEqual(fills, [[0, 1, 'gray', 0.65], [1, 1, 2, 0.18]]);
            assert.equal(densePixelAt(boundary, 0, 0, 1).index, 1);
            const starts = [0.1, 0.2, 1.1, 1.2, 3.1, 3.2, 4.1];
            paint(setup(starts, starts), [1, 3, 5, 6]);
            assert.deepEqual(fills, [[0, 2, 'gray', 0.65], [3, 1, 'gray', 0.65], [4, 1, 6, 1]]);
            const dense = setup([...Array(4096).fill(0), 0.75, 3], [...Array(4096).fill(0), 2.25, 3]);
            paint(dense, [2500]);
            assert.equal(searches, 2);
            assert.ok(reads < 40, `read ${reads} row members for one dense group`);
            assert.deepEqual(spans, [[0, 4096], [4097, 4098]]);
            assert.deepEqual(fills, [[0, 1, 'gray', 0.65], [3, 1, 4097, 0.18]]);
            assert.deepEqual(wide, [[4096, 0.75, 2.25]]);
            assert.deepEqual(densePixelAt(dense, 0, 0, 1), { index: 2500, count: 4096, matches: 1 });
            paint(dense, [3999]);
            assert.equal(searches, 0);
            assert.deepEqual(fills, [[0, 1, 'gray', 0.65], [3, 1, 4097, 0.18]]);
            assert.deepEqual(wide, [[4096, 0.75, 2.25]]);
            paint(dense, [3999], 0, 10, 0.5);
            assert.equal(searches, 2);
            assert.deepEqual(spans, [[0, 4097], [4097, 4098]]);
            assert.deepEqual(wide, []);
            prepareDenseRowView(0, 9, 1, {});
            denseRowView.remaining = 1;
            paint(dense, [3999], 0, 9, 1);
            assert.equal(denseRowView.remaining, 0);
            assert.equal(denseRowView.rows.get(dense).ends.length, 1);
            paint(dense, [3999], 0, 9, 1);
            assert.equal(searches, 1);
            assert.deepEqual(spans, [[0, 4096], [4097, 4098]]);
        '''], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dense_row_fill_reuse_preserves_wide_slice_and_filter_paint_order(self):
        asset = Path(helpers.ROOT, 'assets', 'timeline', 'render.js').read_text(encoding='utf-8')
        pixel = asset[asset.index('function lbPixel('):asset.index('// ---- detail panel split')]
        cache = asset[asset.index('const DENSE_SLICE_WIDTH'):asset.index('function render()')]
        row = asset[asset.index('function drawDenseRun('):asset.index('function drawSlice(')]
        result = subprocess.run(['node', '-e', pixel + cache + row + '''
            const assert = require('node:assert/strict');
            const P = {dense: 0.65, denseFill: 'gray'}, searchHits = true, textHits = null;
            const sStart = [0, 0.5, 3, 3.5, 6, 10, 10.5, 13, 13.5, 16];
            const sEnd = sStart.map((start, index) => index === 4 ? 8 : start);
            const row = Array.from(sStart.keys()), matching = {};
            const lbEnd = () => 0, lbStart = () => 0;
            let denseColor = 'red', fill, assignments, rects;
            const sliceStyle = () => [denseColor];
            const ctx = {
                globalAlpha: 1,
                set fillStyle(value) { fill = value; assignments.push(value); },
                fillRect(left, top, width, height) { rects.push([left, width, fill, this.globalAlpha]); }
            };
            function denseSummary(row, first) { return {index: row[first], matches: Number(first === 0 || first === 7)}; }
            function drawSlice(index, left, right, top, height) {
                ctx.fillStyle = 'blue';
                ctx.globalAlpha = 1;
                ctx.fillRect(left, top, right - left, height);
            }
            for (const replay of [false, false, true]) {
                prepareDenseRowView(0, 20, 1, matching);
                assert.equal(denseRowView.paints.has(row), replay);
                if (replay) { P.denseFill = 'silver'; denseColor = 'green'; }
                assignments = []; rects = [];
                drawRow(row, 0, 12, 0, 20, 1, true);
                assert.deepEqual(assignments, [P.denseFill, 'blue', P.denseFill, denseColor]);
                assert.deepEqual(rects, [
                    [0, 1, P.denseFill, 0.65], [3, 1, P.denseFill, 0.2], [6, 2, 'blue', 1],
                    [10, 1, P.denseFill, 0.2], [13, 1, P.denseFill, 0.65], [16, 1, denseColor, 0.18]
                ]);
                assert.equal(ctx.globalAlpha, 1);
            }
        '''], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wide_row_paint_skips_recording_and_rechecks_density_after_zoom(self):
        asset = Path(helpers.ROOT, 'assets', 'timeline', 'render.js').read_text(encoding='utf-8')
        functions = (asset[asset.index('function lbPixel('):asset.index('// ---- detail panel split')] +
                     asset[asset.index('const DENSE_SLICE_WIDTH'):asset.index('function render()')] +
                     asset[asset.index('function drawDenseRun('):asset.index('function drawSlice(')])
        result = subprocess.run(['node', '-e', functions + '''
            const assert = require('node:assert/strict');
            const P = {dense: 0.65}, searchHits = null, textHits = null;
            const sStart = [0, 3, 6], sEnd = [2, 5, 8], row = [0, 1, 2];
            const sliceStyle = () => ['red'];
            let lookups = 0, wideCalls = 0, denseRuns = 0;
            const lbEnd = () => { lookups++; return 0; }, lbStart = () => 0;
            const ctx = { globalAlpha: 1, fillRect() { denseRuns++; } };
            function drawSlice() { wideCalls++; }

            const matching = {};
            prepareDenseRowView(0, 10, 1, matching);
            drawRow(row, 0, 12, 0, 10, 1, true);
            for (const matches of [matching, {}]) {
                prepareDenseRowView(0, 10, 1, matches);
                drawRow(row, 0, 12, 0, 10, 1, true);
                assert.equal(denseRowView.paints.has(row), false);
                assert.equal(denseRowView.paintRemaining, DENSE_ROW_PAINT_LIMIT);
            }
            assert.equal(lookups, 1);
            assert.equal(wideCalls, 9);
            assert.equal(denseRuns, 0);
            drawRow([], 0, 12, 0, 10, 1, true);
            assert.equal(lookups, 1);

            const uncached = [];
            for (let index = 0; index < WIDE_ROW_CACHE_LIMIT; index++) {
                uncached.push([0, 1, 2]);
                drawRow(uncached[index], 0, 12, 0, 10, 1, true);
            }
            assert.equal(denseRowView.wideRemaining, 0);
            assert.equal(denseRowView.rows.has(uncached.at(-1)), false);
            assert.equal(denseRowView.remaining, DENSE_ROW_GROUP_LIMIT);

            prepareDenseRowView(0, 20, 0.5, matching);
            drawRow(row, 0, 12, 0, 20, 0.5, true);
            prepareDenseRowView(0, 20, 0.5, matching);
            drawRow(row, 0, 12, 0, 20, 0.5, true);
            assert(denseRowView.paints.has(row));
            assert.equal(denseRuns, 6);
        '''], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_trace_line_lookup_preserves_exact_exit_and_preceding_call_ties(self):
        asset = Path(__file__).resolve().parents[1] / 'assets' / 'timeline' / 'model.js'
        source = asset.read_text(encoding='utf-8')
        source = source[source.index('function createTraceLineLookup('):source.index('let traceLineLookup =')]
        result = subprocess.run(['node', '-e', source + '''
            const assert = require('node:assert/strict');
            function reference(entries, exits, count, line) {
                let exact = -1, preceding = -1;
                for (let index = 0; index < count; index++) {
                    if (entries[index] === line || exits?.[index] && exits[index] === line) exact = index;
                    if (entries[index] <= line && (preceding < 0 || entries[index] > entries[preceding])) preceding = index;
                }
                return exact >= 0 ? exact : preceding;
            }
            for (const multiplier of [1, 1000]) {
                const entries = Int32Array.from([9,4,4,3,7,10,11,2], line => line * multiplier);
                const exits = Int32Array.from([12,8,7,6,11,0,13,14], line => line * multiplier);
                for (const endLines of [exits, null]) {
                    for (const count of [0, 1, 7]) {
                        const lookup = createTraceLineLookup(entries, endLines, count);
                        for (let line = 1; line <= 15 * multiplier; line++) {
                            assert.equal(lookup(line), reference(entries, endLines, count, line),
                                JSON.stringify({line,count,multiplier,withExits:!!endLines}));
                        }
                    }
                }
            }
            const entries = Int32Array.from({length: 769}, (_, index) => (index * 17 % 769 + 1) * 32);
            entries[0] = entries[256] = entries[768] = 24_576;
            entries[10] = entries[265] = entries[765] = 42_496;
            const exits = Int32Array.from(entries, (entry, index) => index % 3 ? entry + 8 : 0);
            exits[766] = entries[10];
            for (const count of [255, 256, 257, 512, 768, 769]) {
                const lookup = createTraceLineLookup(entries, exits, count);
                const lines = [1, 42_496, 42_497, 42_504, 42_505, 2_147_483_647];
                for (let rank = 1; rank <= 769; rank++) lines.push(rank * 32, rank * 32 + 1, rank * 32 + 8);
                for (const line of lines) assert.equal(lookup(line), reference(entries, exits, count, line), `${count}:${line}`);
            }
            const signedEntries = Int32Array.from({length: 3073}, (_, index) => (index * 179 % 3079 - 1539) * 65537);
            signedEntries[0] = signedEntries[2048] = -2_147_483_648;
            signedEntries[22] = signedEntries[2050] = 2_147_483_647;
            const signedExits = Int32Array.from(signedEntries, (line, index) => index % 7 ? line : 0);
            signedExits[3072] = signedEntries[0];
            for (const count of [2048, 2049, 3073]) {
                const queries = [-2_147_483_649, -2_147_483_648, -2_147_483_647, 0, 2_147_483_646, 2_147_483_647, 2_147_483_648];
                for (let index = 0; index < count; index += 17) {
                    queries.push(signedEntries[index], signedEntries[index] + 1, signedExits[index]);
                }
                for (const skipZero of [false, true]) {
                    const lines = skipZero ? signedExits : signedEntries;
                    const expected = Array.from({length: count}, (_, index) => index)
                        .filter(index => !skipZero || lines[index] !== 0)
                        .sort((left, right) => lines[left] - lines[right] || left - right);
                    assert.deepEqual([...sortTraceLineOrdinals(lines, count, skipZero)], expected);
                }
                const lookup = createTraceLineLookup(signedEntries, signedExits, count);
                for (const line of queries) assert.equal(lookup(line), reference(signedEntries, signedExits, count, line), `${count}:${line}`);
            }
        '''], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
                sLine, sLdAll, sDur, sSelf, TRACKS, sortTimelineRow, buildRows, setAdjusted,
                durationState: () => ({sDur, sSelf, sSelfRaw, adjOn, maxSelf, maxDuration})''' + (
                    ', CPU_SEGS, CPU_IRQ, CPU_LIST, CPU_IRQ_INDEX, ORDER' if cpu else '') + '''};
        }
        '''
        result = subprocess.run(['node', '-e', setup + factory + assertions],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_duration_switches_ignore_synthetic_parents_and_preserve_full_geometry(self):
        self.run_model('''
            for (const inline of [false, true]) {
                input = data(4);
                Object.assign(input.cols, {start:[0,100,9900,100], dur:[9000,4000,9000,4000],
                    adj:[6000,2000,6000,2000], parent:[-1,0,-1,2], name:[0,1,0,1],
                    caller:[-1,2,-1,2], depth:[0,inline ? 1 : 4,0,inline ? 1 : 4]});
                if (inline) input.inline = {'2':[['root','root.c',1],['outer','outer.h',2],['inner','inner.h',3]]};
                freeze(input);
                const original = JSON.stringify(input), {model} = drain(prepare());
                assert.ok(model.NA > model.N);
                const geometry = JSON.stringify([model.sStart,model.sEnd,model.sParent,model.sDepth,
                    model.sLdAll,model.SYN,model.TRACKS]);
                const raw = model.durationState();
                let corrected;
                for (const on of [true,false,true,false,true]) {
                    model.setAdjusted(on);
                    const current = model.durationState();
                    assert.deepEqual(Array.from(current.sSelf), on ? [4000,2000,4000,2000] : [5000,4000,5000,4000]);
                    assert.deepEqual(Array.from(current.sSelfRaw), [5000,4000,5000,4000]);
                    assert.equal(current.maxSelf, on ? 4000 : 5000);
                    assert.equal(current.maxDuration, on ? 6000 : 9000);
                    if (on) { if (corrected) assert.equal(current.sSelf, corrected); corrected = current.sSelf; }
                    else assert.equal(current.sSelf, raw.sSelf);
                    assert.equal(JSON.stringify(input), original);
                    assert.equal(JSON.stringify([model.sStart,model.sEnd,model.sParent,model.sDepth,
                        model.sLdAll,model.SYN,model.TRACKS]), geometry);
                }
            }
        ''')

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
        for scenario, phase, occurrence in (('inline', 'Building inline frames…', 5),
                                            ('gaps', 'Building untraced frames…', 5),
                                            ('gaps', 'Indexing task tracks…', 5),
                                            ('gaps', 'Indexing call ranges…', 2)):
            with self.subTest(scenario=scenario, phase=phase):
                self.gate(scenario, phase, occurrence)
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
