#!/usr/bin/env node
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {performance} = require('node:perf_hooks');

const [count = 1000000, functions = 1000, iterations = 15] = process.argv.slice(2).map(Number);
if (![count, functions, iterations].every(value => Number.isSafeInteger(value) && value > 0) || functions > count) {
    throw new Error('Usage: node tests/benchmark_search.js [calls] [functions <= calls] [iterations]');
}
const source = fs.readFileSync(path.join(__dirname, '../assets/timeline/search.js'), 'utf8');
const {buildSearchIndex, matchSearchNames, collectSearchCalls} = new Function(source +
    '\nreturn {buildSearchIndex, matchSearchNames, collectSearchCalls};')();
const names = Int32Array.from({length:count}, (unused, position) => position % functions);
const strings = Array.from({length:functions}, (unused, position) => 'fn_' + position);

function drain(steps) {
    let result;
    do { result = steps.next(); } while (!result.done);
    return result.value;
}

function baseline(query) {
    const matcher = new RegExp(query, 'i'), hits = new Uint8Array(functions), matches = [];
    for (let position = 0; position < functions; position++) if (matcher.test(strings[position])) hits[position] = 1;
    for (let position = 0; position < count; position++) if (hits[names[position]]) matches.push(position);
    return matches;
}

const buildStart = performance.now();
const index = drain(buildSearchIndex(names, count));
const buildMilliseconds = performance.now() - buildStart;

function indexed(query) {
    const hits = drain(matchSearchNames(index, strings, new RegExp(query, 'i')));
    return drain(collectSearchCalls(index, names, count, hits));
}

function median(values) {
    return values.slice().sort((left, right) => left - right)[Math.floor(values.length / 2)];
}

const results = [];
for (const kind of ['single-function', 'sparse-union', 'dense']) {
    const baselineTimes = [], indexedTimes = [];
    let matches = 0;
    for (let iteration = -3; iteration < iterations; iteration++) {
        const first = (iteration + 3) % functions, second = (first + 31) % functions;
        const query = kind === 'single-function' ? '^fn_' + first + '$' : kind === 'sparse-union' ?
            '^fn_(' + first + '|' + second + ')$' : '^(fn_.*|not_a_function_' + iteration + ')$';
        let before, after, baselineMilliseconds, indexedMilliseconds;
        for (const mode of iteration % 2 ? ['indexed', 'baseline'] : ['baseline', 'indexed']) {
            const start = performance.now();
            if (mode === 'baseline') { before = baseline(query); baselineMilliseconds = performance.now() - start; }
            else { after = indexed(query); indexedMilliseconds = performance.now() - start; }
        }
        assert.equal(before.length, after.length);
        for (let position = 0; position < before.length; position++) assert.equal(before[position], after[position]);
        matches = after.length;
        if (iteration >= 0) { baselineTimes.push(baselineMilliseconds); indexedTimes.push(indexedMilliseconds); }
    }
    results.push({kind, matches, baseline_median_ms:median(baselineTimes), indexed_median_ms:median(indexedTimes),
        speedup:median(baselineTimes) / median(indexedTimes)});
}
console.log(JSON.stringify({calls:count, functions, iterations, cold_index_ms:buildMilliseconds,
    retained_index_typed_bytes:index.names.byteLength + index.offsets.byteLength + index.calls.byteLength,
    scope:'Model CPU only; generator yields drained synchronously; excludes browser rendering, filtering and process heap overhead',
    baseline:'Former warm-name-cache regex pass followed by a full call-name scan and JavaScript result array',
    results}, null, 2));
