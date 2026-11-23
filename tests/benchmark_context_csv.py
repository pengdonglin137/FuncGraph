#!/usr/bin/env python3
"""Compare context CSV latency and output bytes on a synthetic wide call context."""
import argparse
import json
from pathlib import Path
import re
from statistics import median
from tempfile import TemporaryDirectory


def fixture(report, destination, count):
    html = report.read_text(encoding='utf-8')
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
    if not match:
        raise ValueError(f'Expected an offline Timeline report: {report}')
    data = json.loads(match[2])
    data.update(n=count + 1, mode='abs', strings=['root', '=SUM(1,2)', 'child,"quoted"', '接收', 'child'],
                tasks=[dict(pid=1, comm='fixture', kind=0, cpu=0)], errs=[], chains=[], offcpu=[],
                resched=[], wakeups=[], irqoff=[], sections=[], atomic=[], lost=[], inline={}, sources={}, overhead=None)
    data.pop('uftrace', None)
    data.pop('tail_calls', None)
    data['stats'].update(tasks=1, slices=count + 1, offcpu=0)
    data['cols'] = dict(task=[0] * (count + 1), depth=[0] + [2] * count, start=[0] + [1000] * count,
                        dur=[(count + 2) * 1000] + [500] * count, name=[0, 1, 2, 3] + [4] * (count - 3),
                        parent=[-1] + [0] * count, line=list(range(1, count + 2)), flags=[0] * (count + 1),
                        ret=None, caller=None, args=None, module=None, cpu=None, adj=None)
    destination.write_text(html[:match.start(2)] + json.dumps(data, separators=(',', ':')) + html[match.end(2):], encoding='utf-8')


def measure(browser, report, count, throttle):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri(), timeout=60000)
        cdp = page.context.new_cdp_session(page)
        cdp.send('Emulation.setCPUThrottlingRate', {'rate': throttle})
        result = page.evaluate('''async count => {
            await fgSwitchView('timeline');
            const model = fgView('timeline').probe();
            if (model.N !== count + 1 || model.SYN[0]?.kids.length !== count) throw Error('Wrong Timeline fixture');
            model.select(model.N);
            const button = document.querySelector('[data-context-export=children]');
            const frame = () => new Promise(requestAnimationFrame);
            await frame(); await frame();
            let blob = null, ready = null;
            const create = URL.createObjectURL, click = HTMLAnchorElement.prototype.click, tasks = [];
            const observer = new PerformanceObserver(entries => tasks.push(...entries.getEntries()));
            observer.observe({type: 'longtask'});
            URL.createObjectURL = function(input) { blob = input; return create.call(this, input); };
            HTMLAnchorElement.prototype.click = function() {
                if (this.download) ready = performance.now();
                else click.call(this);
            };
            try {
                const began = performance.now();
                button.click();
                const handler = performance.now() - began;
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
                return {handler, longestFrame, longestTask, ready: ready - began, bytes: blob.size,
                    sha256: Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')};
            } finally {
                observer.disconnect(); URL.createObjectURL = create; HTMLAnchorElement.prototype.click = click;
            }
        }''', count)
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
    parser.add_argument('--children', type=int, nargs='+', default=[20000, 150000])
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--throttle', type=float, default=4)
    args = parser.parse_args()
    if args.rounds < 1 or min(args.children) < 3 or args.throttle < 1:
        parser.error('Use positive rounds, at least three children and a CPU throttle of 1 or greater.')
    sources = {name: getattr(args, name).resolve() for name in ('before', 'after')}
    with TemporaryDirectory(prefix='fg-context-csv-') as root, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for count in args.children:
                reports = {name: Path(root) / f'{name}-{count}.html' for name in sources}
                for name, source in sources.items():
                    fixture(source, reports[name], count)
                samples = {name: [] for name in reports}
                for iteration in range(args.rounds):
                    for name in reports if iteration % 2 == 0 else reversed(reports):
                        samples[name].append(measure(browser, reports[name], count, args.throttle))
                values = [value for observations in samples.values() for value in observations]
                if len({(value['bytes'], value['sha256']) for value in values}) != 1:
                    raise RuntimeError(f'CSV outputs differ for {count} children')
                summary = {name: {key: round(median(value[key] for value in observations), 1)
                                  for key in ('handler', 'longestFrame', 'longestTask', 'ready')}
                           for name, observations in samples.items()}
                print(json.dumps(dict(browser=browser.version, children=count, throttle=args.throttle,
                                      bytes=values[0]['bytes'], sha256=values[0]['sha256'],
                                      median_ms=summary, samples=samples)), flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    main()
