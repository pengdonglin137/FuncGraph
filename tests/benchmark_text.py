#!/usr/bin/env python3
"""Measure Text folding feedback with real pointer input and fresh Chromium contexts."""
import argparse
import json
from pathlib import Path
import time

from playwright.sync_api import sync_playwright

from benchmark_startup import report_info


def settle(page):
    page.evaluate('async () => { await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame); }')


def renderer_memory(browser):
    session = browser.new_browser_cdp_session()
    try:
        processes = session.send('SystemInfo.getProcessInfo')['processInfo']
        records = []
        for process in processes:
            if process['type'] != 'renderer':
                continue
            status = Path('/proc') / str(process['id']) / 'status'
            if not status.exists():
                continue
            for line in status.read_text().splitlines():
                if line.startswith('VmRSS:'):
                    records.append(dict(pid=process['id'], rss_bytes=int(line.split()[1]) * 1024))
        return records
    finally:
        session.detach()


def measure(browser, report, samples, timeout):
    context = browser.new_context(viewport={'width': 1500, 'height': 900})
    page = context.new_page()
    page.set_default_timeout(timeout)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script("localStorage.clear(); localStorage.setItem('theme', 'light');")
    try:
        start = time.monotonic()
        page.goto(Path(report['path']).as_uri(), wait_until='load', timeout=timeout)
        settle(page)
        load_ms = (time.monotonic() - start) * 1000
        setup = page.evaluate('''() => {
            const content = document.getElementById('content');
            if (!content || !content.getBoundingClientRect().height) throw new Error('A report with visible Text rows is required');
            window.benchmarkFold = Array.from(content.querySelectorAll('.fold-icon')).find(control => {
                const bounds = control.getBoundingClientRect();
                return bounds.top > document.querySelector('.controls').getBoundingClientRect().bottom && bounds.bottom < innerHeight;
            });
            if (!benchmarkFold) throw new Error('No visible folding control; generate a report with --enable-fold');
            const bounds = benchmarkFold.getBoundingClientRect();
            return {point:[bounds.left + bounds.width / 2, bounds.top + bounds.height / 2],
                row:benchmarkFold.closest('.line-container').id, rows:content.querySelectorAll('.line-container').length,
                elements:document.getElementsByTagName('*').length, chunks:content.querySelectorAll('.text-chunk').length,
                containment:getComputedStyle(benchmarkFold.closest('.text-chunk') || benchmarkFold.closest('.line-container')).contain,
                compositingHint:getComputedStyle(benchmarkFold.closest('.text-chunk') || benchmarkFold.closest('.line-container')).willChange};
        }''')
        pointer = []
        for sample in range(samples):
            start = time.monotonic()
            page.mouse.move(*setup['point'])
            settle(page)
            enter_ms = (time.monotonic() - start) * 1000
            start = time.monotonic()
            page.mouse.move(0, 0)
            settle(page)
            leave_ms = (time.monotonic() - start) * 1000
            pointer.append(dict(enter_ms=enter_ms, leave_ms=leave_ms))
        focus = []
        for sample in range(samples):
            focus.append(page.evaluate('''async () => {
                const start = performance.now();
                benchmarkFold.focus({preventScroll:true});
                await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
                const enter_ms = performance.now() - start;
                const leave = performance.now();
                benchmarkFold.blur();
                await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
                return {enter_ms, leave_ms:performance.now() - leave};
            }'''))
        final = page.evaluate('''() => ({rows:document.querySelectorAll('#content .line-container').length,
            elements:document.getElementsByTagName('*').length, scopes:document.querySelectorAll('.fold-scope').length,
            layers:document.querySelectorAll('.fold-guide-layer').length})''')
        if errors or final['rows'] != setup['rows'] or final['scopes']:
            raise RuntimeError(json.dumps(dict(errors=errors, setup=setup, final=final)))
        return dict(load_ms=load_ms, setup=setup, pointer=pointer, focus=focus, final=final,
                    renderer_processes=renderer_memory(browser))
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+', type=Path)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--samples', type=int, default=5, help='includes the first, cold pointer interaction')
    parser.add_argument('--timeout', type=int, default=180000, help='per-navigation timeout in milliseconds')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.rounds, args.samples, args.timeout) < 1:
        parser.error('--rounds, --samples and --timeout must be positive')
    reports = [report_info(path.resolve()) for path in args.reports]
    output = dict(reports=reports, runs=[], timing_basis='action to two requestAnimationFrames; not physical presentation time')
    with sync_playwright() as playwright:
        for iteration in range(args.rounds):
            for report in reports if iteration % 2 == 0 else reversed(reports):
                browser = playwright.chromium.launch(ignore_default_args=['--hide-scrollbars'])
                try:
                    result = measure(browser, report, args.samples, args.timeout)
                    result.update(report=report['path'], iteration=iteration + 1, browser=browser.version)
                    output['runs'].append(result)
                    args.output.write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')
                    print(f"{report['path']}: load {result['load_ms']:.0f} ms; "
                          f"pointer enter {[round(sample['enter_ms'], 1) for sample in result['pointer']]}", flush=True)
                finally:
                    browser.close()


if __name__ == '__main__':
    main()
