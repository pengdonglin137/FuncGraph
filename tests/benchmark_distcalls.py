#!/usr/bin/env python3
"""Compare duration-call browsing on identical synthetic Timeline populations."""
import argparse
import json
from pathlib import Path
import re
from statistics import median
import tempfile

def fixture(report, destination, count):
    html = report.read_text(encoding='utf-8')
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
    if not match:
        raise ValueError('Expected a single-file Timeline report: ' + str(report))
    data = json.loads(match[2])
    data.update(n=count, mode='abs', strings=['hot_function', 'caller+0x10/0x20'],
                tasks=[dict(pid=1, comm='distribution-benchmark', kind=0, cpu=0)],
                errs=[], chains=[], offcpu=[], resched=[], irqoff=[], sections=[], atomic=[],
                lost=[], inline={}, sources={}, wakeups=[], overhead=None)
    data.pop('tail_calls', None)
    data.pop('uftrace', None)
    data['cols'] = dict(task=[0] * count, depth=[0] * count, start=[0] + [1000] * (count - 1),
                        dur=[1 + index % 999 for index in range(count)], name=[0] * count,
                        parent=[-1] * count, line=list(range(1, count + 1)), flags=[0] * count,
                        ret=None, caller=[1] * count, args=None, module=None, cpu=None, adj=None)
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
            model.dist(model.sName[0]);
            const result = {open_handler_ms: performance.now() - began, page_handler_ms: [], page_frame_ms: []};
            const pane = document.querySelector('.tl-detail'), visited = new Set();
            const observe = () => {
                const rows = Array.from(pane.querySelectorAll('.an-list [data-slice]'));
                for (const row of rows) {
                    const index = +row.dataset.slice;
                    if (!Number.isInteger(index) || index < 0 || index >= count) throw new Error('Invalid call identity');
                    visited.add(index);
                }
                return rows.length;
            };
            result.initial_rows = observe();
            result.maximum_rows = result.initial_rows;
            const next = pane.querySelector('[data-act=dist-next], [data-act=dist-more]');
            while (!next.disabled) {
                const start = performance.now();
                next.click();
                result.page_handler_ms.push(performance.now() - start);
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                result.page_frame_ms.push(performance.now() - start);
                result.maximum_rows = Math.max(result.maximum_rows, observe());
            }
            result.final_rows = observe();
            result.visited_calls = visited.size;
            if (visited.size !== count) throw new Error('Incomplete call traversal');
            result.panel_elements = pane.querySelectorAll('*').length;
            result.final_status = pane.querySelector('.an-dist-count').textContent;
            result.all_page_handlers_ms = result.page_handler_ms.reduce((total, value) => total + value, 0);
            result.all_page_frames_ms = result.page_frame_ms.reduce((total, value) => total + value, 0);
            return result;
        }''', count)
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
    parser.add_argument('--calls', type=int, default=5000)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.calls < 1 or args.rounds < 1:
        parser.error('--calls and --rounds must be positive')
    samples = {'before': [], 'after': []}
    with tempfile.TemporaryDirectory(prefix='fg-dist-benchmark-') as directory:
        reports = {name: Path(directory) / (name + '.html') for name in samples}
        fixture(args.before, reports['before'], args.calls)
        fixture(args.after, reports['after'], args.calls)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                version = browser.version
                for round_index in range(args.rounds):
                    for name in ('before', 'after') if round_index % 2 == 0 else ('after', 'before'):
                        samples[name].append(measure(browser, reports[name], args.calls))
            finally:
                browser.close()
    metrics = ('open_handler_ms', 'all_page_handlers_ms', 'all_page_frames_ms', 'initial_rows', 'maximum_rows', 'final_rows', 'visited_calls', 'panel_elements')
    result = dict(browser=version, viewport=dict(width=1500, height=900), calls=args.calls, rounds=args.rounds,
                  fixture='synthetic non-overlapping calls; one function and one call site',
                  scope='Handlers, two-rAF intervals and retained call rows; excludes startup, CSV and peak memory',
                  samples=samples,
                  medians={name: {metric: median(sample[metric] for sample in records) for metric in metrics} for name, records in samples.items()})
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)


if __name__ == '__main__':
    main()
