#!/usr/bin/env python3
"""Compare complete child-group/call traversal without dropping recorded members."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import re
from statistics import median
import tempfile


def fixture(report, destination, groups):
    html = report.read_text(encoding='utf-8')
    match = re.search(r'(<script type="application/json" id="tlData">)(.*?)(</script>)', html, re.S)
    if not match:
        raise ValueError('Expected a single-file Timeline report: ' + str(report))
    data = json.loads(match[2])
    starts, durations, parents, names, depths = [0], [groups * 10000 + 1000], [-1], [0], [3]
    for group in range(groups):
        first = len(names)
        starts.extend([group * 10000 + offset for offset in (100, 200, 2000, 2100)])
        durations.extend([1000, 300, 4000, 500])
        parents.extend([0, first, 0, first + 2])
        names.extend([group + 2, 1, group + 2, 1])
        depths.extend([5, 6, 5, 6])
    count = len(names)
    data.update(n=count, mode='abs', strings=['parent', 'grandchild'] + [f'child_{group:06d}' for group in range(groups)],
                tasks=[dict(pid=1, comm='children-benchmark', kind=0, cpu=0)],
                errs=[], chains=[], offcpu=[], resched=[], irqoff=[], sections=[], atomic=[],
                lost=[], inline={}, sources={}, wakeups=[], overhead=None)
    data.pop('tail_calls', None)
    data.pop('uftrace', None)
    data['cols'] = dict(task=[0] * count, depth=depths,
                        start=[value - (starts[index - 1] if index else 0) for index, value in enumerate(starts)],
                        dur=durations, name=names, parent=parents, line=list(range(1, count + 1)),
                        flags=[0] * count, ret=None, caller=None, args=None, module=None, cpu=None, adj=None)
    data['stats'].update(tasks=1, slices=count, offcpu=0)
    destination.write_text(html[:match.start(2)] + json.dumps(data, separators=(',', ':')) + html[match.end(2):], encoding='utf-8')


def measure_preview(browser, report, index):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri(), timeout=60000)
        page.context.new_cdp_session(page).send('Emulation.setCPUThrottlingRate', {'rate': 4})
        result = page.evaluate('''async index => {
            await fgSwitchView('timeline');
            const model = fgView('timeline').probe(), frame = () => new Promise(requestAnimationFrame);
            model.select(2);
            await frame(); await frame();
            const began = performance.now();
            model.select(index);
            const handler = performance.now() - began;
            const button = document.querySelector('[data-child-open]');
            if (!button || model.sel() !== index) throw Error('Missing selected child summary');
            const pending = button.getAttribute('aria-disabled') === 'true';
            const ready = pending ? new Promise((resolve, reject) => {
                const observer = new MutationObserver(() => {
                    if (button.getAttribute('aria-disabled') === 'true') return;
                    observer.disconnect(); clearTimeout(timeout); resolve(performance.now());
                });
                const timeout = setTimeout(() => { observer.disconnect(); reject(Error('No child summary')); }, 15000);
                observer.observe(button, {attributes: true, attributeFilter: ['aria-disabled']});
            }) : Promise.resolve(performance.now());
            await frame(); await frame();
            const twoFrames = performance.now() - began, readyMs = (await ready) - began;
            const title = document.querySelector('.tl-child-summary .tl-sec').textContent;
            const preview = Array.from(document.querySelectorAll('.tl-child-preview'), row => [row.dataset.slice, row.textContent]);
            const explorerStart = performance.now();
            button.click();
            const explore = performance.now() - explorerStart;
            const table = document.querySelector('.an-child-table');
            const explorerPending = table.getAttribute('aria-busy') === 'true';
            const explorerReadyMs = explorerPending ? await new Promise((resolve, reject) => {
                const observer = new MutationObserver(() => {
                    if (table.getAttribute('aria-busy') === 'true') return;
                    observer.disconnect(); clearTimeout(timeout); resolve(performance.now() - explorerStart);
                });
                const timeout = setTimeout(() => { observer.disconnect(); reject(Error('No child list')); }, 15000);
                observer.observe(table, {attributes: true, attributeFilter: ['aria-busy']});
            }) : explore;
            const groups = Array.from(table.tBodies[0].rows, row => row.textContent);
            if (!preview.length || !groups.length) throw Error('No children');
            return {handler, twoFrames, readyMs, explore, explorerReadyMs, pending, explorerPending, title, preview, groups};
        }''', index)
        if errors:
            raise RuntimeError('\n'.join(errors))
        payload = [result.pop('preview'), result.pop('groups')]
        result['visible_sha256'] = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
        return result
    finally:
        page.close()


def measure(browser, report, groups):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    page.set_default_timeout(120000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri())
        page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().select(0); }")
        page.wait_for_function("document.querySelector('[data-child-open]:not([aria-disabled=true])')")
        result = page.evaluate('''async (groups) => {
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const began = performance.now();
            document.querySelector('[data-child-open]').click();
            const result = {open_handler_ms: performance.now() - began, modes: {}};
            const pane = document.querySelector('.tl-detail');
            const waitForOrder = async () => {
                while (pane.querySelector('.an-child-table[aria-busy=true]')) await new Promise(requestAnimationFrame);
            };
            await waitForOrder();
            for (const mode of ['groups', 'calls']) {
                pane.querySelector('[data-child-mode=' + mode + ']').click();
                await waitForOrder();
                const visited = new Set(), handlers = [], frames = [];
                const observe = () => {
                    const rows = Array.from(pane.querySelectorAll('.an-child-table tbody tr'));
                    for (const row of rows) {
                        if (mode === 'groups') {
                            const name = row.querySelector('td').textContent;
                            const index = Number(name.slice('child_'.length));
                            if (!name.startsWith('child_') || !Number.isInteger(index) || index < 0 || index >= groups) throw new Error('Invalid child group');
                            visited.add(name);
                        } else {
                            const index = Number(row.dataset.childCall);
                            if (!Number.isInteger(index) || index < 1 || index > groups * 4 || ![1, 3].includes(index % 4)) throw new Error('Grandchild or invalid call included');
                            visited.add(index);
                        }
                    }
                    return rows.length;
                };
                const metrics = {initial_rows: observe()};
                metrics.maximum_rows = metrics.initial_rows;
                const next = pane.querySelector('[data-child-page="1"], [data-child-more]');
                while (next && !next.disabled) {
                    const started = performance.now();
                    next.click();
                    handlers.push(performance.now() - started);
                    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    frames.push(performance.now() - started);
                    metrics.maximum_rows = Math.max(metrics.maximum_rows, observe());
                }
                metrics.final_rows = observe();
                const expected = mode === 'groups' ? groups : groups * 2;
                if (visited.size !== expected) throw new Error('Incomplete child traversal: ' + visited.size + '/' + expected);
                Object.assign(metrics, {visited: visited.size, page_actions: handlers.length,
                    page_handler_ms: handlers, page_frame_ms: frames,
                    all_page_handlers_ms: handlers.reduce((total, value) => total + value, 0),
                    all_page_frames_ms: frames.reduce((total, value) => total + value, 0),
                    panel_elements: pane.querySelectorAll('*').length,
                    status: pane.querySelector('.an-child-footer [role=status]').textContent});
                result.modes[mode] = metrics;
            }
            return result;
        }''', groups)
        unchanged = page.locator('.an-child-footer [role=status]').inner_text()
        result['csv'] = {}
        for mode, expected in (('groups', groups), ('calls', groups * 2)):
            with page.expect_download() as download:
                page.locator('[data-child-csv=' + mode + ']').click()
            content = Path(download.value.path()).read_bytes()
            records = list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
            if len(records) != expected:
                raise RuntimeError('Incomplete ' + mode + ' CSV')
            result['csv'][mode] = dict(rows=len(records), bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
        if page.locator('.an-child-footer [role=status]').inner_text() != unchanged:
            raise RuntimeError('CSV changed child paging state')
        if errors:
            raise RuntimeError('\n'.join(errors))
        return result
    finally:
        page.close()


def measure_find(browser, report, groups):
    page = browser.new_page(viewport={'width': 1500, 'height': 900})
    page.set_default_timeout(120000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(report.as_uri())
        page.evaluate("async () => { await fgSwitchView('timeline'); fgView('timeline').probe().select(0); }")
        page.wait_for_function("document.querySelector('[data-child-open]:not([aria-disabled=true])')")
        result = page.evaluate(r'''async (groups) => {
            document.querySelector('[data-child-open]').click();
            const panel = document.querySelector('.tl-detail');
            const field = panel.querySelector('[data-child-query]');
            const queries = ['child_0', 'child_00', 'child_01', 'child_000', 'missing', ''];
            const names = Array.from({length: groups}, (_, index) => 'child_' + String(index).padStart(6, '0'));
            const counts = queries.map(query => names.filter(name => name.includes(query)).length);
            const nextFrame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            const waitForOrder = async () => {
                do { await nextFrame(); } while (panel.querySelector('.an-child-table[aria-busy=true]'));
            };
            const result = {};
            for (const mode of ['groups', 'calls']) {
                panel.querySelector('[data-child-mode=' + mode + ']').click();
                await waitForOrder();
                const dense = [], sparse = [], visible = [];
                for (let round = 0; round < 3; round++) for (const [index, query] of queries.entries()) {
                    field.value = query;
                    const began = performance.now();
                    field.dispatchEvent(new Event('input', {bubbles: true}));
                    const ended = performance.now();
                    await waitForOrder();
                    const count = counts[index] * (mode === 'groups' ? 1 : 2);
                    const status = panel.querySelector('.an-child-footer [role=status]').textContent;
                    const kind = mode === 'groups' ? 'functions' : 'calls';
                    const expected = count ? `Showing 1–${Math.min(count, 100)} of ${count} ${kind}` : 'No matching ' + kind;
                    if (status !== expected) throw new Error(status + ' != ' + expected);
                    const rows = [...panel.querySelectorAll('.an-child-table tbody tr')];
                    visible.push(rows.map(row => row.dataset.childCall ?? row.cells[0].textContent).join(','));
                    if (query) (counts[index] * 4 >= groups ? dense : sparse).push(ended - began);
                }
                result[mode] = {dense, sparse, visible: visible.join('\n')};
            }
            return result;
        }''', groups)
        if errors:
            raise RuntimeError('\n'.join(errors))
        for metrics in result.values():
            metrics['visible_sha256'] = hashlib.sha256(metrics.pop('visible').encode()).hexdigest()
        return result
    finally:
        page.close()


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--groups', type=int, default=2500)
    parser.add_argument('--rounds', type=int, default=3)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--find', action='store_true', help='measure repeated Find edits instead of full traversal')
    mode.add_argument('--preview', action='store_true', help='measure selection, child preview and first explorer page')
    parser.add_argument('--real-index', type=int, help='use this real-report slice instead of synthetic data with --preview')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.groups < 1 or args.rounds < 1 or args.find and not 10000 <= args.groups <= 40000:
        parser.error('--groups and --rounds must be positive; --find requires 10000–40000 groups')
    if args.real_index is not None and not args.preview:
        parser.error('--real-index requires --preview')
    samples = {'before': [], 'after': []}
    with tempfile.TemporaryDirectory(prefix='fg-children-benchmark-') as directory:
        if args.real_index is None:
            reports = {name: Path(directory) / (name + '.html') for name in samples}
            for name in reports:
                fixture(getattr(args, name), reports[name], args.groups)
        else:
            reports = {name: getattr(args, name).resolve() for name in samples}
        index = args.real_index if args.real_index is not None else 0
        benchmark = measure_preview if args.preview else measure_find if args.find else measure
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                version = browser.version
                for round_index in range(args.rounds):
                    for name in ('before', 'after') if round_index % 2 == 0 else ('after', 'before'):
                        samples[name].append(benchmark(browser, reports[name], index if args.preview else args.groups))
            finally:
                browser.close()
    if args.preview:
        if len({(sample['title'], sample['visible_sha256']) for group in samples.values() for sample in group}) != 1:
            raise RuntimeError('Before/after child summary or visible group rows differ')
        result = dict(browser=version, groups=args.groups if args.real_index is None else None, index=index,
                      rate=4, samples=samples,
                      medians={name: {key: round(median(sample[key] for sample in records), 1)
                                      for key in ('handler', 'twoFrames', 'readyMs', 'explore', 'explorerReadyMs')} for name, records in samples.items()})
        rendered = json.dumps(result, indent=2)
        if args.output:
            args.output.write_text(rendered + '\n', encoding='utf-8')
        print(rendered)
        return
    if args.find:
        for mode in ('groups', 'calls'):
            hashes = {sample[mode]['visible_sha256'] for records in samples.values() for sample in records}
            if len(hashes) != 1:
                raise RuntimeError('Before/after ' + mode + ' Find rows differ')
        result = dict(browser=version, groups=args.groups, rounds=args.rounds,
                      scope='Input handlers with unscoped orders prepared; excludes startup, paint and CSV', samples=samples,
                      medians={name: {mode: {scope: median(median(sample[mode][scope]) for sample in records)
                                             for scope in ('dense', 'sparse')} for mode in ('groups', 'calls')}
                               for name, records in samples.items()})
        rendered = json.dumps(result, indent=2)
        if args.output:
            args.output.write_text(rendered + '\n', encoding='utf-8')
        print(rendered)
        return
    metrics = ('initial_rows', 'maximum_rows', 'final_rows', 'visited', 'page_actions',
               'all_page_handlers_ms', 'all_page_frames_ms', 'panel_elements')
    for mode in ('groups', 'calls'):
        hashes = {sample['csv'][mode]['sha256'] for records in samples.values() for sample in records}
        if len(hashes) != 1:
            raise RuntimeError('Before/after ' + mode + ' CSV bytes differ')
    result = dict(browser=version, viewport=dict(width=1500, height=900), groups=args.groups,
                  direct_calls=args.groups * 2, grandchildren=args.groups * 2, rounds=args.rounds,
                  fixture='one parent; two direct calls and two excluded grandchildren per function',
                  scope='Handlers, two-rAF intervals and retained rows; excludes startup, CSV and peak memory',
                  samples=samples,
                  medians={name: {mode: {metric: median(sample['modes'][mode][metric] for sample in records)
                                         for metric in metrics} for mode in ('groups', 'calls')}
                           for name, records in samples.items()})
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)


if __name__ == '__main__':
    main()
