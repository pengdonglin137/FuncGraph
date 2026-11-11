#!/usr/bin/env python3
"""Compare scheduling overlay redraw and wait hit testing in single-file reports."""
import argparse
import json
from pathlib import Path
import re
from statistics import median
import tempfile

from playwright.sync_api import sync_playwright


def fixture(source, destination, count):
    html = source.read_text(encoding='utf-8')
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
    hook = 'function hitTest(mx, my) {'
    if not match or html.count(hook) != 1:
        raise ValueError(f'Expected a single-file Timeline report with a hit-test function: {source}')
    data = json.loads(match[2])
    intervals = [(index * 1000, index * 1000 + 500) for index in range(count)]
    data.update(n=1, mode='abs', strings=['worker'], tasks=[dict(pid=1, comm='worker', kind=0, cpu=0)],
                errs=[], chains=[], sections=[], atomic=[], inline={}, sources={}, overhead=None, wakeups=[],
                offcpu=[[0, start, end, 1, -1, -1, 0, 0, 0, -1] for start, end in intervals],
                resched=[[0, start, end, 1, 0, 0] for start, end in intervals],
                irqoff=[[0, start, end] for start, end in intervals])
    data['cols'] = dict(task=[0], depth=[0], start=[0], dur=[count * 1000], name=[0],
                        parent=[-1], line=[1], flags=[0])
    data.pop('uftrace', None)
    data.pop('tail_calls', None)
    html = html[:match.start(2)] + json.dumps(data, separators=(',', ':')) + html[match.end(2):]
    destination.write_text(html.replace(hook, 'window.fgBenchmarkHit = hitTest;\n' + hook), encoding='utf-8')


def measure(browser, report, count, frames, overview_frames):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri())
        page.evaluate("async () => { await fgSwitchView('timeline'); }")
        samples = page.evaluate('''async ({count, frames, overviewFrames}) => {
            const probe = fgView('timeline').probe(), track = probe.TRACKS[0];
            const canvas = document.querySelector('.tl-canvas'), raf = window.requestAnimationFrame;
            const settle = () => new Promise(resolve => raf(() => raf(resolve)));
            const results = {narrow_redraw: [], hit: []};
            const redraw = (start, end) => new Promise((resolve, reject) => {
                let scheduled = false;
                window.requestAnimationFrame = callback => {
                    scheduled = true;
                    return raf(time => {
                        const began = performance.now();
                        callback(time);
                        resolve(performance.now() - began);
                    });
                };
                try { probe.zoomTo(start, end, -1); }
                finally { window.requestAnimationFrame = raf; }
                if (!scheduled) reject(new Error('Expected redraw after zoom'));
            });
            await settle();
            for (let index = 0; index < frames + 5; index++) {
                const ordinal = Math.floor(count / 2) + index, start = ordinal * 1000;
                const frame = await redraw(start, start + 3500);
                const [pageX, pageY] = probe.offXY(track.off[ordinal]), box = canvas.getBoundingClientRect();
                const hit = window.fgBenchmarkHit;
                if (hit(pageX - box.left, pageY - box.top).off !== track.off[ordinal]) throw new Error('Expected wait hit');
                const began = performance.now();
                for (let repeat = 0; repeat < 200; repeat++) hit(pageX - box.left, pageY - box.top);
                if (index >= 5) {
                    results.hit.push((performance.now() - began) / 200);
                    results.narrow_redraw.push(frame);
                }
            }
            if (overviewFrames) {
                results.overview_redraw = [];
                await redraw(0, count * 1000);
                for (let index = 0; index < overviewFrames; index++) results.overview_redraw.push(await redraw(0, count * 1000));
            }
            return results;
        }''', dict(count=count, frames=frames, overviewFrames=overview_frames))
        if errors:
            raise RuntimeError('; '.join(errors))
        return samples
    finally:
        page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+', type=Path)
    parser.add_argument('--intervals', type=int, default=200_000, help='intervals per overlay')
    parser.add_argument('--frames', type=int, default=20)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--overview-frames', type=int, default=0)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.intervals < 1000 or not 1 <= args.frames <= args.intervals // 4 or args.rounds < 1 or args.overview_frames < 0:
        parser.error('require at least 1000 intervals, positive rounds, 1 to intervals/4 frames and nonnegative overview frames')
    with tempfile.TemporaryDirectory(prefix='fg-scheduling-') as directory, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        output = dict(browser=browser.version, intervals_per_overlay=args.intervals, runs=[])
        reports = [(source, Path(directory) / f'{index}.html') for index, source in enumerate(args.reports)]
        for source, destination in reports:
            fixture(source, destination, args.intervals)
        for iteration in range(args.rounds):
            for source, destination in reports if iteration % 2 == 0 else reversed(reports):
                samples = measure(browser, destination, args.intervals, args.frames, args.overview_frames)
                output['runs'].append(dict(report=str(source), round=iteration + 1, samples_ms=samples))
                args.output.write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')
                print(source, {key: round(median(values), 4) for key, values in samples.items()}, flush=True)
        browser.close()


if __name__ == '__main__':
    main()
