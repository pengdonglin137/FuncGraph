import json
from pathlib import Path
import shutil
import subprocess
import unittest

from fglib.textindex import FIELDS


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class TextModelTest(unittest.TestCase):
    def run_model(self, source):
        asset = Path(__file__).resolve().parents[1] / 'assets' / 'report' / 'filter-index.js'
        setup = '''
            const assert = require('node:assert/strict');
            globalThis.window = globalThis;
            Object.defineProperty(globalThis, 'document', {get() { throw new Error('DOM access'); }});
            require(ASSET);
            assert.deepEqual(fgTextModel.fields, FIELDS);
            function drain(iterator) {
                let result;
                do { result = iterator.next(); } while (!result.done);
                return result.value;
            }
            function data(rows) {
                return {version:1, fields:Array.from(fgTextModel.fields), rows:rows.map(([number, text, attributes]) =>
                    [number, text, ...fgTextModel.fields.map(field => attributes?.[field] ?? null)])};
            }
        '''.replace('ASSET', json.dumps(str(asset))).replace('FIELDS', json.dumps(list(FIELDS)))
        result = subprocess.run(['node', '-e', setup + source], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_records_keep_stable_identity_and_exact_attribute_semantics_without_dom(self):
        self.run_model('''
            const input = data([[2, '任务 & < raw', {cpu:'0', params:'', preempt:'0'}], [4, 'next', {cpu:'1'}]]);
            const model = drain(fgTextModel.prepare(input));
            const first = model.records[0];
            const retained = Array.from(model.records);
            assert.equal(first, retained[0]);
            assert.equal(first.textContent, '任务 & < raw');
            assert.equal(first.querySelector('.line-content'), first);
            assert.equal(first.querySelector('.missing'), null);
            assert.equal(first.getAttribute('data-line-number'), '2');
            assert.equal(first.getAttribute('data-cpu'), '0');
            assert.equal(first.getAttribute('data-params'), '');
            assert.equal(first.getAttribute('data-preempt'), '0');
            assert.equal(first.getAttribute('data-ret'), null);
            assert.equal(first.getAttribute('missing'), null);
            assert.equal(retained[1].getAttribute('data-cpu'), '1');
            assert.equal(first.getAttribute('data-cpu'), '0');
        ''')

    def test_complete_preparation_is_cached_without_mutating_input(self):
        self.run_model('''
            const input = data([[1, 'row', {comm:'worker'}]]);
            const original = JSON.stringify(input);
            Object.freeze(input.fields);
            input.rows.forEach(Object.freeze);
            Object.freeze(input.rows);
            Object.freeze(input);
            const model = drain(fgTextModel.prepare(input));
            const cached = fgTextModel.prepare(input).next();
            assert.equal(cached.done, true);
            assert.equal(cached.value, model);
            assert.equal(JSON.stringify(input), original);
        ''')

    def test_cancelled_preparation_never_caches_a_partial_model(self):
        self.run_model('''
            const input = data(Array.from({length:2500}, (value, index) => [index+1, 'row '+index, {}]));
            const cancelled = fgTextModel.prepare(input);
            assert.equal(cancelled.next().done, false);
            cancelled.return();
            const retry = fgTextModel.prepare(input);
            assert.equal(retry.next().done, false);
            const model = drain(retry);
            assert.equal(model.records.length, 2500);
            assert.equal(model.records[2499].getAttribute('data-line-number'), '2500');
            assert.equal(fgTextModel.prepare(input).next().value, model);
        ''')

    def test_validation_rejects_incomplete_or_misordered_records_before_caching(self):
        self.run_model('''
            const mutations = [input => input.version++, input => input.fields.reverse(),
                input => input.rows[0][0] = 0, input => input.rows[1][0] = input.rows[0][0],
                input => input.rows[0][2] = 5, input => input.rows[0][1] = null,
                input => input.rows[0].pop()];
            for (const mutate of mutations) {
                const input = data([[1, 'first', {}], [2, 'second', {}]]);
                mutate(input);
                assert.throws(() => drain(fgTextModel.prepare(input)), /(Unsupported|Invalid) Text filter index/);
            }
        ''')

    def test_filter_results_are_independent_of_any_mounted_projection(self):
        self.run_model('''
            globalThis.parseFilterExpr = text => ({ast:text ? {type:'COND'} : null, sortDir:'asc'});
            globalThis.collectGraphConditions = () => [];
            globalThis.fgGraphSetsSteps = function* () { return {}; };
            globalThis.evalExpr = record => record.getAttribute('data-cpu') === '0';
            const input = data([[1, '', {cpu:'0'}], [2, '-----', {cpu:'0'}],
                [3, 'included', {cpu:'0'}], [5, 'excluded', {cpu:'1'}]]);
            const model = drain(fgTextModel.prepare(input));
            const filtered = drain(fgTextModel.filter(model, 'cpu:0'));
            assert.deepEqual(Array.from(filtered.lines), [3]);
            assert.equal(filtered.visible, 1);
            assert.equal(filtered.total, 4);
            assert.equal(filtered.sortDir, 'asc');
            const all = drain(fgTextModel.filter(model, ''));
            assert.equal(all.lines, null);
            assert.equal(all.visible, 4);
            assert.equal(model.records[0].textContent, '');
            globalThis.parseFilterExpr = () => ({ast:null, sortDir:'desc'});
            const sorted = drain(fgTextModel.filter(model, 'sort:desc'));
            assert.equal(sorted.text, 'sort:desc');
            assert.equal(sorted.lines, null);
            assert.equal(sorted.visible, 4);
        ''')

    def test_folding_counts_members_and_visibility_compose_without_dom(self):
        self.run_model('''
            const input = data([[1, 'A {', {'fold-id':'a', 'fold-type':'entry', func:'A'}],
                [2, 'B {', {'fold-id':'b', 'fold-type':'entry', 'fold-ancestors':'a', func:'B'}],
                [3, 'leaf()', {'fold-id':'b', 'fold-type':'inner', 'fold-ancestors':'a b b', func:'leaf'}],
                [4, 'B }', {'fold-id':'b', 'fold-type':'exit', 'fold-ancestors':'a'}],
                [5, 'A }', {'fold-id':'a', 'fold-type':'exit'}], [6, 'outside', {}]]);
            const original = JSON.stringify(input);
            const model = drain(fgTextModel.prepare(input));
            const folding = fgTextModel.folding(model);
            assert.equal(fgTextModel.folding(model), folding);
            assert.equal(folding.records, model.records);
            assert.equal(folding.blocks.get('a').calls, 2);
            assert.equal(folding.blocks.get('b').calls, 1);
            assert.equal(folding.blocks.get('a').entry, model.records[0]);
            assert.equal(folding.blocks.get('b').exit, model.records[3]);
            assert.deepEqual(Array.from(folding.members('a'), record => record.row[0]), [1,2,3,4,5]);
            assert.deepEqual(Array.from(folding.members('b'), record => record.row[0]), [2,3,4]);
            assert.deepEqual(Array.from(folding.visibleLines({a:true}, null)), [1,6]);
            assert.deepEqual(Array.from(folding.visibleLines({b:true}, null)), [1,2,5,6]);
            assert.deepEqual(Array.from(folding.visibleLines({a:true,b:true}, new Set([2,3,6]))), [6]);
            assert.deepEqual(Array.from(folding.visibleLines({b:true}, new Set([2,3,6]))), [2,6]);
            assert.deepEqual(Array.from(folding.visibleLines({}, new Set([2,3,6]))), [2,3,6]);
            assert.equal(JSON.stringify(input), original);
        ''')

    def test_folding_handles_interleaved_tasks_missing_entries_and_deep_ancestry(self):
        self.run_model('''
            const depth = 600;
            const ancestors = Array.from({length:depth}, (unused, index) => 'scope' + index);
            const input = data([[1, 'root', {'fold-id':'root', 'fold-type':'entry', func:'same'}],
                [2, 'other', {'fold-id':'other', 'fold-type':'entry', func:'same'}],
                [3, 'leaf', {'fold-id':'root', 'fold-type':'inner', 'fold-ancestors':'root', func:'leaf'}],
                [4, 'other exit', {'fold-id':'other', 'fold-type':'exit'}],
                [5, 'root exit', {'fold-id':'root', 'fold-type':'exit'}],
                [6, 'partial', {'fold-id':'partial', 'fold-type':'inner', 'fold-ancestors':ancestors.join(' '), func:'leaf'}]]);
            const model = drain(fgTextModel.prepare(input));
            const folding = fgTextModel.folding(model);
            assert.deepEqual(Array.from(folding.members('root'), record => record.row[0]), [1,3,5]);
            assert.deepEqual(Array.from(folding.visibleLines({root:true}, null)), [1,2,4,6]);
            assert.equal(folding.blocks.get('scope599').entry, null);
            assert.equal(folding.blocks.get('scope599').calls, 1);
            assert.equal(folding.visible(model.records[5], {scope599:true}, null), false);
            assert.deepEqual(Array.from(folding.members('scope59'), record => record.row[0]), [6]);
            assert.deepEqual(Array.from(folding.members('scope600')), []);
        ''')

    def test_fold_preparation_yields_and_never_caches_partial_metadata(self):
        self.run_model('''
            const input = data(Array.from({length:2501}, (unused, index) => [index + 1, 'call',
                {'fold-id':'root', 'fold-type':index ? 'inner' : 'entry', func:'call',
                    'fold-ancestors':index ? 'root' : ''}]));
            const model = drain(fgTextModel.prepare(input));
            const cancelled = fgTextModel.prepareFolding(model);
            assert.equal(cancelled.next().done, false);
            cancelled.return();
            const retry = fgTextModel.prepareFolding(model);
            assert.equal(retry.next().done, false);
            const folding = drain(retry);
            assert.equal(folding.records.length, 2501);
            assert.equal(folding.blocks.get('root').calls, 2500);
            assert.equal(folding.blocks.get('root').entry, model.records[0]);
            const cached = fgTextModel.prepareFolding(model).next();
            assert.equal(cached.done, true);
            assert.equal(cached.value, folding);
            assert.equal(fgTextModel.folding(model), folding);
            assert.deepEqual(Array.from(folding.visibleLines({root:true}, null)), [1]);
        ''')

    def test_async_indexed_filter_preserves_result_shape_and_cancellation(self):
        self.run_model('''
            globalThis.parseFilterExpr = text => ({ast:text ? {type:'COND'} : null, sortDir:null});
            globalThis.collectGraphConditions = () => [];
            globalThis.fgGraphSetsSteps = function* () { return {}; };
            globalThis.evalExpr = () => true;
            (async () => {
                const input = data([[1, 'first', {}], [2, 'second', {}]]);
                const result = await fgIndexedFilter(input, 'func:any', {aborted:false}, () => {});
                assert.deepEqual(Object.keys(result).sort(), ['lines','sortDir','text','total','visible']);
                assert.deepEqual(Array.from(result.lines), [1,2]);
                await assert.rejects(fgIndexedFilter(input, 'func:any', {aborted:true}, () => {}),
                    error => error.name === 'AbortError');
            })().catch(error => { console.error(error); process.exitCode = 1; });
        ''')
