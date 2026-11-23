"""Compare first open and navigation of Sections / Scheduling latency in real reports."""

import argparse
import hashlib
import json
from pathlib import Path
from statistics import median


def measure(browser, report, action, cpu_rate, sort):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.resolve().as_uri())
        page.evaluate("async () => { await fgSwitchView('timeline'); }")
        cdp = page.context.new_cdp_session(page)
        cdp.send('Emulation.setCPUThrottlingRate', {'rate': cpu_rate})
        result = page.evaluate('''async ({action, sort}) => {
            const pane = document.querySelector('.tl-detail');
            const frame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const measure = async activate => {
                await frame();
                const start = performance.now();
                activate();
                const handler_ms = performance.now() - start;
                await frame();
                return {handler_ms, two_frames_ms: performance.now() - start,
                        rows: pane.querySelectorAll('tr[data-sec], tr[data-win]').length,
                        status: pane.querySelector('.an-window-footer [role=status]')?.textContent};
            };
            const open = await measure(() => document.querySelector(`[data-act=${action}]`).click());
            if (!open.rows) throw new Error('Fixture must have analysis windows');
            const next = pane.querySelector('[data-act=window-next]');
            const forward = next && !next.disabled ? await measure(() => next.click()) : null;
            const go = pane.querySelector('[data-act=window-go]');
            const direct = go ? await measure(() => {
                const input = pane.querySelector('[data-act=window-page]');
                input.value = '1';
                go.click();
            }) : null;
            const result = {open, forward, direct};
            if (sort) {
                const button = pane.querySelector(action === 'an-sec' ? 'th[data-ssort=top] button' : 'th[data-lsort=end] button');
                button.scrollIntoView({block: 'nearest', inline: 'nearest'});
                const heading = pane.querySelector('.an-window-head');
                const table = button.closest('table');
                result.sort = await measure(() => button.click());
                const active = pane.querySelector(action === 'an-sec' ? 'th[data-ssort=top] button' : 'th[data-lsort=end] button');
                if (active !== document.activeElement || !['ascending', 'descending'].includes(active.parentElement.getAttribute('aria-sort')))
                    throw new Error('Sort did not activate the focused column');
                result.sort.retained_panel = heading.isConnected && table.isConnected;
                result.repeat_sort = await measure(() => active.click());
            }
            return result;
        }''', {'action': action, 'sort': sort})
        with page.expect_download() as download:
            page.locator('[data-act=window-csv]').click()
        result['csv_sha256'] = hashlib.sha256(Path(download.value.path()).read_bytes()).hexdigest()
        if errors:
            raise RuntimeError('\n'.join(errors))
        return result
    finally:
        page.context.close()


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--cpu-rate', type=float, default=4)
    parser.add_argument('--sort', action='store_true', help='also benchmark changing and reactivating the sort column')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.rounds < 1 or args.cpu_rate < 1:
        parser.error('--rounds and --cpu-rate must be at least 1')
    samples = {action: {'before': [], 'after': []} for action in ('an-sec', 'an-lat')}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for action, records in samples.items():
            for round_index in range(args.rounds):
                for name in ('before', 'after') if round_index % 2 == 0 else ('after', 'before'):
                    records[name].append(measure(browser, getattr(args, name), action, args.cpu_rate, args.sort))
            if len({sample['csv_sha256'] for versions in records.values() for sample in versions}) != 1:
                raise RuntimeError(f'{action} full CSV changed; check that the reports use identical input and options')
        version = browser.version
        browser.close()
    medians = {action: {name: {
        step: {field: median(sample[step][field] for sample in versions)
               for field in ('handler_ms', 'two_frames_ms', 'rows')}
        for step in ('open', 'forward', 'direct', 'sort', 'repeat_sort') if all(sample.get(step) for sample in versions)
    } for name, versions in records.items()} for action, records in samples.items()}
    rendered = json.dumps(dict(browser=version, cpu_rate=args.cpu_rate, viewport=[1500, 900],
                               rounds=args.rounds, medians=medians, samples=samples), indent=2)
    if args.output:
        args.output.write_text(rendered + '\n', encoding='utf-8')
    print(json.dumps(medians, indent=2))


if __name__ == '__main__':
    main()
