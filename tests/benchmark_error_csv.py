#!/usr/bin/env python3
"""Measure Errors CSV responsiveness and compare output bytes across runtimes."""

import argparse
import json
from pathlib import Path
import re
from statistics import median
from tempfile import TemporaryDirectory

from benchmark_errors import fixture as error_fixture
from benchmark_window_csv import measure


ACTIONS = ('errors-csv', 'error-occurrences-csv', 'atomic-csv', 'user-errors-csv')


def fixture(source, destination, count):
    error_fixture(source, destination, count * 2, count)
    html = destination.read_text(encoding='utf-8')
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
    data = json.loads(match[2])
    data['chains'] = data['chains'][:count]
    details = {}
    for index in range(count, count * 2):
        data['cols']['flags'][index] = 1 << 25
        symbol = (index - count) % 4
        data['cols']['name'][index] = count + symbol
        data['strings'][count + symbol] = f'open, "用户" {symbol}'
        details[index + 1] = {'retval': '-1', 'failure': {'rule': 'minus_one', 'summary': 'Open failed',
                                                          'note': 'errno not captured'}}
    data['uftrace'] = {'spaces': ['kernel', 'user'], 'user_details': details}
    encoded = json.dumps(data, separators=(',', ':')).replace('<', r'\u003c')
    destination.write_text(html[:match.start(2)] + encoded + html[match.end(2):], encoding='utf-8')


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path, nargs='?')
    parser.add_argument('--rows', type=int, default=50000, help='rows per export; 0 uses the real report')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--cpu-rate', type=float, default=4)
    args = parser.parse_args()
    if args.rows < 0 or args.rounds < 1 or args.cpu_rate < 1:
        parser.error('rows must be nonnegative; rounds and CPU rate must be >= 1')

    with TemporaryDirectory(prefix='fg-error-csv-') as directory, sync_playwright() as playwright:
        reports = {'before': args.before.resolve()}
        if args.after:
            reports['after'] = args.after.resolve()
        if args.rows:
            for name, source in reports.items():
                reports[name] = Path(directory) / f'{name}.html'
                fixture(source, reports[name], args.rows)
        browser = playwright.chromium.launch()
        for action in ACTIONS:
            samples = {name: [] for name in reports}
            for round_index in range(args.rounds):
                for name in reports if round_index % 2 == 0 else reversed(reports):
                    samples[name].append(measure(browser, reports[name], 'errors', args.cpu_rate, action))
            records = [sample for values in samples.values() for sample in values]
            if not args.rows and all(sample is None for sample in records):
                continue
            if any(sample is None for sample in records) or len({(sample['bytes'], sample['digest']) for sample in records}) != 1:
                raise RuntimeError(f'{action} output differs or is missing')
            timings = {name: {key: round(median(value[key] for value in values), 1)
                              for key in ('handler', 'ready', 'first', 'maximum')} for name, values in samples.items()}
            print(json.dumps(dict(browser=browser.version, action=action, rows=args.rows, cpu_rate=args.cpu_rate,
                                  median_ms=timings, samples=samples)), flush=True)
        browser.close()


if __name__ == '__main__':
    main()
