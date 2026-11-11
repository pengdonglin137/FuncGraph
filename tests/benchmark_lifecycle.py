#!/usr/bin/env python3
"""Compare lifecycle panel and marker costs using identical synthetic event streams."""
import argparse
import json
from pathlib import Path
import re
from statistics import median
import tempfile

from playwright.sync_api import sync_playwright


def fixture(report, destination, count):
    html = report.read_text(encoding='utf-8')
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
    if not match:
        raise ValueError('Expected a single-file Timeline report: ' + str(report))
    data = json.loads(match[2])
    end = count * 1000
    data.update(n=1, mode='abs', strings=['work'], tasks=[dict(pid=42, comm='worker', kind=0, cpu=0)],
                errs=[], chains=[], offcpu=[], resched=[], irqoff=[], sections=[], atomic=[], inline={},
                sources={}, wakeups=[], overhead=None)
    data.pop('tail_calls', None)
    data['cols'] = dict(task=[0], depth=[0], start=[0], dur=[end], name=[0], parent=[-1], line=[1], flags=[0])
    data['uftrace'] = dict(spaces=['kernel'], clock='mono', aligned=True, origin_ns='18446744000000000000',
                           lifecycle=[[0, index * 1000, 'exec' if index % 2 == 0 else 'exit', 1]
                                      for index in range(count)])
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
            const probe = fgView('timeline').probe(), raf = requestAnimationFrame;
            const settle = () => new Promise(resolve => raf(() => raf(resolve)));
            const result = {narrow:[], overview:[]};
            await settle();
            for (const mode of ['narrow', 'overview']) {
                for (let index = 0; index < frames + 3; index++) {
                    const start = mode === 'narrow' ? Math.floor(count / 2) * 1000 + index * 1000 : 0;
                    const span = mode === 'narrow' ? 3500 : count * 1000;
                    const elapsed = await new Promise((resolve, reject) => {
                        let scheduled = false;
                        window.requestAnimationFrame = callback => {
                            scheduled = true;
                            return raf(time => {
                                const began = performance.now(); callback(time); resolve(performance.now() - began);
                            });
                        };
                        try { probe.zoomTo(start, start + span, -1); }
                        finally { window.requestAnimationFrame = raf; }
                        if (!scheduled) reject(new Error('Zoom did not schedule a redraw'));
                    });
                    if (index >= 3) result[mode].push(elapsed);
                }
            }
            const began = performance.now();
            document.querySelector('[data-act=process-events]').click();
            result.panel_ms = performance.now() - began;
            result.panel_rows = document.querySelectorAll('.tl-process-table tbody tr, .tl-table tbody tr').length;
            result.panel_nodes = document.querySelector('.tl-detail').querySelectorAll('*').length;
            return result;
        }''', dict(count=count, frames=frames))
        if errors:
            raise RuntimeError('; '.join(errors))
        return results
    finally:
        page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', type=Path, nargs='+')
    parser.add_argument('--events', type=int, default=100000)
    parser.add_argument('--frames', type=int, default=20)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.events < 1000 or args.frames < 1 or args.rounds < 1:
        parser.error('require at least 1000 events and positive frames / rounds')
    with tempfile.TemporaryDirectory(prefix='fg-lifecycle-benchmark-') as directory, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            output = dict(browser=browser.version, events=args.events, runs=[])
            reports = [(source.resolve(), Path(directory) / (str(index) + '.html')) for index, source in enumerate(args.reports)]
            for source, destination in reports:
                fixture(source, destination, args.events)
            for iteration in range(args.rounds):
                for source, destination in reports if iteration % 2 == 0 else reversed(reports):
                    samples = measure(browser, destination, args.events, args.frames)
                    output['runs'].append(dict(report=str(source), iteration=iteration, **samples))
                    print(source.name, 'round', iteration + 1, 'panel', round(samples['panel_ms'], 2), 'ms;',
                          samples['panel_rows'], 'rows;', 'narrow', round(median(samples['narrow']), 2), 'ms;',
                          'overview', round(median(samples['overview']), 2), 'ms', flush=True)
            args.output.write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')
        finally:
            browser.close()


if __name__ == '__main__':
    main()
