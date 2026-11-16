"""Compare complete shared-filter scans using one unchanged real Text index."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import statistics
import threading

from playwright.sync_api import sync_playwright


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True, help='complete HTML containing the baseline script')
    parser.add_argument('--before-js', type=Path, required=True)
    parser.add_argument('--after-js', type=Path, default=root / 'assets/report/report.js')
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.rounds < 1:
        parser.error('--rounds must be positive')
    content = arguments.report.read_text()
    before = arguments.before_js.read_text()
    after = arguments.after_js.read_text()
    if content.count(before) != 1:
        parser.error('--report must contain --before-js exactly once')
    documents = {'/before.html': content.encode(), '/after.html': content.replace(before, after, 1).encode()}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/index.json':
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(arguments.index.stat().st_size))
                self.end_headers()
                with arguments.index.open('rb') as source:
                    shutil.copyfileobj(source, self.wfile)
            elif self.path in documents:
                document = documents[self.path]
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(document)))
                self.end_headers()
                self.wfile.write(document)
            else:
                self.send_error(404)

        def log_message(self, *parameters):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    observations = []
    queries = ['func:spin', 'func:[', 'cpu:0 AND ret:-1', 'NOT (comm:swapper OR func:spin)']
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                version = browser.version
                for iteration in range(arguments.rounds):
                    for variant in ('before', 'after'):
                        context = browser.new_context()
                        try:
                            page = context.new_page()
                            page.set_default_timeout(120000)
                            page.goto(f'http://127.0.0.1:{server.server_port}/{variant}.html')
                            page.evaluate('''async () => {
                                const data = await (await fetch('/index.json')).json();
                                function drain(iterator) {
                                    let result;
                                    do { result = iterator.next(); } while (!result.done);
                                    return result.value;
                                }
                                window.filterBenchModel = drain(fgTextModel.prepare(data));
                                window.filterBenchDrain = drain;
                            }''')
                            for query in queries:
                                result = page.evaluate('''async query => {
                                    filterBenchDrain(fgTextModel.filter(filterBenchModel, query));
                                    const started = performance.now();
                                    const result = filterBenchDrain(fgTextModel.filter(filterBenchModel, query));
                                    const elapsed = performance.now() - started;
                                    const signature = JSON.stringify([result.text, result.visible, result.total,
                                        result.sortDir, [...result.lines]]);
                                    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(signature));
                                    const NativeRegExp = RegExp;
                                    let constructors = 0;
                                    window.RegExp = new Proxy(NativeRegExp, {
                                        construct(target, parameters) {
                                            constructors++;
                                            return Reflect.construct(target, parameters);
                                        }
                                    });
                                    try { filterBenchDrain(fgTextModel.filter(filterBenchModel, query)); }
                                    finally { window.RegExp = NativeRegExp; }
                                    return {elapsed_ms: elapsed, constructors, rows: result.total, matches: result.visible,
                                        result_sha256: [...new Uint8Array(digest)].map(value =>
                                            value.toString(16).padStart(2, '0')).join('')};
                                }''', query)
                                result.update(query=query, variant=variant, iteration=iteration + 1)
                                observations.append(result)
                        finally:
                            context.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    for query in queries:
        signatures = {record['result_sha256'] for record in observations if record['query'] == query}
        if len(signatures) != 1:
            raise RuntimeError(f'Complete filter results differ for {query}: {signatures}')
    payload = {
        'browser': version, 'index': str(arguments.index), 'index_bytes': arguments.index.stat().st_size,
        'index_sha256': hashlib.sha256(arguments.index.read_bytes()).hexdigest(), 'rounds': arguments.rounds,
        'script_sha256': {'before': hashlib.sha256(before.encode()).hexdigest(),
                          'after': hashlib.sha256(after.encode()).hexdigest()},
        'report_sha256': {path: hashlib.sha256(document).hexdigest() for path, document in documents.items()},
        'observations': observations,
        'medians': {query: {variant: statistics.median(record['elapsed_ms'] for record in observations
                                                     if record['query'] == query and record['variant'] == variant)
                            for variant in ('before', 'after')} for query in queries}
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(payload, indent=2) + '\n')
    print(json.dumps(payload, indent=2))


if __name__ == '__main__':
    main()
