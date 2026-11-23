"""Measure full analysis-window CSV responsiveness and verify unchanged bytes."""

import argparse
import json
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory

from benchmark_windows import fixture


def measure(browser, report, panel, throttle, action='window-csv', open_action=None, setup=None):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri())
        page.evaluate('''async ({panel, openAction}) => {
            await fgSwitchView('timeline');
            if (openAction) document.querySelector('[data-act="' + openAction + '"]').click();
            else if (panel) fgView('timeline').probe()[panel]();
        }''', dict(panel=panel, openAction=open_action))
        if setup:
            page.evaluate(setup)
        session = page.context.new_cdp_session(page)
        session.send('Emulation.setCPUThrottlingRate', {'rate': throttle})
        result = page.evaluate('''async action => {
            const frame = () => new Promise(requestAnimationFrame);
            await frame(); await frame();
            const create = URL.createObjectURL, click = HTMLAnchorElement.prototype.click;
            let blob = null, ready = null;
            URL.createObjectURL = value => { blob = value; return create.call(URL, value); };
            HTMLAnchorElement.prototype.click = function () {
                if (this.download) ready = performance.now();
                else click.call(this);
            };
            try {
                const start = performance.now();
                const button = document.querySelector('[data-act="' + action + '"]');
                if (!button || button.disabled) return null;
                button.click();
                const handler = performance.now() - start;
                let last = start, maximum = 0, first = null;
                const trackFrame = async () => {
                    await frame();
                    const now = performance.now(), gap = now - last;
                    first ??= gap;
                    maximum = Math.max(maximum, gap);
                    last = now;
                };
                while (ready === null && performance.now() - start < 30000) await trackFrame();
                if (ready === null || !blob) throw Error(action + ' did not produce a CSV');
                await trackFrame();
                const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
                return {handler, ready: ready - start, first, maximum, bytes: blob.size,
                        digest: Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')};
            } finally {
                URL.createObjectURL = create;
                HTMLAnchorElement.prototype.click = click;
            }
        }''', action)
        if errors:
            raise RuntimeError('\n'.join(errors))
        return result
    finally:
        page.context.close()


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', nargs='?', type=Path)
    parser.add_argument('--windows', nargs='+', type=int, default=[0, 50000], help='synthetic window counts; 0 uses the original report')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--cpu-rate', type=float, default=4)
    args = parser.parse_args()
    if args.rounds < 1 or args.cpu_rate < 1 or any(count < 0 for count in args.windows):
        parser.error('rounds and CPU rate must be >= 1; window counts must be nonnegative')
    sources = {name: getattr(args, name).resolve() for name in ('before', 'after') if getattr(args, name)}
    with TemporaryDirectory(prefix='fg-window-csv-') as root, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for count in args.windows:
            reports = sources.copy()
            if count:
                for name, source in sources.items():
                    reports[name] = Path(root) / f'{name}-{count}.html'
                    fixture(source, reports[name], count)
            for panel in ('sections', 'latency'):
                samples = {name: [] for name in reports}
                for round_index in range(args.rounds):
                    for name in reports if round_index % 2 == 0 else reversed(reports):
                        samples[name].append(measure(browser, reports[name], panel, args.cpu_rate))
                identities = {(sample['bytes'], sample['digest']) for values in samples.values() for sample in values}
                if len(identities) != 1:
                    raise RuntimeError(f'{panel} output differs for {count or "the original report"}')
                summary = {name: {metric: round(median(value[metric] for value in values), 1)
                                  for metric in ('handler', 'ready', 'first', 'maximum')} for name, values in samples.items()}
                print(json.dumps(dict(browser=browser.version, cpu_rate=args.cpu_rate, windows=count, panel=panel,
                                      median_ms=summary, samples=samples)), flush=True)
        browser.close()


if __name__ == '__main__':
    main()
