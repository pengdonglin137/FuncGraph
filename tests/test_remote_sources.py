"""A debug image plus requested remote files works without a local source tree."""
import http.server
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

import helpers


SOURCE = '''__attribute__((noinline)) int remote_target(int value)
{
    return value + 17;
}
__attribute__((noinline)) int start_kernel(void)
{
    return remote_target(5);
}
int main(void) { return start_kernel(); }
'''
REVISION = '1234567890abcdef1234567890abcdef12345678'


@unittest.skipUnless(all(shutil.which(tool) for tool in ('gcc', 'addr2line', 'nm', 'objcopy')) and
                     importlib.util.find_spec('elftools'), 'compiler/binutils/pyelftools not installed')
class RemoteSourceReportTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='fg-remote-source-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = self.root / 'kernel/demo.c'
        source.parent.mkdir()
        source.write_text(SOURCE)
        self.vmlinux = self.root / 'vmlinux'
        mapped = '/funcgraph-unavailable/' + self.root.name
        compiled = subprocess.run(['gcc', '-g', '-O0', '-fno-pie', '-no-pie',
                                   '-fdebug-prefix-map=' + str(self.root) + '=' + mapped,
                                   '-o', str(self.vmlinux), str(source)], capture_output=True, text=True)
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        source.unlink()
        source.parent.rmdir()
        self.assertFalse(Path(mapped).exists())
        symbols = subprocess.check_output(['nm', '-S', str(self.vmlinux)], text=True)
        size = int(re.search(r'^[0-9a-f]+ ([0-9a-f]+) T start_kernel$', symbols, re.M)[1], 16)
        self.trace = self.root / 'trace.txt'
        self.trace.write_text('# tracer: function_graph\n'
                              ' 0)   1.000 us    |  remote_target(value=5); '
                              f'/* <-start_kernel+0x1/0x{size:x} */\n')
        self.requests = []
        self.available = True
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append(self.path)
                if not owner.available or self.path != '/' + REVISION + '/kernel/demo.c':
                    self.send_error(404)
                    return
                content = SOURCE.encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                      kwargs={'poll_interval': 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.server.shutdown)
        self.url = 'http://127.0.0.1:' + str(self.server.server_address[1]) + '/{rev}/{path}'

    def report(self, name, image=None):
        output = self.root / (name + '.html')
        result = subprocess.run([sys.executable, str(Path(helpers.ROOT) / 'funcgraph.py'),
                                 '--no-config', '--fast', '--vmlinux', str(image or self.vmlinux),
                                 '--source-url', self.url, '--source-rev', REVISION,
                                 '--source-cache', str(self.root / 'cache'), '--source-browser', 'github',
                                 '--source-browser-rev', REVISION, '--timeline', '--filter', '--enable-fold',
                                 '--func-links', '--output', str(output), str(self.trace)],
                                capture_output=True, text=True, timeout=30,
                                env=dict(os.environ, CROSS_COMPILE='', LLVM='', DEBUGINFOD_URLS=''))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / 'kernel').exists())
        return output.read_text()

    def payload(self, report, identifier):
        return json.loads(re.search(r'<script type="application/json" id="' + identifier +
                                    r'">(.*?)</script>', report, re.S)[1])

    def test_only_referenced_file_is_fetched_and_merged_without_local_sources(self):
        report = self.report('remote')
        self.assertEqual(self.requests, ['/' + REVISION + '/kernel/demo.c'])
        self.assertEqual(len(list((self.root / 'cache').iterdir())), 1)
        self.assertIn('return value + 17;', json.dumps(self.payload(report, 'fgSrc')))
        timeline = self.payload(report, 'tlData')
        self.assertEqual(timeline['n'], 1)
        self.assertIn('kernel/demo.c', json.dumps(timeline['sources']))
        self.assertIn('/blob/' + REVISION + '/kernel/demo.c', report)

    def test_cached_source_rebuilds_when_remote_file_is_unavailable(self):
        first = self.report('first')
        requested = list(self.requests)
        self.available = False
        second = self.report('cached')
        self.assertEqual(self.requests, requested)
        for identifier in ('fgSrc', 'fgSrcLines'):
            self.assertEqual(self.payload(first, identifier), self.payload(second, identifier))

    def test_missing_remote_source_preserves_locations_without_fabricated_code(self):
        self.available = False
        report = self.report('missing')
        self.assertEqual(self.requests, ['/' + REVISION + '/kernel/demo.c'])
        self.assertIn('kernel/demo.c', json.dumps(self.payload(report, 'tlData')['sources']))
        self.assertNotIn('return value + 17;', json.dumps(self.payload(report, 'fgSrc')))

    def test_symbol_table_without_debug_lines_does_not_invent_locations_or_fetch_files(self):
        stripped = self.root / 'vmlinux-symbols'
        subprocess.run(['objcopy', '--strip-debug', str(self.vmlinux), str(stripped)], check=True)
        self.assertIn('remote_target', subprocess.check_output(['nm', str(stripped)], text=True))
        report = self.report('symbols-only', stripped)
        self.assertEqual(self.requests, [])
        self.assertNotIn('kernel/demo.c', json.dumps(self.payload(report, 'tlData').get('sources')))
        self.assertNotIn('return value + 17;', json.dumps(self.payload(report, 'fgSrc')))


if __name__ == '__main__':
    unittest.main()
