"""A debug image plus requested remote files works without a local source tree."""
import hashlib
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
from fglib import uftrace


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
        self.target_address = '0x' + re.search(r'^([0-9a-f]+) [0-9a-f]+ T remote_target$', symbols, re.M)[1]
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

    def report(self, name, image=None, cache=None, mode='single', verbose=False):
        output = self.root / (name + '.html' if mode == 'single' else name)
        mode_args = ['--report-mode', mode]
        if mode == 'auto':
            mode_args.extend(['--sidecar-threshold', '0'])
        if verbose:
            mode_args.append('--verbose')
        result = subprocess.run([sys.executable, str(Path(helpers.ROOT) / 'funcgraph.py'),
                                 '--no-config', '--fast', '--vmlinux', str(image or self.vmlinux),
                                 '--source-url', self.url, '--source-rev', REVISION,
                                 '--source-cache', str(cache or self.root / 'cache'), '--source-browser', 'github',
                                 '--source-browser-rev', REVISION, '--timeline', '--filter', '--enable-fold',
                                 '--func-links', '--output', str(output), *mode_args, str(self.trace)],
                                capture_output=True, text=True, timeout=30,
                                env=dict(os.environ, CROSS_COMPILE='', LLVM='', DEBUGINFOD_URLS=''))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.last_result = result
        self.last_output = output
        self.assertFalse((self.root / 'kernel').exists())
        return (output if output.is_file() else output / 'index.html').read_text()

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

    def test_failed_cache_keeps_downloaded_source_and_reports_warning(self):
        baseline = self.report('baseline')
        blocked = self.root / 'not-a-cache-directory'
        blocked.write_text('keep this file')
        report = self.report('cache-failed', cache=blocked)
        self.assertEqual(self.requests, ['/' + REVISION + '/kernel/demo.c'] * 2)
        self.assertEqual(blocked.read_text(), 'keep this file')
        identifiers = re.findall(r'<script type="application/json" id="([^"]+)">', baseline)
        self.assertEqual(set(identifiers), set(re.findall(r'<script type="application/json" id="([^"]+)">', report)))
        for identifier in identifiers:
            self.assertEqual(self.payload(report, identifier), self.payload(baseline, identifier))
        self.assertIn('return value + 17;', json.dumps(self.payload(report, 'fgSrc')))
        self.assertEqual(self.payload(report, 'tlData')['n'], 1)
        self.assertIn('Warning: source cache', self.last_result.stderr)
        self.assertIn('offline cache reuse is not guaranteed', self.last_result.stderr)
        self.assertNotIn('not found', self.last_result.stdout)

    def test_failed_cache_preserves_source_chunks_in_sidecar_and_auto(self):
        baseline = self.report('baseline')
        blocked = self.root / 'not-a-cache-directory'
        blocked.write_text('keep this file')
        for mode in ('sidecar', 'auto'):
            with self.subTest(mode=mode):
                report = self.report('cache-failed-' + mode, cache=blocked, mode=mode)
                descriptor = self.payload(report, 'fgDataManifest')
                manifest_path = self.last_output / descriptor['manifest']['file']
                payload = manifest_path.read_bytes()
                self.assertEqual(len(payload), descriptor['manifest']['bytes'])
                self.assertEqual(hashlib.sha256(payload).hexdigest(), descriptor['manifest']['sha256'])
                resources = {}
                for identifier, resource in json.loads(payload)['resources'].items():
                    content = (manifest_path.parent / resource['file']).read_bytes()
                    self.assertEqual(len(content), resource['bytes'])
                    self.assertEqual(hashlib.sha256(content).hexdigest(), resource['sha256'])
                    resources[identifier] = content
                self.assertEqual(json.loads(resources['fgSrc0']), self.payload(baseline, 'fgSrc'))
                self.assertEqual(json.loads(resources['tlData']), self.payload(baseline, 'tlData'))
                for identifier in ('fgSrcLines', 'fgErrnoNames', 'fgTimelineIdentity'):
                    self.assertEqual(self.payload(report, identifier), self.payload(baseline, identifier))
                self.assertEqual(self.last_result.stderr.count('Warning: source cache'), 1)
                self.assertEqual(blocked.read_text(), 'keep this file')
        self.assertEqual(self.requests, ['/' + REVISION + '/kernel/demo.c'] * 3)

    def test_verbose_cache_warning_lists_affected_file_without_discarding_source(self):
        blocked = self.root / 'not-a-cache-directory'
        blocked.write_text('keep this file')
        report = self.report('verbose-cache-failed', cache=blocked, verbose=True)
        self.assertIn('return value + 17;', json.dumps(self.payload(report, 'fgSrc')))
        self.assertEqual(self.last_result.stderr.count('Warning: source cache'), 1)
        self.assertEqual(self.last_result.stderr.count('Source cache: kernel/demo.c:'), 1)
        self.assertEqual(blocked.read_text(), 'keep this file')

    def test_cache_and_network_failure_preserve_locations_without_fabricated_code(self):
        blocked = self.root / 'not-a-cache-directory'
        blocked.write_text('keep this file')
        self.available = False
        report = self.report('cache-and-download-failed', cache=blocked)
        self.assertEqual(self.requests, ['/' + REVISION + '/kernel/demo.c'])
        self.assertIn('kernel/demo.c', json.dumps(self.payload(report, 'tlData')['sources']))
        self.assertNotIn('return value + 17;', json.dumps(self.payload(report, 'fgSrc')))
        self.assertIn('Warning: source cache', self.last_result.stderr)
        self.assertIn('1 not found', self.last_result.stdout)
        self.assertEqual(blocked.read_text(), 'keep this file')

    def test_function_entry_source_without_call_site_also_reports_cache_failure(self):
        self.trace = self.root / 'entry.jsonl'
        records = [dict(type='metadata', schema=uftrace.SCHEMA, order='streams', timestamp_unit='ns',
                        depth_basis='address-space', clock='mono',
                        kernel_settings={'trace_clock': 'local [mono] boot'}),
                   dict(type='entry', space='kernel', time_ns=1000, depth=0, name='remote_target',
                        address=self.target_address, tid=42, cpu=0),
                   dict(type='exit', space='kernel', time_ns=2000, depth=0, name='remote_target',
                        address=self.target_address, tid=42, cpu=0),
                   dict(type='end', complete=True)]
        self.trace.write_text('\n'.join(json.dumps(record) for record in records) + '\n')
        blocked = self.root / 'not-a-cache-directory'
        blocked.write_text('keep this file')
        report = self.report('entry-cache-failed', cache=blocked)
        self.assertEqual(self.requests, ['/' + REVISION + '/kernel/demo.c'])
        self.assertIn('return value + 17;', json.dumps(self.payload(report, 'fgSrc')))
        self.assertIn('kernel function entry', json.dumps(self.payload(report, 'fgSrc')))
        self.assertEqual(self.payload(report, 'tlData')['n'], 1)
        self.assertIn('Warning: source cache failed for 1 file;', self.last_result.stderr)
        self.assertEqual(self.last_result.stderr.count('Warning: source cache'), 1)
        self.assertEqual(blocked.read_text(), 'keep this file')

    def test_native_function_without_call_site_fetches_and_embeds_its_entry_source(self):
        self.trace.write_text('# tracer: function_graph\n'
                              ' 0)   1.000 us    |  remote_target(value=5);\n')
        report = self.report('native-entry')
        self.assertEqual(self.requests, ['/' + REVISION + '/kernel/demo.c'])
        sources = self.payload(report, 'fgSrc')
        self.assertEqual(len(sources), 1)
        self.assertIn('kernel function entry', sources[0])
        self.assertIn('return value + 17;', sources[0])
        location = subprocess.check_output(['addr2line', '-e', str(self.vmlinux), self.target_address], text=True).strip()
        number = int(location.rsplit(':', 1)[1])
        self.assertIn('/kernel/demo.c#L' + str(number), sources[0])
        self.assertEqual(self.payload(report, 'fgSrcLines'), {'2': 0})
        timeline = self.payload(report, 'tlData')
        self.assertEqual(timeline['n'], 1)
        self.assertEqual(timeline['sources']['callers'], {})
        self.assertNotIn('0 expandable', report)
        self.assertNotIn('kernel funcs</span>', report)
        self.assertIn('Call-site rows:</div><div class="info-value">0</div>', report)

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
