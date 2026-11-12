"""Saved user strings and prototype names retain their capture layout and provenance."""

import io
from itertools import product
import json
from pathlib import Path
import shutil
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
class NativeStringExportTest(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix='fg-user-strings-')
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)

    def record(self, source, *options, debug=True, compiler='gcc', suffix='.c'):
        source_path = self.root / ('workload' + suffix)
        source_path.write_text(source)
        binary = self.root / 'workload'
        subprocess.run([compiler, *COMPILE_FLAGS, '-g' if debug else '-g0', '-O0',
                        '-o', str(binary), str(source_path)], check=True, capture_output=True)
        directory = self.root / 'capture'
        libraries = EXECUTABLE.parent / 'libmcount'
        library_options = ['--libmcount-path', str(libraries)] if libraries.is_dir() else []
        subprocess.run([str(EXECUTABLE), *library_options, 'record', '--no-sched', '--auto-args', '--srcline',
                        *RECORD_FLAGS, *options, '-d', str(directory), str(binary)],
                       check=True, capture_output=True, timeout=30)
        binary.unlink()
        source_path.unlink()
        result = subprocess.run([str(EXECUTABLE), 'dump', '--fgraph', '--srcline', '-d', str(directory)],
                                check=True, capture_output=True, text=True)
        inputs.UftraceInput(io.StringIO(result.stdout)).build(enable_fold=True)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def test_open_strings_and_flags_use_matching_builtin_prototype_names(self):
        records = self.record('#include <fcntl.h>\n#include <unistd.h>\n'
                              'int main(void) { int fd = open("/dev/null", O_WRONLY); '
                              'return fd < 0 || close(fd); }\n')
        entry = next(record for record in records if record.get('name') == 'open' and record['type'] == 'entry')
        self.assertEqual(entry['user_args'], ['"/dev/null"', '1'])
        self.assertEqual(entry['user_arg_names'], ['pathname', 'flags'])
        self.assertEqual(entry['user_arg_name_sources'], ['prototype', 'prototype'])
        self.assertIn(b'/dev/null', bytes.fromhex(entry['payload_hex']))
        self.assertEqual(len(entry['user_args']), 2)

    def test_builtin_enum_arguments_do_not_discard_adjacent_integer_values(self):
        records = self.record('#include <sys/socket.h>\n#include <unistd.h>\n'
                              'int main(void) { int fd = socket(AF_INET, SOCK_DGRAM, 0); '
                              'return fd < 0 || close(fd); }\n')
        entry = next(record for record in records if record.get('name') == 'socket' and record['type'] == 'entry')
        self.assertEqual(entry['user_args'], ['2', '2', '0'])
        self.assertEqual(entry['user_arg_names'], ['domain', 'type', 'protocol'])
        self.assertEqual(entry['user_arg_name_sources'], ['prototype'] * 3)

    def test_recorded_strings_preserve_empty_quotes_controls_bytes_and_padding(self):
        records = self.record(
            '__attribute__((noinline)) long child(const char *text, int flags, long count) '
            '{ return text != 0 ? flags + count : -1; }\n'
            'int main(void) { child("", -3, 17); child("a, \\\"b\\\"\\\\c\\n\\xff", 4, 19); '
            'child("NULL", 0, 1); child((void *)0, 0, 1); return 0; }\n',
            '--no-libcall', '-A', 'child@arg1/s,arg2/i32,arg3/i64')
        entries = [record for record in records if record.get('name') == 'child' and record['type'] == 'entry']
        self.assertEqual([entry['user_args'] for entry in entries], [
            ['""', '-3', '17'], ['"a, \\"b\\"\\\\c\\x0a\\xff"', '4', '19'],
            ['"NULL"', '0', '1'], ['"NULL"', '0', '1']])
        self.assertTrue(all(entry['user_arg_names'] == ['text', 'flags', 'count'] for entry in entries))
        self.assertTrue(all('user_arg_name_sources' not in entry for entry in entries))

    def test_explicit_incompatible_specifications_do_not_inherit_prototype_names(self):
        records = self.record('#include <fcntl.h>\n#include <unistd.h>\n'
                              'int main(void) { int fd = open("/dev/null", O_RDONLY); '
                              'return fd < 0 || close(fd); }\n', '-A', 'open@arg1/x,arg2/u32')
        entry = next(record for record in records if record.get('name') == 'open' and record['type'] == 'entry')
        self.assertNotIn('user_arg_names', entry)
        self.assertNotIn('user_arg_name_sources', entry)
        self.assertTrue(entry['user_args'][0].startswith('0x'))

    def test_only_matching_parameters_gain_builtin_names(self):
        records = self.record('#include <fcntl.h>\n#include <unistd.h>\n'
                              'int main(void) { int fd = open("/dev/null", O_RDONLY); '
                              'return fd < 0 || close(fd); }\n', '-A', 'open@arg1/s,arg2/u32')
        entry = next(record for record in records if record.get('name') == 'open' and record['type'] == 'entry')
        self.assertEqual(entry['user_args'], ['"/dev/null"', '0'])
        self.assertEqual(entry['user_arg_names'], ['pathname', None])
        self.assertEqual(entry['user_arg_name_sources'], ['prototype', None])

    def test_same_named_local_function_does_not_borrow_libc_parameter_names(self):
        records = self.record('static __attribute__((noinline)) long open(long value) { return value; }\n'
                              'int main(void) { return open(3) != 3; }\n',
                              '--no-libcall', '-A', 'open@arg1/i64', debug=False)
        entry = next(record for record in records if record.get('name') == 'open' and record['type'] == 'entry')
        self.assertEqual(entry['user_args'], ['3'])
        self.assertNotIn('user_arg_names', entry)

    def test_unsupported_adjacent_format_keeps_complete_raw_payload(self):
        records = self.record('long child(const char *text, long mode) { return mode + (text != 0); }\n'
                              'int main(void) { return child("hello", 7) != 8; }\n',
                              '--no-libcall', '-A', 'child@arg1/s,arg2/o64')
        entry = next(record for record in records if record.get('name') == 'child' and record['type'] == 'entry')
        self.assertIn(b'hello', bytes.fromhex(entry['payload_hex']))
        self.assertNotIn('user_args', entry)

    @unittest.skipUnless(shutil.which('g++'), 'g++ required')
    def test_std_string_uses_its_saved_length_without_losing_following_arguments(self):
        records = self.record('#include <string>\nextern "C" long child(const std::string &text, long count) '
                              '{ return text.size() + count; }\n'
                              'int main() { return child(std::string("hello, world"), 2) != 14; }\n',
                              '--no-libcall', '-A', 'child@arg1/S,arg2/i64', compiler='g++', suffix='.cpp')
        entry = next(record for record in records if record.get('name') == 'child' and record['type'] == 'entry')
        self.assertEqual(entry['user_args'], ['"hello, world"', '2'])


class StringImportTest(unittest.TestCase):
    def records(self):
        records = capture()
        records[1].update(user_args=['"a, \\"b\\"\\\\c\\x0a"', '1'],
                          user_arg_names=['pathname', 'flags'], user_arg_name_sources=['prototype'] * 2,
                          payload_hex='0123456789abcdef')
        return records

    def test_string_values_and_name_sources_reach_text_and_timeline(self):
        builder = inputs.UftraceInput(io.StringIO(stream(self.records()))).build()
        data = builder.to_data()
        detail = data['uftrace']['user_details'][data['cols']['line'][1]]
        self.assertEqual(detail['args'], 'pathname="a, \\"b\\"\\\\c\\x0a", flags=1')
        self.assertEqual(detail['arg_name_sources'], ('prototype', 'prototype'))
        self.assertEqual(data['strings'][data['cols']['args'][1]], '0123456789abcdef')
        self.assertIn(detail['args'], builder.parsed[data['cols']['line'][1] - 1].raw_line)

    def test_malformed_literals_remain_invalid(self):
        for literal in ('unquoted', '"unterminated', '"a"b"', '"\\z"', '"\\x0"', '"\\xgg"',
                        '"a\nb"', '"a\x7fb"', '"x";alert(1)', 'null', 'NULL', '1.5', '[]', '{}'):
            records = self.records()
            records[1]['user_args'][0] = literal
            with self.subTest(literal=literal), self.assertRaises(uftrace.UftraceInputError):
                uftrace.read_export(io.StringIO(stream(records)))

    def test_provenance_must_match_every_named_or_unnamed_value(self):
        for sources in (None, 'prototype', [], ['prototype'], ['prototype'] * 3,
                        ['guessed', 'prototype'], ['prototype', None], [True, 'prototype'], [{}, 'prototype']):
            records = self.records()
            records[1]['user_arg_name_sources'] = sources
            with self.subTest(sources=sources), self.assertRaises(uftrace.UftraceInputError):
                uftrace.read_export(io.StringIO(stream(records)))
        records = self.records()
        del records[1]['user_arg_names']
        with self.assertRaises(uftrace.UftraceInputError):
            uftrace.read_export(io.StringIO(stream(records)))
        records = self.records()
        records[1]['user_arg_names'][1] = None
        with self.assertRaises(uftrace.UftraceInputError):
            uftrace.read_export(io.StringIO(stream(records)))


class StringBrowserTest(unittest.TestCase):
    def test_strings_are_atomic_escaped_and_readable_in_both_views_and_delivery_modes(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            self.skipTest(str(error))
        kernel = helpers.FakeKernel()
        self.addCleanup(kernel.close)
        with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:
                self.skipTest(str(error))
            trace = Path(directory) / 'capture.jsonl'
            records = StringImportTest().records()
            value = '"</script><script>window.stringExecuted=1</script>, \\"quoted\\""'
            records[1]['user_args'][0] = value
            trace.write_text(stream(records))
            for mode, theme in product(('single', 'sidecar'), ('light', 'dark')):
                with self.subTest(mode=mode, theme=theme):
                    output = 'strings.html' if mode == 'single' else 'strings-sidecar'
                    kernel.run(str(trace), '--timeline', '--enable-fold', '--report-mode', mode, out=output)
                    with create_server(Path(kernel.dir) / output) as server:
                        thread = threading.Thread(target=server.serve_forever, daemon=True)
                        thread.start()
                        try:
                            page = browser.new_page()
                            page.goto(server.url)
                            page.evaluate('theme => document.documentElement.dataset.theme = theme', theme)
                            page.evaluate("async () => { await fgSwitchView('timeline'); }")
                            page.evaluate("() => fgView('timeline').probe().select(1)")
                            arguments = page.locator('.tl-detail .tl-argument')
                            self.assertEqual(arguments.all_text_contents(), ['pathname=' + value + ',', 'flags=1'])
                            self.assertEqual(arguments.first.locator('.tl-code-string').inner_text(), value)
                            self.assertIsNone(page.evaluate('window.stringExecuted'))
                            page.get_by_text('Original argument bytes', exact=True).click()
                            self.assertIn('uftrace built-in prototypes', page.locator('.tl-detail').inner_text())
                            self.assertIn('0123456789abcdef', page.locator('.tl-detail').inner_text())
                            page.evaluate("async () => { await fgSwitchView('text'); }")
                            self.assertIn('pathname=' + value, page.locator('#content').inner_text())
                            self.assertIsNone(page.evaluate('window.stringExecuted'))
                            page.close()
                        finally:
                            server.shutdown()
                            thread.join(timeout=5)
            browser.close()


if __name__ == '__main__':
    unittest.main()
