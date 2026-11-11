#!/usr/bin/env python3
"""Compare task-table retention using the same synthetic task identities."""
import argparse
import json
from pathlib import Path
import re
from statistics import median
import tempfile

from playwright.sync_api import sync_playwright


def fixture(report, destination, count):
    html = report.read_text(encoding='utf-8')
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
    if not match:
        raise ValueError('Expected a single-file Timeline report: ' + str(report))
    data = json.loads(match[2])
    data.update(n=0, mode='abs', strings=[],
                tasks=[dict(pid=index + 1, comm=f'task_{index:05}', comms=[], kind=0, cpu=0,
                            runs=[[index * 1000, index * 1000, 0]]) for index in range(count)],
                errs=[], chains=[], offcpu=[], resched=[], irqoff=[], sections=[], atomic=[],
                lost=[], inline={}, sources={}, wakeups=[], overhead=None)
    data.pop('tail_calls', None)
    data['cols'] = {name: [] for name in ('task', 'depth', 'start', 'dur', 'name', 'parent', 'line', 'flags')}
    data['stats'].update(tasks=count, slices=0, offcpu=0)
    data['uftrace'] = dict(spaces=['kernel'], clock='mono', aligned=True, origin_ns='18446744000000000000',
                           lifecycle=[[index, index * 1000, 'exec', 3] for index in range(count)])
    destination.write_text(html[:match.start(2)] + json.dumps(data, separators=(',', ':')) + html[match.end(2):],
                           encoding='utf-8')


def measure(browser, report):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    page.set_default_timeout(60000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri())
        page.evaluate("async () => { await fgSwitchView('timeline'); }")
        result = page.evaluate('''async () => {
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const began = performance.now();
            document.querySelector('[data-act=an-tasks]').click();
            const result = {open_handler_ms: performance.now() - began, page_handler_ms: [], page_frame_ms: []};
            const pane = document.querySelector('.tl-detail'), footer = pane.querySelector('.an-task-footer');
            const bounds = pane.getBoundingClientRect(), position = footer.getBoundingClientRect();
            result.initial_footer_visible = position.top >= bounds.top && position.bottom <= bounds.bottom;
            result.initial_rows = pane.querySelectorAll('[data-task-row]').length;
            result.maximum_rows = result.initial_rows;
            const next = pane.querySelector('[data-task-page="1"], [data-act=tasks-more]');
            while (!next.disabled) {
                const start = performance.now();
                next.click();
                result.page_handler_ms.push(performance.now() - start);
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                result.page_frame_ms.push(performance.now() - start);
                result.maximum_rows = Math.max(result.maximum_rows, pane.querySelectorAll('[data-task-row]').length);
            }
            result.final_rows = pane.querySelectorAll('[data-task-row]').length;
            result.panel_elements = pane.querySelectorAll('*').length;
            result.final_status = footer.querySelector('[role=status]').textContent;
            result.all_page_handlers_ms = result.page_handler_ms.reduce((total, value) => total + value, 0);
            result.all_page_frames_ms = result.page_frame_ms.reduce((total, value) => total + value, 0);
            return result;
        }''')
        if errors:
            raise RuntimeError('\n'.join(errors))
        return result
    finally:
        page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--tasks', type=int, default=10000)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.tasks < 1 or args.rounds < 1:
        parser.error('--tasks and --rounds must be positive')
    samples = {'before': [], 'after': []}
    with tempfile.TemporaryDirectory(prefix='fg-tasks-benchmark-') as directory:
        root = Path(directory)
        reports = {name: root / (name + '.html') for name in samples}
        fixture(args.before, reports['before'], args.tasks)
        fixture(args.after, reports['after'], args.tasks)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                version = browser.version
                for round_index in range(args.rounds):
                    for name in ('before', 'after') if round_index % 2 == 0 else ('after', 'before'):
                        samples[name].append(measure(browser, reports[name]))
            finally:
                browser.close()
    metrics = ('open_handler_ms', 'all_page_handlers_ms', 'all_page_frames_ms', 'initial_rows', 'maximum_rows', 'final_rows', 'panel_elements')
    result = dict(browser=version, viewport=dict(width=1500, height=900), tasks=args.tasks, rounds=args.rounds,
                  fixture='synthetic lifecycle-only identities; one observed point per task',
                  scope='Handlers, two-animation-frame intervals and retained DOM; excludes startup, full paint, CSV and peak memory',
                  samples=samples,
                  medians={name: {metric: median(sample[metric] for sample in records) for metric in metrics}
                           for name, records in samples.items()})
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)


if __name__ == '__main__':
    main()
