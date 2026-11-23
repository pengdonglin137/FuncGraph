"""Measure first visits and return visits to full Compare pages."""

import argparse
import hashlib
import json
from pathlib import Path
import statistics

from playwright.sync_api import sync_playwright

from test_compare_paging import comparison_rows


def metrics(session):
    return {item['name']: item['value'] for item in session.send('Performance.getMetrics')['metrics']}


def measure(browser, template, width, height):
    context = browser.new_context(viewport={'width': width, 'height': height})
    try:
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(template.resolve().as_uri())
        page.evaluate('''async rows => {
            const data = await fgData.load('cmpData');
            data.rows = rows;
            data.rows_adj = null;
            await fgSwitchView('compare');
            const tbody = document.querySelector('.cmp-table tbody');
            const html = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
            window.compareParseMs = 0;
            Object.defineProperty(tbody, 'innerHTML', {
                get() { return html.get.call(this); },
                set(markup) {
                    const started = performance.now();
                    html.set.call(this, markup);
                    window.compareParseMs += performance.now() - started;
                },
            });
        }''', comparison_rows())
        session = context.new_cdp_session(page)
        session.send('Performance.enable')
        observations = []
        for index in range(13):
            page.locator('.cmp-wrap').evaluate('node => { node.scrollTop = 120; }')
            start = metrics(session)
            button = 'next' if index % 2 == 0 else 'prev'
            observation = page.locator(f'.cmp-{button}').evaluate('''async button => {
                window.compareParseMs = 0;
                const started = performance.now();
                button.click();
                const handler = performance.now() - started;
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                return {handler_ms:handler, tbody_setter_ms:window.compareParseMs,
                    two_raf_ms:performance.now()-started};
            }''')
            end = metrics(session)
            observation['layout_ms'] = (end['LayoutDuration'] - start['LayoutDuration']) * 1000
            observation['style_ms'] = (end['RecalcStyleDuration'] - start['RecalcStyleDuration']) * 1000
            observation['visit'] = 'return' if index else 'first'
            observations.append(observation)
            assert page.locator('.cmp-page-status').inner_text() == (
                'Rows 401–800 of 1207' if button == 'next' else 'Rows 1–400 of 1207')
            assert page.locator('.cmp-wrap').evaluate('node => node.scrollTop') == 0
            assert page.locator('.cmp-table tbody tr').count() == 400
        first_two_pages = []
        for action in ('prev', 'next'):
            page.locator(f'.cmp-{action}').click()
            markup = page.locator('.cmp-table tbody').inner_html()
            first_two_pages.append(hashlib.sha256(markup.encode()).hexdigest())
        assert not errors, errors
        return {'width': width, 'height': height, 'observations': observations,
                'body_sha256': first_two_pages}
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error('--rounds must be positive')
    runs, medians = [], {}
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        version = browser.version
        try:
            for iteration in range(args.rounds):
                order = ('before', 'after') if iteration % 2 == 0 else ('after', 'before')
                for width, height in ((1280, 720), (390, 600)):
                    for label in order:
                        run = measure(browser, getattr(args, label), width, height)
                        runs.append(dict(run, template=label, round=iteration + 1))
        finally:
            browser.close()
    for width in (1280, 390):
        viewport = [run for run in runs if run['width'] == width]
        assert len({tuple(run['body_sha256']) for run in viewport}) == 1
        medians[width] = {}
        for label in ('before', 'after'):
            medians[width][label] = {}
            for visit in ('first', 'return'):
                sample = [item for run in viewport if run['template'] == label
                          for item in run['observations'] if item['visit'] == visit]
                medians[width][label][visit] = {key: round(statistics.median(row[key] for row in sample), 3)
                                                 for key in sample[0] if key != 'visit'}
    templates = {label: hashlib.sha256(getattr(args, label).read_bytes()).hexdigest()
                 for label in ('before', 'after')}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(browser=version, medians=medians, runs=runs,
                                           templates=templates), indent=2) + '\n')
    print(json.dumps(dict(browser=version, medians=medians), indent=2))


if __name__ == '__main__':
    main()
