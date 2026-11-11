#!/usr/bin/env python3
"""Compare complete error-group and atomic-group browsing with identical inputs."""
import argparse
import json
from pathlib import Path
import re
from statistics import median
import tempfile

from benchmark_range import fixture as range_fixture


def fixture(template, destination, count, atomic_count=None):
    atomic_count = count if atomic_count is None else atomic_count
    range_fixture(template, destination, max(count, atomic_count, 1))
    html = destination.read_text(encoding='utf-8')
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
    data = json.loads(match[2])
    data['chains'] = [[index, index, 2 + index % 3, index % 3, 1, index % 2, 0] for index in range(count)]
    data['atomic'] = [[index, 1 + index % 3] for index in range(atomic_count)]
    data['cols']['adj'] = [duration / 2 for duration in data['cols']['dur']]
    data['caps']['retval'] = True
    destination.write_text(html[:match.start(2)] + json.dumps(data, separators=(',', ':')) + html[match.end(2):], encoding='utf-8')


def measure(browser, report, count, atomic):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    page.set_default_timeout(60000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri())
        page.evaluate("async () => { await fgSwitchView('timeline'); }")
        result = page.evaluate('''async ({count, atomic}) => {
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const began = performance.now();
            fgView('timeline').probe().errors();
            const result = {open_handler_ms: performance.now() - began, page_handler_ms: [], page_frame_ms: []};
            const pane = document.querySelector('.tl-detail'), visited = new Set(), kind = atomic ? 'atomic' : 'errors';
            const observe = () => {
                const rows = [...pane.querySelectorAll(atomic ? 'tr[data-atomic-group]' : 'tr[data-chains]')];
                for (const row of rows) {
                    const index = +(atomic ? row.dataset.atomicGroup : row.dataset.chains);
                    if (!Number.isInteger(index) || index < 0 || index >= count) throw new Error('Invalid group identity');
                    visited.add(index);
                }
                return rows.length;
            };
            result.initial_rows = observe();
            result.maximum_rows = result.initial_rows;
            const next = pane.querySelector('[data-act="' + kind + '-next"], [data-act="' + kind + '-more"]');
            while (next && !next.disabled) {
                const start = performance.now();
                next.click();
                result.page_handler_ms.push(performance.now() - start);
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                result.page_frame_ms.push(performance.now() - start);
                result.maximum_rows = Math.max(result.maximum_rows, observe());
            }
            result.final_rows = observe();
            result.visited_groups = visited.size;
            if (visited.size !== count) throw new Error('Incomplete group traversal');
            result.panel_elements = pane.querySelectorAll('*').length;
            result.page_actions = result.page_handler_ms.length;
            result.all_page_handlers_ms = result.page_handler_ms.reduce((total, value) => total + value, 0);
            result.all_page_frames_ms = result.page_frame_ms.reduce((total, value) => total + value, 0);
            return result;
        }''', dict(count=count, atomic=atomic))
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
    parser.add_argument('--groups', type=int, default=10000)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.groups < 1 or args.rounds < 1:
        parser.error('--groups and --rounds must be positive')
    samples = {panel: {'before': [], 'after': []} for panel in ('errors', 'atomic')}
    with tempfile.TemporaryDirectory(prefix='fg-error-benchmark-') as directory:
        reports = {panel: {name: Path(directory) / f'{panel}-{name}.html' for name in ('before', 'after')}
                   for panel in samples}
        for panel, paths in reports.items():
            for name, destination in paths.items():
                fixture(getattr(args, name), destination, args.groups if panel == 'errors' else 0,
                        args.groups if panel == 'atomic' else 0)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                version = browser.version
                for panel, measurements in samples.items():
                    for round_index in range(args.rounds):
                        for name in ('before', 'after') if round_index % 2 == 0 else ('after', 'before'):
                            measurements[name].append(measure(browser, reports[panel][name], args.groups, panel == 'atomic'))
            finally:
                browser.close()
    metrics = ('open_handler_ms', 'all_page_handlers_ms', 'all_page_frames_ms', 'initial_rows',
               'maximum_rows', 'final_rows', 'visited_groups', 'panel_elements', 'page_actions')
    result = dict(browser=version, viewport=dict(width=1500, height=900), groups=args.groups, rounds=args.rounds,
                  fixture='synthetic distinct error or atomic groups, one occurrence per group',
                  scope='Handlers, two-rAF intervals and retained rows; excludes startup, CSV and peak memory', samples=samples,
                  medians={panel: {name: {metric: median(sample[metric] for sample in records) for metric in metrics}
                                   for name, records in measurements.items()} for panel, measurements in samples.items()})
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)


if __name__ == '__main__':
    main()
