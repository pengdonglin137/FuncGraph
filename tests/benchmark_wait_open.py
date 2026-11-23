#!/usr/bin/env python3
"""Measure cold wait-chain lookup on open, next page, and CSV export."""

import argparse
import json
from pathlib import Path
from random import Random
import re
from statistics import median
from tempfile import TemporaryDirectory

from benchmark_tasks import fixture as task_fixture
from benchmark_window_csv import measure as csv_measure


def fixture(source, destination, count, late):
    task_fixture(source, destination, 3)
    html = destination.read_text(encoding='utf-8')
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
    data = json.loads(match[2])
    data['caps']['sched_events'] = True
    chain = [[index % 2, count * 4 + (200 - index) * 3000, count * 4 + (200 - index) * 3000 + 1000,
              0, -1, -1, 0, 1, 0, -1] for index in range(200)]
    cutoff = 110 if late else 0
    wakes = [[next_record[0], row[2] - 100, row[0], 0, -1, index]
             for index, (row, next_record) in enumerate(zip(chain, chain[1:]))][:cutoff]
    wakes.append([2, chain[cutoff][2] - 100, chain[cutoff][0], 0, -1, cutoff])
    times = list(range(count))
    Random(42).shuffle(times)
    edge = chain[cutoff][2] - 100
    dense = [[2, edge - 2 * (count - index) - 100, edge - 2 * (count - index),
              0, -1, -1, 0, 1, 0, -1] for index in times]
    data.update(offcpu=chain + dense, wakeups=wakes)
    data['stats']['offcpu'] = len(data['offcpu'])
    destination.write_text(html[:match.start(2)] + json.dumps(data, separators=(',', ':')) + html[match.end(2):],
                           encoding='utf-8')


OPEN_CHAIN = '''() => {
    const probe = fgView('timeline').probe();
    probe.wait(probe.TRACKS.find(track => track.off.length).off[0]);
}'''


def measure(browser, report, throttle, late):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri() + '#fg-wait-open-benchmark')
        session = page.context.new_cdp_session(page)
        session.send('Emulation.setCPUThrottlingRate', {'rate': throttle})
        result = page.evaluate('''async late => {
            const frame = () => new Promise(requestAnimationFrame);
            if (fgCurrentView() !== 'text') throw Error('Benchmark requires a deferred report starting in Text');
            const boot = performance.now();
            await fgSwitchView('timeline');
            const timeline = performance.now() - boot;
            const openChain = () => {
                const probe = fgView('timeline').probe();
                probe.wait(probe.TRACKS.find(track => track.off.length).off[0]);
            };
            if (late) openChain();
            await frame(); await frame();
            const start = performance.now();
            if (late) document.querySelector('[data-act=wait-next]').click();
            else openChain();
            const handler = performance.now() - start;
            const detail = document.querySelector('.tl-detail');
            const pending = () => detail.querySelector('.an-wait-table[aria-busy=true]');
            let ready = pending() ? null : performance.now() - start;
            let maximum = 0, last = start;
            do {
                await frame();
                const now = performance.now();
                maximum = Math.max(maximum, now - last);
                last = now;
                if (ready === null && !pending()) ready = now - start;
            } while (ready === null && last - start < 30000);
            if (ready === null) throw Error('Wait page did not finish');
            return {timeline, handler, maximum, ready, ids: [...detail.querySelectorAll('tr[data-wait]')].map(row => row.dataset.wait),
                    status: detail.querySelector('.an-wait-stop').textContent};
        }''', late)
        if errors:
            raise RuntimeError('\n'.join(errors))
        return result
    finally:
        page.context.close()


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path, nargs='?')
    parser.add_argument('--rows', type=int, default=200000)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--cpu-rate', type=float, default=4)
    args = parser.parse_args()
    if args.rows < 0 or args.rounds < 1 or args.cpu_rate < 1:
        parser.error('rows must be nonnegative; rounds and CPU rate must be >= 1')
    sources = {name: getattr(args, name).resolve() for name in ('before', 'after') if getattr(args, name)}
    with TemporaryDirectory(prefix='fg-wait-open-') as directory, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for scenario in ('open', 'next', 'csv') if args.rows else ('open',):
            reports = sources.copy()
            if args.rows:
                for name, source in sources.items():
                    reports[name] = Path(directory) / f'{scenario}-{name}.html'
                    fixture(source, reports[name], args.rows, scenario != 'open')
            samples = {name: [] for name in reports}
            identity = None
            for round_index in range(args.rounds):
                for name in reports if round_index % 2 == 0 else reversed(reports):
                    if scenario == 'csv':
                        result = csv_measure(browser, reports[name], None, args.cpu_rate, 'wait-csv', setup=OPEN_CHAIN)
                        evidence = (result.pop('bytes'), result.pop('digest'))
                    else:
                        result = measure(browser, reports[name], args.cpu_rate, scenario == 'next')
                        evidence = (result.pop('ids'), result.pop('status'))
                    if identity is None:
                        identity = evidence
                    elif identity != evidence:
                        raise RuntimeError(f'{scenario} result differs across runs or versions')
                    samples[name].append(result)
            timings = {name: {key: round(median(sample[key] for sample in values), 1) for key in values[0]}
                       for name, values in samples.items()}
            print(json.dumps(dict(browser=browser.version, scenario=scenario, rows=args.rows, cpu_rate=args.cpu_rate,
                                  median_ms=timings, samples=samples)), flush=True)
        browser.close()


if __name__ == '__main__':
    main()
