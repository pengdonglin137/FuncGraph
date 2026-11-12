#!/usr/bin/env python3
"""Compare complete and opt-in windowed Text in alternating isolated browsers."""
import argparse
import hashlib
import json
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright

from benchmark_startup import report_info
from benchmark_text import renderer_memory
from fglib.viewer import create_server


def measure(browser, url, mode, timeout):
    context = browser.new_context(viewport={'width': 1500, 'height': 900})
    page = context.new_page()
    page.set_default_timeout(timeout)
    errors = []
    requests = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('request', lambda request: requests.append(request.url))
    try:
        page.goto(url + ('?text=windowed' if mode == 'windowed' else ''), wait_until='load')
        page.evaluate('async () => { await fgSwitchView("timeline"); }')
        facts = page.evaluate('''async () => {
            const tasks = [];
            const observer = new PerformanceObserver(list => tasks.push(...list.getEntries().map(entry => entry.duration)));
            observer.observe({type:'longtask'});
            const start = performance.now();
            if (!await fgSwitchView('text')) throw new Error('Text startup failed');
            const ready_ms = performance.now()-start;
            await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
            const feedback_ms = performance.now()-start;
            tasks.push(...observer.takeRecords().map(entry => entry.duration)); observer.disconnect();
            return {ready_ms, feedback_ms, longest_task_ms:Math.max(0,...tasks),
                rows:document.querySelectorAll('#content .line-container').length,
                elements:document.getElementsByTagName('*').length,
                records:fgDomTextModels.get(document.getElementById('content')).records.length,
                calls:fgData.read('tlData').n, windowed:!!fgTextWindow};
        }''')
        facts['rss_sample'] = renderer_memory(browser)
        session = context.new_cdp_session(page)
        try:
            session.send('Performance.enable')
            facts['heap_sample'] = {item['name']: item['value'] for item in session.send('Performance.getMetrics')['metrics']
                                    if item['name'] in ['JSHeapUsedSize', 'JSHeapTotalSize', 'Nodes', 'Documents']}
        finally:
            session.detach()
        facts['startup_text_resources'] = sorted({url.rsplit('/', 1)[-1] for url in requests if '/text-' in url})
        facts['fidelity'] = page.evaluate('''async () => {
            const model = fgDomTextModels.get(document.getElementById('content'));
            const rows = model.records.map(record => [record.row[0], record.textContent,
                ...fgTextModel.fields.map(field=>record.getAttribute('data-'+field)),record.source]);
            const digest = await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(rows)));
            const sha256 = [...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,'0')).join('');
            const jumps=[];
            for (const ordinal of [Math.floor(model.records.length/2),model.records.length-1,0]) {
                const number=model.records[ordinal].row[0], start=performance.now();
                const row=await fgEnsureTextLine(number);
                row.scrollIntoView({block:'center',behavior:'instant'});
                await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
                if (row.id!=='L'+number || !row.isConnected) throw new Error('Lost requested line');
                jumps.push({ordinal,number,ms:performance.now()-start,
                    mounted:document.querySelectorAll('#content .line-container').length});
            }
            return {sha256,jumps};
        }''')
        if errors or mode == 'windowed' and (facts['rows'] > 160 or any(jump['mounted'] > 160 for jump in facts['fidelity']['jumps'])):
            raise RuntimeError(json.dumps(dict(errors=errors, facts=facts)))
        facts.update(mode=mode, browser=browser.version)
        return facts
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--timeout', type=int, default=180000)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    if min(arguments.rounds, arguments.timeout) < 1:
        parser.error('rounds and timeout must be positive')
    server = create_server(arguments.report.resolve())
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
    thread.start()
    output = dict(report=report_info(arguments.report.resolve() / 'index.html'),
                  trace=dict(path=str(arguments.trace.resolve()), bytes=arguments.trace.stat().st_size,
                             sha256=hashlib.sha256(arguments.trace.read_bytes()).hexdigest()), runs=[],
                  basis='Same sidecar; alternating independent browsers; Timeline then cold Text; two-rAF feedback; sampled RSS/heap, not peak; complete metadata retained; jumps and full-model digests after startup/memory sampling')
    try:
        with sync_playwright() as playwright:
            for iteration in range(arguments.rounds):
                for mode in ['complete', 'windowed'] if iteration % 2 == 0 else ['windowed', 'complete']:
                    browser = playwright.chromium.launch()
                    try:
                        facts = measure(browser, server.url, mode, arguments.timeout)
                        facts['round'] = iteration + 1
                        output['runs'].append(facts)
                        if len({run['fidelity']['sha256'] for run in output['runs']}) != 1:
                            raise RuntimeError('Complete Text model changed')
                        arguments.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
                        print(mode, iteration + 1, round(facts['ready_ms'], 1), facts['rows'], flush=True)
                    finally:
                        browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


if __name__ == '__main__':
    main()
