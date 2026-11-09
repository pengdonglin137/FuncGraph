#!/usr/bin/env python3
"""Measure cold report startup / first Timeline switch in fresh Chromium contexts."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import time

from playwright.sync_api import sync_playwright


def report_info(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        head = stream.read(16384)
        digest.update(head)
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    title = re.search(r'<title>(.*?)</title>', head.decode('utf-8', errors='replace'), re.S)
    return dict(path=str(path), bytes=path.stat().st_size, sha256=digest.hexdigest(),
                title=html.unescape(title.group(1)) if title else '')


def measure(browser, report, mode, timeout):
    context = browser.new_context(viewport={'width': 1500, 'height': 900})
    page = context.new_page()
    page.set_default_timeout(timeout)
    errors, logs = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda message: logs.append(message.text))
    config = dict(title=report['title'], view='timeline' if mode == 'restore' else 'text')
    page.add_init_script('''(() => {
        const config = CONFIG;
        localStorage.clear();
        localStorage.setItem('theme', 'light');
        let hash = 0;
        for (let index = 0; index < config.title.length; index++) {
            hash = ((hash << 5) - hash + config.title.charCodeAt(index)) | 0;
        }
        localStorage.setItem('fg_' + (hash >>> 0).toString(36) + '_view', config.view);
        window.startupLongTasks = [];
        new PerformanceObserver(list => {
            for (const entry of list.getEntries()) {
                startupLongTasks.push({start:entry.startTime, duration:entry.duration});
            }
        }).observe({type:'longtask', buffered:true});
    })()'''.replace('CONFIG', json.dumps(config)))
    try:
        start = time.monotonic()
        page.goto(Path(report['path']).as_uri(), wait_until='load', timeout=timeout)
        load_ms = (time.monotonic() - start) * 1000
        if mode == 'switch':
            page.wait_for_timeout(250)
        timing = page.evaluate('''async mode => {
            const start = performance.now();
            const ready = mode === 'switch' ? fgSwitchView('timeline') : null;
            const sync = mode === 'switch' ? performance.now() - start : null;
            const staged = document.getElementById('timelineView').getAttribute('aria-busy') === 'true';
            let feedback = null;
            if (staged) {
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                feedback = performance.now() - (mode === 'switch' ? start : 0);
            }
            if (mode === 'switch') await ready;
            else if (staged) await new Promise(resolve => {
                const el = document.getElementById('timelineView');
                const observer = new MutationObserver(() => {
                    if (el.getAttribute('aria-busy') === 'true') return;
                    observer.disconnect(); resolve();
                });
                if (el.getAttribute('aria-busy') !== 'true') resolve();
                else observer.observe(el, {attributes:true, attributeFilter:['aria-busy']});
            });
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            return {start:mode === 'switch' ? start : 0, sync_ms:sync, staged, feedback_ms:feedback,
                frame_ms:performance.now() - (mode === 'switch' ? start : 0)};
        }''', mode)
        page.wait_for_timeout(50)
        facts = page.evaluate('''() => {
            if (fgCurrentView() !== 'timeline') throw new Error('Timeline did not open');
            const model = fgView('timeline').probe();
            return {calls:model.N, frames:model.NA, tracks:model.TRACKS.length,
                elements:document.getElementsByTagName('*').length, longtasks:startupLongTasks,
                navigation:performance.getEntriesByType('navigation')[0].toJSON()};
        }''')
        if errors:
            raise RuntimeError('; '.join(errors))
        end = timing['start'] + timing['frame_ms']
        tasks = [task['duration'] for task in facts['longtasks']
                 if task['start'] < end and task['start'] + task['duration'] > timing['start']]
        return dict(load_ms=load_ms, timing=timing, facts=facts, logs=logs,
                    longest_task_ms=max(tasks, default=0), blocked_ms=sum(max(0, task - 50) for task in tasks))
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+', type=Path)
    parser.add_argument('--mode', choices=['restore', 'switch', 'auto'], default='restore',
                        help='restore saved Timeline / switch from Text / report default (--no-text-view)')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--timeout', type=int, default=180000, help='per-navigation timeout, milliseconds')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.rounds < 1 or args.timeout < 1:
        parser.error('--rounds and --timeout must be positive')
    reports = [report_info(path.resolve()) for path in args.reports]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(ignore_default_args=['--hide-scrollbars'])
        output = dict(browser=browser.version, mode=args.mode, reports=reports, runs=[])
        try:
            for iteration in range(args.rounds):
                for report in reports if iteration % 2 == 0 else reversed(reports):
                    result = measure(browser, report, args.mode, args.timeout)
                    result.update(report=report['path'], iteration=iteration + 1)
                    output['runs'].append(result)
                    args.output.write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')
                    print(f"{report['path']}: frame {result['timing']['frame_ms']:.1f} ms, "
                          f"longest task {result['longest_task_ms']:.1f} ms", flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    main()
