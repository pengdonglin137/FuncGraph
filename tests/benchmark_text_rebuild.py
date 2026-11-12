#!/usr/bin/env python3
"""Compare windowed Text interactions in alternating independent browsers."""
import argparse
import hashlib
import json
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright

from benchmark_startup import report_info
from fglib.viewer import create_server


ACTIONS = [
    ('rebuild', '() => fgTextWindow.rebuild()'),
    ('task-cold', '() => fgSetTextOrder("task")'),
    ('task-warm', '() => fgTextWindow.rebuild()'),
    ('duration-cold', '() => {fgTextSort="desc";return fgTextWindow.rebuild()}'),
    ('duration-warm', '() => fgTextWindow.rebuild()'),
    ('filter', '() => {document.getElementById("filterInput").value="func:mutex";return applyFilter()}'),
    ('clear', '() => clearFilter()'),
]


def measure(browser, url, timeout):
    context = browser.new_context(viewport={'width': 1440, 'height': 900})
    page = context.new_page()
    page.set_default_timeout(timeout)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(url + '?text=windowed')
        if not page.evaluate('async () => !!(await fgSwitchView("text"))'):
            raise RuntimeError('Text startup failed')
        identity = page.evaluate('''async () => {
            if (!fgTextWindow) throw new Error('Windowed Text required');
            const model=fgTextWindow.model;
            const data=JSON.stringify(model.records.map(record=>[record.row[0],record.textContent,
                ...fgTextModel.fields.map(field=>record.getAttribute('data-'+field)),record.source]));
            const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(data));
            return {records:model.records.length,
                sha256:[...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,'0')).join('')};
        }''')
        results = []
        for name, action in ACTIONS:
            facts = page.evaluate('''async () => {
                const timings=[], observer=new PerformanceObserver(list=>timings.push(...list.getEntries().map(entry=>entry.duration)));
                observer.observe({type:'longtask'});
                const start=performance.now(), pending=ACTION();
                const sync_ms=performance.now()-start;
                const outcome=await pending;
                const ready_ms=performance.now()-start;
                if (outcome===false || outcome===null || document.getElementById('content').hasAttribute('aria-busy'))
                    throw new Error('Interaction returned before successful completion');
                await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
                const feedback_ms=performance.now()-start;
                await new Promise(resolve=>setTimeout(resolve,60));
                timings.push(...observer.takeRecords().map(entry=>entry.duration)); observer.disconnect();
                const data=JSON.stringify([fgTextWindow.visibleNumbers,fgTextNavigation.order,fgTextNavigation.visibleCount]);
                const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(data));
                return {sync_ms,ready_ms,feedback_ms,longest_task_ms:Math.max(0,...timings),
                    mounted:document.querySelectorAll('#content .line-container').length,
                    visible:fgTextNavigation.visibleCount,order:fgTextOrder,sort:fgTextSort,
                    sha256:[...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,'0')).join('')};
            }'''.replace('ACTION()', '(' + action + ')()'))
            if facts['mounted'] > 160 or errors:
                raise RuntimeError(json.dumps(dict(errors=errors, facts=facts)))
            results.append(dict(action=name, **facts))
        return dict(browser=browser.version, model=identity, actions=results, errors=errors)
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--timeout', type=int, default=180000)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    if min(arguments.rounds, arguments.timeout) < 1:
        parser.error('rounds and timeout must be positive')
    reports = dict(before=arguments.before.resolve(), after=arguments.after.resolve())
    servers, threads = {}, []
    output = dict(reports={name: report_info(path / 'index.html') for name, path in reports.items()},
                  trace=dict(path=str(arguments.trace.resolve()), bytes=arguments.trace.stat().st_size,
                             sha256=hashlib.sha256(arguments.trace.read_bytes()).hexdigest()), runs=[],
                  basis='No CPU profiler; alternating independent Chromium processes; same actions; API, synchronous-prefix and two-rAF timings; full model and per-action complete visibility/order digests; not a frame or total-memory bound')
    try:
        for name, path in reports.items():
            servers[name] = create_server(path)
            thread = threading.Thread(target=servers[name].serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
            thread.start()
            threads.append(thread)
        with sync_playwright() as playwright:
            for iteration in range(arguments.rounds):
                for variant in ['before', 'after'] if iteration % 2 == 0 else ['after', 'before']:
                    browser = playwright.chromium.launch()
                    try:
                        facts = dict(variant=variant, round=iteration + 1,
                                     **measure(browser, servers[variant].url, arguments.timeout))
                        if output['runs']:
                            reference = output['runs'][0]
                            if facts['model'] != reference['model'] or [item['sha256'] for item in facts['actions']] != [item['sha256'] for item in reference['actions']]:
                                raise RuntimeError('Complete model or interaction visibility/order changed')
                        output['runs'].append(facts)
                        arguments.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
                        print(variant, iteration + 1, [(item['action'], round(item['sync_ms'], 1), round(item['ready_ms'], 1)) for item in facts['actions']], flush=True)
                    finally:
                        browser.close()
    finally:
        for server in servers.values():
            server.shutdown()
        for thread in threads:
            thread.join(timeout=5)
        for server in servers.values():
            server.server_close()


if __name__ == '__main__':
    main()
