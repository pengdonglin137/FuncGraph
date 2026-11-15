#!/usr/bin/env python3
"""Compare query responsiveness on equivalent single-file reports."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics

from playwright.sync_api import sync_playwright


def payloads(content):
    values = {}
    for identifier in ('tlData', 'fgTimelineIdentity', 'fgSrc', 'fgSrcLines'):
        match = re.search(r'<script type="application/json" id="' + identifier + r'">(.*?)</script>', content, re.S)
        value = json.loads(match[1]) if match else None
        if identifier == 'tlData':
            if value is None or not isinstance(value.get('cols'), dict):
                raise ValueError('Use a single-file report with embedded Timeline data.')
            value.get('stats', {}).pop('build_time', None)
        values[identifier] = value
    return values


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def run(browser, path, query, expected):
    context = browser.new_context(viewport={'width': 1440, 'height': 900})
    try:
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(path.as_uri(), timeout=120000)
        page.evaluate("async () => { localStorage.clear(); await fgSwitchView('timeline'); }")
        result = page.evaluate('''async query => {
            const input = document.querySelector('.tl-search'), status = document.querySelector('.tl-count');
            const tasks = [], supported = typeof PerformanceObserver === 'function' && PerformanceObserver.supportedEntryTypes.includes('longtask');
            const observer = supported ? new PerformanceObserver(list => tasks.push(...list.getEntries())) : null;
            if (observer) observer.observe({type:'longtask'});
            let complete = null, heartbeat = null, dispatch = null;
            const started = performance.now();
            await new Promise(resolve => {
                const finish = () => { if (complete !== null && heartbeat !== null) resolve(); };
                const changes = new MutationObserver(() => {
                    if (complete === null && status.getAttribute('aria-busy') === 'false') {
                        complete = performance.now() - started; changes.disconnect(); finish();
                    }
                });
                changes.observe(status,{attributes:true,childList:true,subtree:true});
                setTimeout(() => { heartbeat = performance.now() - started; finish(); },0);
                input.value = query;
                input.dispatchEvent(new Event('input',{bubbles:true}));
                input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));
                dispatch = performance.now() - started;
                if (status.getAttribute('aria-busy') === 'false') { complete = dispatch; changes.disconnect(); finish(); }
            });
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            if (observer) { tasks.push(...observer.takeRecords()); observer.disconnect(); }
            return {dispatch_ms:dispatch,heartbeat_ms:heartbeat,search_ready_ms:complete,
                long_tasks_supported:supported,
                observed_long_tasks_ms:tasks.filter(task => task.startTime + task.duration > started && task.startTime <= started + complete).map(task => task.duration),
                status:status.textContent,invalid:input.getAttribute('aria-invalid'),
                selected:fgView('timeline').probe().sel(),calls:fgView('timeline').probe().N};
        }''', query)
        if errors:
            raise AssertionError(errors)
        actual = page.evaluate("JSON.stringify(fgData.read('tlData'))")
        actual = json.loads(actual)
        actual.get('stats', {}).pop('build_time', None)
        if actual != expected['tlData']:
            raise AssertionError('The report mutated its recorded Timeline payload.')
        result['timeline_sha256'] = digest(actual)
        return result
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--query', default='^(a+)+$')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error('--rounds must be positive')
    paths = dict(before=args.before.resolve(), after=args.after.resolve())
    expected = payloads(paths['before'].read_text())
    if expected != payloads(paths['after'].read_text()):
        parser.error('Recorded Timeline/source payloads differ, excluding only stats.build_time')
    observations = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for repeat in range(args.rounds):
                for side in (('before', 'after') if repeat % 2 == 0 else ('after', 'before')):
                    observations.append(dict(side=side, repeat=repeat, **run(browser, paths[side], args.query, expected)))
            version = browser.version
        finally:
            browser.close()
    signatures = {(row['status'], row['invalid'], row['selected'], row['calls'], row['timeline_sha256']) for row in observations}
    if len(signatures) != 1:
        raise AssertionError('UI outcomes or recorded data differ between runs.')
    medians = {}
    for side in paths:
        rows = [row for row in observations if row['side'] == side]
        medians[side] = {field: statistics.median(row[field] for row in rows)
                         for field in ('dispatch_ms', 'heartbeat_ms', 'search_ready_ms')}
    result = dict(browser=version, query=args.query, rounds=args.rounds,
                  files={side: dict(path=str(path), bytes=path.stat().st_size,
                                    sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for side, path in paths.items()},
                  recorded_payload_sha256=digest(expected), medians=medians, observations=observations,
                  scope='Fresh browser contexts; query through settled status. Excludes report startup and post-timing data verification. Heartbeat is a timer responsiveness proxy, not frame presentation, peak memory or a worst-case bound. UI outcomes agree; complete regex-hit equivalence is covered separately by worker regressions.')
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
