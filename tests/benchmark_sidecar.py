#!/usr/bin/env python3
"""Compare complete Timeline startup over fgview HTTP in fresh Chromium processes."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics
import threading

from playwright.sync_api import sync_playwright

import helpers
from fglib.viewer import create_server
from benchmark_startup import report_info
from benchmark_text import renderer_memory, settle


def text_resource(url):
    return bool(re.search(r'/(?:text|text-[0-9]+)\.html$', url))


def measure(browser, report, timeout, mount_text, filter_query=None, text_queries=None):
    server = create_server(Path(report['root']))
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
    thread.start()
    context = browser.new_context(viewport={'width': 1500, 'height': 900})
    try:
        page = context.new_page()
        page.set_default_timeout(timeout)
        errors, requests, transferred, workers = [], [], [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: requests.append(request.url))
        page.on('response', lambda response: transferred.append(int(response.headers.get('content-length', 0))))
        page.on('worker', lambda worker: workers.append(worker.url))
        page.add_init_script('''(() => {
            let hash = 0;
            const title = TITLE;
            for (let index = 0; index < title.length; index++) hash = ((hash << 5) - hash + title.charCodeAt(index)) | 0;
            localStorage.setItem('fg_' + (hash >>> 0).toString(36) + '_view', 'timeline');
            window.startupTasks = [];
            window.startupViolations = [];
            window.textLayoutSkipped = new WeakSet();
            document.addEventListener('contentvisibilityautostatechange', event => {
                if (event.skipped) textLayoutSkipped.add(event.target);
                else textLayoutSkipped.delete(event.target);
            }, true);
            document.addEventListener('securitypolicyviolation', event => startupViolations.push(event.violatedDirective));
            new PerformanceObserver(list => startupTasks.push(...list.getEntries().map(entry => ({start:entry.startTime, duration:entry.duration}))))
                .observe({type:'longtask', buffered:true});
            function feedback() {
                const view = document.getElementById('timelineView');
                if (view && view.querySelector('.fg-loading-status, .tl-canvas')) window.startupFeedback = performance.now();
                else requestAnimationFrame(feedback);
            }
            requestAnimationFrame(feedback);
        })()'''.replace('TITLE', json.dumps(report['title'])))
        page.goto(server.url, wait_until='load', timeout=timeout)
        page.evaluate('async () => { await fgSwitchView("timeline"); }')
        settle(page)
        ready = page.evaluate('performance.now()')
        page.wait_for_timeout(80)
        facts = page.evaluate('''end => {
            const model = fgView('timeline').probe();
            const tasks = startupTasks.filter(task => task.start < end).map(task => task.duration);
            return {ready_ms:end, feedback_ms:startupFeedback, calls:model.N, frames:model.NA, tracks:model.TRACKS.length,
                elements:document.getElementsByTagName('*').length,
                text_rows:document.querySelectorAll('#content .line-container').length,
                longest_task_ms:Math.max(0, ...tasks), blocked_ms:tasks.reduce((sum, duration) => sum + Math.max(0, duration - 50), 0),
                violations:startupViolations};
        }''', ready)
        facts.update(renderer_rss_bytes=sum(process['rss_bytes'] for process in renderer_memory(browser)),
                     transferred_bytes=sum(transferred), requests=list(requests))
        if filter_query:
            filtered = page.evaluate('''async query => {
                const start = performance.now();
                document.getElementById('filterInput').value = query;
                await applyFilter();
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                if (window.fgFilterProgress?.error) throw new Error(window.fgFilterProgress.message);
                return {duration_ms:performance.now() - start,
                    rows:document.querySelectorAll('#content .line-container').length,
                    hits:Array.from(fgFilterState.lines || []).sort((left, right) => left - right),
                    tasks:startupTasks.filter(task => task.start >= start)};
            }''', filter_query)
            hits = filtered.pop('hits')
            filtered.update(query=filter_query, hits=len(hits),
                            hit_sha256=hashlib.sha256(','.join(map(str, hits)).encode('ascii')).hexdigest(),
                            renderer_rss_bytes=sum(process['rss_bytes'] for process in renderer_memory(browser)),
                            index_requests=sum(url.endswith('text-index.json') for url in requests),
                            text_requests=sum(text_resource(url) for url in requests))
            if report['mode'] == 'sidecar' and (filtered['rows'] or filtered['text_requests'] or filtered['index_requests'] != 1):
                raise RuntimeError('Indexed filtering mounted Text or failed to use exactly one index request')
            facts['filter'] = filtered
        if report['mode'] == 'sidecar':
            if facts['text_rows'] or any(text_resource(url) or url.endswith('compare.json') for url in requests):
                raise RuntimeError('Sidecar initial Timeline unexpectedly materialized Text or fetched Compare')
            if mount_text:
                if not page.evaluate('fgTextDeferred()'):
                    raise RuntimeError('--text needs a sidecar report retaining Text')
                session = context.new_cdp_session(page)
                session.send('Performance.enable')
                before = {metric['name']: metric['value']
                          for metric in session.send('Performance.getMetrics')['metrics']}
                mounted = page.evaluate('''async () => {
                    const start = performance.now();
                    let chunksLoading = 0, concurrentChunks = 0;
                    const loadTextChunk = fgData.loadTextChunk;
                    fgData.loadTextChunk = (...parameters) => {
                        chunksLoading++;
                        concurrentChunks = Math.max(concurrentChunks, chunksLoading);
                        return loadTextChunk(...parameters).finally(() => chunksLoading--);
                    };
                    const labels = new Set();
                    let preparationFeedback = null, preparationFrames = 0, finishingFeedback = null, parsedFeedback = null;
                    const ready = fgSwitchView('text');
                    function sample() {
                        const status = document.querySelector('#content .fg-loading-status');
                        if (status) {
                            labels.add(status.textContent);
                            if (status.textContent.startsWith('Preparing Text')) {
                                if (preparationFeedback === null) preparationFeedback = performance.now() - start;
                                preparationFrames++;
                            }
                            if (status.textContent === 'Finishing Text layout…' && finishingFeedback === null) {
                                finishingFeedback = performance.now() - start;
                            }
                            if (/^Preparing Text… [0-9]+%$/.test(status.textContent) && parsedFeedback === null) {
                                parsedFeedback = performance.now() - start;
                            }
                        }
                        if (document.getElementById('content').hasAttribute('aria-busy')) requestAnimationFrame(sample);
                    }
                    requestAnimationFrame(sample);
                    await ready;
                    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    const tasks = startupTasks.filter(task => task.start >= start).map(task => task.duration);
                    const catalog = fgData.textChunks?.();
                    const chunks = catalog ? Math.ceil(catalog.count / catalog.chunk_size) : 1;
                    const cached = catalog ? Array.from({length:chunks}, (unused, index) =>
                        fgData.ready('fgText' + index * catalog.chunk_size)).some(Boolean) : fgData.ready('fgText');
                    return {duration_ms:performance.now() - start, expected_text_requests:chunks,
                        max_concurrent_text_chunks:concurrentChunks,
                        preparation_feedback_ms:preparationFeedback, preparation_frames:preparationFrames,
                        parsed_feedback_ms:parsedFeedback,
                        finishing_feedback_ms:finishingFeedback,
                        labels:Array.from(labels), longest_task_ms:Math.max(0, ...tasks),
                        blocked_ms:tasks.reduce((sum, duration) => sum + Math.max(0, duration - 50), 0),
                        rows:document.querySelectorAll('#content .line-container').length,
                        automatic_layout:document.getElementById('content').classList.contains('text-layout-auto'),
                        paint_blocks:document.querySelectorAll('#content > .text-chunk').length,
                        skipped_blocks:Array.from(document.querySelectorAll('#content > .text-chunk')).filter(chunk => textLayoutSkipped.has(chunk)).length,
                        cached_html:cached};
                }''')
                after = {metric['name']: metric['value']
                         for metric in session.send('Performance.getMetrics')['metrics']}
                mounted['dom_counters'] = session.send('Memory.getDOMCounters')
                mounted['workers_started'] = len(workers)
                if mounted['max_concurrent_text_chunks'] > 2:
                    raise RuntimeError('Text prefetch exceeded current plus one lookahead chunk')
                session.detach()
                mounted['browser_work'] = {
                    name: (after[metric] - before[metric]) * scale
                    for name, metric, scale in [
                        ('script_ms', 'ScriptDuration', 1000), ('style_ms', 'RecalcStyleDuration', 1000),
                        ('layout_ms', 'LayoutDuration', 1000), ('layout_count', 'LayoutCount', 1),
                        ('style_count', 'RecalcStyleCount', 1)]}
                page.evaluate('async () => { await fgSwitchView("timeline"); await fgSwitchView("text"); }')
                mounted['text_requests'] = sum(text_resource(url) for url in requests)
                mounted['renderer_rss_bytes'] = sum(process['rss_bytes'] for process in renderer_memory(browser))
                if mounted['cached_html'] or mounted['text_requests'] != mounted['expected_text_requests']:
                    raise RuntimeError('Text did not release HTML or reuse its mounted view')
                facts['first_text'] = mounted
                facts['text_filters'] = []
                for query in text_queries or []:
                    filtered = page.evaluate('''async query => {
                        const start = performance.now();
                        document.getElementById('filterInput').value = query;
                        const pending = applyFilter();
                        const call_ms = performance.now() - start;
                        await pending;
                        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                        const lines = fgFilterState.lines === null
                            ? Array.from(document.querySelectorAll('#content .line-container'), row => +row.dataset.lineNumber)
                            : Array.from(fgFilterState.lines);
                        return {duration_ms:performance.now() - start, call_ms,
                            rows:document.querySelectorAll('#content .line-container').length,
                            lines:lines.sort((left, right) => left - right)};
                    }''', query)
                    lines = filtered.pop('lines')
                    filtered.update(query=query, hits=len(lines),
                                    hit_sha256=hashlib.sha256(','.join(map(str, lines)).encode('ascii')).hexdigest(),
                                    renderer_rss_bytes=sum(process['rss_bytes'] for process in renderer_memory(browser)))
                    if filtered['rows'] != mounted['rows']:
                        raise RuntimeError('Native Text filtering changed the complete row inventory')
                    facts['text_filters'].append(filtered)
                if sum(text_resource(url) for url in requests) != mounted['expected_text_requests']:
                    raise RuntimeError('Native queries fetched Text again instead of using the mounted report')
                mounted['model_sha256'] = page.evaluate('''async () => {
                    const rows = fgGetTextFolding().records.map(record => record.row);
                    const bytes = new TextEncoder().encode(JSON.stringify(rows));
                    const digest = await crypto.subtle.digest('SHA-256', bytes);
                    return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
                }''')
        violations = page.evaluate('startupViolations')
        if errors or violations:
            raise RuntimeError(json.dumps(dict(errors=errors, violations=violations)))
        return facts
    finally:
        context.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('single', type=Path)
    parser.add_argument('sidecar', type=Path)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--timeout', type=int, default=180000)
    parser.add_argument('--text', action='store_true', help='also mount complete Text on the last sidecar round')
    parser.add_argument('--text-filter', action='append', default=[], metavar='QUERY',
                        help='repeatable native Text query after mounting; implies --text on the last sidecar round')
    parser.add_argument('--filter', help='apply a full-trace filter before mounting Text and compare exact hit digests')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.rounds, args.timeout) < 1:
        parser.error('--rounds and --timeout must be positive')
    if not args.single.is_file() or not (args.sidecar / 'index.html').is_file():
        parser.error('provide a single HTML file and a complete sidecar directory')
    reports = []
    for mode in ['single', 'sidecar']:
        root = getattr(args, mode).resolve()
        entry = root / 'index.html' if root.is_dir() else root
        reports.append(dict(report_info(entry), root=str(root), mode=mode))
    output = dict(reports=reports, runs=[], timing_basis='navigation to two rAF after complete Timeline; feedback is an rAF proxy, not physical presentation; RSS is sampled renderer total, not peak')
    with sync_playwright() as playwright:
        for iteration in range(args.rounds):
            for report in reports if iteration % 2 == 0 else reversed(reports):
                browser = playwright.chromium.launch(ignore_default_args=['--hide-scrollbars'])
                try:
                    facts = measure(browser, report, args.timeout,
                                    bool(args.text or args.text_filter) and iteration == args.rounds - 1,
                                    args.filter, args.text_filter)
                    facts.update(mode=report['mode'], iteration=iteration + 1, browser=browser.version)
                    output['runs'].append(facts)
                    identities = {(run['calls'], run['frames'], run['tracks']) for run in output['runs']}
                    if len(identities) > 1:
                        raise RuntimeError('Reports have different model counts; use the same trace/options')
                    filters = {(run['filter']['hits'], run['filter']['hit_sha256']) for run in output['runs'] if 'filter' in run}
                    if len(filters) > 1:
                        raise RuntimeError('Indexed and native full-trace filters returned different line hits')
                    text = facts.get('first_text')
                    if text and text['rows'] != next(run['text_rows'] for run in output['runs'] if run['mode'] == 'single'):
                        raise RuntimeError('Mounted sidecar Text row count differs from the single report')
                    args.output.write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')
                    print(f"{report['mode']}: ready {facts['ready_ms']:.1f} ms, elements {facts['elements']}, "
                          f"renderer RSS {facts['renderer_rss_bytes'] / 1048576:.1f} MiB", flush=True)
                finally:
                    browser.close()
    fields = ['ready_ms', 'feedback_ms', 'longest_task_ms', 'blocked_ms', 'renderer_rss_bytes', 'transferred_bytes', 'elements']
    for mode in ['single', 'sidecar']:
        runs = [run for run in output['runs'] if run['mode'] == mode]
        print(mode, {field: statistics.median(run[field] for run in runs) for field in fields})


if __name__ == '__main__':
    main()
