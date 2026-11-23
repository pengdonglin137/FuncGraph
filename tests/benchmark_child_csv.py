#!/usr/bin/env python3
"""Measure child explorer exports and verify byte parity between two offline reports."""
import argparse
import json
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory

from benchmark_children import fixture


def measure(browser, report, mode, cached, throttle):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri(), timeout=60000)
        page.context.new_cdp_session(page).send('Emulation.setCPUThrottlingRate', {'rate': throttle})
        page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().select(0); }")
        page.locator('[data-child-open]:not([aria-disabled=true])').click(timeout=60000)
        page.wait_for_function("document.querySelector('.an-child-table:not([aria-busy=true]) tbody tr')")
        if mode == 'calls' and cached:
            page.locator('[data-child-mode=calls]').click()
            page.wait_for_function("document.querySelector('[data-child-call]')")
        result = page.evaluate('''async mode => {
            const frame = () => new Promise(requestAnimationFrame);
            await frame(); await frame();
            const button = document.querySelector('[data-child-csv=' + mode + ']');
            const create = URL.createObjectURL, click = HTMLAnchorElement.prototype.click, tasks = [];
            let blob = null, ready = null;
            const observer = new PerformanceObserver(entries => tasks.push(...entries.getEntries()));
            observer.observe({type: 'longtask'});
            URL.createObjectURL = input => { blob = input; return create(input); };
            HTMLAnchorElement.prototype.click = function() {
                if (this.download) ready = performance.now();
                else click.call(this);
            };
            try {
                const began = performance.now();
                button.click();
                const handler = performance.now() - began;
                const phase = document.querySelector('[data-child-csv-status]')?.textContent || 'synchronous';
                let previous = began, longestFrame = 0;
                do {
                    await frame();
                    const now = performance.now();
                    longestFrame = Math.max(longestFrame, now - previous);
                    previous = now;
                } while (ready === null && previous - began < 30000);
                if (ready === null || !blob) throw Error('No CSV produced');
                await frame(); await frame();
                tasks.push(...observer.takeRecords());
                const longestTask = Math.max(0, ...tasks.filter(task => task.startTime >= began - 2 &&
                    task.startTime <= ready).map(task => task.duration));
                const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
                return {handler, longestFrame, longestTask, ready: ready - began, phase, bytes: blob.size,
                    sha256: Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')};
            } finally {
                observer.disconnect(); URL.createObjectURL = create; HTMLAnchorElement.prototype.click = click;
            }
        }''', mode)
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
    parser.add_argument('--groups', type=int, default=25000)
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--throttle', type=float, default=4)
    args = parser.parse_args()
    if args.rounds < 1 or args.groups < 1 or args.throttle < 1:
        parser.error('Use positive rounds and groups, and a CPU throttle of 1 or greater.')
    with TemporaryDirectory(prefix='fg-child-csv-') as root, sync_playwright() as playwright:
        reports = {name: Path(root) / (name + '.html') for name in ('before', 'after')}
        for name, report in reports.items():
            fixture(getattr(args, name), report, args.groups)
        browser = playwright.chromium.launch()
        call_outputs = set()
        try:
            for mode, cached in [('groups', True), ('calls', False), ('calls', True)]:
                samples = {name: [] for name in reports}
                for iteration in range(args.rounds):
                    for name in reports if iteration % 2 == 0 else reversed(reports):
                        samples[name].append(measure(browser, reports[name], mode, cached, args.throttle))
                outputs = {(value['bytes'], value['sha256']) for observations in samples.values() for value in observations}
                if len(outputs) != 1:
                    raise RuntimeError(f'{mode} CSV outputs differ, cached={cached}')
                if mode == 'calls':
                    call_outputs.update(outputs)
                    if len(call_outputs) != 1:
                        raise RuntimeError('Cached and uncached call CSV outputs differ')
                size, sha256 = outputs.pop()
                summary = {name: {key: round(median(value[key] for value in observations), 1)
                                  for key in ('handler', 'longestFrame', 'longestTask', 'ready')}
                           for name, observations in samples.items()}
                print(json.dumps(dict(browser=browser.version, groups=args.groups, mode=mode, cached=cached,
                                      throttle=args.throttle, bytes=size, sha256=sha256,
                                      median_ms=summary, samples=samples)), flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    main()
