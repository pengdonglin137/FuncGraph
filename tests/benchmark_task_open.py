#!/usr/bin/env python3
"""Measure cold Tasks opening separately from Timeline preparation and CSV export."""

import argparse
import hashlib
import json
from pathlib import Path
import re
from statistics import median
from tempfile import TemporaryDirectory

from benchmark_tasks import fixture as many_tasks


def many_calls(source, destination, count):
    many_tasks(source, destination, 1)
    html = destination.read_text(encoding='utf-8')
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
    data = json.loads(match[2])
    data.update(n=count, strings=['work'])
    data['stats']['slices'] = count
    data['cols'] = dict(task=[0] * count, depth=[0] * count, start=[0] + [1000] * (count - 1),
                        dur=[20] * (count - 1) + [7000], name=[0] * count,
                        parent=[-1] * count, line=list(range(1, count + 1)), flags=[0] * count)
    destination.write_text(html[:match.start(2)] + json.dumps(data, separators=(',', ':')) + html[match.end(2):],
                           encoding='utf-8')


def measure(browser, report, throttle):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri())
        page.evaluate("async () => { await fgSwitchView('timeline'); }")
        session = page.context.new_cdp_session(page)
        session.send('Emulation.setCPUThrottlingRate', {'rate': throttle})
        result = page.evaluate('''async () => {
            const frame = () => new Promise(requestAnimationFrame);
            await frame(); await frame();
            const detail = document.querySelector('.tl-detail'), button = document.querySelector('[data-act=an-tasks]');
            const start = performance.now(), firstRow = () => detail.querySelector('[data-task-row]');
            button.click();
            const handler = performance.now() - start;
            let ready = firstRow() ? performance.now() : null;
            const observer = new MutationObserver(() => { if (ready === null && firstRow()) ready = performance.now(); });
            observer.observe(detail, {childList: true, subtree: true});
            let last = start, maximum = 0;
            do {
                await frame();
                const now = performance.now();
                maximum = Math.max(maximum, now - last);
                last = now;
            } while (ready === null && last - start < 30000);
            observer.disconnect();
            if (ready === null) throw new Error('Tasks did not open');
            return {handler, maximum, ready: ready - start,
                    rows: [...detail.querySelectorAll('[data-task-row]')].map(row => [row.dataset.taskRow, row.textContent]),
                    status: detail.querySelector('.an-task-footer').textContent};
        }''')
        with page.expect_download() as download:
            page.locator('[data-act=tasks-csv]').click()
        result['digest'] = hashlib.sha256(Path(download.value.path()).read_bytes()).hexdigest()
        if errors:
            raise RuntimeError('\n'.join(errors))
        return result
    finally:
        page.context.close()


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path, nargs='?')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--cpu-rate', type=float, default=4)
    parser.add_argument('--tasks', type=int, default=50000)
    parser.add_argument('--calls', type=int, default=500000)
    args = parser.parse_args()
    if args.rounds < 1 or args.cpu_rate < 1 or min(args.tasks, args.calls) < 0:
        parser.error('rounds and CPU rate must be >= 1; tasks and calls must be nonnegative')
    with TemporaryDirectory(prefix='fg-task-open-') as directory, sync_playwright() as playwright:
        sources = {'before': args.before.resolve()}
        if args.after:
            sources['after'] = args.after.resolve()
        browser = playwright.chromium.launch()
        for scenario, build, count in (('tasks', many_tasks, args.tasks), ('calls', many_calls, args.calls), ('original', None, 0)):
            if build and not count:
                continue
            reports = sources.copy()
            if build:
                for name, source in sources.items():
                    reports[name] = Path(directory) / f'{scenario}-{name}.html'
                    build(source, reports[name], count)
            samples = {name: [] for name in reports}
            identity = None
            for index in range(args.rounds):
                for name in reports if index % 2 == 0 else reversed(reports):
                    record = measure(browser, reports[name], args.cpu_rate)
                    evidence = (record.pop('rows'), record.pop('status'), record['digest'])
                    if identity is None:
                        identity = evidence
                    elif evidence != identity:
                        raise RuntimeError(f'{scenario}: Tasks rows, paging status or CSV differ')
                    samples[name].append(record)
            timings = {name: {key: round(median(value[key] for value in values), 1)
                              for key in ('handler', 'ready', 'maximum')} for name, values in samples.items()}
            print(json.dumps(dict(browser=browser.version, scenario=scenario, count=count, cpu_rate=args.cpu_rate,
                                  median_ms=timings, samples=samples)), flush=True)
        browser.close()


if __name__ == '__main__':
    main()
