"""Measure the detached on-demand Text block reader, not production windowing."""
import argparse
import json
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright

import helpers
from fglib.viewer import create_server
from benchmark_startup import report_info


def measure(browser, server, timeout):
    context = browser.new_context(viewport={'width': 1500, 'height': 900})
    try:
        page = context.new_page()
        page.set_default_timeout(timeout)
        errors, requests = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: requests.append(request.url))
        page.add_init_script('''localStorage.clear(); window.rowViolations = [];
            document.addEventListener('securitypolicyviolation', event => rowViolations.push(event.violatedDirective));''')
        page.goto(server.url, wait_until='load')
        page.locator('#timelineView .tl-canvas').wait_for(state='attached')
        facts = page.evaluate('''async () => {
            if (!fgData.textChunks()) throw new Error('Requires a chunked sidecar report');
            const start = performance.now();
            const data = await fgData.load('fgTextIndex');
            const iterator = fgTextModel.prepare(data);
            let result;
            try {
                while (true) {
                    const before = performance.now();
                    do { result = iterator.next(); } while (!result.done && performance.now() - before < 8);
                    if (result.done) break;
                    await new Promise(resolve => setTimeout(resolve, 0));
                }
            } finally { iterator.return(); }
            const model = result.value, model_ms = performance.now() - start;
            const rows = [];
            for (const ordinal of [...new Set([0, Math.floor(model.records.length/2), model.records.length-1])]) {
                const number = model.records[ordinal].row[0], before = performance.now();
                const block = await fgLoadTextBlock(model, number);
                const load_parse_ms = performance.now() - before;
                const identities = Array.from(block.element.querySelectorAll('.line-container'), row => +row.dataset.lineNumber);
                if (JSON.stringify(identities) !== JSON.stringify(model.records.slice(block.first, block.first+block.count).map(record => record.row[0])) ||
                    block.line.id !== 'L'+number || block.element.isConnected || block.count > fgData.textChunks().paint_rows) {
                    throw new Error('On-demand block lost complete model identity');
                }
                rows.push({ordinal, number, first:block.first, count:block.count, load_parse_ms,
                    nodes:block.element.querySelectorAll('*').length, source:fgTextModel.locate(model, number).record.source});
            }
            const bytes = new TextEncoder().encode(JSON.stringify(model.records.map(record => record.row)));
            const model_sha256 = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)),
                value => value.toString(16).padStart(2,'0')).join('');
            if (JSON.stringify(data.rows) !== JSON.stringify(model.records.map(record => record.row))) throw new Error('Input records changed');
            return {model_ms, records:model.records.length, model_sha256, rows,
                mounted:document.querySelectorAll('#content .line-container').length, deferred:fgTextDeferred(),
                violations:rowViolations};
        }''')
        if errors or facts['violations'] or facts['mounted'] or not facts['deferred']:
            raise RuntimeError(str(dict(errors=errors, facts=facts)))
        facts.update(requests=requests, browser=browser.version)
        return facts
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--timeout', type=int, default=180000)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.rounds < 1 or arguments.timeout < 1 or not (arguments.report / 'index.html').is_file():
        parser.error('use a complete chunked sidecar report and positive rounds/timeout')
    server = create_server(arguments.report.resolve())
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
    thread.start()
    output = dict(report=report_info(arguments.report.resolve() / 'index.html'), runs=[],
                  basis='Detached block reader only; complete index/model retained; not production Text startup, windowing, or a process-memory bound')
    try:
        with sync_playwright() as playwright:
            for iteration in range(arguments.rounds):
                browser = playwright.chromium.launch()
                try:
                    facts = measure(browser, server, arguments.timeout)
                    facts['round'] = iteration + 1
                    output['runs'].append(facts)
                    if len({run['model_sha256'] for run in output['runs']}) != 1:
                        raise RuntimeError('Complete model identity changed between rounds')
                    arguments.output.write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')
                    print(json.dumps(dict(round=iteration+1, records=facts['records'], rows=facts['rows'])), flush=True)
                finally:
                    browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


if __name__ == '__main__':
    main()
