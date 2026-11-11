"""Recorded user DWARF parameter names, ABI matching and report presentation."""
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import unittest

import helpers
from fglib import inputs, uftrace
from fglib.viewer import create_server
from test_uftrace_export import AVAILABLE, COMPILE_FLAGS, EXECUTABLE, RECORD_FLAGS
from test_uftrace_import import capture, stream


@unittest.skipUnless(AVAILABLE, 'patched uftrace and gcc are required')
class NativeArgumentNamesTest(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix='fg-argument-names-')
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.capture = self.root / 'capture'
        self.sequence = 0

    def compile(self, source, *options, name='workload'):
        source_path = self.root / (name + '.c')
        source_path.write_text(source)
        binary = source_path.with_suffix('')
        subprocess.run(['gcc', *COMPILE_FLAGS, '-g', '-O0', *options, '-o', str(binary), str(source_path)],
                       check=True, capture_output=True)
        return binary

    def record(self, binary, *options, automatic=True):
        self.sequence += 1
        self.capture = self.root / ('capture-' + str(self.sequence))
        libraries = EXECUTABLE.parent / 'libmcount'
        library_options = ['--libmcount-path', str(libraries)] if libraries.is_dir() else []
        auto_options = ['--auto-args'] if automatic else []
        subprocess.run([str(EXECUTABLE), 'record', '--no-sched', '--no-libcall', *auto_options, *library_options,
                        *RECORD_FLAGS, *options, '-d', str(self.capture), str(binary)],
                       check=True, capture_output=True)
        return self.entries()

    def entries(self):
        result = subprocess.run([str(EXECUTABLE), 'dump', '--fgraph', '--srcline', '-d', str(self.capture)],
                                check=True, capture_output=True, text=True)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        inputs.UftraceInput(io.StringIO(result.stdout)).build(enable_fold=True)
        return [record for record in records if record.get('name', '').startswith('child') and record['type'] == 'entry']

    def debug_files(self):
        return sorted({path.resolve() for path in self.capture.glob('*.dbg')})

    def test_names_follow_recorded_integer_locations_across_pie_and_optimization(self):
        source = ('__attribute__((noinline, noclone)) long child(long first, long second, long third, '
                  'long fourth, long fifth, long sixth, long seventh, long eighth) '
                  '{ return first + second + third + fourth + fifth + sixth + seventh + eighth; }\n'
                  'int main(void) { return child(-1, 2, -3, 4, -5, 6, -7, 8) != 4; }\n')
        expected = ['first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth']
        for optimization in ('-O0', '-O2'):
            for pie in ('-pie', '-no-pie'):
                with self.subTest(optimization=optimization, pie=pie):
                    binary = self.compile(source, optimization, pie)
                    entry = self.record(binary)[0]
                    self.assertEqual(entry['user_args'], ['-1', '2', '-3', '4', '-5', '6', '-7', '8'])
                    self.assertEqual(entry['user_arg_names'], expected)
                    self.assertTrue(entry['payload_hex'])

    def test_explicit_subset_and_narrower_values_keep_abi_names(self):
        binary = self.compile('__attribute__((noinline)) long child(long first, long second, long third) '
                              '{ return first + second + third; }\n'
                              'int main(void) { return child(257, 2, 3) != 262; }\n')
        entry = self.record(binary, '-A', 'child@arg1/u8,arg3/i32')[0]
        self.assertEqual(entry['user_args'], ['1', '3'])
        self.assertEqual(entry['user_arg_names'], ['first', 'third'])

    def test_integer_names_do_not_shift_past_floating_point_parameters(self):
        binary = self.compile('__attribute__((noinline)) long child(long first, double scale, long count) '
                              '{ return first + (long)scale + count; }\n'
                              'int main(void) { return child(1, 2.0, 3) != 6; }\n')
        entry = self.record(binary, '-A', 'child@arg1/i64,arg2/i64')[0]
        self.assertEqual(entry['user_args'], ['1', '3'])
        self.assertEqual(entry['user_arg_names'], ['first', 'count'])

    def test_explicit_capture_also_saves_dwarf_parameter_names(self):
        binary = self.compile('long child(long count) { return count; }\n'
                              'int main(void) { return child(1) != 1; }\n')
        entry = self.record(binary, '-A', 'child@arg1/i64', automatic=False)[0]
        self.assertEqual(entry['user_args'], ['1'])
        self.assertEqual(entry['user_arg_names'], ['count'])

    def test_unmatched_explicit_locations_remain_unnamed(self):
        binary = self.compile('long child(long first, long second) { return first + second; }\n'
                              'int main(void) { return child(1, 2) != 3; }\n')
        entry = self.record(binary, '-A', 'child@arg1/i64%stack+1,arg2/i64')[0]
        self.assertEqual(entry['user_arg_names'], [None, 'second'])

    def test_stripped_dwarf_does_not_fall_back_to_guessed_names(self):
        binary = self.compile('long child(long count) { return count; }\n'
                              'int main(void) { return child(1) != 1; }\n', '-g0')
        entry = self.record(binary, '-A', 'child@arg1/i64')[0]
        self.assertEqual(entry['user_args'], ['1'])
        self.assertNotIn('user_arg_names', entry)

    def test_optimized_clones_do_not_reuse_the_source_signature(self):
        binary = self.compile('static __attribute__((noinline)) long child(long fixed, long count) '
                              '{ return fixed + count; }\n'
                              'int main(int count, char **unused) { return child(7, count) != 8; }\n', '-O2')
        entries = self.record(binary)
        clones = [entry for entry in entries if '.constprop.' in entry['name'] or '.isra.' in entry['name']]
        if not clones:
            self.skipTest('compiler did not emit a signature-specialized clone')
        for entry in clones:
            self.assertNotIn('user_arg_names', entry)

    def test_names_survive_removal_of_original_executable_and_source(self):
        binary = self.compile('long child(long count) { return count + 1; }\n'
                              'int main(void) { return child(2) != 3; }\n')
        expected = self.record(binary)[0]
        self.assertEqual(expected['user_arg_names'], ['count'])
        binary.unlink()
        binary.with_suffix('.c').unlink()
        entry = self.entries()[0]
        self.assertEqual(entry['user_arg_names'], expected['user_arg_names'])
        self.assertEqual(entry['user_args'], expected['user_args'])
        self.assertEqual(entry['payload_hex'], expected['payload_hex'])

    def test_legacy_debug_metadata_retains_values_without_guessing_names(self):
        binary = self.compile('long child(long count) { return count; }\n'
                              'int main(void) { return child(1) != 1; }\n')
        entry = self.record(binary)[0]
        self.assertEqual(entry['user_arg_names'], ['count'])
        for path in self.debug_files():
            path.write_text(re.sub(r'^# fgraph-param: .*\n', '', path.read_text(), flags=re.M))
        entry = self.entries()[0]
        self.assertEqual(entry['user_args'], ['1'])
        self.assertNotIn('user_arg_names', entry)

    def test_conflicting_or_malformed_metadata_does_not_invent_names(self):
        binary = self.compile('long child(long count) { return count; }\n'
                              'int main(void) { return child(1) != 1; }\n')
        self.record(binary)
        for path in self.debug_files():
            content = path.read_text()
            content = re.sub(r'^(# fgraph-param: .* )count$',
                             r'\g<0>\n\1conflicting_name', content, flags=re.M)
            path.write_text(content + '# fgraph-param: -1 1 0 0 0 0 0 0 0 invalid\n')
        entry = self.entries()[0]
        self.assertEqual(entry['user_args'], ['1'])
        self.assertNotIn('user_arg_names', entry)

    def test_same_named_functions_in_dlopen_libraries_keep_separate_parameter_names(self):
        for name, parameter in (('left', 'left_count'), ('right', 'right_count')):
            self.compile('long child(long ' + parameter + ') { return ' + parameter + ' + 1; }\n',
                         '-fPIC', '-shared', name=name + '.so')
        binary = self.compile('#include <dlfcn.h>\nint main(void) {\n'
                              'void *left = dlopen("' + str(self.root / 'left.so') + '", RTLD_NOW);\n'
                              'void *right = dlopen("' + str(self.root / 'right.so') + '", RTLD_NOW);\n'
                              'long (*first)(long) = dlsym(left, "child");\n'
                              'long (*second)(long) = dlsym(right, "child");\n'
                              'return first(1) + second(2) != 5; }\n', '-ldl')
        entries = self.record(binary)
        self.assertEqual([entry['user_args'] for entry in entries], [['1'], ['2']])
        self.assertEqual([entry['user_arg_names'] for entry in entries], [['left_count'], ['right_count']])


class ArgumentNamesImportTest(unittest.TestCase):
    def records(self, names=('count', 'flags')):
        records = capture()
        records[1].update(user_args=['3', '0xffffffffffffffff'], payload_hex='03000000ffffffffffffffff')
        if names is not None:
            records[1]['user_arg_names'] = list(names)
        return records

    def test_named_arguments_reach_text_and_timeline_without_changing_raw_bytes(self):
        builder = inputs.UftraceInput(io.StringIO(stream(self.records()))).build(enable_fold=True)
        data = builder.to_data()
        line = data['cols']['line'][1]
        detail = data['uftrace']['user_details'][line]
        self.assertEqual(detail['args'], 'count=3, flags=0xffffffffffffffff')
        self.assertEqual(detail['arg_names'], ('count', 'flags'))
        self.assertEqual(data['strings'][data['cols']['args'][1]], '03000000ffffffffffffffff')
        self.assertIn('count=3, flags=0xffffffffffffffff', builder.parsed[line - 1].raw_line)

    def test_partial_and_legacy_names_are_explicit(self):
        for names, expected in (((None, 'flags'), 'slot1=3, flags=0xffffffffffffffff'),
                                (None, '3, 0xffffffffffffffff')):
            with self.subTest(names=names):
                builder = inputs.UftraceInput(io.StringIO(stream(self.records(names)))).build()
                detail = next(value for value in builder.to_data()['uftrace']['user_details'].values()
                              if 'args' in value)
                self.assertEqual(detail['args'], expected)

    def test_invalid_names_fail_at_the_jsonl_boundary(self):
        for names in ([], ['count'], ['count', 'flags', 'extra'], [None, None], ['', 'flags'],
                      [False, 'flags'], [1, 'flags'], ['count\n', 'flags'], ['count\x7f', 'flags'],
                      ['x' * 1025, 'flags'], 'count'):
            with self.subTest(names=names):
                records = self.records()
                records[1]['user_arg_names'] = names
                with self.assertRaisesRegex(uftrace.UftraceInputError, 'user argument names'):
                    inputs.UftraceInput(io.StringIO(stream(records))).build()
        for kind, space in (('exit', 'user'), ('entry', 'kernel')):
            records = self.records()
            records[1].update(type=kind, space=space)
            with self.subTest(kind=kind, space=space), self.assertRaises(uftrace.UftraceInputError):
                inputs.UftraceInput(io.StringIO(stream(records))).build()

    def test_names_without_values_are_rejected(self):
        records = self.records()
        del records[1]['user_args']
        with self.assertRaisesRegex(uftrace.UftraceInputError, 'user argument names'):
            inputs.UftraceInput(io.StringIO(stream(records))).build()


class ArgumentNamesBrowserTest(unittest.TestCase):
    def test_named_arguments_and_raw_bytes_in_both_delivery_modes(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:
                self.skipTest(str(error))
            kernel = helpers.FakeKernel()
            self.addCleanup(kernel.close)
            name = 'count<script>window.argNameExecuted=1</script>'
            records = ArgumentNamesImportTest().records((name, None))
            trace = Path(directory) / 'capture.jsonl'
            trace.write_text(stream(records))
            for mode in ('single', 'sidecar'):
                with self.subTest(mode=mode):
                    output = 'report.html' if mode == 'single' else 'sidecar'
                    kernel.run(str(trace), '--timeline', '--enable-fold', '--report-mode', mode, out=output)
                    with create_server(Path(kernel.dir) / output) as server:
                        thread = threading.Thread(target=server.serve_forever, daemon=True)
                        thread.start()
                        try:
                            page = browser.new_page()
                            errors = []
                            page.on('pageerror', lambda error: errors.append(str(error)))
                            page.goto(server.url)
                            page.evaluate('async () => { await fgSwitchView("timeline"); }')
                            page.evaluate('() => fgView("timeline").probe().select(1)')
                            detail = page.locator('.tl-detail')
                            self.assertIn(name + '=3, slot2=0xffffffffffffffff', detail.inner_text())
                            self.assertIn('Names from recorded user DWARF', detail.inner_text())
                            raw = detail.get_by_text('Original argument bytes', exact=True)
                            self.assertFalse(raw.locator('..').evaluate('element => element.open'))
                            raw.click()
                            self.assertIn('03000000ffffffffffffffff', detail.inner_text())
                            self.assertIsNone(page.evaluate('window.argNameExecuted'))
                            page.evaluate('async () => { await fgSwitchView("text"); }')
                            self.assertIn(name + '=3, slot2=0xffffffffffffffff', page.locator('#content').inner_text())
                            self.assertIsNone(page.evaluate('window.argNameExecuted'))
                            self.assertEqual(errors, [])
                            page.close()
                        finally:
                            server.shutdown()
                            thread.join(5)
            browser.close()


if __name__ == '__main__':
    unittest.main()
