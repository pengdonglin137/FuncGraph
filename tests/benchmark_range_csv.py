#!/usr/bin/env python3
"""Compare range CSV response times and verify output bytes on identical reports."""
import argparse
import json
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory

from benchmark_range import fixture


def measure(browser, report):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri(), timeout=60000)
        result = page.evaluate('''async () => {
            await fgSwitchView('timeline');
            const model = fgView('timeline').probe();
            model.range(...model.view());
            const frame = () => new Promise(requestAnimationFrame);
            await frame(); await frame();
            let blob = null, ready = null;
            const create = URL.createObjectURL, click = HTMLAnchorElement.prototype.click;
            URL.createObjectURL = function(input) { blob = input; return create.call(this, input); };
            HTMLAnchorElement.prototype.click = function() {
                if (this.download) ready = performance.now();
                else click.call(this);
            };
            try {
                const start = performance.now();
                document.querySelector('[data-act=range-csv]').click();
                const handler = performance.now() - start;
                const gaps = [];
                let last = start;
                const trackFrame = async () => {
                    await frame();
                    const now = performance.now();
                    gaps.push(now - last);
                    last = now;
                };
                while (ready === null && performance.now() - start < 15000) await trackFrame();
                if (ready === null || !blob) throw Error('CSV not produced');
                await trackFrame(); await trackFrame();
                const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
                return {calls:model.N, handler, ready:ready-start, frame:Math.max(...gaps), bytes:blob.size,
                    digest:Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')};
            } finally {
                URL.createObjectURL = create;
                HTMLAnchorElement.prototype.click = click;
            }
        }''')
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
    parser.add_argument('--functions', nargs='+', type=int, default=[0, 300000, 750000],
                        help='synthetic function counts (0 measures the original report)')
    parser.add_argument('--rounds', type=int, default=3)
    args = parser.parse_args()
    if args.rounds < 1 or any(count < 0 for count in args.functions):
        parser.error('rounds must be positive and function counts nonnegative')
    sources = {name: getattr(args, name).resolve() for name in ('before', 'after')}
    with TemporaryDirectory(prefix='fg-range-csv-') as root, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for count in args.functions:
                reports = sources.copy()
                if count:
                    for name, source in sources.items():
                        reports[name] = Path(root) / f'{name}-{count}.html'
                        fixture(source, reports[name], count)
                samples = {name: [] for name in reports}
                for round_index in range(args.rounds):
                    for name in reports if round_index % 2 == 0 else reversed(reports):
                        samples[name].append(measure(browser, reports[name]))
                values = [value for observations in samples.values() for value in observations]
                if len({(value['calls'], value['bytes'], value['digest']) for value in values}) != 1:
                    raise RuntimeError(f'CSV outputs differ for {count or "the original report"}')
                summary = {name: {key: round(median(value[key] for value in observations), 1)
                                  for key in ('handler', 'ready', 'frame')} for name, observations in samples.items()}
                print(json.dumps(dict(browser=browser.version, calls=values[0]['calls'], bytes=values[0]['bytes'],
                                      median_ms=summary, samples=samples)), flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    main()
