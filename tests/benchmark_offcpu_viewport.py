#!/usr/bin/env python3
"""Compare off-CPU redraws with only a small part of a deep track visible."""

import argparse
import hashlib
import json
from pathlib import Path
import re
from statistics import median
from tempfile import TemporaryDirectory

from playwright.sync_api import sync_playwright


def fixture(source, destination, count, *, call_depth=92):
    html = source.read_text(encoding='utf-8')
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
    data = json.loads(match[2])
    data.update(n=2, mode='abs', strings=['worker'], tasks=[dict(pid=1, comm='worker', kind=0, cpu=0)],
                errs=[], chains=[], sections=[], atomic=[], inline={}, sources={}, overhead=None, wakeups=[],
                resched=[], irqoff=[], lost=[])
    data['cols'] = dict(task=[0, 0], depth=[0, call_depth], start=[0, 10], dur=[count * 1000, count * 1000 - 20],
                        name=[0, 0], parent=[-1, 0], line=[1, 2], flags=[0, 0])
    data['offcpu'] = [[0, index * 1000, index * 1000 + 500, 71 if index // 128 % 64 == 0 else 1,
                       -1, -1, 0, 0, index % 2, -1] for index in range(count)]
    for field in ('uftrace', 'tail_calls', 'kvm', 'process_events'):
        data.pop(field, None)
    destination.write_text(html[:match.start(2)] + json.dumps(data, separators=(',', ':')) + html[match.end(2):],
                           encoding='utf-8')


def measure(browser, report, count, frames, cpu_rate):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri() + '#fg-offcpu-benchmark')
        session = page.context.new_cdp_session(page)
        session.send('Emulation.setCPUThrottlingRate', {'rate': cpu_rate})
        result = page.evaluate('''async ({count, frames}) => {
            const begin = performance.now(), api = await fgSwitchView('timeline');
            const startup = performance.now() - begin;
            const probe = api.probe(), scroll = document.querySelector('.tl-scroll');
            const canvas = document.querySelector('.tl-canvas');
            const raf = window.requestAnimationFrame;
            const settle = () => new Promise(resolve => raf(() => raf(resolve)));
            const draw = () => new Promise(resolve => {
                window.requestAnimationFrame = callback => raf(time => {
                    const start = performance.now();
                    callback(time);
                    resolve(performance.now() - start);
                });
                try { api.draw(); } finally { window.requestAnimationFrame = raf; }
            });
            const scenarios = count ? [['deep-wide', 23000, count * 1000 - 3000, true],
                                       ['deep-zoom', 0, 80000, true], ['shallow-zoom', 150000, 230000, false]] :
                                      [['original', null, null, false]];
            const results = {startup};
            for (const [name, start, end, deep] of scenarios) {
                if (start !== null) probe.zoomTo(start, end);
                if (deep) {
                    const row = probe.TRACKS[0].off[0];
                    scroll.scrollTop += probe.offXY(row)[1] - canvas.getBoundingClientRect().top - 30;
                } else scroll.scrollTop = 0;
                await settle();
                const samples = [];
                for (let index = 0; index < frames + 3; index++) {
                    const duration = await draw();
                    if (index >= 3) samples.push(duration);
                }
                results[name] = {samples, image: canvas.toDataURL()};
            }
            return results;
        }''', dict(count=count, frames=frames))
        if errors:
            raise RuntimeError('; '.join(errors))
        for name, value in result.items():
            if name != 'startup':
                value['sha256'] = hashlib.sha256(value.pop('image').encode()).hexdigest()
        return result
    finally:
        page.context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+', type=Path)
    parser.add_argument('--intervals', type=int, default=200000, help='0 uses original data')
    parser.add_argument('--frames', type=int, default=12)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--cpu-rate', type=float, default=4)
    parser.add_argument('--allow-dense-pixels', action='store_true', help='allow different wide-view pixels only')
    args = parser.parse_args()
    if not (args.intervals == 0 or args.intervals >= 1024) or min(args.frames, args.rounds, args.cpu_rate) < 1:
        parser.error('use 0 or at least 1024 intervals and positive frames, rounds and CPU rate')
    with TemporaryDirectory(prefix='fg-offcpu-rows-') as directory, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        reports = [source.resolve() for source in args.reports]
        if args.intervals:
            reports = [Path(directory) / f'{index}.html' for index in range(len(args.reports))]
            for source, target in zip(args.reports, reports):
                fixture(source, target, args.intervals)
        expected = None
        for iteration in range(args.rounds):
            for index in (range(len(reports)) if iteration % 2 == 0 else reversed(range(len(reports)))):
                result = measure(browser, reports[index], args.intervals, args.frames, args.cpu_rate)
                startup = result.pop('startup')
                identity = {name: value['sha256'] for name, value in result.items()}
                compared = {name: digest for name, digest in identity.items()
                            if not (args.allow_dense_pixels and name == 'deep-wide')}
                if expected is None:
                    expected = compared
                elif compared != expected:
                    raise RuntimeError(f'Track pixels differ: {compared} != {expected}')
                print(json.dumps(dict(browser=browser.version, report=str(args.reports[index]), intervals=args.intervals,
                                      round=iteration + 1, startup=startup, sha256=identity,
                                      medians={name: median(value['samples']) for name, value in result.items()})), flush=True)
        browser.close()


if __name__ == '__main__':
    main()
