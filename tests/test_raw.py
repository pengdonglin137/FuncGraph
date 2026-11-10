import bz2
import gzip
import io
import json
import lzma
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import helpers
from fglib import inputs, prefilter, raw, timeline
from fglib.cli import build_parser
from fglib.util import format_args_info
from raw_fixture import container


@unittest.skipUnless(shutil.which('bash'), 'bash not installed')
class RawLibraryBuildTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='fg-raw-build-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.build = self.root / 'build.sh'
        shutil.copyfile(Path(helpers.ROOT) / 'third_party' / 'build.sh', self.build)
        for library in ['libtraceevent', 'libtracefs', 'trace-cmd']:
            (self.root / library).mkdir()
            (self.root / library / 'Makefile').touch()
        tools = self.root / 'tools'
        tools.mkdir()
        self.log = self.root / 'calls.jsonl'
        tool = '#!' + sys.executable + '''
import json, os, pathlib, sys
with open(os.environ['BUILD_CALLS'], 'a') as stream:
    stream.write(json.dumps([pathlib.Path(sys.argv[0]).name, sys.argv[1:]]) + '\\n')
if pathlib.Path(sys.argv[0]).name == 'cc':
    sys.exit(int(os.environ.get('FAIL_HEADERS', '0')))
settings = dict(argument.split('=', 1) for argument in sys.argv[1:] if '=' in argument)
actual = settings['libdir']
metadata = actual if pathlib.Path(sys.argv[2]).name == 'trace-cmd' else settings['prefix'] + '/' + settings.get('libdir_relative', 'lib64')
if actual != metadata:
    sys.exit('pkg-config library directory does not match installed libraries')
'''
        for name in ['cc', 'make']:
            path = tools / name
            path.write_text(tool)
            path.chmod(0o755)
        self.env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ['PATH'],
                        CC='cc', BUILD_CALLS=str(self.log), JOBS='3', EXTRA_CFLAGS='-I/custom/uapi')

    def run_build(self, prefix=None, **environment):
        return subprocess.run(['bash', str(self.build), prefix or str(self.root / 'raw prefix')],
                              env=dict(self.env, **environment), capture_output=True, text=True)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_private_lib_directory_agrees_with_pkgconfig_for_every_build_and_install(self):
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 7)
        self.assertEqual(calls[0], ['cc', ['-I/custom/uapi', '-x', 'c', '-', '-fsyntax-only']])
        for index, (name, arguments) in enumerate(calls[1:]):
            self.assertEqual(name, 'make')
            self.assertEqual(arguments[:2], ['-C', str(self.root / ['libtraceevent', 'libtracefs', 'trace-cmd'][index // 2])])
            self.assertIn('prefix=' + str(self.root / 'raw prefix'), arguments)
            self.assertIn('libdir=' + str(self.root / 'raw prefix' / 'lib'), arguments)
            self.assertIn('libdir_relative=lib', arguments)
            self.assertIn('pkgconfig_dir=' + str(self.root / 'raw prefix' / 'lib' / 'pkgconfig'), arguments)
            self.assertIn('NO_PYTHON=1', arguments)
            self.assertEqual(arguments[-1], 'install_libs' if index % 2 else 'libs')
            self.assertIn('DESTDIR=/' if index % 2 else '-j3', arguments)
            if index % 2:
                self.assertEqual(arguments[2:4], ['-W', ['libtraceevent', 'libtracefs', 'libtracecmd'][index // 2] + '.pc.template'])

    def test_old_headers_fail_before_building_or_installing(self):
        result = self.run_build(FAIL_HEADERS='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Recent Linux UAPI headers', result.stderr)
        self.assertEqual([name for name, arguments in self.calls()], ['cc'])

    def test_missing_submodule_and_relative_prefix_do_not_invoke_tools(self):
        (self.root / 'libtracefs' / 'Makefile').unlink()
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing submodules', result.stderr)
        result = self.run_build(prefix='relative-prefix')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('prefix must be absolute', result.stderr)
        self.assertEqual(self.calls(), [])


class RawInputTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='fg-raw-test-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'trace.dat'

    def test_detection_by_magic_not_extension(self):
        self.path.write_bytes(raw.MAGIC + b'\0payload')
        expected = ['# tracer: function_graph', '1.000000001 |  0) task-42 |  f();']
        with mock.patch.object(raw, 'read_lines', return_value=expected) as decoder:
            self.assertIs(inputs.read_lines([str(self.path)])[0], expected)
            decoder.assert_called_once_with(str(self.path), instance=None)

    def test_raw_read_text_retains_string_api_and_empty_rows(self):
        self.path.write_bytes(raw.MAGIC + b'\0payload')
        expected = ['# tracer: function_graph', '', '# 函数调用', '1.000000001 |  0) task-42 |  f();', '']
        original = expected[:]
        with mock.patch.object(raw, 'read_lines', return_value=expected):
            self.assertEqual(inputs.read_text(str(self.path)), '\n'.join(expected) + '\n')
            self.assertIs(inputs.read_lines([str(self.path)])[0], expected)
        self.assertEqual(expected, original)

    def test_text_named_dat_needs_no_optional_libraries(self):
        self.path.write_text('# tracer: function_graph\n 0) task-42 |  root();\n')
        with mock.patch.object(raw, '_helper', side_effect=AssertionError('must not compile')):
            self.assertIn('root();', inputs.read_text(str(self.path)))

    def test_default_input_options_do_not_change_text_report_metadata(self):
        parser = build_parser()
        info = dict(format_args_info(parser.parse_args(['trace.txt'])))
        self.assertNotIn('Input Format', info)
        self.assertNotIn('Raw Instance', info)
        explicit = dict(format_args_info(parser.parse_args(
            ['trace.dat', '--input-format', 'raw', '--raw-instance', 'capture'])))
        self.assertEqual(explicit['Input Format'], 'raw')
        self.assertEqual(explicit['Raw Instance'], 'capture')

    def test_unknown_binary_and_explicit_mismatch_fail(self):
        for content, mode in [(b'\0raw page', 'auto'), (b'text trace', 'raw'),
                              (raw.MAGIC, 'text')]:
            with self.subTest(mode=mode):
                self.path.write_bytes(content)
                with self.assertRaises(raw.RawInputError):
                    inputs.read_text(str(self.path), mode)

    def test_compressed_and_stdin_raw_spool_to_real_file(self):
        data = raw.MAGIC + b'\0payload'
        expected = ['# decoded']
        temporary_paths = []
        def decode(path, instance=None):
            self.assertEqual(Path(path).read_bytes(), data)
            self.assertEqual(instance, 'named')
            temporary_paths.append(path)
            return expected
        with mock.patch.object(raw, 'read_lines', side_effect=decode):
            for suffix, compress in [('.gz', gzip.compress), ('.xz', lzma.compress), ('.lzma', lzma.compress),
                                     ('.bz2', bz2.compress)]:
                path = str(self.path) + suffix
                Path(path).write_bytes(compress(data))
                self.assertEqual(inputs.read_text(path, raw_instance='named'), '# decoded\n')
                self.assertIs(inputs.read_lines([path], raw_instance='named')[0], expected)
            for reader, result in [(inputs.read_text, '# decoded\n'),
                                   (lambda path, **options: inputs.read_lines([path], **options)[0], expected)]:
                with io.TextIOWrapper(io.BytesIO(data)) as stream:
                    with mock.patch.object(sys, 'stdin', stream):
                        actual = reader('-', raw_instance='named')
                    self.assertEqual(actual, result)
                    if isinstance(actual, list):
                        self.assertIs(actual, expected)
                    self.assertFalse(stream.closed)
        self.assertTrue(all(not Path(path).exists() for path in temporary_paths))

    def test_raw_text_merge_reuses_rows_and_preserves_ties_and_notes(self):
        self.path.write_bytes(raw.MAGIC + b'\0payload')
        expected = ['# tracer: function_graph', '1.000000101 |  0) raw-42 |  first();',
                    '# recorded note', '1.000000301 |  0) raw-42 |  tie();']
        original = expected[:]
        text = self.path.with_name('trace.txt')
        text_rows = ['1.000000200 |  1) text-43 |  between();',
                     '1.000000301 |  1) text-43 |  tie();']
        text.write_text('\n'.join(text_rows) + '\n')
        with mock.patch.object(raw, 'read_lines', return_value=expected), mock.patch.object(
                inputs, 'split_lines', wraps=inputs.split_lines) as splitter:
            lines, note = inputs.read_lines([str(self.path), str(text)])
        self.assertEqual(lines, expected[:3] + text_rows[:1] + expected[3:] + text_rows[1:])
        self.assertEqual(note, 'merged 2 inputs by timestamp')
        splitter.assert_called_once()
        self.assertEqual(expected, original)

    def test_text_representation_preserves_newlines_and_text_only_stdin(self):
        for data in [b'', b'\n', b'row', b'row\n\n', b'row\r\n\r\n', b'row\rnext\r', b'bad\xff\n']:
            with self.subTest(data=data), io.TextIOWrapper(io.BytesIO(data), errors='replace') as source:
                expected = source.read()
                self.path.write_bytes(data)
                self.assertEqual(inputs.read_text(str(self.path)), expected)
                self.assertEqual(inputs.read_lines([str(self.path)])[0], inputs.split_lines(expected))
                with mock.patch.object(sys, 'stdin', io.StringIO(expected)):
                    self.assertEqual(inputs.read_text('-'), expected)
                with mock.patch.object(sys, 'stdin', io.StringIO(expected)):
                    self.assertEqual(inputs.read_lines(['-'])[0], inputs.split_lines(expected))

    def test_missing_dependencies_actionable(self):
        with mock.patch.object(raw, '_executable', None), mock.patch.object(
                subprocess, 'run', side_effect=FileNotFoundError('pkg-config')):
            with self.assertRaisesRegex(raw.RawInputError, 'development libraries'):
                raw._helper()

    def test_decoder_failure_does_not_return_partial_lines(self):
        executable = Path(self.directory.name) / 'decode'
        executable.write_text('#!/bin/sh\nprintf "partial report\\n"\nprintf "corrupt container\\n" >&2\nexit 1\n')
        executable.chmod(0o755)
        with mock.patch.object(raw, '_helper', return_value=str(executable)):
            with self.assertRaisesRegex(raw.RawInputError, 'corrupt container'):
                raw.read_lines(str(self.path))

    def test_nanosecond_merge_and_model(self):
        self.path.write_text('1.000000100 |  0) task-42 |  root() {\n'
                             '1.000000300 |  0) task-42 |   0.200 us |  } /* root */\n')
        other = self.path.with_name('second.txt')
        other.write_text('1.000000 |  1) second-43 |   0.050 us |  child();\n')
        lines, note = inputs.read_lines([str(self.path), str(other)])
        self.assertIn('merged', note)
        self.assertTrue(lines[0].startswith('1.000000 |'))
        data = timeline.build_timeline(lines).to_data()
        self.assertEqual(data['mode'], 'abs')
        self.assertEqual(data['n'], 2)
        self.assertIn(200, data['cols']['dur'])
        self.assertEqual(prefilter.parse_time('2003249.691500001'), 2003249691500001)


class NativeRawTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            raw._helper()
        except raw.RawInputError as error:
            if os.environ.get('FG_RAW_REQUIRED'):
                raise
            raise unittest.SkipTest(str(error))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='fg-native-raw-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'trace.dat'

    def decode(self, **options):
        self.path.write_bytes(container(**options))
        return inputs.read_lines([str(self.path)])[0]

    def test_raw_sidecar_preserves_the_single_file_timeline(self):
        self.path.write_bytes(container(sched=True, retaddr=True))
        single = Path(self.directory.name) / 'single.html'
        sidecar = Path(self.directory.name) / 'sidecar'
        offline = Path(self.directory.name) / 'auto-offline'
        automatic = Path(self.directory.name) / 'auto-sidecar'
        for output, mode, extra in [(single, 'single', []), (sidecar, 'sidecar', []),
                                    (offline, 'auto', ['--sidecar-threshold', '1000000000']),
                                    (automatic, 'auto', ['--sidecar-threshold', '0'])]:
            result = subprocess.run([sys.executable, str(Path(helpers.ROOT) / 'funcgraph.py'), '--no-config',
                                     '--timeline', '--report-mode', mode, *extra, '--output', str(output), str(self.path)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        descriptor = helpers.json_script((sidecar / 'index.html').read_text(), 'fgDataManifest')
        manifest_path = sidecar / descriptor['manifest']['file']
        manifest = json.loads(manifest_path.read_text())
        resource = manifest_path.parent / manifest['resources']['tlData']['file']
        actual = json.loads(resource.read_text())
        expected = helpers.json_script(single.read_text(), 'tlData')
        actual['stats'].pop('build_time', None)
        expected['stats'].pop('build_time', None)
        self.assertEqual(actual, expected)
        self.assertIn('fgText', manifest['resources'])

        for output in [sidecar, automatic]:
            with self.subTest(source_metadata=output.name):
                report = (output / 'index.html').read_text()
                descriptor = helpers.json_script(report, 'fgDataManifest')
                manifest_path = output / descriptor['manifest']['file']
                manifest = json.loads(manifest_path.read_text())
                index = json.loads((manifest_path.parent / manifest['resources']['fgTextIndex']['file']).read_text())
                self.assertEqual(index['version'], 2)
                self.assertTrue(all(len(row) == len(index['fields']) + 3 for row in index['rows']))
                sources = {str(row[0]): row[-1] for row in index['rows'] if row[-1] is not None}
                self.assertEqual(sources, {})
                self.assertEqual(sources, helpers.json_script(report, 'fgSrcLines'))
                pool = helpers.json_script(report, 'fgSrc')
                self.assertEqual(pool, [])
        embedded = helpers.json_script((offline / 'index.html').read_text(), 'tlData')
        embedded['stats'].pop('build_time', None)
        self.assertEqual(embedded, expected)
        descriptor = helpers.json_script((automatic / 'index.html').read_text(), 'fgDataManifest')
        manifest_path = automatic / descriptor['manifest']['file']
        manifest = json.loads(manifest_path.read_text())
        resource = manifest_path.parent / manifest['resources']['tlData']['file']
        actual = json.loads(resource.read_text())
        actual['stats'].pop('build_time', None)
        self.assertEqual(actual, expected)
        self.assertIn('fgText', manifest['resources'])

    def test_raw_source_index_preserves_resolved_source_pool_identities(self):
        self.path.write_bytes(container(sched=True, retaddr=True))
        kernel = helpers.FakeKernel()
        self.addCleanup(kernel.close)
        report, _ = kernel.run(str(self.path), '--timeline', '--func-links', '--report-mode', 'sidecar', out='resolved')
        output = Path(kernel.dir) / 'resolved'
        descriptor = helpers.json_script(report, 'fgDataManifest')
        manifest_path = output / descriptor['manifest']['file']
        manifest = json.loads(manifest_path.read_text())
        index = json.loads((manifest_path.parent / manifest['resources']['fgTextIndex']['file']).read_text())
        self.assertEqual(index['version'], 2)
        sources = {str(row[0]): row[-1] for row in index['rows'] if row[-1] is not None}
        self.assertTrue(sources)
        self.assertIn(0, sources.values())
        self.assertEqual(sources, helpers.json_script(report, 'fgSrcLines'))
        pool = helpers.json_script(report, 'fgSrc')
        self.assertTrue(all(isinstance(pool[source], str) for source in sources.values()))

    def test_endian_and_word_size(self):
        for endian in ('<', '>'):
            for width in (4, 8):
                with self.subTest(endian=endian, width=width):
                    lines = self.decode(endian=endian, width=width)
                    data = timeline.build_timeline(lines).to_data()
                    self.assertEqual(data['n'], 2)
                    self.assertEqual(data['cols']['dur'], [513, 209])
                    self.assertTrue(data['caps']['args'])
                    self.assertTrue(data['caps']['retval'])
                    self.assertIn('arg0=0x123, arg1=0x456', '\n'.join(lines))
                    self.assertIn('ret=0x' + format((1 << (width * 8)) - 2, 'x'), '\n'.join(lines))

    def test_raw_retaddr_variant(self):
        lines = self.decode(retaddr=True)
        self.assertIn('/* <-root+0x8/0x100 */', '\n'.join(lines))
        self.assertTrue(timeline.build_timeline(lines).to_data()['caps']['retaddr'])

    def test_module_symbol_ownership(self):
        lines = self.decode(module=True)
        self.assertIn('child [demo_mod](', '\n'.join(lines))
        data = timeline.build_timeline(lines).to_data()
        self.assertIn('demo_mod', data['strings'])

    def test_old_kernel_without_args_or_retval(self):
        data = timeline.build_timeline(self.decode(args=False, retval=False)).to_data()
        self.assertEqual(data['n'], 2)
        self.assertFalse(data['caps']['args'])
        self.assertFalse(data['caps']['retval'])

    def test_dynamic_argument_arrays(self):
        for width in (4, 8):
            with self.subTest(width=width):
                lines = self.decode(width=width, dynamic_args=True)
                self.assertIn('arg0=0x123, arg1=0x456', '\n'.join(lines))

    def test_scheduling_uses_exact_switch_and_wakeup_times(self):
        for endian in ('<', '>'):
            with self.subTest(endian=endian):
                data = timeline.build_timeline(self.decode(sched=True, endian=endian)).to_data()
                self.assertTrue(data['caps']['sched_events'])
                self.assertEqual(data['n'], 2)
                self.assertTrue(any(record[1:3] == [200, 250] for record in data['offcpu']))
                self.assertEqual(len(data['wakeups']), 1)
                self.assertEqual(data['wakeups'][0][1], 225)
                self.assertIn('D', data['strings'])

    def test_prefilter_balances_nanosecond_call_window(self):
        lines = self.decode()
        filtered, _ = prefilter.apply(lines, prefilter.Filter(
            pids=[42], graph=['child'], time_range='1.000000000:1.000000200'))
        data = timeline.build_timeline(filtered).to_data()
        self.assertEqual(data['n'], 1)
        self.assertEqual(data['cols']['dur'], [209])
        self.assertEqual(data['stats']['truncated'], 0)

    def test_loss_counts_and_unknown(self):
        for count in (3, -1):
            with self.subTest(count=count):
                data = timeline.build_timeline(self.decode(lost=count)).to_data()
                self.assertTrue(data['lost'])
                self.assertEqual(data['lost'][0][1], 3 if count == 3 else None)

    def test_unresolved_symbols_still_visible(self):
        lines = self.decode(symbols=False, args=False)
        self.assertIn('unresolved_0x1000() {', '\n'.join(lines))
        self.assertEqual(timeline.build_timeline(lines).to_data()['n'], 2)

    def test_bad_clock_and_depth_rejected(self):
        for options, message in [({'clock': 'counter'}, 'clock'),
                                 ({'bad_depth': True}, 'event fields')]:
            with self.subTest(options=options):
                with self.assertRaisesRegex(raw.RawInputError, message):
                    self.decode(**options)

    def test_truncated_container_rejected(self):
        self.path.write_bytes(container()[:-4000])
        with self.assertRaises(raw.RawInputError):
            raw.read_lines(str(self.path))

    def test_named_instance_missing_is_actionable(self):
        self.path.write_bytes(container())
        with self.assertRaisesRegex(raw.RawInputError, 'raw instance not found: absent'):
            inputs.read_lines([str(self.path)], raw_instance='absent')

    def test_actual_compressed_container_and_stdin(self):
        original = container()
        expected = None
        for suffix, compress in [('.gz', gzip.compress), ('.xz', lzma.compress),
                                 ('.bz2', bz2.compress)]:
            path = str(self.path) + suffix
            Path(path).write_bytes(compress(original))
            lines = inputs.read_lines([path])[0]
            if expected is None:
                expected = lines
            self.assertEqual(lines, expected)
        with mock.patch.object(sys, 'stdin', io.TextIOWrapper(io.BytesIO(original))):
            self.assertEqual(inputs.read_lines(['-'])[0], expected)

    def test_cli_decode_error_preserves_old_report(self):
        valid = self.path.with_name('valid.dat')
        valid.write_bytes(container())
        self.path.write_bytes(container()[:-4000])
        output = self.path.with_suffix('.html')
        for arguments in [[str(self.path)], [str(valid), '--compare', str(self.path)]]:
            with self.subTest(arguments=arguments):
                output.write_text('previous valid report')
                result = subprocess.run([sys.executable, os.path.join(helpers.ROOT, 'funcgraph.py'),
                                         '--no-config', '--timeline', '--output', str(output)] + arguments,
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Error: cannot read', result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                self.assertEqual(output.read_text(), 'previous valid report')

    def test_cli_raw_compare_and_export(self):
        self.path.write_bytes(container())
        output = self.path.with_suffix('.html')
        exported = self.path.with_suffix('.json')
        result = subprocess.run([sys.executable, os.path.join(helpers.ROOT, 'funcgraph.py'),
                                 '--no-config', '--timeline', '--enable-fold', '--compare', str(self.path),
                                 '--export', str(exported), '--output', str(output), str(self.path)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = output.read_text()
        self.assertEqual(helpers.json_script(report, 'tlData')['n'], 2)
        self.assertEqual(len(helpers.json_script(report, 'cmpData')['rows']), 2)
        self.assertTrue(exported.is_file())
