"""Compare append-only browsing with bounded pages using identical report data."""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics

from playwright.sync_api import sync_playwright


def measure(browser, template, rows):
    context = browser.new_context(viewport=dict(width=1280, height=720), accept_downloads=True)
    try:
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(template.resolve().as_uri())
        initial = page.evaluate('''async rows => {
            const data = await fgData.load('cmpData');
            data.rows = rows;
            data.rows_adj = null;
            window.compareOriginal = JSON.stringify(data);
            const started = performance.now();
            await fgSwitchView('compare');
            const handler = performance.now() - started;
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            return {handler_ms:handler,two_raf_ms:performance.now()-started};
        }''', rows)
        observations, seen, ordered = [], set(), []
        while True:
            snapshot = page.evaluate('''() => ({names:Array.from(document.querySelectorAll('.cmp-table tbody td.l[title]'),node=>node.title),
                elements:document.querySelector('.cmp-wrap').querySelectorAll('*').length})''')
            for name in snapshot.pop('names'):
                if name not in seen:
                    seen.add(name)
                    ordered.append(name)
            snapshot['visited'] = len(seen)
            snapshot['attached_rows'] = page.locator('.cmp-table tbody td.l[title]').count()
            observations.append(snapshot)
            button = page.locator('.cmp-next')
            if button.count():
                if button.is_disabled():
                    break
            else:
                button = page.locator('.cmp-table .more')
                if not button.count():
                    break
            duration = button.evaluate('''async button => {
                const started = performance.now();
                button.click();
                const handler = performance.now() - started;
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                return {handler_ms:handler,two_raf_ms:performance.now()-started};
            }''')
            snapshot.update(duration)
        assert ordered == [row[0] for row in reversed(rows)]
        before_export = page.locator('.cmp-table tbody td.l[title]').evaluate_all('nodes=>nodes.map(node=>node.title)')
        with page.expect_download() as download:
            page.locator('.cmp-export').click()
        csv_bytes = Path(download.value.path()).read_bytes()
        csv_rows = list(csv.DictReader(csv_bytes.decode('utf-8-sig').splitlines()))
        assert [row['function'] for row in csv_rows] == ordered
        assert [int(row['current_self_ns']) for row in csv_rows] == [row[8] for row in reversed(rows)]
        assert page.locator('.cmp-table tbody td.l[title]').evaluate_all('nodes=>nodes.map(node=>node.title)') == before_export
        assert page.evaluate("compareOriginal === JSON.stringify(fgData.read('cmpData'))")
        assert not errors, errors
        return {'initial': initial, 'observations': observations, 'unique_rows_visited': len(seen),
                'peak_attached_rows': max(item['attached_rows'] for item in observations),
                'peak_table_elements': max(item['elements'] for item in observations),
                'navigation_actions': len(observations) - 1,
                'handler_sum_ms': sum(item.get('handler_ms', 0) for item in observations),
                'two_raf_sum_ms': sum(item.get('two_raf_ms', 0) for item in observations),
                'data_sha256': hashlib.sha256(page.evaluate('compareOriginal').encode()).hexdigest(),
                'csv_sha256': hashlib.sha256(csv_bytes).hexdigest(), 'csv_rows': len(csv_rows),
                'export_keeps_page': True}
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--rows', type=int, default=10000)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.rows < 1 or args.rounds < 1:
        parser.error('--rows and --rounds must be positive')
    rows = [[f'worker_{index:05d}', 2, 100, 50, 70, 0, 2, 200 + index, 100 + index, 120 + index, 0]
            for index in range(args.rows)]
    results = []
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        version = browser.version
        try:
            for iteration in range(args.rounds):
                order = ('before', 'after') if iteration % 2 == 0 else ('after', 'before')
                for name in order:
                    result = measure(browser, getattr(args, name), rows)
                    result.update(template=name, round=iteration + 1)
                    results.append(result)
        finally:
            browser.close()
    assert len({result['data_sha256'] for result in results}) == 1
    assert len({result['csv_sha256'] for result in results}) == 1
    medians = {}
    for name in ('before', 'after'):
        population = [result for result in results if result['template'] == name]
        medians[name] = {key: statistics.median(result[key] for result in population)
                         for key in ('peak_attached_rows', 'peak_table_elements', 'navigation_actions',
                                     'handler_sum_ms', 'two_raf_sum_ms')}
    output = {'browser': version, 'viewport': [1280, 720], 'rows': args.rows, 'rounds': args.rounds,
              'templates': {name: {'path': str(getattr(args, name).resolve()),
                                   'sha256': hashlib.sha256(getattr(args, name).read_bytes()).hexdigest()}
                            for name in ('before', 'after')},
              'medians': medians, 'results': results,
              'limits': ['Synthetic distinct functions, not a new capture or measured application lifetime.',
                         'Append and page actions expose different numbers of new rows; action totals are not equal workloads.',
                         'Two-rAF timing includes frame scheduling, not complete presentation or human reading time.',
                         'DOM counts are not heap, startup or overall large-report memory bounds.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps({'browser': version, 'rows': args.rows, 'medians': medians}, indent=2))


if __name__ == '__main__':
    main()
