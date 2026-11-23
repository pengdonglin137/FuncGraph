#!/usr/bin/env python3
"""Measure Timeline range commits with many scheduling records and few calls."""

import argparse
import json
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory

COMMIT_RANGE = '''({width = 1, track = false} = {}) => {
    const canvas = document.querySelector('.tl-canvas'), bounds = canvas.getBoundingClientRect();
    const left = Math.ceil(bounds.left) + 1, right = Math.floor(bounds.left + bounds.width * width) - 1;
    const send = (target, type, clientX) => target.dispatchEvent(new MouseEvent(type, {
        button: 0, buttons: type === 'mouseup' ? 0 : 1, shiftKey: true, altKey: !track, bubbles: true, cancelable: true,
        clientX, clientY: bounds.top + 12,
    }));
    send(canvas, 'mousedown', left);
    send(window, 'mousemove', right);
    const start = performance.now();
    send(window, 'mouseup', right);
    return {start, handler: performance.now() - start};
}'''


def measure(browser, report, count, cpu_rate):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri())
        session = page.context.new_cdp_session(page)
        session.send('Emulation.setCPUThrottlingRate', {'rate': cpu_rate})
        page.evaluate("async () => { await fgSwitchView('timeline'); }")
        result = page.evaluate('''async count => {
            const commit = ''' + COMMIT_RANGE + ''';
            const frame = () => new Promise(requestAnimationFrame), model = fgView('timeline').probe();
            const pane = document.querySelector('#tlDetail');
            const initial = model.view();
            const observe = () => pane.querySelector('.tl-range-table');
            const results = {};
            const scenarios = count ? ['wide-hidden', 'narrow-hidden', 'wide-visible'] : ['real-hidden', 'real-visible'];
            for (const scenario of scenarios) {
                const overlay = document.querySelector('[data-ov=off]');
                if (overlay.classList.contains('off') !== scenario.endsWith('-hidden')) overlay.click();
                const close = pane.querySelector('.tl-close');
                if (close) close.click();
                if (count) model.zoomTo(0, scenario.startsWith('narrow') ? 80000 : count * 1000);
                else model.zoomTo(...initial);
                await frame(); await frame();
                let ready;
                const observer = new MutationObserver(() => { if (observe()) ready ??= performance.now(); });
                observer.observe(pane, {childList: true, subtree: true});
                const {start, handler} = commit();
                if (observe()) ready = start + handler;
                const pending = pane.querySelector('.tl-range-pending')?.textContent ?? null;
                const gaps = [];
                let last = start, tail = 2;
                while ((ready == null || tail > 0) && performance.now() - start < 15000) {
                    await frame();
                    const now = performance.now();
                    gaps.push(now - last);
                    last = now;
                    if (ready != null) tail--;
                }
                observer.disconnect();
                if (ready == null) throw Error('Range panel never finished: ' + scenario);
                const heading = pane.querySelector('.tl-range-head').textContent;
                let blob;
                const create = URL.createObjectURL, click = HTMLAnchorElement.prototype.click;
                URL.createObjectURL = function(input) { blob = input; return create.call(this, input); };
                HTMLAnchorElement.prototype.click = function() { if (!this.download) click.call(this); };
                try { pane.querySelector('[data-act=range-csv]').click(); }
                finally { URL.createObjectURL = create; HTMLAnchorElement.prototype.click = click; }
                if (!blob) throw Error('No range CSV in ' + scenario);
                const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
                results[scenario] = {handler, frame: Math.max(...gaps), ready: ready-start, pending, heading,
                    sha256: Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')};
            }
            return {calls:model.N, waits:model.TRACKS.reduce((sum, track) => sum + track.off.length, 0), scenarios:results};
        }''', count)
        if errors:
            raise RuntimeError('; '.join(errors))
        return result
    finally:
        page.context.close()


def main():
    from benchmark_offcpu_viewport import fixture, sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+', type=Path)
    parser.add_argument('--intervals', nargs='+', type=int, default=[0, 200000, 750000])
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--cpu-rate', type=float, default=4)
    args = parser.parse_args()
    if args.rounds < 1 or args.cpu_rate < 1 or any(count != 0 and count < 1024 for count in args.intervals):
        parser.error('use 0 or at least 1024 intervals and positive rounds and CPU rate')
    with TemporaryDirectory(prefix='fg-range-offcpu-') as directory, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        sources = [report.resolve() for report in args.reports]
        for count in args.intervals:
            reports = sources
            if count:
                reports = [Path(directory) / f'{index}-{count}.html' for index in range(len(sources))]
                for source, target in zip(sources, reports):
                    fixture(source, target, count)
            samples = [[] for _ in reports]
            expected = None
            for iteration in range(args.rounds):
                for index in (range(len(reports)) if iteration % 2 == 0 else reversed(range(len(reports)))):
                    result = measure(browser, reports[index], count, args.cpu_rate)
                    identity = {name: (value['heading'], value['sha256']) for name, value in result['scenarios'].items()}
                    if expected is None:
                        expected = identity
                    elif identity != expected:
                        raise RuntimeError(f'Panel or CSV differs for {sources[index]} ({count})')
                    samples[index].append(result)
            for report, observations in zip(args.reports, samples):
                medians = {name: {key: round(median(item['scenarios'][name][key] for item in observations), 1)
                                  for key in ('handler', 'frame', 'ready')} for name in expected}
                print(json.dumps(dict(browser=browser.version, report=str(report), cpu_rate=args.cpu_rate,
                                      calls=observations[0]['calls'], intervals=observations[0]['waits'],
                                      median_ms=medians, samples=observations)), flush=True)
        browser.close()


if __name__ == '__main__':
    main()
