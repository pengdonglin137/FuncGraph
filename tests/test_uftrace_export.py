"""Exercise the patched uftrace executable with real user and synthetic kernel data."""
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import struct
import tempfile
import unittest

import helpers
from uftrace_fixture import add_kernel
from verify_uftrace_records import verify


EXECUTABLE = Path(os.environ.get('FG_UFTRACE', str(Path(helpers.ROOT) /
                                                'build/uftrace-x86_64/bin/uftrace')))
AVAILABLE = EXECUTABLE.is_file() and shutil.which('gcc')
PATCHABLE = platform.machine() in ('aarch64', 'arm64')
COMPILE_FLAGS = ['-fpatchable-function-entry=2'] if PATCHABLE else ['-pg']
RECORD_FLAGS = ['-P', '.'] if PATCHABLE else []


class UftraceExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not AVAILABLE:
            if os.environ.get('FG_UFTRACE_REQUIRED'):
                raise AssertionError('patched uftrace and gcc are required')
            raise unittest.SkipTest('optional patched uftrace build not available')
        cls.workspace = tempfile.TemporaryDirectory(prefix='fg-uftrace-export-')
        cls.addClassCleanup(cls.workspace.cleanup)
        cls.root = Path(cls.workspace.name)
        source = cls.root / 'workload.c'
        source.write_text('int child(int);\nint main(void) { return child(1) != 2; }\n')
        library = cls.root / 'workload_lib.c'
        library.write_text('int child(int value) { return value + 1; }\n')
        subprocess.run(['gcc', *COMPILE_FLAGS, '-g', '-O0', '-c', 'workload_lib.c'], cwd=cls.root,
                       check=True, capture_output=True)
        subprocess.run(['ar', 'rcs', 'libworkload.a', 'workload_lib.o'], cwd=cls.root,
                       check=True, capture_output=True)
        cls.binary = cls.root / 'workload'
        subprocess.run(['gcc', *COMPILE_FLAGS, '-g', '-O0', '-o', 'workload', 'workload.c', 'libworkload.a'],
                       cwd=cls.root, check=True, capture_output=True)
        cls.base = cls.root / 'base'
        result = subprocess.run([str(EXECUTABLE), 'record', '--no-sched', '--no-libcall', '--srcline',
                                 *RECORD_FLAGS, '-d', str(cls.base), str(cls.binary)],
                                capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix='fg-uftrace-case-')
        self.addCleanup(self.workspace.cleanup)
        self.capture = Path(self.workspace.name) / 'capture'
        shutil.copytree(self.base, self.capture)

    def dump(self, *arguments):
        return subprocess.run([str(EXECUTABLE), 'dump', '--fgraph', '--srcline', '-d',
                               str(self.capture), *arguments], capture_output=True, text=True)

    def records(self, *arguments):
        result = self.dump(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(records[0]['schema'], 'funcgraph.uftrace.v1')
        self.assertEqual(records[-1], {'type': 'end', 'complete': True})
        return records

    def test_user_only_source_clock_and_unknown_cpu(self):
        records = self.records()
        self.assertEqual(records[0]['clock'], 'mono')
        self.assertEqual(records[0]['kernel_settings'], {})
        calls = [record for record in records if record['type'] in ('entry', 'exit')]
        self.assertEqual({record['space'] for record in calls}, {'user'})
        self.assertTrue(all(record['cpu'] is None for record in calls))
        self.assertTrue(all(type(record['time_ns']) is int for record in calls))
        child = next(record for record in calls if record['name'] == 'child' and record['type'] == 'entry')
        self.assertEqual(Path(child['source']['file']).name, 'workload_lib.c')
        self.assertEqual(child['source']['line'], 1)
        self.assertNotIn('user_args', child)

    def record_user_with_spec(self, arg_spec):
        directory = Path(self.workspace.name) / 'typed'
        subprocess.run([str(EXECUTABLE), 'record', '--no-sched', '--no-libcall',
                        *RECORD_FLAGS,
                        '-A', arg_spec, '-R', 'child@retval/i32',
                        '-d', str(directory), str(self.binary)], check=True, capture_output=True)
        self.capture = directory
        return [record for record in self.records() if record.get('name') == 'child']

    def test_user_scalars_use_recorded_specifications_and_keep_payload(self):
        entry, exit_record = self.record_user_with_spec('child@arg1/i32')
        self.assertEqual(entry['user_args'], ['1'])
        self.assertEqual(exit_record['user_retval'], '2')
        for record in (entry, exit_record):
            self.assertIn('payload_hex', record)
        self.assertNotIn('user_retval', entry)
        self.assertNotIn('user_args', exit_record)

    def test_unsupported_user_format_keeps_raw_bytes_only(self):
        entry, exit_record = self.record_user_with_spec('child@arg1/o32')
        self.assertIn('payload_hex', entry)
        self.assertNotIn('user_args', entry)
        self.assertEqual(exit_record['user_retval'], '2')

    def test_integer_arguments_survive_pie_non_pie_and_optimization(self):
        values = [-11, 22, -33, 44, 55, -66, 77, 88]
        directory = Path(self.workspace.name)
        source = directory / 'register arguments.c'
        source.write_text(
            '__attribute__((noinline, noclone)) '
            'long aggregate(long first, long second, long third, long fourth, '
            'long fifth, long sixth, long seventh, long eighth) {\n'
            '    return first + second + third + fourth + fifth + sixth + seventh + eighth;\n'
            '}\nint main(void) { return aggregate(-11, 22, -33, 44, 55, -66, 77, 88) != 176; }\n')
        specification = 'aggregate@' + ','.join('arg' + str(index) + '/i64' for index in range(1, 9))
        for optimization in ['-O0', '-O2']:
            for kind, options in [('pie', ['-fPIE', '-pie']), ('exec', ['-fno-pie', '-no-pie'])]:
                with self.subTest(optimization=optimization, kind=kind):
                    binary = directory / (kind + optimization)
                    subprocess.run(['gcc', *COMPILE_FLAGS, '-g', optimization, *options,
                                    '-o', str(binary), str(source)], check=True, capture_output=True)
                    self.capture = directory / (binary.name + '.data')
                    subprocess.run([str(EXECUTABLE), 'record', '--no-sched', '--no-libcall',
                                    *RECORD_FLAGS, '-A', specification, '-R', 'aggregate@retval/i64',
                                    '-d', str(self.capture), str(binary)], check=True, capture_output=True)
                    calls = [record for record in self.records() if record.get('name') == 'aggregate']
                    self.assertEqual([record['type'] for record in calls], ['entry', 'exit'])
                    self.assertEqual(calls[0]['user_args'], [str(value) for value in values])
                    self.assertEqual(calls[1]['user_retval'], '176')

    def test_recursive_arguments_returns_and_depth_remain_paired(self):
        directory = Path(self.workspace.name)
        source = directory / 'recursive.c'
        source.write_text('__attribute__((noinline, noclone)) int descend(int level, int seed) {\n'
                          '    return level ? descend(level - 1, seed + 3) + 1 : seed;\n'
                          '}\nint main(void) { return descend(3, 5) != 17; }\n')
        for optimization in ['-O0', '-O2']:
            with self.subTest(optimization=optimization):
                binary = directory / ('recursive' + optimization)
                subprocess.run(['gcc', *COMPILE_FLAGS, '-g', optimization, '-fno-optimize-sibling-calls',
                                '-o', str(binary), str(source)], check=True, capture_output=True)
                self.capture = directory / (binary.name + '.data')
                subprocess.run([str(EXECUTABLE), 'record', '--no-sched', '--no-libcall', *RECORD_FLAGS,
                                '-A', 'descend@arg1/i32,arg2/i32', '-R', 'descend@retval/i32',
                                '-d', str(self.capture), str(binary)], check=True, capture_output=True)
                calls = [record for record in self.records() if record.get('name') == 'descend']
                entries = [record for record in calls if record['type'] == 'entry']
                exits = [record for record in calls if record['type'] == 'exit']
                self.assertEqual([record['user_args'] for record in entries],
                                 [['3', '5'], ['2', '8'], ['1', '11'], ['0', '14']])
                self.assertEqual([record['user_retval'] for record in exits], ['14', '15', '16', '17'])
                self.assertEqual([record['depth'] for record in entries], [1, 2, 3, 4])
                self.assertEqual([record['depth'] for record in exits], [4, 3, 2, 1])

    def test_executable_and_source_paths_with_spaces_survive_offline_export(self):
        cases = [('build with spaces', 'application with spaces', []),
                 ('构建 \t tabs', '应用程序 \t name  ', []),
                 ('without build id', 'program build-id:deadbeef', ['-Wl,--build-id=none'])]
        for folder, filename, options in cases:
            with self.subTest(filename=filename):
                directory = Path(self.workspace.name) / folder
                directory.mkdir()
                source = directory / 'source with spaces.c'
                source.write_text('int child(int value) { return value + 1; }\n'
                                  'int main(void) { return child(1) != 2; }\n')
                binary = directory / filename
                subprocess.run(['gcc', *COMPILE_FLAGS, '-g', '-O0', *options, '-o', str(binary), str(source)],
                               check=True, capture_output=True)
                self.capture = directory / 'recording with spaces'
                subprocess.run([str(EXECUTABLE), 'record', '--no-sched', '--no-libcall', '--srcline',
                                *RECORD_FLAGS,
                                '-A', 'child@arg1/i32', '-R', 'child@retval/i32',
                                '-d', str(self.capture), str(binary)], check=True, capture_output=True)
                self.assertTrue(any(str(binary) in path.read_text() for path in self.capture.glob('*.map')))
                binary.rename(binary.with_name('moved application'))
                calls = [record for record in self.records() if record.get('name') == 'child']
                self.assertEqual([record['type'] for record in calls], ['entry', 'exit'])
                self.assertEqual(calls[0]['user_args'], ['1'])
                self.assertEqual(calls[1]['user_retval'], '2')
                self.assertEqual(calls[0]['source'], {'file': str(source), 'line': 1})
                self.assertGreater(int(calls[0]['address'], 16), 0)

    def test_shared_library_paths_with_spaces_preserve_symbols_and_sources(self):
        directory = Path(self.workspace.name) / 'library build with spaces'
        directory.mkdir()
        source = directory / 'library source.c'
        source.write_text('int shared_child(int value) { return value + 1; }\n')
        library = directory / 'libwith spaces.so'
        subprocess.run(['gcc', *COMPILE_FLAGS, '-g', '-O0', '-fPIC', '-shared', '-o', str(library), str(source)],
                       check=True, capture_output=True)
        main = directory / 'main.c'
        main.write_text('int shared_child(int);\nint main(void) { return shared_child(1) != 2; }\n')
        binary = directory / 'workload'
        subprocess.run(['gcc', *COMPILE_FLAGS, '-g', '-O0', '-o', str(binary), str(main), str(library),
                        '-Wl,-rpath,' + str(directory)], check=True, capture_output=True)
        self.capture = directory / 'capture'
        subprocess.run([str(EXECUTABLE), 'record', '--no-sched', '--no-libcall', '--srcline',
                        *RECORD_FLAGS, *(['-P', '.@' + library.name] if PATCHABLE else []),
                        '-A', 'shared_child@arg1/i32', '-R', 'shared_child@retval/i32',
                        '-d', str(self.capture), str(binary)], check=True, capture_output=True)
        self.assertTrue(any(str(library) in path.read_text() for path in self.capture.glob('*.map')))
        library.rename(library.with_name('moved library.so'))
        calls = [record for record in self.records() if record.get('name') == 'shared_child']
        self.assertEqual([record['type'] for record in calls], ['entry', 'exit'])
        self.assertEqual(calls[0]['user_args'], ['1'])
        self.assertEqual(calls[1]['user_retval'], '2')
        self.assertEqual(calls[0]['source'], {'file': str(source), 'line': 1})

    def test_recorded_settings_not_export_host(self):
        (self.capture / 'kernel-funcgraph.info').write_text(
            'kernel_release=fixture-kernel\ntrace_clock=local [mono]\n'
            'options/funcgraph-args=unavailable\n')
        header = self.records()[0]['kernel_settings']
        self.assertEqual(header['kernel_release'], 'fixture-kernel')
        self.assertEqual(header['options/funcgraph-args'], 'unavailable')

    def test_old_capture_clock_is_unknown_not_assumed(self):
        (self.capture / 'funcgraph-clock').unlink()
        self.assertIsNone(self.records()[0]['clock'])

    def test_native_fields_all_byte_orders_and_word_sizes(self):
        for endian in ('<', '>'):
            for width in (4, 8):
                for retaddr in (False, True):
                    for argument_mode in ('flexible', 'dynamic', 'relative', 'fixed'):
                        with self.subTest(endian=endian, width=width, retaddr=retaddr,
                                          argument_mode=argument_mode):
                            add_kernel(self.capture, endian=endian, width=width,
                                       retaddr=retaddr, dynamic_args=argument_mode == 'dynamic',
                                       relative_args=argument_mode == 'relative',
                                       fixed_args=argument_mode == 'fixed', sched=True)
                            records = self.records('--kernel-only')
                            calls = [record for record in records if record['type'] in ('entry', 'exit')]
                            self.assertEqual(len(calls), 4)
                            self.assertEqual({record['space'] for record in calls}, {'kernel'})
                            first, nested, returning, last = calls
                            self.assertEqual(first['time_ns'], 1000000000)
                            self.assertEqual(first['tid'], 42)
                            self.assertEqual(first['cpu'], 0)
                            self.assertEqual(first['depth'], 0)
                            self.assertEqual(nested['depth'], 1)
                            self.assertEqual(first['address'], '0x1000')
                            self.assertEqual(first['common_flags'], 0)
                            self.assertEqual(first['common_preempt_count'], 1)
                            self.assertEqual(first['args'], ['0x123', '0x456'])
                            self.assertEqual('retaddr' in first, retaddr)
                            if retaddr:
                                self.assertEqual(first['retaddr'], '0x1008')
                                self.assertEqual(first['retaddr_symbol'], {
                                    'name': 'root', 'address': '0x1000', 'offset': 8, 'size': 256,
                                    'size_basis': 'next_symbol', 'source': 'saved_kallsyms'})
                            else:
                                self.assertNotIn('retaddr_symbol', first)
                            self.assertEqual(returning['retval'], hex((1 << (width * 8)) - 2))
                            self.assertEqual(returning['calltime_ns'], 1000000101)
                            self.assertEqual(returning['rettime_ns'], 1000000310)
                            self.assertEqual(last['time_ns'], 1000000513)
                            events = [record for record in records if record['type'] == 'event'
                                      and record.get('space') == 'kernel']
                            self.assertEqual(len(events), 3)
                            self.assertEqual(events[1]['tid'], 43)
                            self.assertIn('pid=42', events[1]['text'])
                            exported = Path(self.workspace.name) / 'export.jsonl'
                            exported.write_text('\n'.join(json.dumps(record) for record in records))
                            self.assertEqual(verify(self.capture, exported)['kernel_records'], 7)

    def test_absent_fields_and_empty_arguments_stay_distinct(self):
        add_kernel(self.capture, args=False, retval=False)
        calls = [record for record in self.records('--kernel-only') if record['type'] in ('entry', 'exit')]
        self.assertEqual(calls[0]['args'], [])
        self.assertNotIn('retval', calls[2])
        self.assertNotIn('retaddr', calls[0])

    def test_mixed_streams_are_not_silently_sorted_or_rebased(self):
        add_kernel(self.capture, sched=True)
        records = self.records()
        spaces = {record.get('space') for record in records if record['type'] == 'entry'}
        self.assertEqual(spaces, {'user', 'kernel'})
        self.assertEqual(records[0]['order'], 'streams')
        self.assertEqual(records[0]['depth_basis'], 'address-space')

    def test_page_loss_remains_explicit(self):
        add_kernel(self.capture, lost=7)
        losses = [record for record in self.records('--kernel-only') if record['type'] == 'lost']
        self.assertEqual(len(losses), 1)
        self.assertEqual(losses[0]['count'], 7)
        self.assertIsNone(losses[0]['tid'])
        self.assertEqual(losses[0]['cpu'], 0)

    def test_sparse_cpu_files_keep_recorded_identity(self):
        add_kernel(self.capture)
        (self.capture / 'kernel-cpu0.dat').rename(self.capture / 'kernel-cpu7.dat')
        calls = [record for record in self.records('--kernel-only') if record['type'] in ('entry', 'exit')]
        self.assertEqual({record['cpu'] for record in calls}, {7})

    def test_unsorted_low_module_symbols_cannot_shadow_kernel_text(self):
        base = 0xffffffc080000000
        add_kernel(self.capture, address_base=base)
        with (self.capture / 'kallsyms').open('ab') as stream:
            for offset in (0x800, 0x900, 0x100, 0x200):
                stream.write(f'{base + offset:016x} t lower_module_{offset:x}\t[fixture]\n'.encode())
        calls = [record for record in self.records('--kernel-only') if record['type'] in ('entry', 'exit')]
        self.assertEqual([record['name'] for record in calls], ['root', 'child', 'child', 'root'])

    def test_captured_return_symbols_use_relocated_saved_addresses(self):
        base = 0xffffffff81000000
        add_kernel(self.capture, address_base=base, retaddr=True)
        calls = [record for record in self.records('--kernel-only') if record['type'] == 'entry']
        for record in calls:
            self.assertEqual(record['retaddr'], hex(base + 0x1008))
            self.assertEqual(record['retaddr_symbol']['address'], hex(base + 0x1000))
            self.assertEqual(record['retaddr_symbol']['offset'], 8)
            self.assertEqual(record['retaddr_symbol']['name'], 'root')

    def test_return_at_symbol_end_belongs_to_preceding_function(self):
        add_kernel(self.capture, retaddr=True)
        (self.capture / 'kallsyms').write_text('1000 T root\n1008 T next\n1100 T child\n1200 T boundary\n')
        first = next(record for record in self.records('--kernel-only') if record['type'] == 'entry')
        self.assertEqual(first['retaddr_symbol']['name'], 'root')
        self.assertEqual(first['retaddr_symbol']['offset'], 8)
        self.assertEqual(first['retaddr_symbol']['size'], 8)

    def test_caller_module_is_preserved_independently_of_callee(self):
        base = 0xffffffff81000000
        add_kernel(self.capture, retaddr=True, address_base=base)
        (self.capture / 'kallsyms').write_text(
            f'{base + 0x1000:x} T root\t[caller_mod]\n{base + 0x1100:x} T child\n{base + 0x1200:x} T boundary\n')
        calls = [record for record in self.records('--kernel-only') if record['type'] == 'entry']
        self.assertEqual(calls[1]['name'], 'child')
        self.assertEqual(calls[1]['retaddr_symbol']['module'], 'caller_mod')

    def test_unresolved_redacted_data_and_unbounded_callers_stay_raw(self):
        add_kernel(self.capture, retaddr=True)
        for symbols in ['', '0 T root\n0 T child\n', '1000 D root\n1100 T child\n',
                        '1000 T root\n', '1009 T root\n1100 T child\n',
                        '1000 T root\n1000 t alias\t[other]\n1100 T child\n']:
            with self.subTest(symbols=symbols):
                (self.capture / 'kallsyms').write_text(symbols)
                calls = [record for record in self.records('--kernel-only') if record['type'] == 'entry']
                self.assertTrue(calls)
                for record in calls:
                    self.assertEqual(record['retaddr'], '0x1008')
                    self.assertNotIn('retaddr_symbol', record)

    def test_unsorted_aliases_share_a_bounded_code_range(self):
        add_kernel(self.capture, retaddr=True)
        (self.capture / 'kallsyms').write_text('1100 T child\n1000 t alias\n1200 T boundary\n1000 W root\n')
        first = next(record for record in self.records('--kernel-only') if record['type'] == 'entry')
        self.assertEqual(first['retaddr_symbol']['name'], 'root')
        self.assertEqual(first['retaddr_symbol']['size'], 256)

    def test_malformed_arguments_fail_without_success_footer(self):
        add_kernel(self.capture, dynamic_args=True)
        path = self.capture / 'kernel-cpu0.dat'
        payload = bytearray(path.read_bytes())
        struct.pack_into('<I', payload, 16 + 4 + 24, 0xffffffff)
        path.write_bytes(payload)
        result = self.dump('--kernel-only')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('"type":"end"', result.stdout)
        self.assertIn('cannot decode kernel CPU', result.stderr)

    def test_invalid_native_depth_is_not_wrapped(self):
        add_kernel(self.capture, bad_depth=True)
        result = self.dump('--kernel-only')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('"type":"end"', result.stdout)

    def test_truncated_or_invalid_user_record_cannot_claim_completion(self):
        path = next(self.capture.glob('[0-9]*.dat'))
        original = path.read_bytes()
        for payload in (original + b'\x00', bytes(16) + original[16:]):
            with self.subTest(payload=payload[:16]):
                path.write_bytes(payload)
                result = self.dump()
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('"type":"end"', result.stdout)
                self.assertIn('cannot decode user record', result.stderr)

    def test_unknown_ftrace_event_cannot_reuse_previous_function(self):
        add_kernel(self.capture, unknown_entry=True)
        records = self.records('--kernel-only')
        self.assertEqual([record['type'] for record in records if record.get('space') == 'kernel'],
                         ['event', 'event', 'exit', 'exit'])
        self.assertEqual(records[1]['name'], 'ftrace:unknown_entry')


if __name__ == '__main__':
    unittest.main()
