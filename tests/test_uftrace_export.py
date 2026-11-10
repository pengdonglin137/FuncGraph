"""Exercise the patched uftrace executable with real user and synthetic kernel data."""
import json
import os
from pathlib import Path
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
        source.write_text('__attribute__((noinline)) int child(int value) { return value + 1; }\n'
                          'int main(void) { return child(1) != 2; }\n')
        cls.binary = cls.root / 'workload'
        subprocess.run(['gcc', '-pg', '-g', '-O0', '-o', str(cls.binary), str(source)], check=True,
                       capture_output=True)
        cls.base = cls.root / 'base'
        result = subprocess.run([str(EXECUTABLE), 'record', '--no-sched', '--no-libcall', '--srcline',
                                 '-d', str(cls.base), str(cls.binary)], capture_output=True, text=True)
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
        self.assertEqual(Path(child['source']['file']).name, 'workload.c')
        self.assertEqual(child['source']['line'], 1)

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
