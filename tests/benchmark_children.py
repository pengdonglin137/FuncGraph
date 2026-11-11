#!/usr/bin/env python3
"""Compare complete child-group/call traversal without dropping recorded members."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import re
from statistics import median
import tempfile


def fixture(report, destination, groups):
    html = report.read_text(encoding='utf-8')
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
    if not match:
        raise ValueError('Expected a single-file Timeline report: ' + str(report))
    data = json.loads(match[2])
    starts, durations, parents, names, depths = [0], [groups * 10000 + 1000], [-1], [0], [3]
    for group in range(groups):
        first = len(names)
        starts.extend([group * 10000 + offset for offset in (100, 200, 2000, 2100)])
        durations.extend([1000, 300, 4000, 500])
        parents.extend([0, first, 0, first + 2])
        names.extend([group + 2, 1, group + 2, 1])
        depths.extend([5, 6, 5, 6])
    count = len(names)
    data.update(n=count, mode='abs', strings=['parent', 'grandchild'] + [f'child_{group:06d}' for group in range(groups)],
                tasks=[dict(pid=1, comm='children-benchmark', kind=0, cpu=0)],
                errs=[], chains=[], offcpu=[], resched=[], irqoff=[], sections=[], atomic=[],
                lost=[], inline={}, sources={}, wakeups=[], overhead=None)
    data.pop('tail_calls', None)
    data.pop('uftrace', None)
    data['cols'] = dict(task=[0] * count, depth=depths,
                        start=[value - (starts[index - 1] if index else 0) for index, value in enumerate(starts)],
                        dur=durations, name=names, parent=parents, line=list(range(1, count + 1)),
                        flags=[0] * count, ret=None, caller=None, args=None, module=None, cpu=None, adj=None)
    data['stats'].update(tasks=1, slices=count, offcpu=0)
    destination.write_text(html[:match.start(2)] + json.dumps(data, separators=(',', ':')) + html[match.end(2):], encoding='utf-8')


def measure(browser, report, groups):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    page.set_default_timeout(120000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri())
        page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().select(0); }")
        result = page.evaluate('''async (groups) => {
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const began = performance.now();
            document.querySelector('[data-child-open]').click();
            const result = {open_handler_ms: performance.now() - began, modes: {}};
            const pane = document.querySelector('.tl-detail');
            for (const mode of ['groups', 'calls']) {
                pane.querySelector('[data-child-mode=' + mode + ']').click();
                const visited = new Set(), handlers = [], frames = [];
                const observe = () => {
                    const rows = Array.from(pane.querySelectorAll('.an-child-table tbody tr'));
                    for (const row of rows) {
                        if (mode === 'groups') {
                            const name = row.querySelector('td').textContent;
                            const index = Number(name.slice('child_'.length));
                            if (!name.startsWith('child_') || !Number.isInteger(index) || index < 0 || index >= groups) throw new Error('Invalid child group');
                            visited.add(name);
                        } else {
                            const index = Number(row.dataset.childCall);
                            if (!Number.isInteger(index) || index < 1 || index > groups * 4 || ![1, 3].includes(index % 4)) throw new Error('Grandchild or invalid call included');
                            visited.add(index);
                        }
                    }
                    return rows.length;
                };
                const metrics = {initial_rows: observe()};
                metrics.maximum_rows = metrics.initial_rows;
                const next = pane.querySelector('[data-child-page="1"], [data-child-more]');
                while (next && !next.disabled) {
                    const started = performance.now();
                    next.click();
                    handlers.push(performance.now() - started);
                    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    frames.push(performance.now() - started);
                    metrics.maximum_rows = Math.max(metrics.maximum_rows, observe());
                }
                metrics.final_rows = observe();
                const expected = mode === 'groups' ? groups : groups * 2;
                if (visited.size !== expected) throw new Error('Incomplete child traversal: ' + visited.size + '/' + expected);
                Object.assign(metrics, {visited: visited.size, page_actions: handlers.length,
                    page_handler_ms: handlers, page_frame_ms: frames,
                    all_page_handlers_ms: handlers.reduce((total, value) => total + value, 0),
                    all_page_frames_ms: frames.reduce((total, value) => total + value, 0),
                    panel_elements: pane.querySelectorAll('*').length,
                    status: pane.querySelector('.an-child-footer [role=status]').textContent});
                result.modes[mode] = metrics;
            }
            return result;
        }''', groups)
        unchanged = page.locator('.an-child-footer [role=status]').inner_text()
        result['csv'] = {}
        for mode, expected in (('groups', groups), ('calls', groups * 2)):
            with page.expect_download() as download:
                page.locator('[data-child-csv=' + mode + ']').click()
            content = Path(download.value.path()).read_bytes()
            records = list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
            if len(records) != expected:
                raise RuntimeError('Incomplete ' + mode + ' CSV')
            result['csv'][mode] = dict(rows=len(records), bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
        if page.locator('.an-child-footer [role=status]').inner_text() != unchanged:
            raise RuntimeError('CSV changed child paging state')
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
    parser.add_argument('--groups', type=int, default=2500)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.groups < 1 or args.rounds < 1:
        parser.error('--groups and --rounds must be positive')
    samples = {'before': [], 'after': []}
    with tempfile.TemporaryDirectory(prefix='fg-children-benchmark-') as directory:
        reports = {name: Path(directory) / (name + '.html') for name in samples}
        for name in reports:
            fixture(getattr(args, name), reports[name], args.groups)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                version = browser.version
                for round_index in range(args.rounds):
                    for name in ('before', 'after') if round_index % 2 == 0 else ('after', 'before'):
                        samples[name].append(measure(browser, reports[name], args.groups))
            finally:
                browser.close()
    metrics = ('initial_rows', 'maximum_rows', 'final_rows', 'visited', 'page_actions',
               'all_page_handlers_ms', 'all_page_frames_ms', 'panel_elements')
    for mode in ('groups', 'calls'):
        hashes = {sample['csv'][mode]['sha256'] for records in samples.values() for sample in records}
        if len(hashes) != 1:
            raise RuntimeError('Before/after ' + mode + ' CSV bytes differ')
    result = dict(browser=version, viewport=dict(width=1500, height=900), groups=args.groups,
                  direct_calls=args.groups * 2, grandchildren=args.groups * 2, rounds=args.rounds,
                  fixture='one parent; two direct calls and two excluded grandchildren per function',
                  scope='Handlers, two-rAF intervals and retained rows; excludes startup, CSV and peak memory',
                  samples=samples,
                  medians={name: {mode: {metric: median(sample['modes'][mode][metric] for sample in records)
                                         for metric in metrics} for mode in ('groups', 'calls')}
                           for name, records in samples.items()})
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)


if __name__ == '__main__':
    main()
