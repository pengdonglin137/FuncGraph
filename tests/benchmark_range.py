#!/usr/bin/env python3
"""Compare full range-summary browsing with identical synthetic function populations."""
import argparse
import json
from pathlib import Path
import re
from statistics import median
import tempfile


def fixture(report, destination, count, nested_parent=False):
    html = report.read_text(encoding='utf-8')
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
    if not match:
        raise ValueError('Expected a single-file Timeline report: ' + str(report))
    data = json.loads(match[2])
    data.update(n=count, mode='abs', strings=[f'function_{index:06d}' for index in range(count)],
                tasks=[dict(pid=1, comm='range-benchmark', kind=0, cpu=0)],
                errs=[], chains=[], offcpu=[], resched=[], irqoff=[], sections=[], atomic=[],
                lost=[], inline={}, sources={}, wakeups=[], overhead=None)
    data.pop('tail_calls', None)
    data.pop('uftrace', None)
    data['cols'] = dict(task=[0] * count, depth=[0] * count, start=[0] + [1000] * (count - 1),
                        dur=[1 + index % 999 for index in range(count)], name=list(range(count)),
                        parent=[-1] * count, line=list(range(1, count + 1)), flags=[0] * count,
                        ret=None, caller=None, args=None, module=None, cpu=None, adj=None)
    if nested_parent:
        data['cols'].update(depth=[0] + [1] * (count - 1), parent=[-1] + [0] * (count - 1))
        data['cols']['dur'][0] = count * 1000
    data['stats'].update(tasks=1, slices=count, offcpu=0)
    destination.write_text(html[:match.start(2)] + json.dumps(data, separators=(',', ':')) + html[match.end(2):], encoding='utf-8')


def measure(browser, report, count):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    page.set_default_timeout(60000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri())
        page.evaluate("async () => { await fgSwitchView('timeline'); }")
        result = page.evaluate('''async (count) => {
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const model = fgView('timeline').probe(), began = performance.now();
            model.range(0, count * 1000);
            const result = {open_handler_ms: performance.now() - began, page_handler_ms: [], page_frame_ms: []};
            const pane = document.querySelector('.tl-detail'), visited = new Set();
            const observe = () => {
                const rows = Array.from(pane.querySelectorAll('.tl-range-table tbody tr[data-name]'));
                for (const row of rows) {
                    const name = row.dataset.name, index = Number(name.slice('function_'.length));
                    if (!name.startsWith('function_') || !Number.isInteger(index) || index < 0 || index >= count) {
                        throw new Error('Invalid function identity');
                    }
                    visited.add(name);
                }
                return rows.length;
            };
            result.initial_rows = observe();
            result.maximum_rows = result.initial_rows;
            const next = pane.querySelector('[data-act=range-next], [data-act=range-more]');
            while (next && !next.disabled) {
                const start = performance.now();
                next.click();
                result.page_handler_ms.push(performance.now() - start);
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                result.page_frame_ms.push(performance.now() - start);
                result.maximum_rows = Math.max(result.maximum_rows, observe());
            }
            result.final_rows = observe();
            result.visited_functions = visited.size;
            if (visited.size !== count) throw new Error('Incomplete function traversal');
            result.panel_elements = pane.querySelectorAll('*').length;
            result.final_status = pane.querySelector('.tl-range-footer [role=status]').textContent;
            result.page_actions = result.page_handler_ms.length;
            result.all_page_handlers_ms = result.page_handler_ms.reduce((total, value) => total + value, 0);
            result.all_page_frames_ms = result.page_frame_ms.reduce((total, value) => total + value, 0);
            return result;
        }''', count)
        if errors:
            raise RuntimeError('\n'.join(errors))
        return result
    finally:
        page.close()


def measure_selections(browser, report, count, selections):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    page.set_default_timeout(60000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri())
        result = page.evaluate('''async ({count, selections}) => {
            const frame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const startup = performance.now();
            await fgSwitchView('timeline');
            await frame();
            const ready = performance.now();
            const model = fgView('timeline').probe(), handlers = [], intervals = [];
            const rows = '.tl-range-table tbody tr[data-name]';
            for (let ordinal = 0; ordinal < selections; ordinal++) {
                const index = count - 1 - ordinal * Math.max(1, Math.floor(count / (2 * selections)));
                const start = performance.now();
                model.range(index * 1000, (index + 1) * 1000);
                handlers.push(performance.now() - start);
                await frame();
                intervals.push(performance.now() - start);
                const names = [...document.querySelectorAll(rows)].map(row => row.dataset.name).sort();
                if (names.join(',') !== 'function_000000,function_' + String(index).padStart(6, '0')) {
                    throw new Error('Expected the early spanning parent and the selected child: ' + names.join(','));
                }
            }
            return {ready_ms: ready - startup, handlers_ms: handlers, intervals_ms: intervals};
        }''', dict(count=count, selections=selections))
        if errors:
            raise RuntimeError('\n'.join(errors))
        result['median_handler_ms'] = median(result['handlers_ms'])
        result['first_handler_ms'] = result['handlers_ms'][0]
        result['median_interval_ms'] = median(result['intervals_ms'])
        return result
    finally:
        page.close()


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--functions', type=int, default=10000)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--short-selections', type=int, default=0,
                        help='measure this many distinct narrow selections in a large parent call instead of paging')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.functions < 1 or args.rounds < 1:
        parser.error('--functions and --rounds must be positive')
    if not 0 <= args.short_selections < args.functions:
        parser.error('--short-selections must be nonnegative and smaller than --functions')
    samples = {'before': [], 'after': []}
    with tempfile.TemporaryDirectory(prefix='fg-range-benchmark-') as directory:
        reports = {name: Path(directory) / (name + '.html') for name in samples}
        for name in samples:
            fixture(getattr(args, name), reports[name], args.functions, nested_parent=bool(args.short_selections))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                version = browser.version
                for round_index in range(args.rounds):
                    for name in ('before', 'after') if round_index % 2 == 0 else ('after', 'before'):
                        samples[name].append(measure_selections(browser, reports[name], args.functions, args.short_selections)
                                             if args.short_selections else measure(browser, reports[name], args.functions))
            finally:
                browser.close()
    metrics = ('open_handler_ms', 'all_page_handlers_ms', 'all_page_frames_ms', 'initial_rows',
               'maximum_rows', 'final_rows', 'visited_functions', 'panel_elements', 'page_actions')
    short = args.short_selections > 0
    if short:
        metrics = ('ready_ms', 'first_handler_ms', 'median_handler_ms', 'median_interval_ms')
    result = dict(browser=version, viewport=dict(width=1500, height=900), functions=args.functions, rounds=args.rounds,
                  short_selections=args.short_selections,
                  fixture='one early spanning parent with non-overlapping children' if short else
                          'synthetic non-overlapping calls; one distinct function per call',
                  scope='Timeline switch to ready, selection handlers and two-rAF intervals; excludes CSV and peak memory' if short else
                        'Handlers, two-rAF intervals and retained rows; excludes startup, CSV and peak memory',
                  samples=samples,
                  medians={name: {metric: median(sample[metric] for sample in records) for metric in metrics} for name, records in samples.items()})
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)


if __name__ == '__main__':
    main()
