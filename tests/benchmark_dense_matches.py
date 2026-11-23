#!/usr/bin/env python3
"""Compare cold and warm filtered Timeline redraws on identical dense rows."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from statistics import median
import tempfile

from playwright.sync_api import sync_playwright


def fixture(template, destination, count):
    html = template.read_text(encoding='utf-8')
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
    if not match:
        raise ValueError('Expected a single-file Timeline report: ' + str(template))
    data = json.loads(match[2])
    burst = count // 2
    offsets = [0] + [1000] * (count - 1)
    offsets[burst + 1:burst + 2000] = [0] * 1999
    offsets[burst + 2000] = 2000000
    data.update(n=count, mode='abs', strings=['call'], tasks=[dict(pid=1, comm='worker', kind=0, cpu=0)],
                errs=[], chains=[], resched=[], irqoff=[], sections=[], atomic=[], inline={}, sources={}, overhead=None,
                offcpu=[], wakeups=[], lost=[])
    for field in ('uftrace', 'tail_calls', 'kvm', 'process_events'):
        data.pop(field, None)
    data['cols'] = dict(task=[0] * count, depth=[0] * count, start=offsets, dur=[0] * count, name=[0] * count,
                        parent=[-1] * count, line=list(range(1, count + 1)), flags=[0] * count)
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
            const api = fgView('timeline'), probe = api.probe(), raf = window.requestAnimationFrame;
            const settle = () => new Promise(resolve => raf(() => raf(resolve)));
            const measureFrame = action => new Promise((resolve, reject) => {
                let scheduled = false;
                window.requestAnimationFrame = callback => {
                    scheduled = true;
                    return raf(time => {
                        const start = performance.now();
                        callback(time);
                        resolve(performance.now() - start);
                    });
                };
                try { action(); } finally { window.requestAnimationFrame = raf; }
                if (!scheduled) reject(new Error('Redraw not scheduled'));
            });
            const burst = Math.floor(count / 2), timestamp = burst * 1000, result = {};
            const matches = [burst + 1401, burst + 2000];
            for (let index = 1; index < count; index += 17) matches.push(index);
            await settle();
            for (const [mode, start, end] of [['narrow', timestamp - 100, timestamp + 100],
                                             ['sparse', timestamp - 950000, timestamp - 50000],
                                             ['full', 0, count * 1000]]) {
                probe.zoomTo(start, end);
                await settle();
                const cold = [], warm = [];
                for (let index = 0; index < frames + 3; index++) {
                    fgNotifyFilter('', null);
                    await settle();
                    const redraw = await measureFrame(() => fgNotifyFilter('dense benchmark', new Set(matches)));
                    const repeat = await measureFrame(api.draw);
                    if (index >= 3) { cold.push(redraw); warm.push(repeat); }
                }
                result[mode] = {cold, warm, pixels: document.querySelector('.tl-canvas').toDataURL()};
            }
            return result;
        }''', dict(count=count, frames=frames))
        if errors:
            raise RuntimeError('\n'.join(errors))
        for mode in results.values():
            mode['pixels_sha256'] = hashlib.sha256(mode.pop('pixels').encode()).hexdigest()
        return results
    finally:
        page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--calls', type=int, default=400_000)
    parser.add_argument('--frames', type=int, default=12)
    parser.add_argument('--rounds', type=int, default=3)
    args = parser.parse_args()
    if args.calls < 10000 or min(args.frames, args.rounds) < 1:
        parser.error('require at least 10000 calls, one frame and one round')
    with tempfile.TemporaryDirectory(prefix='fg-dense-matches-') as directory, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        results = dict(browser=browser.version, calls=args.calls, frames=args.frames, rounds=args.rounds,
                       phases={variant: {mode: dict(cold=[], warm=[], pixels_sha256=None) for mode in ('narrow', 'sparse', 'full')}
                               for variant in ('before', 'after')})
        reports = {}
        for variant in ('before', 'after'):
            report = Path(directory) / (variant + '.html')
            fixture(getattr(args, variant), report, args.calls)
            reports[variant] = report
        for round_index in range(args.rounds):
            order = ('before', 'after') if round_index % 2 == 0 else ('after', 'before')
            for variant in order:
                for mode, sample in measure(browser, reports[variant], args.calls, args.frames).items():
                    combined = results['phases'][variant][mode]
                    for phase in ('cold', 'warm'):
                        combined[phase].extend(sample[phase])
                    if combined['pixels_sha256'] not in (None, sample['pixels_sha256']):
                        raise RuntimeError(variant + ' ' + mode + ' canvas changed across runs')
                    combined['pixels_sha256'] = sample['pixels_sha256']
        browser.close()
    for mode in ('narrow', 'sparse', 'full'):
        before, after = (results['phases'][variant][mode] for variant in ('before', 'after'))
        if before['pixels_sha256'] != after['pixels_sha256']:
            raise RuntimeError(mode + ' changed the filtered canvas')
        for variant in (before, after):
            variant['cold_median_ms'] = median(variant['cold'])
            variant['warm_median_ms'] = median(variant['warm'])
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
