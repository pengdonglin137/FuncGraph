"""Compare hover-preview bounds and geometry reads in saved HTML reports.

Uses a synthetic long call, not the report's recorded execution. Counts tooltip
getBoundingClientRect calls during settled same-slice moves, not overall FPS,
startup cost or every browser layout operation.
"""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


VIEWPORTS = ((1500, 900), (390, 400), (844, 390), (320, 480))


def settle(page):
    page.evaluate('async () => { for (let frame = 0; frame < 3; frame++) await new Promise(requestAnimationFrame); }')


def measure(browser, report, width, height, moves, screenshot):
    page = browser.new_page(viewport=dict(width=width, height=height))
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.add_init_script('localStorage.clear()')
        page.goto(report.as_uri(), wait_until='load')
        page.evaluate('''() => {
            const data = JSON.parse(document.getElementById('tlData').textContent);
            data.n = 1;
            data.strings = ['long_wrapper_' + 'nested_namespace_'.repeat(30) + 'operation',
                'outer_' + 'caller_namespace_'.repeat(30) + 'invoke+0x12/0x40',
                'path="' + 'segment/'.repeat(200) + 'payload", count=17'];
            data.tasks = [{pid:42, comm:'demo', kind:0, cpu:0}];
            for (const field of ['errs', 'chains', 'sections', 'atomic', 'offcpu', 'resched', 'wakeups', 'irqoff', 'lost'])
                data[field] = [];
            data.inline = {};
            data.sources = {};
            data.overhead = null;
            delete data.tail_calls;
            delete data.uftrace;
            data.cols = {task:[0], depth:[0], start:[0], dur:[1000000], name:[0],
                parent:[-1], line:[1], flags:[(1<<15)|4|8|(7<<4)|(1<<7)|(2<<11)],
                ret:null, caller:[1], args:[2], module:null, cpu:null};
            window.tooltipBenchmarkData = JSON.stringify(data);
            document.getElementById('tlData').textContent = tooltipBenchmarkData;
        }''')
        page.evaluate('async () => { await fgSwitchView("timeline"); await document.fonts.ready; }')
        point = page.evaluate('fgView("timeline").probe().xyOf(0)')
        page.mouse.move(*point)
        page.locator('.tl-tip').wait_for(state='visible')
        settle(page)
        page.evaluate('''() => {
            const tooltip = document.querySelector('.tl-tip');
            const original = tooltip.getBoundingClientRect.bind(tooltip);
            window.tooltipBenchmarkReads = 0;
            tooltip.getBoundingClientRect = () => { tooltipBenchmarkReads++; return original(); };
        }''')
        for step in range(moves):
            page.mouse.move(point[0] + step % 3 + 1, point[1])
            settle(page)
        result = page.evaluate('''() => {
            const tooltip = document.querySelector('.tl-tip');
            const reads = tooltipBenchmarkReads, bounds = tooltip.getBoundingClientRect();
            return {geometry_reads:reads, tooltip:{left:bounds.left, top:bounds.top,
                width:bounds.width, height:bounds.height, right:bounds.right, bottom:bounds.bottom},
                fits:bounds.left >= 4 && bounds.top >= 4 && bounds.right <= innerWidth - 4 && bounds.bottom <= innerHeight - 4,
                full_details_hint:!!tooltip.querySelector('.tl-tip-more:not([hidden])')};
        }''')
        if screenshot:
            page.screenshot(path=str(screenshot))
        page.mouse.click(*point)
        intact = page.evaluate('''() => {
            const data = fgData.read('tlData');
            return JSON.stringify(data) === tooltipBenchmarkData &&
                document.querySelector('.tl-detail-title').textContent === data.strings[0] &&
                document.querySelector('.tl-call-facts').textContent.includes(data.strings[2]);
        }''')
        if not intact or errors:
            raise RuntimeError(f'Incomplete details, changed data or JavaScript errors: {errors}')
        return dict(report=str(report), viewport=dict(width=width, height=height), moves=moves,
                    details_and_data_intact=intact, **result)
    finally:
        page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+', type=Path)
    parser.add_argument('--moves', type=int, default=12)
    parser.add_argument('--screenshots', type=Path)
    args = parser.parse_args()
    if args.moves < 1:
        parser.error('--moves must be positive')
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        results = []
        for ordinal, report in enumerate(args.reports):
            for width, height in VIEWPORTS:
                screenshot = args.screenshots / f'{ordinal}-{report.stem}-{width}x{height}.png' if args.screenshots else None
                results.append(measure(browser, report.resolve(strict=True), width, height, args.moves, screenshot))
        browser.close()
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
