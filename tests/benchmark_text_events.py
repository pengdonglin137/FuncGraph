"""Measure mounted Text event toggles against complete visibility and evidence.

Timing starts after Text is ready; normally its complete fold model is prepared.
--cold-model instead requires unprepared mounted Text before the first toggle.
The API duration and two-rAF feedback proxy do not measure physical presentation
or report startup/download/mount time.
"""
import argparse
import json
from pathlib import Path
import threading
import time
from urllib.parse import quote, urljoin

from playwright.sync_api import sync_playwright

import helpers
from benchmark_startup import report_info
from benchmark_text import settle
from fglib.viewer import create_server


def measure(browser, report, timeout, cold_model=False):
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
        started = time.monotonic()
        page.goto(urljoin(server.url, quote(path.name)), wait_until='load', timeout=timeout)
        startup_ms = (time.monotonic() - started) * 1000
        page.evaluate("async () => { await fgSwitchView('text'); }")
        settle(page)
        facts = page.evaluate('''async cold => {
            if (fgTextWindow || fgTextDeferred()) throw new Error('Full mounted Text is required');
            const root = document.getElementById('content');
            if (cold && fgDomTextModels.has(root)) throw new Error('Text model is already prepared');
            if (cold && (fgFilterState.lines !== null || Object.values(foldedBlocks).some(value => value === true)))
                throw new Error('Cold-model measurement requires the original unfolded/unfiltered view');
            window.eventBenchmarkRows = Array.from(root.querySelectorAll('.line-container'));
            const folding = cold ? null : fgGetTextFolding();
            window.eventBenchmarkSnapshot = cold ? () => eventBenchmarkRows.map(row => [
                row.querySelector('.line-content')?.textContent || '',row.getAttribute('data-line-number'),
                row.getAttribute('data-trace-event'),...fgTextModel.symbolFields.map(field=>row.getAttribute('data-'+field))]) :
                () => folding.records.map(record => [record.row, record.getAttribute('data-trace-event')]);
            window.eventBenchmarkData = eventBenchmarkSnapshot();
            const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(eventBenchmarkData)));
            return {rows:eventBenchmarkRows.length,
                events:eventBenchmarkRows.filter(record => record.getAttribute('data-trace-event') === '1').length,
                signature:Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2,'0')).join('')};
        }''', cold_model)
        if not facts['events']:
            raise RuntimeError('The report has no trace events')
        samples = []
        for visible in (False, True, False, True):
            settle(page)
            samples.append(page.evaluate('''async ({visible,cold}) => {
                const started = performance.now();
                await fgSetTextEventsVisible(visible);
                const handler_ms = performance.now() - started;
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                const feedback_ms = performance.now() - started;
                const model_prepared = fgDomTextModels.has(document.getElementById('content'));
                const folding = cold ? null : fgGetTextFolding();
                const expected = cold ? eventBenchmarkRows.filter(row => visible || row.getAttribute('data-trace-event') !== '1')
                    .map(row => +row.dataset.lineNumber) :
                    folding.records.filter(record => folding.visible(record, foldedBlocks, fgFilterState.lines) &&
                        (visible || record.getAttribute('data-trace-event') !== '1')).map(record => record.row[0]);
                const shown = Array.from(document.querySelectorAll('#content .line-container'))
                    .filter(line => line.style.display !== 'none').map(line => +line.dataset.lineNumber);
                if (JSON.stringify(shown) !== JSON.stringify(expected)) throw new Error('Complete visibility mismatch');
                if (JSON.stringify(eventBenchmarkData) !== JSON.stringify(eventBenchmarkSnapshot())) throw new Error('Text evidence mutated');
                const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(shown)));
                return {visible,handler_ms,feedback_ms,model_prepared,rows:shown.length,
                    signature:Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2,'0')).join('')};
            }''', dict(visible=visible,cold=cold_model)))
        if errors:
            raise RuntimeError('; '.join(errors))
        return dict(facts=facts, samples=samples, startup_ms=startup_ms,
                    navigation=page.evaluate('performance.getEntriesByType("navigation")[0].toJSON()'))
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
    parser.add_argument('--cold-model', action='store_true', help='require unprepared Text before the first toggle')
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    if min(arguments.rounds, arguments.timeout) < 1:
        parser.error('--rounds and --timeout must be positive')
    reports = [report_info(path.resolve()) for path in arguments.reports]
    output = dict(reports=reports, runs=[], cold_model=arguments.cold_model, basis=__doc__)
    with sync_playwright() as playwright:
        for iteration in range(arguments.rounds):
            for report in reports if iteration % 2 == 0 else reversed(reports):
                browser = playwright.chromium.launch(ignore_default_args=['--hide-scrollbars'])
                try:
                    result = measure(browser, report, arguments.timeout, arguments.cold_model)
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
