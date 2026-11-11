#!/usr/bin/env python3
"""Compare complete critical-section and scheduling-window browsing."""
import argparse
import json
from pathlib import Path
import re
from statistics import median
import tempfile

from benchmark_range import fixture as range_fixture


def fixture(template, destination, count):
    range_fixture(template, destination, count)
    html = destination.read_text(encoding='utf-8')
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
    data = json.loads(match[2])
    data['caps']['latency'] = True
    data['preempt_base'] = 0
    data['resched'] = [[0, index * 1000, index * 1000 + 1 + index % 999, index % 4, 1, index % 3] for index in range(count)]
    data['sections'] = [[0, kind, raw[1], raw[2], index, index, raw[3], index + 1, index + 1]
                        for kind in range(3) for index, raw in enumerate(data['resched'])]
    data['cols']['flags'] = [4] * count
    destination.write_text(html[:match.start(2)] + json.dumps(data, separators=(',', ':')) + html[match.end(2):], encoding='utf-8')


def measure(browser, report, count, panel):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    page.set_default_timeout(60000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri())
        page.evaluate("async () => { await fgSwitchView('timeline'); }")
        result = page.evaluate('''async ({count, panel}) => {
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const began = performance.now();
            fgView('timeline').probe()[panel]();
            const result = {open_handler_ms: performance.now() - began, page_handler_ms: [], page_frame_ms: []};
            const pane = document.querySelector('.tl-detail'), visited = new Set();
            const observe = () => {
                const rows = [...pane.querySelectorAll('tr[data-sec], tr[data-win]')];
                for (const row of rows) {
                    const index = panel === 'sections' ? +row.dataset.sec : +row.dataset.win.split(',')[0] / 1000;
                    if (!Number.isInteger(index) || index < 0 || index >= count) throw new Error('Invalid window identity');
                    visited.add(index);
                }
                return rows.length;
            };
            result.initial_rows = observe();
            result.maximum_rows = result.initial_rows;
            const next = pane.querySelector('[data-act=window-next], [data-act=window-more]');
            while (next && !next.disabled) {
                const start = performance.now();
                next.click();
                result.page_handler_ms.push(performance.now() - start);
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                result.page_frame_ms.push(performance.now() - start);
                result.maximum_rows = Math.max(result.maximum_rows, observe());
            }
            result.final_rows = observe();
            result.visited_windows = visited.size;
            if (visited.size !== count) throw new Error('Incomplete window traversal');
            result.panel_elements = pane.querySelectorAll('*').length;
            result.page_actions = result.page_handler_ms.length;
            result.all_page_handlers_ms = result.page_handler_ms.reduce((total, value) => total + value, 0);
            result.all_page_frames_ms = result.page_frame_ms.reduce((total, value) => total + value, 0);
            return result;
        }''', dict(count=count, panel=panel))
        if errors:
            raise RuntimeError('\n'.join(errors))
        return result
    finally:
        page.close()


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--windows', type=int, default=10000)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.windows < 1 or args.rounds < 1:
        parser.error('--windows and --rounds must be positive')
    samples = {panel: {'before': [], 'after': []} for panel in ('sections', 'latency')}
    with tempfile.TemporaryDirectory(prefix='fg-window-benchmark-') as directory:
        reports = {name: Path(directory) / (name + '.html') for name in ('before', 'after')}
        fixture(args.before, reports['before'], args.windows)
        fixture(args.after, reports['after'], args.windows)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                version = browser.version
                for panel, measurements in samples.items():
                    for round_index in range(args.rounds):
                        for name in ('before', 'after') if round_index % 2 == 0 else ('after', 'before'):
                            measurements[name].append(measure(browser, reports[name], args.windows, panel))
            finally:
                browser.close()
    metrics = ('open_handler_ms', 'all_page_handlers_ms', 'all_page_frames_ms', 'initial_rows',
               'maximum_rows', 'final_rows', 'visited_windows', 'panel_elements', 'page_actions')
    result = dict(browser=version, viewport=dict(width=1500, height=900), windows=args.windows, rounds=args.rounds,
                  fixture='synthetic non-overlapping windows with one distinct function per window and all three section kinds',
                  scope='Handlers, two-rAF intervals and retained rows; excludes startup, CSV and peak memory', samples=samples,
                  medians={panel: {name: {metric: median(sample[metric] for sample in records) for metric in metrics}
                                   for name, records in measurements.items()} for panel, measurements in samples.items()})
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)


if __name__ == '__main__':
    main()
