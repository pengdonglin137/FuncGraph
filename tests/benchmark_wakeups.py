#!/usr/bin/env python3
"""Measure zoom redraws with a large, identical synthetic wakeup stream in each report."""
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
    end = count * 1000
    data.update(n=2, mode='abs', strings=['waker', 'sleeper'],
                tasks=[dict(pid=index + 1, comm=name, kind=0, cpu=0)
                       for index, name in enumerate(['waker', 'sleeper'])],
                errs=[], chains=[], resched=[], irqoff=[], sections=[], atomic=[], inline={}, sources={}, overhead=None,
                offcpu=[[1, 0, end, 0, 0, 1, 0, 0, 0, -1]],
                wakeups=[[0, index * 1000, 1, 0, -1, 0 if index == 1 else -1] for index in range(count)])
    data.pop('uftrace', None)
    data.pop('tail_calls', None)
    data['cols'] = dict(task=[0, 1], depth=[0, 0], start=[0, 0], dur=[end, end], name=[0, 1],
                        parent=[-1, -1], line=[1, 2], flags=[0, 0])
    destination.write_text(html[:match.start(2)] + json.dumps(data, separators=(',', ':')) + html[match.end(2):],
                           encoding='utf-8')


def measure(browser, report, count, frames):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    page.set_default_timeout(60000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri())
        page.evaluate("async () => { await fgSwitchView('timeline'); }")
        results = page.evaluate('''async ({count, frames}) => {
            const api = fgView('timeline'), probe = api.probe();
            if (probe.N !== 2) throw new Error('Wakeup fixture was not loaded');
            const raf = window.requestAnimationFrame;
            const settle = () => new Promise(resolve => raf(() => raf(resolve)));
            const results = {narrow: [], dense: []};
            await settle();
            for (const [mode, span] of [['narrow', 3500], ['dense', 700000]]) {
                for (let index = 0; index < frames + 5; index++) {
                    const start = Math.floor(count / 2) * 1000 + index * 1000;
                    const duration = await new Promise((resolve, reject) => {
                        let scheduled = false;
                        window.requestAnimationFrame = callback => {
                            scheduled = true;
                            return raf(time => {
                                const began = performance.now();
                                callback(time);
                                resolve(performance.now() - began);
                            });
                        };
                        try { probe.zoomTo(start, start + span, -1); }
                        finally { window.requestAnimationFrame = raf; }
                        if (!scheduled) reject(new Error('Zoom did not schedule a redraw'));
                    });
                    if (index >= 5) results[mode].push(duration);
                }
            }
            return results;
        }''', dict(count=count, frames=frames))
        if errors:
            raise RuntimeError('; '.join(errors))
        return results
    finally:
        page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', type=Path, nargs='+')
    parser.add_argument('--wakeups', type=int, default=500_000)
    parser.add_argument('--frames', type=int, default=20)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.wakeups < 10_000 or args.rounds < 1 or not 1 <= args.frames <= args.wakeups // 4:
        parser.error('require at least 10000 wakeups, positive rounds and 1 to wakeups/4 frames')
    with tempfile.TemporaryDirectory(prefix='fg-wakeups-') as directory, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        output = dict(browser=browser.version, wakeups=args.wakeups, runs=[])
        reports = [(source, Path(directory) / (str(index) + '.html')) for index, source in enumerate(args.reports)]
        for source, destination in reports:
            fixture(source, destination, args.wakeups)
        for iteration in range(args.rounds):
            for source, destination in reports if iteration % 2 == 0 else reversed(reports):
                samples = measure(browser, destination, args.wakeups, args.frames)
                output['runs'].append(dict(report=str(source), round=iteration + 1, samples_ms=samples))
                args.output.write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')
                print(source, {mode: round(median(times), 3) for mode, times in samples.items()}, flush=True)
        browser.close()


if __name__ == '__main__':
    main()
