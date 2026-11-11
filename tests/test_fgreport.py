"""fgreport: presets, the composed funcgraph.py command, output naming, big-trace switch, real run."""
import os
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import helpers

SCRIPT = os.path.join(helpers.ROOT, 'fgreport')


class FgreportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ, FG_CONFIG=os.path.join(self.tmp.name, 'config.ini'))
        self.env.pop('FG_PRESET', None)

    def tearDown(self):
        self.tmp.cleanup()

    def run_fg(self, *args, check=True):
        p = subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True, text=True, env=self.env)
        if check:
            self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def command(self, *args):
        """funcgraph.py arguments fgreport would run (dry run)."""
        line = [x for x in self.run_fg('-n', *args).stderr.splitlines() if 'funcgraph.py' in x][0]
        return line.split('funcgraph.py ', 1)[1].split()

    def test_init_presets_and_default(self):
        vml = os.path.join(self.tmp.name, 'vmlinux')
        open(vml, 'w').close()
        self.run_fg('init', 'a', '--vmlinux', vml, '--kernel-src', self.tmp.name, '--source-rev', 'HEAD', '--default')
        self.run_fg('init', 'b', '--vmlinux', vml, '--cross-compile', 'aarch64-linux-gnu-')
        out = self.run_fg('presets').stdout
        self.assertIn('* a', out)
        self.assertIn('  b', out)
        c = self.command('t.txt')
        self.assertEqual(c[c.index('--preset') + 1], 'a')          # default preset
        self.assertIn('--func-links', c)
        self.assertEqual(c[c.index('--output') + 1], 't.html')
        c = self.command('-p', 'b', 'dir/t.txt.gz')
        self.assertEqual(c[c.index('--preset') + 1], 'b')
        self.assertEqual(c[c.index('--output') + 1], 'dir/t.html')
        self.assertNotIn('--highlight-code', c)                     # preset b has no sources
        c = self.command('-P', '--lite', 't.txt')
        self.assertNotIn('--preset', c)
        self.assertNotIn('--func-links', c)
        p = self.run_fg('-p', 'zz', 't.txt', check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("no preset 'zz'", p.stderr)

    def test_passthrough_and_big_trace(self):
        c = self.command('t.txt', '--', '--pid', '7')
        self.assertEqual(c[-3:], ['--pid', '7', 't.txt'])
        big = os.path.join(self.tmp.name, 'big.txt')
        with open(big, 'w') as f:
            f.write('x\n' * 150001)
        self.assertIn('--no-text-view', self.command(big))
        self.assertNotIn('--no-text-view', self.command('--text', big))

    def test_directory_modes_preserve_text_and_choose_stable_output_names(self):
        big = os.path.join(self.tmp.name, 'big.txt')
        with open(big, 'w') as output:
            output.write('x\n' * 150001)
        for mode in ['auto', 'sidecar']:
            for options in [('--report-mode', mode, '--sidecar-threshold', '100'),
                            ('--', '--report-mode', mode, '--sidecar-threshold', '100')]:
                with self.subTest(mode=mode, options=options):
                    command = self.command(big, *options)
                    self.assertEqual(command[command.index('--output') + 1], os.path.splitext(big)[0] + '.report')
                    self.assertNotIn('--no-text-view', command)
                    self.assertIn('--sidecar-threshold', command)
        command = self.command('--report-mode', 'auto', '--sidecar-threshold', '100', '-')
        self.assertEqual(command[command.index('--output') + 1], 'stdin.report')

    def test_directory_mode_preset_and_explicit_single_override(self):
        self.run_fg('init', 'auto', '--report-mode', 'auto', '--sidecar-threshold', '100', '--default')
        command = self.command('t.txt')
        self.assertEqual(command[command.index('--output') + 1], 't.report')
        command = self.command('t.txt', '--report-mode', 'single')
        self.assertEqual(command[command.index('--output') + 1], 't.html')
        command = self.command('t.txt', '--report-mode', 'sidecar', '--', '--report-mode', 'single')
        self.assertEqual(command[command.index('--output') + 1], 't.html')
        command = self.command('t.txt', '--', '--no-config')
        self.assertEqual(command[command.index('--output') + 1], 't.html')
        alternate = os.path.join(self.tmp.name, 'alternate.ini')
        with open(alternate, 'w') as output:
            output.write('[default]\nreport-mode = single\n[preset auto]\nreport-mode = sidecar\n')
        command = self.command('t.txt', '--', '--config', alternate)
        self.assertEqual(command[command.index('--output') + 1], 't.report')
        command = self.command('-P', 't.txt', '--', '--config', alternate)
        self.assertEqual(command[command.index('--output') + 1], 't.html')

    def test_directory_open_runs_loopback_viewer_instead_of_file_url(self):
        loader = SourceFileLoader('fgreport_test', SCRIPT)
        module = module_from_spec(spec_from_loader(loader.name, loader))
        loader.exec_module(module)
        for mode in ['sidecar', 'auto']:
            with self.subTest(mode=mode), mock.patch.dict(os.environ, self.env, clear=True), \
                    mock.patch.object(module.subprocess, 'call', return_value=0), \
                    mock.patch.object(module.webbrowser, 'open') as offline, \
                    mock.patch('fglib.viewer.main', return_value=0) as viewer:
                result = module.cmd_report(['-P', '--open', 't.txt', '--', '--report-mode', mode,
                                            '--sidecar-threshold', '0', '--output', 'actual report'])
                self.assertEqual(result, 0)
                viewer.assert_called_once_with(['actual report', '--open'])
                offline.assert_not_called()

    def test_real_auto_report_can_keep_text_or_defer_it(self):
        for threshold, external in [('0', True), ('1000000000', False)]:
            with self.subTest(threshold=threshold):
                output = os.path.join(self.tmp.name, 'auto-' + threshold)
                result = self.run_fg('-P', '--report-mode', 'auto', '--sidecar-threshold', threshold,
                                     '-o', output, helpers.fixture('pi4_sched'))
                with open(os.path.join(output, 'index.html'), encoding='utf-8') as entry:
                    html = entry.read()
                self.assertEqual('id="fgDataManifest"' in html, external)
                self.assertIn('Automatic report mode: ' + ('sidecar' if external else 'single'), result.stdout)

    def test_source_browser_preset_and_passthrough(self):
        self.run_fg('init', 'links', '--base-url', 'https://legacy.test/src')
        self.run_fg('init', 'links', '--source-browser', 'github', '--source-browser-rev', 'v7.2', '--default')
        config_path = self.env['FG_CONFIG']
        with open(config_path, encoding='utf-8') as source:
            preset = source.read()
        self.assertIn('source-browser = github', preset)
        self.assertIn('source-browser-rev = v7.2', preset)
        self.assertNotIn('base-url', preset)
        command = self.command('t.txt', '--', '--source-browser', 'elixir', '--source-browser-rev', 'v6.18')
        self.assertEqual(command[command.index('--source-browser') + 1], 'elixir')
        self.assertEqual(command[command.index('--source-browser-rev') + 1], 'v6.18')

    def test_raw_binary_is_not_counted_as_text_lines(self):
        path = os.path.join(self.tmp.name, 'trace.dat')
        with open(path, 'wb') as output:
            output.write(b'\x17\x08Dtracing' + b'\n' * 150001)
        command = self.command(path, '--', '--input-format', 'raw')
        self.assertNotIn('--no-text-view', command)
        self.assertIn('--input-format', command)

    def test_uftrace_directory_does_not_need_positional_trace_or_executable(self):
        capture = os.path.join(self.tmp.name, 'recording.data')
        os.mkdir(capture)
        command = self.command('-P', '--uftrace', capture)
        self.assertEqual(command[command.index('--uftrace') + 1], capture)
        self.assertEqual(command[command.index('--output') + 1], capture + '.html')
        self.assertNotIn('--uftrace-bin', command)
        self.assertNotIn('--no-text-view', command)
        command = self.command('-P', '--uftrace', capture, '--uftrace-bin', '/custom/uftrace')
        self.assertEqual(command[command.index('--uftrace-bin') + 1], '/custom/uftrace')
        for arguments in [[], ['--uftrace', capture, 'trace.txt']]:
            with self.subTest(arguments=arguments):
                result = self.run_fg(*arguments, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('supply trace file(s) or --uftrace', result.stderr)

    def test_real_report(self):
        out = os.path.join(self.tmp.name, 'r.html')
        self.run_fg('-P', '-o', out, helpers.fixture('pi4_sched'))
        with open(out, encoding='utf-8') as f:
            html = f.read()
        self.assertIn('id="tlData"', html)
        self.assertIn('id="filterInput"', html)


if __name__ == '__main__':
    unittest.main()
