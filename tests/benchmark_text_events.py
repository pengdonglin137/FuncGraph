"""Measure mounted Text event toggles against complete visibility and evidence.

Timing starts after Text and its complete fold model are ready. The API duration
and two-rAF feedback proxy do not measure physical presentation or cold startup.
"""
import argparse
import json
from pathlib import Path
import threading
from urllib.parse import quote, urljoin

from playwright.sync_api import sync_playwright

import helpers
from benchmark_startup import report_info
from benchmark_text import settle
from fglib.viewer import create_server


def measure(browser, report, timeout):
    path = Path(report['path'])
    server = create_server(path.parent if (path.parent / '.funcgraph-sidecar').is_file() else path)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
    thread.start()
    context = browser.new_context(viewport={'width': 1500, 'height': 900})
    try:
        page = context.new_page()
        page.set_default_timeout(timeout)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.add_init_script('localStorage.clear()')
        page.goto(urljoin(server.url, quote(path.name)), wait_until='load', timeout=timeout)
        page.evaluate("async () => { await fgSwitchView('text'); }")
        settle(page)
        facts = page.evaluate('''async () => {
            if (fgTextWindow || fgTextDeferred()) throw new Error('Full mounted Text is required');
            const folding = fgGetTextFolding();
            const records = folding.records;
            window.eventBenchmarkData = records.map(record => [record.row, record.getAttribute('data-trace-event')]);
            const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(eventBenchmarkData)));
            return {rows:records.length, events:records.filter(record => record.getAttribute('data-trace-event') === '1').length,
                signature:Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2,'0')).join('')};
        }''')
        if not facts['events']:
            raise RuntimeError('The report has no trace events')
        samples = []
        for visible in (False, True, False, True):
            settle(page)
            samples.append(page.evaluate('''async visible => {
                const started = performance.now();
                await fgSetTextEventsVisible(visible);
                const handler_ms = performance.now() - started;
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                const feedback_ms = performance.now() - started;
                const folding = fgGetTextFolding();
                const expected = folding.records.filter(record => folding.visible(record, foldedBlocks, fgFilterState.lines) &&
                    (visible || record.getAttribute('data-trace-event') !== '1')).map(record => record.row[0]);
                const shown = Array.from(document.querySelectorAll('#content .line-container'))
                    .filter(line => line.style.display !== 'none').map(line => +line.dataset.lineNumber);
                if (JSON.stringify(shown) !== JSON.stringify(expected)) throw new Error('Complete visibility mismatch');
                if (JSON.stringify(eventBenchmarkData) !== JSON.stringify(folding.records.map(record =>
                    [record.row,record.getAttribute('data-trace-event')]))) throw new Error('Text evidence mutated');
                const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(shown)));
                return {visible,handler_ms,feedback_ms,rows:shown.length,
                    signature:Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2,'0')).join('')};
            }''', visible))
        if errors:
            raise RuntimeError('; '.join(errors))
        return dict(facts=facts, samples=samples)
    finally:
        context.close()
        server.shutdown()
        thread.join(5)
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', type=Path, nargs='+')
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--timeout', type=int, default=240000)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    if min(arguments.rounds, arguments.timeout) < 1:
        parser.error('--rounds and --timeout must be positive')
    reports = [report_info(path.resolve()) for path in arguments.reports]
    output = dict(reports=reports, runs=[], basis=__doc__)
    with sync_playwright() as playwright:
        for iteration in range(arguments.rounds):
            for report in reports if iteration % 2 == 0 else reversed(reports):
                browser = playwright.chromium.launch(ignore_default_args=['--hide-scrollbars'])
                try:
                    result = measure(browser, report, arguments.timeout)
                    result.update(report=report['path'], iteration=iteration + 1, browser=browser.version)
                    output['runs'].append(result)
                    if len({json.dumps(run['facts'], sort_keys=True) for run in output['runs']}) != 1:
                        raise RuntimeError('Reports do not retain identical complete Text evidence')
                    if len({tuple(sample['signature'] for sample in run['samples']) for run in output['runs']}) != 1:
                        raise RuntimeError('Reports differ in complete event visibility')
                    arguments.output.write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')
                    print(report['path'], [round(sample['handler_ms'], 1) for sample in result['samples']], flush=True)
                finally:
                    browser.close()


if __name__ == '__main__':
    main()
