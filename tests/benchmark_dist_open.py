#!/usr/bin/env python3
"""Measure duration-distribution opens or exports without changing trace data."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory

from benchmark_distcalls import fixture


def measure(browser, report, throttle, export_csv=False):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri(), timeout=60000)
        page.context.new_cdp_session(page).send('Emulation.setCPUThrottlingRate', {'rate': throttle})
        page.evaluate("async () => { await fgSwitchView('timeline'); }")
        observed = page.evaluate('''async () => {
            const frame = () => new Promise(requestAnimationFrame);
            const model = fgView('timeline').probe(), count = model.N;
            await frame(); await frame();
            const began = performance.now();
            model.dist(model.sName[0]);
            const handler = performance.now() - began;
            const pending = !!document.querySelector('.an-dist-pending');
            const firstRow = () => document.querySelector('.an-list [data-slice]');
            let ready = firstRow() ? performance.now() : null;
            const observer = new MutationObserver(() => { if (ready === null && firstRow()) ready = performance.now(); });
            observer.observe(document.querySelector('.tl-detail'), {childList:true, subtree:true});
            let previous = began, longestFrame = 0;
            do {
                await frame();
                const now = performance.now();
                longestFrame = Math.max(longestFrame, now - previous);
                previous = now;
            } while (ready === null && previous - began < 30000);
            observer.disconnect();
            if (ready === null || !document.querySelector('.an-dist-count').textContent.includes('of ' + count)) {
                throw Error('Distribution did not finish with the expected population');
            }
            const pane = document.querySelector('.tl-detail');
            return {handler, longestFrame, ready:ready-began, pending, view:[
                pane.querySelector('.tl-kv').textContent,
                [...pane.querySelector('[data-act=dist-bucket]').options].map(option => option.textContent),
                [...pane.querySelectorAll('.an-list [data-slice]')].map(row => [row.dataset.slice, row.textContent])
            ]};
        }''')
        with page.expect_download() as download:
            if export_csv:
                observed.update(page.evaluate('''async () => {
                    const frame = () => new Promise(requestAnimationFrame);
                    await frame(); await frame();
                    const button = document.querySelector('[data-act=dist-csv]'), click = HTMLAnchorElement.prototype.click;
                    let complete = null;
                    HTMLAnchorElement.prototype.click = function() {
                        if (this.download) complete = performance.now();
                        click.call(this);
                    };
                    try {
                        const began = performance.now();
                        button.click();
                        const handler = performance.now() - began;
                        const pending = button.textContent === 'Cancel CSV';
                        let previous = began, longestFrame = 0;
                        do {
                            await frame();
                            const now = performance.now();
                            longestFrame = Math.max(longestFrame, now - previous);
                            previous = now;
                        } while (complete === null && previous - began < 30000);
                        if (complete === null) throw Error('CSV export did not finish');
                        return {handler, longestFrame, ready:complete-began, pending};
                    } finally { HTMLAnchorElement.prototype.click = click; }
                }'''))
            else:
                page.locator('[data-act=dist-csv]').click()
        if errors:
            raise RuntimeError('\n'.join(errors))
        observed['view_sha256'] = hashlib.sha256(json.dumps(observed.pop('view')).encode()).hexdigest()
        observed['csv_sha256'] = hashlib.sha256(download.value.path().read_bytes()).hexdigest()
        return observed
    finally:
        page.close()


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', nargs='?', type=Path)
    parser.add_argument('--calls', type=int, default=200000)
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--throttle', type=float, default=4)
    parser.add_argument('--export-csv', action='store_true', help='measure CSV export rather than the panel open')
    args = parser.parse_args()
    if args.calls < 1 or args.rounds < 1 or args.throttle < 1:
        parser.error('Positive call/round counts and a throttle of at least 1 are required')
    with TemporaryDirectory(prefix='fg-dist-open-') as folder, sync_playwright() as playwright:
        sources = {'before': args.before, **({'after': args.after} if args.after else {})}
        reports = {name: Path(folder) / (name + '.html') for name in sources}
        for name, source in sources.items():
            fixture(source, reports[name], args.calls)
        browser = playwright.chromium.launch()
        try:
            samples = {name: [] for name in reports}
            for iteration in range(args.rounds):
                for name in reports if iteration % 2 == 0 else reversed(reports):
                    samples[name].append(measure(browser, reports[name], args.throttle, args.export_csv))
            if len({(sample['view_sha256'], sample['csv_sha256']) for rows in samples.values() for sample in rows}) != 1:
                raise RuntimeError('Distribution views or full CSV differ')
            summary = {name: {key: round(median(sample[key] for sample in rows), 1)
                              for key in ('handler', 'longestFrame', 'ready')} for name, rows in samples.items()}
            print(json.dumps(dict(browser=browser.version, calls=args.calls, throttle=args.throttle,
                                  metric='export-csv' if args.export_csv else 'open',
                                  median_ms=summary, samples=samples)), flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    main()
