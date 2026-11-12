import json
from pathlib import Path
import random
import re
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

    def test_locate_uses_original_ordinals_for_sparse_lines_regardless_of_display_order(self):
        self.run_model('''
            const model = drain(fgTextModel.prepare(data([
                [2, 'header'], [8, 'later', {pid:'20'}], [101, 'first task', {pid:'1'}], [9000, 'end']])));
            const original = model.records.slice();
            fgTextModel.order(model, 'task');
            for (const [ordinal, record] of original.entries()) {
                const found = fgTextModel.locate(model, record.row[0]);
                assert.equal(found.ordinal, ordinal); assert.equal(found.record, record);
            }
            for (const number of [1,3,9,102,8999,9001]) assert.equal(fgTextModel.locate(model, number), null);
            for (const number of [0,-1,NaN,Infinity,1.5,'2',null,Number.MAX_SAFE_INTEGER+1])
                assert.throws(() => fgTextModel.locate(model, number), /Invalid Text line identity/);
            assert.equal(fgTextModel.locate(drain(fgTextModel.prepare(data([]))), 1), null);
            assert.deepEqual(model.records, original);
        ''')

    def test_locate_has_logarithmic_probes_without_building_another_full_row_index(self):
        self.run_model('''
            const model = drain(fgTextModel.prepare(data(Array.from({length:131073}, (_, index) => [index*3+2, 'row']))));
            let reads = 0;
            model.records = new Proxy(model.records, {get(target, property) {
                if (/^[0-9]+$/.test(String(property))) reads++;
                return Reflect.get(target, property);
            }});
            for (const ordinal of [0,511,512,4095,4096,65536,131072]) {
                reads = 0;
                assert.equal(fgTextModel.locate(model, ordinal*3+2).ordinal, ordinal);
                assert.ok(reads <= 20, String(reads));
            }
        ''')

    def test_order_plan_preserves_sparse_rows_task_headers_switches_and_exact_gaps(self):
        self.run_model('''
            const input = data([
                [2, '# header', {}], [4, '', {}],
                [10, '1.000000 | A()', {cpu:'0', pid:'7', comm:'工人'}],
                [12, ' 0) worker-7 => other-9', {cpu:'0', pid:'7', comm:'工人'}],
                [17, '-----', {}],
                [25, '1.001000 | B()', {cpu:'1', pid:'9', comm:'other'}], [28, '', {}],
                [40, '1.002000 | A()', {cpu:'1', pid:'7', comm:'工人'}],
                [45, '1.003000 | C()', {cpu:'10', pid:'9', comm:'other'}]]);
            const original = JSON.stringify(input), model = drain(fgTextModel.prepare(input));
            const task = drain(fgTextModel.prepareOrder(model, 'task'));
            assert.deepEqual(task.order, [2,4,10,12,17,40,25,28,45]);
            assert.deepEqual([...task.hidden], [17,28]);
            assert.deepEqual([...task.switches], [12]);
            assert.deepEqual(task.boundaries, [
                {at:2, kind:'task-group-head', text:'工人  7   ·  3 lines  ·  CPU 0,1'},
                {at:5, kind:'task-gap', text:'⇣ switched out on CPU 0 → other-9; resumes at line 40 after 2.000 ms (1 lines of other tasks in between)'},
                {at:6, kind:'task-group-head', text:'other  9   ·  2 lines  ·  CPU 1,10'}]);
            const time = fgTextModel.order(model, 'time');
            assert.deepEqual(time.order, [2,4,10,12,17,25,28,40,45]);
            assert.deepEqual(time.boundaries, []);
            assert.equal(time.hidden.size, 0); assert.equal(time.switches.size, 0);
            assert.equal(JSON.stringify(input), original);
        ''')

    def test_task_order_keeps_idle_cpus_separate_and_stable_ties_with_untasked_rows(self):
        self.run_model('''
            const model = drain(fgTextModel.prepare(data([
                [1, '# header', {}], [3, 'idle-a', {cpu:'0', pid:'0'}],
                [5, 'perCPU', {cpu:'0'}], [7, '', {cpu:'0', pid:''}],
                [9, 'worker', {cpu:'3', pid:'57', comm:'<&任务>'}],
                [11, 'worker2', {cpu:'1', pid:'57', comm:'<&任务>'}],
                [13, 'idle-b', {cpu:'1', pid:'0'}],
                [15, 'untasked text', {}]])));
            const plan = fgTextModel.order(model, 'task');
            assert.deepEqual(plan.order, [1,5,7,9,11,13,15,3]);
            assert.equal(plan.hidden.size, 0);
            assert.deepEqual(plan.boundaries.map(boundary => [boundary.at, boundary.text]), [
                [1, 'cpu0     ·  2 lines  ·  CPU 0'],
                [3, '<&任务>  57   ·  2 lines  ·  CPU 1,3'],
                [5, 'idle1  0   ·  2 lines  ·  CPU 1'],
                [7, 'idle0  0   ·  1 lines  ·  CPU 0']]);
        ''')

    def test_order_plans_handle_empty_header_only_and_invalid_modes(self):
        self.run_model('''
            for (const rows of [[], [[2, '', {}]], [[1, '# header', {}], [3, '---', {}], [11, '任务', {}]]]) {
                const model = drain(fgTextModel.prepare(data(rows)));
                for (const mode of ['time', 'task']) {
                    const plan = fgTextModel.order(model, mode);
                    assert.deepEqual(plan.order, rows.map(row => row[0]));
                    assert.deepEqual(plan.boundaries, []);
                    assert.equal(plan.hidden.size, 0); assert.equal(plan.switches.size, 0);
                }
                assert.throws(() => fgTextModel.order(model, 'duration'), /Invalid Text order/);
                assert.throws(() => fgTextModel.order(model, null), /Invalid Text order/);
            }
        ''')

    def test_order_cache_uses_complete_record_identity_and_never_retains_dom(self):
        self.run_model('''
            for (const version of [1,2,3]) {
                const input = data([[3, 'first', {cpu:'0', pid:'2', comm:'first'}],
                    [9, 'second', {cpu:'1', pid:'3', comm:'second'}]]);
                if (version >= 2) input.rows.forEach(row => row.push(null));
                if (version === 3) {
                    input.fields.push('space');
                    input.rows.forEach(row => row.splice(row.length - 1, 0, 'user'));
                }
                input.version = version;
                const before = JSON.stringify(input), model = drain(fgTextModel.prepare(input));
                const task = fgTextModel.order(model, 'task'), time = fgTextModel.order(model, 'time');
                for (const record of model.records) {
                    record.getAttribute = () => { throw new Error('Repeated metadata read'); };
                    Object.defineProperty(record, 'textContent', {get() { throw new Error('Repeated raw text read'); }});
                }
                model.records[Symbol.iterator] = () => { throw new Error('Repeated record enumeration'); };
                assert.equal(fgTextModel.order(model, 'task'), task);
                assert.equal(drain(fgTextModel.prepareOrder(model, 'time')), time);
                assert.equal(JSON.stringify(input), before);
                const other = drain(fgTextModel.prepare(data([[3, 'other', {pid:'8', comm:'other'}]])));
                assert.notEqual(fgTextModel.order(other, 'task'), task);
                assert.equal(fgTextModel.order(other, 'task').boundaries[0].text.startsWith('other '), true);
            }
        ''')

    def test_cancelled_order_preparation_publishes_no_partial_plan_and_can_restart(self):
        self.run_model('''
            for (const mode of ['time', 'task']) for (const stage of mode === 'task' ? ['Grouping', 'Summarizing', 'Ordering'] : ['Preparing']) {
                const rows = Array.from({length:4097}, (unused, index) => [index * 3 + 1, 'call()', {cpu:'0', pid:'3'}]);
                const model = drain(fgTextModel.prepare(data(rows)));
                const cancelled = fgTextModel.prepareOrder(model, mode);
                let progress;
                do { progress = cancelled.next(); assert.equal(progress.done, false); } while (!progress.value.startsWith(stage));
                assert.equal(cancelled.return().done, true);
                const resumed = fgTextModel.prepareOrder(model, mode);
                assert.equal(resumed.next().value.startsWith(mode === 'task' ? 'Grouping' : 'Preparing'), true);
                const plan = drain(resumed);
                assert.equal(plan.order.length, rows.length);
                assert.equal(new Set(plan.order).size, rows.length);
                assert.equal(fgTextModel.order(model, mode), plan);
            }
        ''')

    def test_random_task_plans_match_an_independent_full_record_reference(self):
        generator = random.Random(19760311)
        for scenario in range(30):
            rows = []
            number = 0
            for unused in range(generator.randrange(250)):
                number += generator.randrange(1, 11)
                cpu = generator.choice([None, '', '0', '1', '10'])
                pid = generator.choice([None, '', '0', '7', '9', '11'])
                text = generator.choice(['', '  ', '-----', '----', '任务 <&> call()',
                                         f'{generator.randrange(100) / 10:.6f} | call()',
                                         f' {cpu or "0"}) old-task-7 => new-task-9'])
                rows.append([number, text, dict(cpu=cpu, pid=pid, comm=generator.choice([None, '', 'worker', '任务<&>']))])
            head, groups, hidden, switches, prefixes = [], {}, set(), set(), {}
            real_count = 0
            current = ''
            for row in rows:
                number, text, attributes = row
                separator = re.fullmatch(r'\s*(-{5,})?\s*', text) is not None
                prefixes[number] = real_count
                real_count += not separator
                pid, cpu = attributes['pid'], attributes['cpu']
                key = ('cpu' + cpu if cpu else '') if pid in (None, '') else ('idle' + ('null' if cpu is None else cpu) if pid == '0' else 'p' + pid)
                if not key:
                    if not current:
                        head.append(row)
                        continue
                    if separator:
                        hidden.add(number)
                    key = current
                current = key
                groups.setdefault(key, []).append(row)
            expected_order = [row[0] for row in head]
            boundaries = []
            for key, members in sorted(groups.items(), key=lambda group: (group[0].startswith('idle'), -len(group[1]))):
                real = [row for row in members if row[0] not in hidden]
                first = (real or members)[0][2]
                cpus = list(dict.fromkeys(row[2]['cpu'] for row in real if row[2]['cpu']))
                boundaries.append(dict(at=len(expected_order), kind='task-group-head',
                                       text=f'{first["comm"] or key}  {first["pid"] or ""}   ·  {len(real)} lines  ·  CPU ' + ','.join(sorted(cpus, key=int))))
                pending, previous, previous_time = None, None, None
                for number, text, attributes in members:
                    switched = re.fullmatch(r'\s*(\d+)\)\s+(.+?)-(\d+)\s+=>\s+(.+?)-(\d+)\s*', text)
                    timestamp = re.match(r'\s*(\d+\.\d+)\s*\|', text)
                    if pending and not switched and number not in hidden:
                        elapsed = f' after {(float(timestamp[1]) - previous_time) * 1000:.3f} ms' if timestamp and previous_time is not None else ''
                        boundaries.append(dict(at=len(expected_order), kind='task-gap',
                                               text=f'⇣ switched out on CPU {pending[1]} → {pending[4].strip()}-{pending[5]}; resumes at line {number}{elapsed} ({prefixes[number] - prefixes[previous] - 1} lines of other tasks in between)'))
                        pending = None
                    if switched:
                        pending = switched
                        switches.add(number)
                    if timestamp:
                        previous_time = float(timestamp[1])
                    if number not in hidden:
                        previous = number
                    expected_order.append(number)
            expected = dict(order=expected_order, boundaries=boundaries, hidden=sorted(hidden), switches=sorted(switches))
            with self.subTest(scenario=scenario):
                self.run_model('''
                    const model = drain(fgTextModel.prepare(data(ROWS)));
                    const plan = fgTextModel.order(model, 'task');
                    assert.deepEqual({order:plan.order, boundaries:plan.boundaries,
                        hidden:[...plan.hidden].sort((left,right) => left-right),
                        switches:[...plan.switches].sort((left,right) => left-right)}, EXPECTED);
                '''.replace('ROWS', json.dumps(rows)).replace('EXPECTED', json.dumps(expected)))

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
            navigation.order = new Proxy(navigation.order, {get(target, property) {
                if (/^\\d+$/.test(String(property))) probes++;
                return Reflect.get(target, property);
            }});
            Object.defineProperty(navigation, 'expandable', {get() { throw new Error('Full candidate snapshot'); }});
            Object.defineProperty(navigation, 'visible', {get() { throw new Error('Full visibility snapshot'); }});
            navigation.setVisible(120001, false);
            navigation.refresh();
            assert.equal(navigation.next(120000, 1), 120001);
            assert.equal(navigation.next(120000, -1), 119999);
            assert.equal(navigation.next(119999, 1), 120001);
            assert.equal(navigation.boundary(false), 0);
            assert.equal(navigation.boundary(true), order.length - 1);
            assert.equal(navigation.expandableCount, order.length - 1);
            assert.equal(navigation.visibleCount, order.length - 1);
            assert.ok(probes < 50, String(probes));
        ''')

    def test_navigation_local_visibility_updates_keep_lazy_snapshots_and_counts_consistent(self):
        self.run_model('''
            const navigation = fgTextModel.navigation([8,2,6,4], [2,4], [6]);
            assert.equal(navigation.visibleCount, 3);
            assert.equal(navigation.expandableCount, 2);
            const initial = navigation.expandable;
            assert.deepEqual(initial, [1,3]);
            navigation.setVisible(6, false);
            assert.equal(navigation.expandable, initial);
            navigation.setVisible(2, false);
            assert.equal(navigation.visibleCount, 2);
            assert.equal(navigation.expandableCount, 1);
            assert.equal(navigation.next(0, 1), 3);
            assert.deepEqual(navigation.expandable, [3]);
            assert.deepEqual(initial, [1,3]);
            assert.deepEqual(navigation.visible, [0,3]);
            navigation.setVisible(6, true);
            assert.equal(navigation.visibleCount, 3);
            assert.equal(navigation.expandableCount, 1);
            assert.deepEqual(navigation.visible, [0,2,3]);
            assert.equal(navigation.boundary(true), 3);
            navigation.setVisible(4, false);
            assert.equal(navigation.expandableCount, 0);
            assert.equal(navigation.next(0, 1), -1);
            assert.equal(navigation.boundary(false), 0);
            assert.equal(navigation.boundary(true), 2);
            navigation.setVisible(8, false);
            navigation.setVisible(6, false);
            assert.equal(navigation.visibleCount, 0);
            assert.equal(navigation.boundary(false), -1);
            assert.equal(navigation.boundary(true), -1);
            assert.deepEqual(navigation.visible, []);
        ''')

    def test_navigation_incremental_updates_match_complete_reference_after_reordering(self):
        self.run_model('''
            let seed = 1234567;
            const random = limit => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed % limit; };
            for (const length of [0,1,2,3,5,7,16,31,33,127]) {
                let order = Array.from({length}, (unused, index) => index * 3 + 2);
                const sources = new Set(order.filter((unused, index) => index % 3 !== 0));
                const hidden = new Set(order.filter((unused, index) => index % 5 === 0));
                const navigation = fgTextModel.navigation(order, [...sources], [...hidden]);
                for (let iteration = 0; iteration < 200; iteration++) {
                    const changes = iteration % 11 === 0 ? length : Math.min(length, 3);
                    for (let change = 0; change < changes; change++) {
                        const line = order[random(length)], show = random(2) === 1;
                        if (show) hidden.delete(line); else hidden.add(line);
                        navigation.setVisible(line, show);
                    }
                    if (iteration % 17 === 0) {
                        order = order.slice();
                        for (let index = length - 1; index > 0; index--) {
                            const partner = random(index + 1);
                            [order[index], order[partner]] = [order[partner], order[index]];
                        }
                        navigation.reorder(order);
                    }
                    if (iteration % 3 === 0) navigation.refresh();
                    const visible = order.flatMap((line, index) => hidden.has(line) ? [] : [index]);
                    const expandable = visible.filter(index => sources.has(order[index]));
                    const boundaries = expandable.length ? expandable : visible;
                    assert.equal(navigation.visibleCount, visible.length);
                    assert.equal(navigation.expandableCount, expandable.length);
                    assert.deepEqual(navigation.visible, visible);
                    assert.deepEqual(navigation.expandable, expandable);
                    assert.equal(navigation.boundary(false), boundaries[0] ?? -1);
                    assert.equal(navigation.boundary(true), boundaries.at(-1) ?? -1);
                    for (const current of [-1,0,random(length + 1),length + 1]) {
                        const after = current < 0 ? expandable[0] : expandable.find(index => index > current) ?? expandable[0];
                        const before = current < 0 ? expandable.at(-1) : expandable.findLast(index => index < current) ?? expandable.at(-1);
                        assert.equal(navigation.next(current, 1), after ?? -1);
                        assert.equal(navigation.next(current, -1), before ?? -1);
                    }
                }
            }
        ''')

    def test_navigation_bulk_visibility_rebuilds_once_without_per_row_tree_updates(self):
        self.run_model('''
            const nativeArray = Uint32Array;
            let writes = 0;
            globalThis.Uint32Array = class extends nativeArray {
                constructor(length) {
                    super(length);
                    return new Proxy(this, {
                        get(target, property) { return Reflect.get(target, property, target); },
                        set(target, property, value) {
                            if (/^\\d+$/.test(String(property))) writes++;
                            return Reflect.set(target, property, value, target);
                        },
                    });
                }
            };
            const length = 131071;
            const order = Array.from({length}, (unused, index) => index + 1);
            const navigation = fgTextModel.navigation(order, order.filter(line => line % 2 === 0));
            let visits = 0;
            navigation.order = new Proxy(navigation.order, {get(target, property) {
                if (/^\\d+$/.test(String(property))) visits++;
                return Reflect.get(target, property);
            }});
            for (const property of ['visible', 'expandable']) {
                Object.defineProperty(navigation, property, {get() { throw new Error('Full candidate snapshot'); }});
            }
            for (let line = 1; line <= 32768; line++) navigation.setVisible(line, false);
            writes = 0;
            for (let line = 32769; line <= length; line++) navigation.setVisible(line, false);
            assert.equal(writes, 0);
            assert.equal(visits, 0);
            assert.equal(navigation.visibleCount, 0);
            assert.equal(navigation.expandableCount, 0);
            navigation.refresh();
            assert.equal(visits, length);
            assert.ok(writes < 4 * length, String(writes));
            assert.equal(navigation.next(-1, 1), -1);
            assert.equal(navigation.boundary(false), -1);
            assert.equal(visits, length);
            visits = 0;
            for (const line of order) navigation.setVisible(line, line % 5 !== 0);
            assert.equal(navigation.visibleCount, order.filter(line => line % 5 !== 0).length);
            assert.equal(navigation.expandableCount, order.filter(line => line % 2 === 0 && line % 5 !== 0).length);
            assert.equal(visits, 0);
            assert.equal(navigation.next(-1, 1), 1);
            assert.equal(visits, length);
            assert.equal(navigation.boundary(false), 1);
            assert.equal(navigation.next(1, 1), 3);
            navigation.refresh();
            assert.equal(visits, length);
            visits = 0;
            navigation.setVisible(2, false);
            assert.equal(navigation.next(-1, 1), 3);
            navigation.refresh();
            assert.equal(visits, 0);
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
