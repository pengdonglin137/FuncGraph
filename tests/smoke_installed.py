"""Run with an installed distribution's Python: python -I tests/smoke_installed.py."""
import json
import os
from pathlib import Path
import platform
import queue
import re
import runpy
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from importlib.metadata import distribution

import fglib
from fglib.assets import all_asset_paths, read_asset
from fglib.constants import APP_VERSION, PROJECT_DIR
from fglib.resolver import AddressResolver

ROOT = Path(__file__).resolve().parent.parent
WITH_EXTRAS = '--with-extras' in sys.argv
if WITH_EXTRAS:
    sys.argv.remove('--with-extras')
WITH_RAW = '--with-raw' in sys.argv
if WITH_RAW:
    sys.argv.remove('--with-raw')


class InstalledPackageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = dict(os.environ, FG_CONFIG=os.path.join(self.tmp.name, 'config.ini'))
        self.env.pop('FG_PRESET', None)

    def run_command(self, *command):
        result = subprocess.run(command, cwd=self.tmp.name, env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def executable(self, name):
        command = shutil.which(name, path=sysconfig.get_path('scripts'))
        self.assertIsNotNone(command, name + ' was not installed')
        return command

    def test_metadata_and_resources(self):
        self.assertNotEqual(Path(fglib.__file__).resolve().parent, ROOT / 'fglib')
        self.assertEqual(distribution('funcgraph').version, APP_VERSION)
        for asset in all_asset_paths():
            with self.subTest(asset=asset):
                self.assertTrue(read_asset(asset))
                self.assertEqual(read_asset(asset), (ROOT / 'assets' / asset).read_text(encoding='utf-8'))
        self.assertTrue(Path(PROJECT_DIR, 'fastfaddr2line.py').is_file())
        self.assertTrue(Path(PROJECT_DIR, 'fglib', 'native', 'raw.c').is_file())

    @unittest.skipUnless(os.open in os.supports_dir_fd and hasattr(os, 'O_NOFOLLOW'), 'safe viewer file access unavailable')
    def test_installed_viewer_serves_only_the_selected_report(self):
        report = Path(self.tmp.name, '报告 trace.html')
        content = b'<!doctype html><title>Installed viewer</title><p>trace</p>'
        report.write_bytes(content)
        Path(self.tmp.name, 'private.txt').write_text('must not be served', encoding='utf-8')
        self.assertIn(APP_VERSION, self.run_command(self.executable('fgview'), '--version'))
        process = subprocess.Popen([self.executable('fgview'), str(report)], cwd=self.tmp.name, env=self.env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        lines = queue.Queue()
        reader = threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True)
        reader.start()
        try:
            url = lines.get(timeout=20).strip()
            self.assertTrue(url.startswith('http://127.0.0.1:'), url)
            with urlopen(url, timeout=5) as response:
                self.assertEqual(response.read(), content)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
            with self.assertRaises(HTTPError) as error:
                urlopen(url.rsplit('/', 1)[0] + '/private.txt', timeout=5)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            reader.join(timeout=5)
            process.stdout.close()
            errors = process.stderr.read()
            process.stderr.close()
        self.assertNotIn('Traceback', errors)

    def test_fold_guides_from_installed_package(self):
        trace = Path(self.tmp.name, 'fold.txt')
        trace.write_text('# tracer: function_graph\n'
                         ' 0)               |  parent() { /* <-caller+0x1/0x20 */\n'
                         ' 0)   0.001 us    |    leaf(); /* <-caller+0x1/0x20 */\n'
                         ' 0)   0.010 us    |  } /* parent */\n', encoding='utf-8')
        output = trace.with_suffix('.html')
        self.run_command(self.executable('funcgraph'), '--no-config', '--enable-fold',
                         '--output', str(output), str(trace))
        report = output.read_text(encoding='utf-8')
        self.assertIn('id="foldGuidesBtn"', report)
        self.assertIn('id="viewHelpBtn"', report)
        self.assertIn('data-help-view="compare" hidden', report)
        self.assertIn('id="fgTreeGuides"', report)
        self.assertIn(read_asset('report/tree.css'), report)
        self.assertIn(read_asset('report/report.js'), report)
        self.assertIn('function fgTrackSource(element)', report)
        self.assertIn('new FinalizationRegistry', report)
        self.assertNotIn('linear-gradient', read_asset('report/tree.css'))
        self.assertIn('const strokePixels = Math.max(1, Math.round(scale));', report)
        self.assertIn('data-fold-end="L4"', report)
        self.assertIn('data-fold-start="L2"', report)

    @unittest.skipUnless(WITH_EXTRAS and platform.machine() in ('x86_64', 'aarch64'), 'tail-jump ELF dependencies / architecture unavailable')
    def test_tail_jump_from_installed_package(self):
        from fglib import tailcalls

        self.assertTrue(tailcalls.objdump_command())
        source = Path(self.tmp.name, 'tail.c')
        binary = source.with_suffix('')
        source.write_text('__attribute__((noinline)) int packaged_child(int value) { return value + 1; }\n'
                          '__attribute__((noinline)) int packaged_parent(int value) { return packaged_child(value); }\n'
                          'int main(int argc, char **argv) { return packaged_parent(argc); }\n')
        self.run_command('cc', '-g', '-O2', '-o', str(binary), str(source))
        trace = Path(self.tmp.name, 'tail.txt')
        trace.write_text('# tracer: function_graph\n'
                         ' 0)               |  packaged_parent() { /* <-main+0x1/0x20 */\n'
                         ' 0)   0.001 us    |    packaged_child(); /* <-main+0x1/0x20 */\n'
                         ' 0)   0.010 us    |  } /* packaged_parent */\n')
        output = trace.with_suffix('.html')
        exported = trace.with_suffix('.json')
        self.run_command(self.executable('funcgraph'), '--no-config', '--timeline', '--no-text-view',
                         '--tail-calls', '--fast', '--no-btf', '--vmlinux', str(binary), '--export', str(exported),
                         '--output', str(output), str(trace))
        report = output.read_text()
        model = json.loads(re.search(r'<script[^>]+id="tlData"[^>]*>(.*?)</script>', report, re.S).group(1))
        pair = next(iter(model['tail_calls']['pairs'].values()))
        self.assertEqual(model['n'], 2)
        self.assertEqual(pair['status'], 'supported')
        self.assertTrue(pair['sites'])
        self.assertFalse(model['tail_calls']['trace_build_id_verified'])
        self.assertNotIn('source', pair['sites'][0])
        self.assertEqual(json.loads(exported.read_text())['tail_calls']['pairs'][0]['child'], 'packaged_child')
        self.assertIn('Static sites in supplied binary; actual branch site not captured.', report)

    @unittest.skipUnless(WITH_RAW, 'optional raw dependencies not requested')
    def test_raw_report_from_installed_package(self):
        factory = runpy.run_path(str(ROOT / 'tests' / 'raw_fixture.py'))['container']
        trace = Path(self.tmp.name, 'trace.dat')
        output = trace.with_suffix('.html')
        trace.write_bytes(factory(sched=True, retaddr=True))
        self.run_command(self.executable('funcgraph'), '--no-config', '--timeline',
                         '--output', str(output), str(trace))
        report = output.read_text()
        match = re.search(r'<script[^>]+id="tlData"[^>]*>(.*?)</script>', report, re.S)
        self.assertIsNotNone(match)
        data = json.loads(match.group(1))
        self.assertEqual(data['n'], 2)
        self.assertIn('2 recorded calls</span>', report)
        self.assertRegex(report, r'data-view="timeline"[^>]*>Timeline</button>')
        self.assertTrue(data['caps']['sched_events'])
        self.assertTrue(data['caps']['retaddr'])

    def test_command_help(self):
        for command in ('funcgraph', 'fgreport', 'fgcapture.sh'):
            with self.subTest(command=command):
                self.assertTrue(self.run_command(self.executable(command), '--help'))
        self.run_command(sys.executable, '-I', '-m', 'funcgraph', '--help')

    def test_report_and_compare(self):
        output = Path(self.tmp.name, 'report.html')
        exported = Path(self.tmp.name, 'analysis.json')
        trace = str(ROOT / 'tests' / 'data' / 'pi4_sched.txt')
        self.run_command(self.executable('funcgraph'), '--no-config', '--timeline',
                         '--enable-fold', '--filter', '--compare', trace,
                         '--export', str(exported), '--output', str(output), trace)
        report = output.read_text(encoding='utf-8')
        for data_id in ('tlData', 'cmpData'):
            match = re.search(r'<script type="application/json" id="' + data_id + r'">(.*?)</script>',
                              report, re.S)
            self.assertIsNotNone(match, data_id)
            self.assertTrue(json.loads(match.group(1)))
        self.assertIn('id="filterInput"', report)
        self.assertTrue(json.loads(exported.read_text(encoding='utf-8'))['functions'])

    def test_fgreport(self):
        output = Path(self.tmp.name, 'wrapper.html')
        self.run_command(self.executable('fgreport'), '-P', '-o', str(output),
                         str(ROOT / 'tests' / 'data' / 'pi4_sched.txt'))
        self.assertIn('id="tlData"', output.read_text(encoding='utf-8'))

    @unittest.skipUnless(WITH_EXTRAS, 'run with --with-extras after installing funcgraph[all]')
    def test_fast_resolver_and_highlighting(self):
        import pygments

        self.assertTrue(pygments.__version__)
        self.run_command(self.executable('fastfaddr2line'), '--help')
        source = Path(self.tmp.name, 'probe.c')
        binary = Path(self.tmp.name, 'probe')
        source.write_text('int packaged_probe(void) { return 7; }\n'
                          'int main(void) { return packaged_probe(); }\n', encoding='utf-8')
        self.run_command('cc', '-g', '-O0', '-o', str(binary), str(source))
        resolver = AddressResolver(os.path.join(PROJECT_DIR, 'fastfaddr2line.py'), str(binary))
        locations = resolver.resolve_batch(['packaged_probe+0x0/0x1'], use_list=True)
        self.assertTrue(locations)
        self.assertIn('probe.c:', '\n'.join(locations.values()))
        trace = Path(self.tmp.name, 'source-trace.txt')
        trace.write_text('# tracer: function_graph\n'
                         '1.000000001 |  0) probe-42 |  0.010 us |  packaged_probe(); '
                         '/* <-packaged_probe+0x1/0x20 */\n')
        output = trace.with_suffix('.html')
        self.run_command(self.executable('funcgraph'), '--no-config', '--timeline', '--no-text-view',
                         '--keep-source', '--fast', '--vmlinux', str(binary),
                         '--kernel-src', self.tmp.name, '--output', str(output), str(trace))
        report = output.read_text()
        self.assertNotIn('id="L2"', report)
        for name in ['fgSrc', 'fgSrcLines']:
            match = re.search(r'<script type="application/json" id="' + name + r'">(.*?)</script>', report, re.S)
            self.assertIsNotNone(match)
            self.assertTrue(json.loads(match.group(1)))
        source_block = json.loads(re.search(
            r'<script type="application/json" id="fgSrc">(.*?)</script>', report, re.S).group(1))[0]
        self.assertIn('probe.c', source_block)
        if shutil.which('gawk'):
            self.assertIn('source-line', source_block)


if __name__ == '__main__':
    unittest.main(verbosity=2)
