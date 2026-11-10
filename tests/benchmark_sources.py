#!/usr/bin/env python3
"""Measure source-layout handling and two-frame feedback in fresh Chromium contexts."""
import argparse
import json
from pathlib import Path
import time

from playwright.sync_api import sync_playwright

from benchmark_startup import report_info
from benchmark_text import settle


def measure(browser, report, opened, samples, timeout):
    context = browser.new_context(viewport={'width': 1500, 'height': 900})
    page = context.new_page()
    page.set_default_timeout(timeout)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script("localStorage.clear(); localStorage.setItem('theme', 'light');")
    try:
        started = time.monotonic()
        page.goto(Path(report['path']).as_uri(), wait_until='load', timeout=timeout)
        settle(page)
        load_ms = (time.monotonic() - started) * 1000
        setup = page.evaluate('''opened => {
            const content = document.getElementById('content');
            const control = document.getElementById('srcModeBtn');
            if (!content || !control || !content.getBoundingClientRect().height) throw new Error('A report with a visible Text view is required');
            const panels = Array.from(content.querySelectorAll('.expanded-content[data-src]'));
            const targets = panels.slice(0, opened);
            if (targets.length !== opened) throw new Error('Not enough source panels for --opened');
            targets.forEach(panel => { fgFill(panel); panel.style.display = 'block'; });
            window.benchmarkSources = targets;
            return {rows:content.querySelectorAll('.line-container').length, placeholders:panels.length,
                opened:targets.length, elements:document.getElementsByTagName('*').length,
                weak_tracking:typeof fgSourceNodes !== 'undefined' && fgSourceNodes !== null};
        }''', opened)
        page.wait_for_timeout(2000)
        timings = []
        for sample in range(samples):
            timings.append(page.evaluate('''() => new Promise(resolve => {
                const started = performance.now();
                document.getElementById('srcModeBtn').click();
                const handler_ms = performance.now() - started;
                requestAnimationFrame(() => requestAnimationFrame(() => resolve({
                    handler_ms, feedback_proxy_ms:performance.now() - started, mode:fgSourceMode(),
                    populated:benchmarkSources.filter(panel => panel.firstChild).length
                })));
            })'''))
            page.wait_for_timeout(120)
        final = page.evaluate('''() => ({rows:document.querySelectorAll('#content .line-container').length,
            connected:benchmarkSources.every(panel => panel.isConnected)})''')
        if errors or final['rows'] != setup['rows'] or not final['connected']:
            raise RuntimeError(json.dumps(dict(errors=errors, setup=setup, final=final)))
        return dict(load_ms=load_ms, setup=setup, timings=timings, final=final)
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+', type=Path)
    parser.add_argument('--opened', type=int, default=3, help='source panels populated before measuring')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--samples', type=int, default=7, help='includes the first, cold layout switch')
    parser.add_argument('--timeout', type=int, default=180000, help='per-navigation timeout in milliseconds')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.opened, args.rounds, args.samples, args.timeout) < 1:
        parser.error('--opened, --rounds, --samples and --timeout must be positive')
    reports = [report_info(path.resolve()) for path in args.reports]
    output = dict(reports=reports, runs=[], timing_basis='programmatic native button click to handler return and two requestAnimationFrames; excludes physical input and presentation')
    with sync_playwright() as playwright:
        for iteration in range(args.rounds):
            for report in reports if iteration % 2 == 0 else reversed(reports):
                browser = playwright.chromium.launch(ignore_default_args=['--hide-scrollbars'])
                try:
                    result = measure(browser, report, args.opened, args.samples, args.timeout)
                    result.update(report=report['path'], iteration=iteration + 1, browser=browser.version)
                    output['runs'].append(result)
                    args.output.write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')
                    print(f"{report['path']}: handler {[round(sample['handler_ms'], 1) for sample in result['timings']]}; "
                          f"feedback {[round(sample['feedback_proxy_ms'], 1) for sample in result['timings']]}", flush=True)
                finally:
                    browser.close()


if __name__ == '__main__':
    main()
