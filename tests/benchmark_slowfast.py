#!/usr/bin/env python3
"""Measure responsiveness and output parity of cold Slow vs fast preparation."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from statistics import median
from tempfile import TemporaryDirectory


def fixture(source, destination, children, roots):
    html = source.read_text(encoding='utf-8')
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
    data = json.loads(match[2])
    count = children + roots
    data.update(n=count, mode='abs', strings=['target'] + [f'child_{ordinal:06d}' for ordinal in range(children)],
                tasks=[dict(pid=1, comm='cohort-benchmark', kind=0, cpu=0)], errs=[], chains=[], offcpu=[],
                resched=[], wakeups=[], irqoff=[], sections=[], atomic=[], lost=[], inline={}, sources={}, overhead=None)
    data.pop('uftrace', None)
    data.pop('tail_calls', None)
    data['stats'].update(tasks=1, slices=count, offcpu=0)
    data['cols'] = dict(task=[0] * count, depth=[0] * roots + [1] * children,
                        start=[0] + [2000] * (roots - 1) + [1000] * children,
                        dur=[1000] * (roots - 1) + [(children + 1) * 1000] + [500] * children,
                        name=[0] * roots + list(range(1, children + 1)), parent=[-1] * roots + [roots - 1] * children,
                        line=list(range(1, count + 1)), flags=[0] * count, ret=None, caller=None,
                        args=None, module=None, cpu=None, adj=None)
    destination.write_text(html[:match.start(2)] + json.dumps(data, separators=(',', ':')) + html[match.end(2):], encoding='utf-8')


def measure(browser, report, throttle, synthetic, export_kind):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri(), timeout=60000)
        page.context.new_cdp_session(page).send('Emulation.setCPUThrottlingRate', {'rate': throttle})
        function = page.evaluate('''async synthetic => {
            await fgSwitchView('timeline');
            const trace = fgView('timeline').probe();
            let selected = 0;
            if (!synthetic) {
                const sizes = new Uint32Array(trace.N), totals = new Map();
                for (let index = trace.N - 1; index >= 0; index--) {
                    const parent = trace.sParent[index], name = trace.sName[index];
                    if (parent >= 0 && parent < index) sizes[parent] += sizes[index] + 1;
                    if (!totals.has(name)) totals.set(name, {count:0, size:0});
                    const stats = totals.get(name);
                    stats.count++; stats.size += sizes[index];
                }
                let maximum = -1;
                for (const [name, stats] of totals) if (stats.count >= 6 && stats.size > maximum) {
                    maximum = stats.size; selected = name;
                }
            }
            trace.dist(selected);
            return trace.S[selected];
        }''', synthetic)
        page.locator('[data-act=slowfast]').wait_for()
        result = page.evaluate('''async () => {
            const frame = () => new Promise(requestAnimationFrame);
            await frame(); await frame();
            const began = performance.now(), toggle = document.querySelector('[data-act=slowfast]');
            toggle.click();
            const handler = performance.now() - began;
            const pending = !!document.querySelector('.an-slowfast-pending');
            const table = () => document.querySelector('.an-slowfast-table');
            let ready = table() ? performance.now() : null;
            const observer = new MutationObserver(() => { if (ready === null && table()) ready = performance.now(); });
            observer.observe(document.querySelector('.an-slowfast'), {childList: true});
            let previous = began, longestFrame = 0;
            do {
                await frame();
                const now = performance.now();
                longestFrame = Math.max(longestFrame, now - previous); previous = now;
            } while (ready === null && previous - began < 30000);
            observer.disconnect();
            if (ready === null) throw Error('Comparison did not finish');
            return {handler, longestFrame, ready: ready - began, pending,
                firstPage: table().textContent, status: document.querySelector('.an-slowfast-pager').textContent};
        }''')
        result['function'] = function
        result['first_page_sha256'] = hashlib.sha256(result.pop('firstPage').encode()).hexdigest()
        for kind in ('csv', 'cohorts-csv'):
            with page.expect_download() as downloaded:
                export = page.locator(f'[data-act=slowfast-{kind}]')
                if kind == export_kind:
                    result.update(export.evaluate('''async button => {
                        const frame = () => new Promise(requestAnimationFrame);
                        await frame(); await frame();
                        const click = HTMLAnchorElement.prototype.click;
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
                                longestFrame = Math.max(longestFrame, now - previous); previous = now;
                            } while (complete === null && previous - began < 30000);
                            if (complete === null) throw Error('CSV did not finish');
                            return {handler, longestFrame, ready: complete - began, pending};
                        } finally { HTMLAnchorElement.prototype.click = click; }
                    }'''))
                else:
                    export.click()
            result[kind + '_sha256'] = hashlib.sha256(downloaded.value.path().read_bytes()).hexdigest()
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
    parser.add_argument('--children', type=int, default=150000, help='unique descendants; zero uses the original report')
    parser.add_argument('--roots', type=int, default=6, help='number of synthetic root calls; non-default forces a fixture')
    parser.add_argument('--export', choices=('profile', 'cohorts'), help='measure this CSV instead of opening the comparison')
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--throttle', type=float, default=4)
    options = parser.parse_args()
    if options.children < 0 or options.roots < 6 or options.rounds < 1 or options.throttle < 1:
        parser.error('Use nonnegative children, at least six roots, positive rounds and a throttle of at least 1')
    synthetic = options.children > 0 or options.roots != 6
    export_kind = {'profile': 'csv', 'cohorts': 'cohorts-csv'}.get(options.export)
    with TemporaryDirectory(prefix='fg-slowfast-') as directory, sync_playwright() as playwright:
        reports = {name: getattr(options, name).resolve() for name in ('before', 'after')}
        if synthetic:
            for name, source in reports.copy().items():
                reports[name] = Path(directory) / (name + '.html')
                fixture(source, reports[name], options.children, options.roots)
        browser = playwright.chromium.launch()
        try:
            samples = {name: [] for name in reports}
            for iteration in range(options.rounds):
                for name in reports if iteration % 2 == 0 else reversed(reports):
                    samples[name].append(measure(browser, reports[name], options.throttle, synthetic, export_kind))
            keys = ('function', 'status', 'first_page_sha256', 'csv_sha256', 'cohorts-csv_sha256')
            if len({tuple(sample[key] for key in keys) for rows in samples.values() for sample in rows}) != 1:
                raise RuntimeError('Comparison displays or full CSVs differ')
            summary = {name: {key: round(median(sample[key] for sample in rows), 1)
                              for key in ('handler', 'longestFrame', 'ready')} for name, rows in samples.items()}
            print(json.dumps(dict(browser=browser.version, children=options.children, throttle=options.throttle,
                                  metric=options.export or 'open', roots=options.roots if synthetic else None,
                                  median_ms=summary, samples=samples)), flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    main()
