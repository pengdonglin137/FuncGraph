#!/usr/bin/env python3
"""Measure task CSV responsiveness and compare exact output across runtimes."""

import argparse
import json
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory

from benchmark_tasks import fixture
from benchmark_window_csv import measure


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path, nargs='?')
    parser.add_argument('--rows', type=int, default=50000, help='synthetic tasks; 0 uses the original report')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--cpu-rate', type=float, default=4)
    args = parser.parse_args()
    if args.rows < 0 or args.rounds < 1 or args.cpu_rate < 1:
        parser.error('rows must be nonnegative; rounds and CPU rate must be >= 1')
    with TemporaryDirectory(prefix='fg-task-csv-') as directory, sync_playwright() as playwright:
        reports = {'before': args.before.resolve()}
        if args.after:
            reports['after'] = args.after.resolve()
        if args.rows:
            for name, source in reports.items():
                reports[name] = Path(directory) / f'{name}.html'
                fixture(source, reports[name], args.rows)
        browser = playwright.chromium.launch()
        samples = {name: [] for name in reports}
        for round_index in range(args.rounds):
            for name in reports if round_index % 2 == 0 else reversed(reports):
                samples[name].append(measure(browser, reports[name], 'tasks', args.cpu_rate, 'tasks-csv', 'an-tasks'))
        records = [sample for values in samples.values() for sample in values]
        if any(sample is None for sample in records) or len({(sample['bytes'], sample['digest']) for sample in records}) != 1:
            raise RuntimeError('Tasks CSV differs or is missing')
        timings = {name: {key: round(median(value[key] for value in values), 1)
                          for key in ('handler', 'ready', 'first', 'maximum')} for name, values in samples.items()}
        print(json.dumps(dict(browser=browser.version, rows=args.rows, cpu_rate=args.cpu_rate,
                              median_ms=timings, samples=samples)), flush=True)
        browser.close()


if __name__ == '__main__':
    main()
