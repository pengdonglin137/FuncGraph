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
