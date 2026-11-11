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

    def test_version_two_sources_prepare_navigation_without_dom_or_inferred_symbols(self):
        self.run_model('''
            const input = data([[2, 'plain', {func:'not-expandable'}], [4, 'first', {}],
                [7, 'second', {}], [10, 'same source', {}]]);
            input.version = 2;
            input.rows.forEach((row, index) => row.push([null, 0, 3, 0][index]));
            const original = JSON.stringify(input);
            input.rows.forEach(Object.freeze);
            Object.freeze(input.rows);
            const model = drain(fgTextModel.prepare(input));
            assert.equal(model.version, 2);
            assert.deepEqual(model.records.map(record => record.source), [null, 0, 3, 0]);
            const navigation = drain(fgTextModel.prepareNavigation(model));
            assert.deepEqual(navigation.order, [2, 4, 7, 10]);
            assert.deepEqual(navigation.expandable, [1, 2, 3]);
            assert.equal(navigation.next(0, 1), 1);
            assert.equal(navigation.boundary(true), 3);
            assert.equal(model.records[0].getAttribute('data-func'), 'not-expandable');
            assert.equal(model.records[1].getAttribute('data-preempt'), null);
            assert.equal(JSON.stringify(input), original);
            assert.equal(fgTextModel.prepare(input).next().value, model);
        ''')

    def test_legacy_source_capability_is_unknown_not_unexpandable(self):
        self.run_model('''
            const model = drain(fgTextModel.prepare(data([[1, 'legacy', {func:'worker'}]])));
            assert.equal(model.version, 1);
            assert.equal(model.records[0].source, undefined);
            assert.throws(() => drain(fgTextModel.prepareNavigation(model)), /source metadata/);
        ''')

    def test_invalid_source_metadata_is_rejected_before_model_caching(self):
        self.run_model('''
            for (const source of [-1, true, false, '0', 1.5, NaN, Infinity, 2**53, undefined]) {
                const input = data([[1, 'row', {}]]);
                input.version = 2;
                input.rows[0].push(source);
                assert.throws(() => drain(fgTextModel.prepare(input)), /Invalid Text filter index/);
                input.rows[0][input.rows[0].length - 1] = 0;
                assert.equal(drain(fgTextModel.prepare(input)).records[0].source, 0);
            }
            const missing = data([[1, 'row', {}]]);
            missing.version = 2;
            assert.throws(() => drain(fgTextModel.prepare(missing)), /Invalid Text filter index/);
        ''')

    def test_source_navigation_preparation_can_cancel_and_retry_all_records(self):
        self.run_model('''
            const input = data(Array.from({length:2500}, (value, index) => [index*2+1, 'row '+index, {}]));
            input.version = 2;
            input.rows.forEach((row, index) => row.push(index % 3 === 0 ? null : index % 17));
            const model = drain(fgTextModel.prepare(input));
            const cancelled = fgTextModel.prepareNavigation(model);
            assert.equal(cancelled.next().done, false);
            cancelled.return();
            const retry = fgTextModel.prepareNavigation(model);
            assert.equal(retry.next().done, false);
            const navigation = drain(retry);
            assert.deepEqual(navigation.order, input.rows.map(row => row[0]));
            assert.deepEqual(navigation.expandable, input.rows.map((row, index) => index).filter(index => index % 3));
            assert.deepEqual(drain(fgTextModel.prepareNavigation(model)).order, navigation.order);
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

    def test_fold_members_use_prepared_ranges_without_rescanning_record_attributes(self):
        self.run_model('''
            const rows = Array.from({length:600}, (unused, index) => [index * 3 + 1, 'row', {'fold-id':'other' + index}]);
            rows[400][2] = {'fold-id':'target', 'fold-type':'entry'};
            rows[401][2] = {'fold-id':'child', 'fold-type':'inner', 'fold-ancestors':'target target'};
            rows[403][2] = {'fold-id':'target', 'fold-type':'exit'};
            rows[599][2] = {'fold-ancestors':'target'};
            const model = drain(fgTextModel.prepare(data(rows)));
            const folding = fgTextModel.folding(model);
            for (const record of model.records) record.getAttribute = () => { throw new Error('Full record scan'); };
            assert.deepEqual(Array.from(folding.members('target'), record => record.row[0]), [1201,1204,1210,1798]);
            assert.deepEqual(Array.from(folding.members('missing')), []);
        ''')

    def test_fold_scope_unions_merge_overlaps_without_filling_interleaved_gaps(self):
        self.run_model('''
            const model = drain(fgTextModel.prepare(data([
                [2, 'outer', {'fold-id':'outer', 'fold-type':'entry'}],
                [7, 'other', {'fold-id':'other', 'fold-type':'entry'}],
                [11, 'inner', {'fold-id':'inner', 'fold-type':'entry', 'fold-ancestors':'outer'}],
                [13, 'unrelated', {}],
                [19, 'leaf', {'fold-id':'inner', 'fold-type':'inner', 'fold-ancestors':'outer inner inner', func:'leaf'}],
                [23, 'inner exit', {'fold-id':'inner', 'fold-type':'exit', 'fold-ancestors':'outer'}],
                [29, 'outer exit', {'fold-id':'outer', 'fold-type':'exit'}],
                [31, 'other exit', {'fold-id':'other', 'fold-type':'exit'}]])));
            const folding = fgTextModel.folding(model);
            model.records[Symbol.iterator] = () => { throw new Error('Global record enumeration'); };
            const numbers = scopes => Array.from(folding.membersOf(scopes), record => record.row[0]);
            assert.deepEqual(numbers(['inner', 'outer', 'inner', 'missing']), [2,11,19,23,29]);
            assert.deepEqual(numbers(['other', 'inner']), [7,11,19,23,31]);
            assert.deepEqual(numbers(['other', 'outer']), [2,7,11,19,23,29,31]);
            assert.deepEqual(numbers([]), []);
            assert.deepEqual(numbers(['missing']), []);
        ''')

    def test_fold_range_membership_and_unions_match_an_independent_randomized_reference(self):
        self.run_model('''
            let seed = 42871;
            const random = () => (seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0);
            const ids = ['a', 'a1', 'a10', 'b', 'quoted"', '层级', null, ''];
            const rows = Array.from({length:1200}, (unused, index) => [index * 5 + 1, 'row', {
                'fold-id':ids[random() % ids.length], 'fold-type':['entry', 'inner', 'exit', null][random() % 4],
                'fold-ancestors':Array.from({length:random() % 6}, () => ids[random() % ids.length] || '').join('  '),
                func:random() % 3 ? 'call' : null}]);
            const input = data(rows);
            const original = JSON.stringify(input);
            const model = drain(fgTextModel.prepare(input));
            const folding = fgTextModel.folding(model);
            const belongs = (record, id) => record.getAttribute('data-fold-id') === id ||
                (record.getAttribute('data-fold-ancestors') || '').split(/\\s+/).filter(Boolean).includes(id);
            for (const id of [...ids, 'missing', undefined]) {
                assert.deepEqual(Array.from(folding.members(id)), model.records.filter(record => belongs(record, id)));
            }
            for (let query = 0; query < 100; query++) {
                const selected = Array.from({length:random() % 12}, () => ids[random() % ids.length]);
                const expected = model.records.filter(record => selected.some(id => belongs(record, id)));
                assert.deepEqual(Array.from(folding.membersOf(selected)), expected);
            }
            assert.equal(JSON.stringify(input), original);
        ''')

    def test_fold_index_handles_deep_and_ancestry_only_scopes_without_recursion(self):
        self.run_model('''
            const ids = Array.from({length:3000}, (unused, index) => 'scope' + index);
            const model = drain(fgTextModel.prepare(data([[3, 'header', {}],
                [9, 'partial leaf', {'fold-id':'leaf', 'fold-type':'inner', func:'leaf', 'fold-ancestors':ids.join(' ')}],
                [20, 'outside', {}]])));
            const folding = fgTextModel.folding(model);
            for (const id of ids) assert.deepEqual(Array.from(folding.members(id)), [model.records[1]]);
            assert.deepEqual(Array.from(folding.membersOf(ids)), [model.records[1]]);
            assert.equal(folding.blocks.get('scope2999').entry, null);
            assert.equal(folding.blocks.get('scope2999').calls, 1);
            assert.equal(folding.visible(model.records[1], {scope2999:true}, null), false);
        ''')

    def test_cancelling_fold_range_finalization_does_not_publish_partial_metadata(self):
        self.run_model('''
            const model = drain(fgTextModel.prepare(data(Array.from({length:1800}, (unused, index) =>
                [index + 1, 'entry', {'fold-id':'scope' + index, 'fold-type':'entry', func:'call'}]))));
            const cancelled = fgTextModel.prepareFolding(model);
            let step;
            do { step = cancelled.next(); } while (!step.done && !String(step.value).startsWith('Indexing Text fold ranges:'));
            assert.equal(step.done, false);
            cancelled.return();
            const retry = fgTextModel.prepareFolding(model);
            assert.match(retry.next().value, /Preparing Text fold metadata/);
            const folding = drain(retry);
            assert.equal(folding.blocks.size, 1800);
            assert.deepEqual(Array.from(folding.membersOf(['scope1799', 'scope0'])), [model.records[0], model.records[1799]]);
            const cached = fgTextModel.prepareFolding(model).next();
            assert.equal(cached.done, true);
            assert.equal(cached.value, folding);
        ''')

    def test_navigation_uses_presentation_order_nearest_calls_and_wraparound_without_dom(self):
        self.run_model('''
            const order = [5,1,9,3];
            const original = order.slice();
            const navigation = fgTextModel.navigation(order, [1,3]);
            assert.deepEqual(navigation.order, original);
            assert.equal(navigation.indexOf(9), 2);
            assert.equal(navigation.indexOf(8), -1);
            assert.equal(navigation.next(0, 1), 1);
            assert.equal(navigation.next(2, -1), 1);
            assert.equal(navigation.next(1, 1), 3);
            assert.equal(navigation.next(3, 1), 1);
            assert.equal(navigation.next(1, -1), 3);
            assert.equal(navigation.next(-1, 1), 1);
            assert.equal(navigation.next(-1, -1), 3);
            navigation.setVisible(1, false);
            navigation.refresh();
            assert.deepEqual(navigation.expandable, [3]);
            navigation.reorder([3,9,5,1]);
            navigation.refresh();
            assert.equal(navigation.indexOf(1), 3);
            assert.deepEqual(navigation.expandable, [0]);
            assert.deepEqual(order, original);
        ''')

    def test_navigation_validates_complete_orders_and_supports_empty_visible_sets(self):
        self.run_model('''
            const navigation = fgTextModel.navigation([2,4,7], [4]);
            for (const invalid of [[2,4], [2,4,4], [2,4,8]]) {
                assert.throws(() => navigation.reorder(invalid), /Invalid Text navigation/);
                assert.deepEqual(navigation.order, [2,4,7]);
            }
            assert.throws(() => fgTextModel.navigation([2,2], []), /Invalid Text navigation/);
            assert.throws(() => fgTextModel.navigation([2,4], [8]), /Invalid Text navigation/);
            for (const line of [2,4,7]) navigation.setVisible(line, false);
            navigation.refresh();
            assert.equal(navigation.next(1, 1), -1);
            assert.equal(navigation.boundary(false), -1);
            assert.equal(navigation.boundary(true), -1);
            navigation.setVisible(7, true);
            navigation.refresh();
            assert.equal(navigation.next(1, 1), -1);
            assert.equal(navigation.boundary(false), 2);
            assert.equal(navigation.boundary(true), 2);
        ''')

    def test_navigation_searches_large_candidate_lists_with_logarithmic_probes(self):
        self.run_model('''
            const order = Array.from({length:131072}, (unused, index) => index + 1);
            const navigation = fgTextModel.navigation(order, order);
            let probes = 0;
            navigation.expandable = new Proxy(navigation.expandable, {get(target, property) {
                if (/^\\d+$/.test(String(property))) probes++;
                return Reflect.get(target, property);
            }});
            assert.equal(navigation.next(120000, 1), 120001);
            assert.equal(navigation.next(120000, -1), 119999);
            assert.ok(probes < 50, String(probes));
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
