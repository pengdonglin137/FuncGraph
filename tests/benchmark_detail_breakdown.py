#!/usr/bin/env python3
"""Compare selected-call responsiveness and exact breakdown output across offline reports."""
import argparse
import json
from pathlib import Path
import re
from statistics import median
from tempfile import TemporaryDirectory


def fixture(source, destination, leaves, waits=0):
    report = source.read_text(encoding='utf-8')
    match = re.search(r'<script type="application/json" id="tlData">(.*?)</script>', report, re.S)
    data = json.loads(match[1])
    count = leaves + 2
    data.update(n=count, mode='abs', strings=['root', 'helper', 'leaf'],
                tasks=[dict(pid=1, comm='breakdown', kind=0, cpu=0)], errs=[], chains=[],
                offcpu=[], resched=[], wakeups=[], irqoff=[], sections=[], atomic=[],
                lost=[], inline={}, sources={}, overhead=None)
    data.pop('uftrace', None)
    data.pop('tail_calls', None)
    data['stats'].update(tasks=1, slices=count, offcpu=0)
    data['cols'] = dict(task=[0] * count, depth=[0, 1] + [2] * leaves,
                        start=[0] + [1000] * (count - 1), dur=[count * 1000, (count - 1) * 1000] + [500] * leaves,
                        name=[0, 1] + [2] * leaves, parent=[-1, 0] + [1] * leaves,
                        line=list(range(1, count + 1)), flags=[0] * count, ret=None, caller=None,
                        args=None, module=None, cpu=None, adj=None)
    if waits:
        data['cols']['dur'][0] = waits * 1000
        data['offcpu'] = [[0, ordinal * 1000, ordinal * 1000 + 400, 1,
                           -1, -1, 0, 0, ordinal % 2, -1] for ordinal in range(waits)]
        data['stats']['offcpu'] = waits
    destination.write_text(report[:match.start(1)] + json.dumps(data, separators=(',', ':')) + report[match.end(1):], encoding='utf-8')


def measure(browser, report, index, rate, warmup_index):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri(), timeout=60000)
        session = page.context.new_cdp_session(page)
        session.send('Emulation.setCPUThrottlingRate', {'rate': rate})
        result = page.evaluate('''async ({index, warmupIndex}) => {
            await fgSwitchView('timeline');
            const model = fgView('timeline').probe(), frame = () => new Promise(requestAnimationFrame);
            if (warmupIndex !== null) {
                if (warmupIndex < 0 || warmupIndex >= model.N || model.sTask[warmupIndex] !== model.sTask[index])
                    throw Error('Warm-up call must be on the measured track');
                if (fgData.read('tlData').cols.flags[warmupIndex] & (1 << 21))
                    throw Error('Warm-up call is marked as a leaf and may not build the call tree');
                model.select(warmupIndex);
                const deadline = performance.now() + 10000;
                while (!document.querySelector('.an-bd-legend') || document.querySelector(
                    '.tl-call-nav[aria-busy], [data-child-open][aria-disabled="true"]')) {
                    if (performance.now() >= deadline) throw Error('Warm-up call detail did not settle');
                    await frame();
                }
            }
            await frame(); await frame();
            const began = performance.now();
            model.select(index);
            const handler = performance.now() - began;
            const pending = !!document.querySelector('[data-detail-breakdown]');
            const nav = document.querySelector('.tl-call-nav');
            const navigationPending = nav.hasAttribute('aria-busy');
            const navigationReady = !navigationPending ? Promise.resolve(performance.now()) : new Promise((resolve, reject) => {
                const watcher = new MutationObserver(() => {
                    if (nav.hasAttribute('aria-busy')) return;
                    watcher.disconnect(); clearTimeout(timeout); resolve(performance.now());
                });
                const timeout = setTimeout(() => { watcher.disconnect(); reject(Error('Navigation not ready')); }, 10000);
                watcher.observe(nav, {attributes: true, attributeFilter: ['aria-busy']});
            });
            const bars = () => document.querySelector('.an-bd-legend');
            let breakdownReady = bars() ? performance.now() : null;
            const ready = breakdownReady !== null ? Promise.resolve(breakdownReady) : new Promise((resolve, reject) => {
                const watcher = new MutationObserver(() => {
                    if (!bars()) return;
                    watcher.disconnect(); clearTimeout(timeout);
                    breakdownReady = performance.now(); resolve(breakdownReady);
                });
                const timeout = setTimeout(() => { watcher.disconnect(); reject(Error('No breakdown')); }, 10000);
                watcher.observe(document.getElementById('tlDetail'), {childList: true, subtree: true});
            });
            let previous = began, maxFrameGap = 0, frames, observedFrames = 0;
            do {
                await frame();
                const current = performance.now();
                maxFrameGap = Math.max(maxFrameGap, current - previous);
                previous = current;
                if (++observedFrames === 2) frames = current - began;
            } while (observedFrames < 2 || (breakdownReady === null && previous - began < 10000));
            const finished = (await ready) - began;
            const navigation = (await navigationReady) - began;
            return {handler, frames, maxFrameGap, ready: finished, pending, navigation, navigationPending,
                controls: Array.from(nav.querySelectorAll('button'), button => [button.disabled, button.getAttribute('aria-label')]),
                legend: bars().innerHTML,
                chart: document.querySelector('.an-bd').innerHTML, N: model.N, selected: model.sel()};
        }''', {'index': index, 'warmupIndex': warmup_index})
        if errors or result['selected'] != index:
            raise RuntimeError(f'Unexpected selection or errors: {result}, {errors}')
        return result
    finally:
        page.close()


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--index', type=int, default=1931, help='wide subtree in the real report')
    parser.add_argument('--warmup-index', type=int, default=0, help='non-leaf call on the same track used to prime the call-tree index')
    parser.add_argument('--cold', action='store_true', help='skip priming; select a leaf in the synthetic track to isolate cold-index work')
    parser.add_argument('--leaves', type=int, default=450000, help='synthetic descendants (0 disables this case)')
    parser.add_argument('--waits', type=int, default=0, help='also select a call spanning this many synthetic off-CPU intervals')
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--rate', type=float, default=4)
    options = parser.parse_args()
    if options.waits != 0 and options.waits < 2:
        parser.error('--waits must be 0 or at least 2')
    originals = {name: getattr(options, name).resolve() for name in ('before', 'after')}
    with TemporaryDirectory(prefix='fg-breakdown-') as directory, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        cases = [(0, 0)]
        if options.leaves:
            cases.append((options.leaves, 0))
        if options.waits:
            cases.append((0, options.waits))
        for leaves, waits in cases:
            reports = originals.copy()
            if leaves or waits:
                reports = {name: Path(directory) / f'{name}.html' for name in originals}
                for name in originals:
                    fixture(originals[name], reports[name], leaves, waits)
            index, warmup_index = ((2 if options.cold else 0), 2) if leaves else (options.index, options.warmup_index)
            if waits:
                index, warmup_index = 0, 1
            if options.cold:
                warmup_index = None
            samples, outputs = {name: [] for name in reports}, set()
            for iteration in range(options.rounds):
                for name in reports if iteration % 2 == 0 else reversed(reports):
                    result = measure(browser, reports[name], index, options.rate, warmup_index)
                    outputs.add((result.pop('legend'), result.pop('chart'), json.dumps(result.pop('controls')), result['N']))
                    samples[name].append(result)
            if len(outputs) != 1:
                raise RuntimeError('Breakdown output changed')
            summary = {name: {key: round(median(sample[key] for sample in group), 1)
                              for key in ('handler', 'frames', 'maxFrameGap', 'ready', 'navigation')}
                       for name, group in samples.items()}
            print(json.dumps(dict(browser=browser.version, synthetic_leaves=leaves, synthetic_waits=waits, index=index, rate=options.rate,
                                  median_ms=summary, samples=samples)), flush=True)
        browser.close()


if __name__ == '__main__':
    main()
