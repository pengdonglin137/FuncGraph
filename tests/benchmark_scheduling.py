#!/usr/bin/env python3
"""Compare scheduling overlays or many-task track redraw and hit testing."""
import argparse
import json
from pathlib import Path
import re
from statistics import median
import tempfile

from playwright.sync_api import sync_playwright


def fixture(source, destination, count, tracks=0):
    html = source.read_text(encoding='utf-8')
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
    hook = 'function hitTest(mx, my) {'
    if not match or html.count(hook) != 1:
        raise ValueError(f'Expected a single-file Timeline report with a hit-test function: {source}')
    data = json.loads(match[2])
    intervals = [] if tracks else [(index * 1000, index * 1000 + 500) for index in range(count)]
    data.update(n=1, mode='abs', strings=['worker'], tasks=[dict(pid=1, comm='worker', kind=0, cpu=0)],
                errs=[], chains=[], sections=[], atomic=[], inline={}, sources={}, overhead=None, wakeups=[],
                offcpu=[[0, start, end, 1, -1, -1, 0, 0, 0, -1] for start, end in intervals],
                resched=[[0, start, end, 1, 0, 0] for start, end in intervals],
                irqoff=[[0, start, end] for start, end in intervals])
    data['cols'] = dict(task=[0], depth=[0], start=[0], dur=[count * 1000], name=[0],
                        parent=[-1], line=[1], flags=[0])
    if tracks:
        data.update(n=tracks, tasks=[dict(pid=index + 1, comm=f'worker_{index}', kind=0, cpu=0)
                                    for index in range(tracks)])
        data['cols'] = dict(task=list(range(tracks)), depth=[0] * tracks, start=[0] * tracks,
                            dur=[count * 1000] * tracks, name=[0] * tracks, parent=[-1] * tracks,
                            line=list(range(1, tracks + 1)), flags=[0] * tracks)
    data.pop('uftrace', None)
    data.pop('tail_calls', None)
    html = html[:match.start(2)] + json.dumps(data, separators=(',', ':')) + html[match.end(2):]
    destination.write_text(html.replace(hook, 'window.fgBenchmarkHit = hitTest;\n' + hook), encoding='utf-8')


def measure(browser, report, count, frames, overview_frames, tracks=0):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri())
        page.evaluate("async () => { await fgSwitchView('timeline'); }")
        samples = page.evaluate('''async ({count, frames, overviewFrames, tracks}) => {
            const probe = fgView('timeline').probe(), task = Math.floor(tracks * 0.9), track = probe.TRACKS[task];
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
            if (tracks) document.querySelector('.tl-scroll').scrollTop = track.y - 5;
            await settle();
            for (let index = 0; index < frames + 5; index++) {
                const ordinal = Math.floor(count / 2) + index, start = ordinal * 1000;
                const frame = await redraw(start, start + 3500);
                const [pageX, pageY] = tracks ? probe.xyOf(task) : probe.offXY(track.off[ordinal]);
                const box = canvas.getBoundingClientRect();
                const hit = window.fgBenchmarkHit;
                const found = hit(pageX - box.left, pageY - box.top);
                if (tracks ? found.slice !== task : found.off !== track.off[ordinal]) throw new Error('Unexpected hit');
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
        }''', dict(count=count, frames=frames, overviewFrames=overview_frames, tracks=tracks))
        if errors:
            raise RuntimeError('; '.join(errors))
        return samples
    finally:
        page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+', type=Path)
    parser.add_argument('--intervals', type=int, default=200_000, help='intervals per overlay')
    parser.add_argument('--tracks', type=int, default=0, help='benchmark task tracks instead of scheduling overlays')
    parser.add_argument('--frames', type=int, default=20)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--overview-frames', type=int, default=0)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.intervals < 1000 or not 1 <= args.frames <= args.intervals // 4 or args.rounds < 1 or args.overview_frames < 0 or args.tracks < 0:
        parser.error('require at least 1000 intervals, positive rounds, 1 to intervals/4 frames and nonnegative overview frames/tracks')
    with tempfile.TemporaryDirectory(prefix='fg-scheduling-') as directory, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        output = dict(browser=browser.version, intervals_per_overlay=0 if args.tracks else args.intervals,
                      tracks=args.tracks, runs=[])
        reports = [(source, Path(directory) / f'{index}.html') for index, source in enumerate(args.reports)]
        for source, destination in reports:
            fixture(source, destination, args.intervals, args.tracks)
        for iteration in range(args.rounds):
            for source, destination in reports if iteration % 2 == 0 else reversed(reports):
                samples = measure(browser, destination, args.intervals, args.frames, args.overview_frames, args.tracks)
                output['runs'].append(dict(report=str(source), round=iteration + 1, samples_ms=samples))
                args.output.write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')
                print(source, {key: round(median(values), 4) for key, values in samples.items()}, flush=True)
        browser.close()


if __name__ == '__main__':
    main()
