"""Shared expression filters reuse compilation, not per-record match results."""
from pathlib import Path
import shutil
import subprocess
import unittest

import helpers


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class FilterMatcherTest(unittest.TestCase):
    def run_script(self, assertions):
        source = (Path(helpers.ROOT) / 'assets/report/report.js').read_text()
        source = source[source.index('        function tokenizeFilter'):
                        source.index('        // ====== Filter Tags Management')]
        setup = '''const assert = require('node:assert/strict'), _graphFoldIds = {};
            const NativeRegExp = RegExp, compilations = [];
            globalThis.RegExp = new Proxy(NativeRegExp, {
                construct(target, parameters) {
                    compilations.push(parameters);
                    return Reflect.construct(target, parameters);
                }
            });
            const record = (attributes, text = '') => ({
                getAttribute: name => attributes[name] ?? null,
                querySelector: () => ({textContent: text})
            });
        '''
        result = subprocess.run(['node', '-e', setup + source + assertions],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_each_regex_condition_compiles_once_and_tests_every_candidate(self):
        self.run_script('''
            const fields = ['func', 'cpu', 'pid', 'comm', 'ret', 'params',
                'irqs', 'resched', 'hirq', 'preempt'];
            for (const field of fields) {
                const condition = parseFilterExpr(field + ':^w[0-9]+$').ast;
                for (let iteration = 0; iteration < 100; iteration++) {
                    for (const value of ['w2', 'W9', 'W2suffix', 'not', 'w2']) {
                        assert.equal(evalExpr(record({['data-' + field]: value}), condition),
                            new NativeRegExp(condition.value, 'i').test(value), field + '/' + value);
                    }
                }
            }
            assert.deepEqual(compilations, fields.map(() => ['^w[0-9]+$', 'i']));
        ''')

    def test_invalid_regex_keeps_branch_specific_literal_fallback(self):
        self.run_script('''
            for (const field of ['func', 'cpu', 'pid', 'comm', 'ret', 'params',
                'irqs', 'resched', 'hirq', 'preempt']) {
                const condition = parseFilterExpr(field + ':[').ast;
                for (let iteration = 0; iteration < 100; iteration++) {
                    assert.equal(evalExpr(record({['data-' + field]: '['}), condition), true);
                    assert.equal(evalExpr(record({['data-' + field]: 'prefix['}), condition), field === 'func');
                    assert.equal(evalExpr(record({['data-' + field]: 'none'}), condition), false);
                    assert.equal(evalExpr(record({}), condition), false);
                    assert.equal(evalExpr(record({['data-' + field]: ''}), condition), false);
                }
            }
            const condition = parseFilterExpr('func:[A').ast;
            assert.equal(evalExpr(record({'data-func': 'prefix[A'}), condition), true);
            assert.equal(evalExpr(record({'data-func': 'prefix[a'}), condition), false);
            assert.equal(compilations.length, 11);
        ''')

    def test_native_unicode_newline_and_escaped_regex_semantics_are_unchanged(self):
        self.run_script(r'''
            const values = ['worker', 'WORKER', 'w0rker', '中文', '🙂', 'İ', 'ı',
                'long\\name', 'a.b', 'a\\nb', 'a\nb', '', null];
            for (const pattern of ['^wo.*$', '^(?:中文|🙂)$', '\\w+', '\\.', '^a.b$',
                '^i$', '^a[\\s\\S]*b$', 'a$', '^']) {
                const condition = Object.freeze({type: 'COND', field: 'comm', op: '=', value: pattern});
                for (const value of [...values, ...values]) {
                    assert.equal(evalExpr(record({'data-comm': value}), condition),
                        value !== null && value !== '' && new NativeRegExp(pattern, 'i').test(value));
                }
            }
            assert.equal(compilations.length, 9);
        ''')

    def test_mutated_conditions_invalidate_without_sharing_ast_state(self):
        self.run_script('''
            const condition = parseFilterExpr('func:^first$').ast;
            const first = record({'data-func': 'first'}), second = record({'data-func': 'second'});
            assert.equal(evalExpr(first, condition), true);
            condition.value = '^second$';
            assert.equal(evalExpr(first, condition), false);
            assert.equal(evalExpr(second, condition), true);
            condition.value = '[';
            assert.equal(evalExpr(first, condition), false);
            assert.equal(evalExpr(second, condition), false);
            condition.value = '^first$';
            assert.equal(evalExpr(first, condition), true);
            const separate = parseFilterExpr('func:^first$').ast;
            assert.equal(evalExpr(first, separate), true);
            assert.equal(compilations.length, 5);
        ''')

    def test_frozen_ast_serialization_and_sort_round_trip_are_unchanged(self):
        self.run_script('''
            const parsed = parseFilterExpr('(cpu:^1$ OR comm:worker) AND NOT func:[ sort:desc');
            const snapshot = JSON.stringify(parsed), text = astToText(parsed.ast);
            function freeze(node) {
                if (!node) return;
                for (const child of [node.left, node.right, node.operand]) freeze(child);
                Object.freeze(node);
            }
            freeze(parsed.ast);
            for (let iteration = 0; iteration < 100; iteration++) {
                assert.equal(evalExpr(record({'data-cpu': '1', 'data-func': 'good'}), parsed.ast), true);
                assert.equal(evalExpr(record({'data-cpu': '2', 'data-comm': 'worker', 'data-func': 'bad['}), parsed.ast), false);
            }
            assert.equal(JSON.stringify(parsed), snapshot);
            assert.equal(astToText(parsed.ast), text);
            assert.deepEqual(parseFilterExpr(text).ast, parsed.ast);
            assert.equal(parsed.sortDir, 'desc');
            assert.equal(compilations.length, 3);
        ''')

    def test_boolean_short_circuit_only_compiles_reached_conditions(self):
        self.run_script('''
            const conjunction = parseFilterExpr('cpu:0 AND func:[').ast;
            for (let iteration = 0; iteration < 100; iteration++)
                assert.equal(evalExpr(record({'data-cpu': '1', 'data-func': '['}), conjunction), false);
            assert.deepEqual(compilations, [['0', 'i']]);
            assert.equal(evalExpr(record({'data-cpu': '0', 'data-func': '['}), conjunction), true);
            assert.deepEqual(compilations, [['0', 'i'], ['[', 'i']]);
            const disjunction = parseFilterExpr('cpu:0 OR comm:^task$').ast;
            for (let iteration = 0; iteration < 100; iteration++)
                assert.equal(evalExpr(record({'data-cpu': '0', 'data-comm': 'task'}), disjunction), true);
            assert.equal(compilations.length, 3);
            assert.equal(evalExpr(record({'data-cpu': '1', 'data-comm': 'task'}), disjunction), true);
            assert.equal(compilations.length, 4);
        ''')

    def test_exact_numeric_graph_unknown_and_missing_fields_do_not_compile(self):
        self.run_script('''
            const row = record({'data-cpu': '1', 'data-duration': '10', 'data-preempt': '2',
                'data-ret': '-1', 'data-func': 'worker', 'data-space': 'user'});
            for (const query of ['cpu:="1"', 'duration:>=10', 'preempt:>1',
                'ret:all', 'space:USER', 'unknown:regex', 'notrace:worker'])
                assert.equal(evalExpr(row, parseFilterExpr(query).ast), true, query);
            assert.equal(evalExpr(row, parseFilterExpr('graph:missing').ast), false);
            assert.equal(evalExpr(row, parseFilterExpr('duration:>bad').ast), false);
            for (const field of ['func', 'cpu', 'pid', 'comm', 'ret', 'params']) {
                const condition = parseFilterExpr(field + ':[').ast;
                assert.equal(evalExpr(record({}), condition), false);
                assert.equal(evalExpr(record({['data-' + field]: ''}), condition), false);
            }
            assert.deepEqual(compilations, []);
        ''')

    def test_return_regex_keeps_raw_return_fallback_and_invalid_short_circuit(self):
        self.run_script('''
            const condition = parseFilterExpr('ret:^0x').ast;
            const row = record({'data-ret': '-1'}, 'call(); /* ret=0xffffffffffffffff */');
            for (let iteration = 0; iteration < 100; iteration++) {
                assert.equal(evalExpr(row, condition), true);
                assert.equal(evalExpr(record({'data-ret': '-1'}, 'call(); /* ret=-1 */'), condition), false);
            }
            const invalid = parseFilterExpr('ret:[').ast;
            let rawReads = 0;
            row.querySelector = () => { rawReads++; return {textContent: 'ret=['}; };
            assert.equal(evalExpr(row, invalid), false);
            assert.equal(evalExpr(record({'data-ret': '['}), invalid), true);
            assert.equal(rawReads, 0);
            assert.equal(compilations.length, 2);
        ''')
