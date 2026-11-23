#!/usr/bin/env python3
"""Measure long wait-chain export responsiveness and compare exact CSV output."""

import argparse
import json
from pathlib import Path
import re
from statistics import median
from tempfile import TemporaryDirectory

from benchmark_tasks import fixture as task_fixture
from benchmark_window_csv import measure


def fixture(source, destination, count):
    task_fixture(source, destination, 2)
    html = destination.read_text(encoding='utf-8')
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
    data = json.loads(match[2])
    data['tasks'][0]['comm'] = '=waiter,"调度"'
    data['tasks'][1]['comm'] = '@waker'
    data['caps']['sched_events'] = True
    data['offcpu'] = [[index % 2, (count - index) * 3000, (count - index) * 3000 + 1000,
                      0, -1, -1, 0, 1, 0, -1] for index in range(count)]
    data['wakeups'] = [[(index + 1) % 2, row[2] - 100, row[0], 0, -1, index]
                       for index, row in enumerate(data['offcpu'][:-1])]
    data['stats']['offcpu'] = count
    destination.write_text(html[:match.start(2)] + json.dumps(data, separators=(',', ':')) + html[match.end(2):],
                           encoding='utf-8')


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path, nargs='?')
    parser.add_argument('--rows', type=int, default=50000, help='synthetic linked waits; 0 uses the original report')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--cpu-rate', type=float, default=4)
    args = parser.parse_args()
    if args.rows < 0 or args.rounds < 1 or args.cpu_rate < 1:
        parser.error('rows must be nonnegative; rounds and CPU rate must be >= 1')
    with TemporaryDirectory(prefix='fg-wait-csv-') as directory, sync_playwright() as playwright:
        sources = {'before': args.before.resolve()}
        if args.after:
            sources['after'] = args.after.resolve()
        reports = sources.copy()
        if args.rows:
            for name, source in sources.items():
                reports[name] = Path(directory) / f'{name}.html'
                fixture(source, reports[name], args.rows)
        browser = playwright.chromium.launch()
        samples = {name: [] for name in reports}
        for round_index in range(args.rounds):
            for name in reports if round_index % 2 == 0 else reversed(reports):
                samples[name].append(measure(browser, reports[name], None, args.cpu_rate, 'wait-csv', setup='''() => {
                    const probe = fgView('timeline').probe();
                    probe.wait(probe.TRACKS.find(track => track.off.length).off[0]);
                }'''))
        records = [sample for values in samples.values() for sample in values]
        if any(sample is None for sample in records) or len({(sample['bytes'], sample['digest']) for sample in records}) != 1:
            raise RuntimeError('Wait-chain CSV differs or is missing')
        timings = {name: {key: round(median(value[key] for value in values), 1)
                          for key in ('handler', 'ready', 'first', 'maximum')} for name, values in samples.items()}
        print(json.dumps(dict(browser=browser.version, rows=args.rows, cpu_rate=args.cpu_rate,
                              median_ms=timings, samples=samples)), flush=True)
        browser.close()


if __name__ == '__main__':
    main()
