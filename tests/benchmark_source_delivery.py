#!/usr/bin/env python3
"""Compare cold Timeline startup and call-site source delivery over loopback HTTP."""
import argparse
import hashlib
import json
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright

import helpers
from benchmark_startup import report_info
from benchmark_text import renderer_memory, settle
from fglib.viewer import create_server


def measure(browser, report, timeout):
    server = create_server(Path(report['root']))
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
    thread.start()
    context = browser.new_context(viewport={'width': 1500, 'height': 900})
    try:
        page = context.new_page()
        page.set_default_timeout(timeout)
        requests, sizes, errors = [], [], []
        page.on('request', lambda request: requests.append(request.url.rsplit('/', 1)[-1]))
        page.on('response', lambda response: sizes.append(int(response.headers.get('content-length', 0))))
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.add_init_script('''(() => {
            let hash = 0;
            const title = TITLE;
            for (let index = 0; index < title.length; index++) hash = ((hash << 5) - hash + title.charCodeAt(index)) | 0;
            localStorage.setItem('fg_' + (hash >>> 0).toString(36) + '_view', 'timeline');
            localStorage.setItem('fgTl.csOpen', '1');
        })()'''.replace('TITLE', json.dumps(report['title'])))
        page.goto(server.url, wait_until='load', timeout=timeout)
        page.evaluate("async () => { await fgSwitchView('timeline'); }")
        settle(page)
        result = page.evaluate('''() => {
            const probe = fgView('timeline').probe();
            return {ready_ms:performance.now(), calls:probe.N, frames:probe.NA, tracks:probe.TRACKS.length,
                text_rows:document.querySelectorAll('#content .line-container').length,
                elements:document.getElementsByTagName('*').length};
        }''')
        result.update(startup_requests=list(requests), startup_bytes=sum(sizes),
                      startup_renderer_rss_bytes=sum(process['rss_bytes'] for process in renderer_memory(browser)))
        result['sources'] = []
        for iteration in range(2):
            source = page.evaluate('''async () => {
                const started = performance.now();
                const data = fgData.read('tlData'), sources = fgData.read('fgSrcLines');
                const index = data.cols.line.findIndex(line => sources[String(line)] !== undefined);
                if (index < 0) throw new Error('Report has no resolved source identities');
                fgView('timeline').probe().select(index);
                const handler_ms = performance.now() - started;
                await new Promise((resolve, reject) => {
                    const check = () => {
                        const panel = document.querySelector('.tl-cs-src');
                        if (panel?.querySelector('[role=alert]')) { reject(new Error(panel.textContent)); return; }
                        if (panel && !panel.hasAttribute('aria-busy') && panel.querySelector('.location-link, .source-line')) resolve();
                        else if (performance.now() - started > 30000) reject(new Error('Source panel did not become ready'));
                        else requestAnimationFrame(check);
                    };
                    check();
                });
                await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                const panel = document.querySelector('.tl-cs-src');
                return {handler_ms, ready_ms:performance.now() - started, index,
                    source_id:sources[String(data.cols.line[index])], html:panel.innerHTML,
                    code_lines:panel.querySelectorAll('.source-line').length};
            }''')
            source['html_sha256'] = hashlib.sha256(source.pop('html').encode('utf-8')).hexdigest()
            source.update(iteration=iteration + 1, requests=list(requests), bytes=sum(sizes))
            result['sources'].append(source)
        if errors or result['sources'][0]['html_sha256'] != result['sources'][1]['html_sha256']:
            raise RuntimeError(json.dumps(dict(errors=errors, result=result)))
        if result['sources'][0]['requests'] != result['sources'][1]['requests']:
            raise RuntimeError('Reopening a source fetched additional resources')
        result['source_renderer_rss_bytes'] = sum(process['rss_bytes'] for process in renderer_memory(browser))
        return result
    finally:
        context.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+', type=Path)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--timeout', type=int, default=180000)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.rounds, args.timeout) < 1:
        parser.error('--rounds and --timeout must be positive')
    reports = [dict(report_info((path / 'index.html' if path.is_dir() else path).resolve()), root=str(path.resolve()))
               for path in args.reports]
    output = dict(reports=reports, runs=[], timing_basis='navigation/selection (including source-ID lookup) to complete API/source plus two rAF; '
                  'RSS is sampled, not peak; transfer counts Content-Length bodies, not headers')
    with sync_playwright() as playwright:
        for iteration in range(args.rounds):
            for report in reports if iteration % 2 == 0 else reversed(reports):
                browser = playwright.chromium.launch(ignore_default_args=['--hide-scrollbars'])
                try:
                    result = measure(browser, report, args.timeout)
                    result.update(report=report['path'], iteration=iteration + 1, browser=browser.version)
                    output['runs'].append(result)
                    identities = {(run['calls'], run['frames'], run['tracks'], run['sources'][0]['html_sha256'])
                                  for run in output['runs']}
                    if len(identities) != 1:
                        raise RuntimeError('Report models or selected source HTML differ')
                    args.output.write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')
                    print(report['path'], result['ready_ms'], result['sources'][0]['ready_ms'], flush=True)
                finally:
                    browser.close()


if __name__ == '__main__':
    main()
