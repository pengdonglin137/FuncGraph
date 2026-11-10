"""Static evidence must not become guessed execution or change recorded geometry."""

import copy
import contextlib
import io
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

import helpers
from fglib import cli, export, tailcalls, timeline, util


TRACE = ''' 0)               |  parent() { /* <-root+0x1/0x20 */
 0)   0.001 us    |    child(); /* <-root+0x1/0x20 */
 0)   0.001 us    |    child(); /* <-root+0x1/0x20 */
 0)   0.003 us    |  } /* parent */
'''


def data():
    return timeline.build_timeline(TRACE.splitlines()).to_data()


def write_elf(path, functions, machine=62, kind=2):
    image = bytearray(64)
    section_names = bytearray(b'\0')
    symbol_names = bytearray(b'\0')
    sections = [(0,) * 10]
    symbols = bytearray(24)
    for index, (name, address, code) in enumerate(functions):
        section_name = len(section_names)
        section_names.extend(('.text' + str(index)).encode() + b'\0')
        sections.append((section_name, 1, 6, address, len(image), len(code), 0, 0, 1, 0))
        image.extend(code)
        symbol_name = len(symbol_names)
        symbol_names.extend(name.encode() + b'\0')
        symbols.extend(struct.pack('<IBBHQQ', symbol_name, 0x12, 0, index + 1, address, len(code)))
    for name, content, section_type, link, entry_size in [
        ('.strtab', symbol_names, 3, 0, 0),
        ('.symtab', symbols, 2, len(functions) + 1, 24),
    ]:
        section_name = len(section_names)
        section_names.extend(name.encode() + b'\0')
        sections.append((section_name, section_type, 0, 0, len(image), len(content), link, 1 if entry_size else 0, 1, entry_size))
        image.extend(content)
    section_name = len(section_names)
    section_names.extend(b'.shstrtab\0')
    sections.append((section_name, 3, 0, 0, len(image), len(section_names), 0, 0, 1, 0))
    image.extend(section_names)
    image.extend(b'\0' * (-len(image) % 8))
    section_offset = len(image)
    for section in sections:
        image.extend(struct.pack('<IIQQQQIIQQ', *section))
    ident = b'\x7fELF\x02\x01\x01' + b'\0' * 9
    image[:64] = struct.pack('<16sHHIQQQIHHHHHH', ident, kind, machine, 1, 0, 0, section_offset,
                             0, 64, 0, 0, 64, len(sections), len(sections) - 1)
    Path(path).write_bytes(image)


class TailDisassemblyTest(unittest.TestCase):
    def test_direct_jumps_only_with_original_coordinates(self):
        functions = [dict(address=0x1000, code=b'\0' * 32)]
        text = '''Disassembly of section .fg0:
1000: call 2000
1005: jmp 1010
100a: jmp *%rax
100c: jne 2000
100e: jmp 2000 <callee>
1013: jmpq 0x3000
1021: jmp 4000
'''
        result = tailcalls.parse_disassembly(text, functions, 62)[0]
        self.assertEqual([(site['offset'], site['target']) for site in result], [(14, '0x2000'), (19, '0x3000')])

    def test_arm64_calls_conditions_and_register_branches_are_not_direct_tail_evidence(self):
        text = '''Disassembly of section .fg0:
1000: bl 0x2000
1004: br x1
1008: b.eq 0x2000
100c: b 0x1010
1010: b 0x2000 <callee>
'''
        result = tailcalls.parse_disassembly(text, [dict(address=0x1000, code=b'\0' * 24)], 183)[0]
        self.assertEqual([site['offset'] for site in result], [16])

    def test_toolchain_selection(self):
        for environment, expected in [({}, 'objdump'), ({'CROSS_COMPILE': 'aarch64-linux-gnu'}, 'aarch64-linux-gnu-objdump'),
                                      ({'LLVM': '1'}, 'llvm-objdump'), ({'LLVM': '-18'}, 'llvm-objdump-18'),
                                      ({'LLVM': '/tools/'}, '/tools/llvm-objdump')]:
            with self.subTest(environment=environment), mock.patch.dict(os.environ, environment, clear=True):
                self.assertEqual(tailcalls.objdump_command(), expected)

    def test_no_candidates_does_not_open_a_binary_or_import_optional_dependency(self):
        model = data()
        model['cols']['flags'] = [0] * model['n']
        with mock.patch.object(tailcalls, '_load_functions', side_effect=AssertionError('unexpected ELF read')):
            self.assertEqual(tailcalls.analyze(model, '/missing')['status'], 'not_needed')

    def test_dependency_and_timeout_failures_are_explicit_unknowns(self):
        for failure, reason in [(ImportError(), 'pyelftools_unavailable'),
                                (subprocess.TimeoutExpired('objdump', 30), 'disassembly_timeout')]:
            with self.subTest(reason=reason), mock.patch.object(tailcalls, '_load_functions', side_effect=failure):
                result = tailcalls.analyze(data(), '/unused')
                self.assertEqual(result['reason'], reason)
                self.assertTrue(all(pair['status'] == 'unresolved' for pair in result['pairs'].values()))

    def test_cli_requires_timeline_and_binary_and_hides_unused_option(self):
        parser = cli.build_parser()
        for options in [('--tail-calls',), ('--timeline', '--tail-calls')]:
            output = io.StringIO()
            with contextlib.redirect_stderr(output), self.assertRaises(SystemExit):
                cli.validate_args(parser.parse_args([helpers.fixture('pi4_default'), *options]))
            self.assertIn('--tail-calls needs --timeline and --vmlinux', output.getvalue())
        self.assertNotIn('Tail Calls', dict(util.format_args_info(parser.parse_args(['trace.txt']))))


@unittest.skipIf(importlib.util.find_spec('elftools') is None or not shutil.which('objdump'), 'pyelftools / objdump unavailable')
class TailBinaryTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, 'kernel')
        self.tool = shutil.which('objdump')
        self.jump = b'\xe9' + struct.pack('<i', 0x100 - 5)

    def fixture(self, code=None, **options):
        write_elf(self.path, [('parent', 0x401000, self.jump if code is None else code), ('child', 0x401100, b'\xc3')], **options)

    def test_batch_evidence_deduplicates_pairs_and_preserves_every_recorded_column(self):
        self.fixture()
        model = data()
        before = copy.deepcopy(model)
        result = tailcalls.analyze(model, self.path, tool=self.tool)
        self.assertEqual(model, before)
        self.assertEqual(len(result['pairs']), 1)
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['analyzed_functions'], 1)
        self.assertEqual(result['analyzed_bytes'], 5)
        self.assertFalse(result['trace_build_id_verified'])
        pair = next(iter(result['pairs'].values()))
        self.assertEqual(pair['status'], 'supported')
        self.assertEqual(pair['sites'][0]['symbol'], 'parent+0x0/0x5')
        self.assertEqual(pair['sites'][0]['target'], '0x401100')
        self.assertEqual(tailcalls.analyze(model, self.path, tool=self.tool), result)
        model['tail_calls'] = result
        exported = export.build(model)['tail_calls']
        self.assertEqual(exported['pairs'][0]['parent'], 'parent')
        self.assertEqual(exported['pairs'][0]['child'], 'child')

    def test_generated_report_resolves_exact_jump_sites_and_honors_no_text_source_policy(self):
        kernel = helpers.FakeKernel()
        self.addCleanup(kernel.close)
        code = b'\x90' * 20 + b'\xe9' + struct.pack('<i', 0x100 - 25)
        write_elf(kernel.vmlinux, [('parent', 0x401000, code), ('child', 0x401100, b'\xc3')])
        trace = Path(kernel.dir, 'tail.txt')
        trace.write_text(TRACE)
        arguments = Path(kernel.dir, 'resolver-arguments')
        tool = Path(kernel.ksrc, 'scripts/faddr2line')
        tool.write_text(tool.read_text().replace('#!/bin/sh\n', '#!/bin/sh\nprintf "%s\\n" "$@" >> ' + str(arguments) + '\n'))
        for options, with_source in [((), True), (('--no-text-view',), False),
                                     (('--no-text-view', '--keep-source'), True)]:
            with self.subTest(options=options):
                if arguments.exists():
                    arguments.unlink()
                report, process = kernel.run(str(trace), '--timeline', '--tail-calls', '--func-links', '--no-btf', *options)
                evidence = helpers.json_script(report, 'tlData')['tail_calls']
                site = next(iter(evidence['pairs'].values()))['sites'][0]
                self.assertEqual(site['symbol'], 'parent+0x14/0x19')
                self.assertEqual('source' in site, with_source)
                if with_source:
                    self.assertIn('parent+0x14/0x19', arguments.read_text().splitlines())
                    self.assertNotIn('parent+0x13/0x19', arguments.read_text().splitlines())
                self.assertIn('1/1 unique', process.stdout)

    def test_normal_calls_indirect_jumps_loops_and_nonentry_targets_stay_unresolved(self):
        for code in [b'\xe8' + struct.pack('<i', 0x100 - 5) + b'\xc3', b'\xff\xe0', b'\xeb\xfe',
                     b'\xe9' + struct.pack('<i', 0x101 - 5)]:
            with self.subTest(code=code):
                self.fixture(code)
                result = tailcalls.analyze(data(), self.path, tool=self.tool)
                self.assertEqual(next(iter(result['pairs'].values()))['status'], 'unresolved')

    def test_binary_and_disassembler_failures_do_not_claim_no_tail_calls(self):
        for contents, tool in [(b'not ELF', self.tool), (None, '/missing/objdump')]:
            with self.subTest(tool=tool):
                self.fixture()
                if contents:
                    Path(self.path).write_bytes(contents)
                result = tailcalls.analyze(data(), self.path, tool=tool)
                self.assertEqual(result['status'], 'unavailable')
                self.assertEqual(next(iter(result['pairs'].values()))['status'], 'unresolved')

    def test_relocatable_and_unsupported_architectures_are_explicitly_unavailable(self):
        for options in [dict(kind=1), dict(machine=40)]:
            with self.subTest(options=options):
                self.fixture(**options)
                self.assertEqual(tailcalls.analyze(data(), self.path, tool=self.tool)['reason'], 'unsupported_binary')

    def test_duplicate_local_symbols_are_not_guessed(self):
        write_elf(self.path, [('parent', 0x401000, self.jump), ('parent', 0x402000, b'\xc3'), ('child', 0x401100, b'\xc3')])
        pair = next(iter(tailcalls.analyze(data(), self.path, tool=self.tool)['pairs'].values()))
        self.assertEqual(pair['reason'], 'ambiguous_symbol')

    def test_budget_exhaustion_stays_unknown_and_child_target_needs_no_code_copy(self):
        self.fixture()
        with mock.patch.object(tailcalls, 'MAX_CODE_BYTES', 1):
            result = tailcalls.analyze(data(), self.path, tool=self.tool)
        self.assertEqual(result['analyzed_bytes'], 0)
        self.assertEqual(next(iter(result['pairs'].values()))['reason'], 'analysis_budget')

    def test_module_symbols_do_not_reuse_same_named_vmlinux_evidence(self):
        self.fixture()
        model = data()
        model['cols']['module'] = [0] * model['n']
        result = tailcalls.analyze(model, self.path, tool=self.tool)
        self.assertEqual(result['reason'], 'module_not_analyzed')

    def test_raw_retaddr_records_use_the_same_static_evidence_pipeline(self):
        from fglib import inputs, raw
        from raw_fixture import container

        try:
            raw._helper()
        except raw.RawInputError as error:
            if os.environ.get('FG_RAW_REQUIRED'):
                raise
            self.skipTest(str(error))
        fixture = container(retaddr=True)
        recorded = struct.pack('<Q', 0x1008)
        self.assertEqual(fixture.count(recorded), 2)
        trace = Path(self.directory.name, 'tail.dat')
        trace.write_bytes(fixture.replace(recorded, struct.pack('<Q', 0x1108)))
        write_elf(self.path, [('root', 0x1000, self.jump), ('child', 0x1100, b'\xc3')])
        rows, note = inputs.read_lines([str(trace)])
        model = timeline.build_timeline(rows).to_data()
        result = tailcalls.analyze(model, self.path, tool=self.tool)
        self.assertTrue(model['caps']['retaddr'])
        pair = next(iter(result['pairs'].values()))
        self.assertEqual(pair['status'], 'supported')
        self.assertEqual(model['strings'][pair['parent']], 'root')

    @unittest.skipUnless(shutil.which('aarch64-linux-gnu-objdump'), 'arm64 objdump unavailable')
    def test_real_arm64_disassembly_uses_linked_addresses(self):
        write_elf(self.path, [('parent', 0x401000, struct.pack('<I', 0x14000040)),
                              ('child', 0x401100, struct.pack('<I', 0xd65f03c0))], machine=183)
        result = tailcalls.analyze(data(), self.path, tool=shutil.which('aarch64-linux-gnu-objdump'))
        pair = next(iter(result['pairs'].values()))
        self.assertEqual(pair['status'], 'supported')
        self.assertEqual(pair['sites'][0]['mnemonic'], 'b')
