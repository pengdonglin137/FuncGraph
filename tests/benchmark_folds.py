"""Compare prepared Text folding over real fgview HTTP in isolated browsers.

Handler timing calls the same APIs as fold controls; two rAF is feedback proxy,
not physical presentation latency. Renderer RSS is a sample, not peak memory.
"""
import argparse
import json
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright

import helpers
from benchmark_startup import report_info
from benchmark_text import renderer_memory, settle
from fglib.viewer import create_server


def measure(browser, report, function, recursive, timeout):
    server = create_server(Path(report['root']))
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
    thread.start()
    context = browser.new_context(viewport={'width': 1500, 'height': 900})
    try:
        page = context.new_page()
        page.set_default_timeout(timeout)
        errors, requests = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: requests.append(request.url.rsplit('/', 1)[-1]))
        page.add_init_script('''(() => {
            localStorage.clear();
            localStorage.setItem('theme', 'light');
            const title = TITLE;
            let hash = 0;
            for (let index = 0; index < title.length; index++) hash = ((hash << 5) - hash + title.charCodeAt(index)) | 0;
            localStorage.setItem('fg_' + (hash >>> 0).toString(36) + '_view', 'text');
        })()'''.replace('TITLE', json.dumps(report['title'])))
        page.goto(server.url, wait_until='load', timeout=timeout)
        page.evaluate("async () => { await fgSwitchView('text'); }")
        settle(page)
        result = page.evaluate('''functionName => {
            if (fgCurrentView() !== 'text' || fgTextDeferred()) throw new Error('Text is not ready');
            const folding = fgGetTextFolding();
            const target = Array.from(folding.blocks.values()).find(block =>
                block.entry?.getAttribute('data-func') === functionName && block.calls);
            if (!target) throw new Error('No requested nonempty call subtree: ' + functionName);
            document.getElementById('L' + target.entry.row[0]).scrollIntoView({block:'center'});
            return {id:target.id, line:target.entry.row[0], calls:target.calls,
                rows:folding.records.length, blocks:folding.blocks.size, text_ready_ms:performance.now()};
        }''', function)
        settle(page)
        result['samples'] = []
        for iteration in range(4):
            sample = page.evaluate('''async config => {
                const started = performance.now();
                if (config.recursive) toggleFoldRecursive(config.id);
                else toggleFold(config.id);
                const handler_ms = performance.now() - started;
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                const feedback_ms = performance.now() - started;
                const folding = fgGetTextFolding();
                const visible = [...document.querySelectorAll('#content .line-container')]
                    .filter(line => line.style.display !== 'none').map(line => +line.dataset.lineNumber).sort((left, right) => left - right);
                const expected = folding.visibleLines(foldedBlocks, fgFilterState.lines);
                if (visible.length !== expected.size || visible.some(line => !expected.has(line))) throw new Error('Visibility differs from full-trace model');
                const folded = Object.keys(foldedBlocks).filter(id => foldedBlocks[id] === true).sort();
                const summaries = [...document.querySelectorAll('#content .fold-summary')]
                    .map(summary => [summary.closest('.line-container').dataset.lineNumber, summary.textContent]);
                const bytes = new TextEncoder().encode(JSON.stringify({visible, folded, summaries}));
                const digest = await crypto.subtle.digest('SHA-256', bytes);
                return {handler_ms, feedback_ms, visible:visible.length, folded:folded.length,
                    signature:Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')};
            }''', dict(id=result['id'], recursive=recursive))
            sample['iteration'] = iteration + 1
            result['samples'].append(sample)
            page.wait_for_timeout(50)
        result.update(requests=requests, renderer_rss_bytes=sum(record['rss_bytes'] for record in renderer_memory(browser)))
        result['projected_rows'] = page.evaluate('''config => {
            const project = fgProjectFoldRecords;
            const counts = [];
            fgProjectFoldRecords = (folding, records) => {
                const selected = Array.from(records);
                counts.push(selected.length);
                return project(folding, selected);
            };
            try {
                for (let iteration = 0; iteration < 2; iteration++) {
                    if (config.recursive) toggleFoldRecursive(config.id);
                    else toggleFold(config.id);
                }
            } finally { fgProjectFoldRecords = project; }
            return counts;
        }''', dict(id=result['id'], recursive=recursive))
        if errors:
            raise RuntimeError('; '.join(errors))
        return result
    finally:
        context.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', type=Path, nargs='+')
    parser.add_argument('--function', default='vfs_statx')
    parser.add_argument('--recursive', action='store_true')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--timeout', type=int, default=180000)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    if min(arguments.rounds, arguments.timeout) < 1:
        parser.error('--rounds and --timeout must be positive')
    reports = [dict(report_info((path / 'index.html' if path.is_dir() else path).resolve()), root=str(path.resolve()))
               for path in arguments.reports]
    output = dict(reports=reports, function=arguments.function, recursive=arguments.recursive, runs=[],
                  basis='prepared Text; synchronous fold API and two-rAF feedback proxy; complete visibility/state/summary hashes; sampled RSS, not peak')
    with sync_playwright() as playwright:
        for iteration in range(arguments.rounds):
            for report in reports if iteration % 2 == 0 else reversed(reports):
                browser = playwright.chromium.launch(ignore_default_args=['--hide-scrollbars'])
                try:
                    run = measure(browser, report, arguments.function, arguments.recursive, arguments.timeout)
                    run.update(report=report['path'], round=iteration + 1, browser=browser.version)
                    output['runs'].append(run)
                    identities = {(entry['id'], entry['rows'], entry['blocks'], tuple(sample['signature'] for sample in entry['samples']))
                                  for entry in output['runs']}
                    if len(identities) != 1:
                        raise RuntimeError('Reports differ in complete call/fold/visibility/summary results')
                    arguments.output.write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')
                    print(report['path'], run['text_ready_ms'], [sample['handler_ms'] for sample in run['samples']], flush=True)
                finally:
                    browser.close()


if __name__ == '__main__':
    main()
