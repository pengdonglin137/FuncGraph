#!/usr/bin/env python3
"""Measure Timeline's work after a full-trace Text filter is ready."""
import argparse
import json
from pathlib import Path
from statistics import median

from playwright.sync_api import sync_playwright


def measure(browser, path, repeats, rate):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(path.as_uri())
        page.evaluate("async () => { await fgSwitchView('timeline'); }")
        session = page.context.new_cdp_session(page)
        result = page.evaluate('''async () => {
            const data = await fgData.load('fgTextIndex'), signal = new AbortController().signal;
            const cases = {};
            for (const [key, query] of Object.entries({sparse: 'func:^ping_v4_sendmsg$',
                    equivalent: '(func:^ping_v4_sendmsg$)', dense: 'cpu:.*', empty: 'func:^unmatched_benchmark_symbol$'})) {
                cases[key] = await fgIndexedFilter(data, query, signal, () => {});
            }
            if (!cases.sparse.lines.size || cases.sparse.lines.size >= cases.dense.lines.size || cases.empty.lines.size ||
                    cases.sparse.lines.size !== cases.equivalent.lines.size ||
                    [...cases.sparse.lines].some(number => !cases.equivalent.lines.has(number)))
                throw new Error('Expected sparse, equivalent, dense and empty filters for ping3');
            const {sparse, equivalent, dense, empty} = cases, clear = {text: '', lines: null};
            window.benchmarkFilters = {sparse, equivalent, dense, clearDense: clear, denseFromClear: dense, empty, clear};
            const probe = fgView('timeline').probe(), [start, end] = probe.view();
            probe.range(start, start + (end - start) * 0.01);
            return {calls: probe.N, identity: fgData.read('fgTimelineIdentity'),
                lines: Object.fromEntries(Object.entries(window.benchmarkFilters).map(([key, result]) => [key, result.lines?.size ?? null]))};
        }''')
        session.send('Emulation.setCPUThrottlingRate', {'rate': rate})
        result['samples'] = page.evaluate('''async repeats => {
            const cases = window.benchmarkFilters, results = {};
            const raf = window.requestAnimationFrame;
            const settle = () => new Promise(resolve => raf(() => raf(resolve)));
            const probe = fgView('timeline').probe();
            for (let run = -2; run <= repeats; run++) {
                const countOnly = run === repeats;
                for (const [key, {text, lines}] of Object.entries(cases)) {
                    const matching = lines && new Set(lines);
                    let checks = 0, scheduled = 0, frame = 0;
                    if (countOnly && matching) matching.has = number => { checks++; return Set.prototype.has.call(matching, number); };
                    const oldRow = document.querySelector('.tl-range-table tbody tr');
                    window.requestAnimationFrame = callback => {
                        scheduled++;
                        return raf(time => {
                            const start = performance.now();
                            try { callback(time); } finally { frame += performance.now() - start; }
                        });
                    };
                    const start = performance.now();
                    try { fgNotifyFilter(text, matching); } finally { window.requestAnimationFrame = raf; }
                    const syncMs = performance.now() - start;
                    await settle();
                    const twoFramesMs = performance.now() - start;
                    const row = document.querySelector('.tl-range-table tbody tr');
                    const facts = {scheduled, sameRow: !!oldRow?.isConnected, row: row?.textContent,
                        matched: probe.TRACKS.reduce((total, track) => total + track.hits, 0)};
                    if (run >= 0) (results[key] ||= []).push(countOnly ? {checks, ...facts} : {syncMs, frameMs: frame, twoFramesMs, ...facts});
                }
            }
            return results;
        }''', repeats)
        if errors:
            raise RuntimeError('\n'.join(errors))
        return result
    finally:
        page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', type=Path, nargs='+')
    parser.add_argument('--repeats', type=int, default=9)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--cpu-rate', type=int, default=4)
    args = parser.parse_args()
    with sync_playwright() as playwright, playwright.chromium.launch() as browser:
        for round_number in range(args.rounds):
            for path in args.reports[::1 if round_number % 2 == 0 else -1]:
                result = measure(browser, path.resolve(), args.repeats, args.cpu_rate)
                result['medians'] = {case: {key: median(run[key] for run in samples[:-1])
                                           for key in ('syncMs', 'frameMs', 'twoFramesMs')}
                                     for case, samples in result['samples'].items()}
                print(json.dumps(dict(report=str(path), round=round_number, **result)), flush=True)


if __name__ == '__main__':
    main()
