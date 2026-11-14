"""Compiler suffixes must not hide trusted BTF or invent a clone's ABI."""

import builtins
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import helpers
from fglib import btf, btf_abi, inputs
from test_btf import BtfBuilder
from test_uftrace_details import arguments
from test_uftrace_import import capture, stream

try:
    import elftools
except ImportError:
    elftools = None


def database_for(definitions, source=None):
    builder = BtfBuilder()
    for name, fields, returned in definitions:
        arguments(builder, name, fields, returned)
    info = btf.BtfInfo(builder.blob(), word_size=8)
    info.elf_source = source
    database = btf.ReturnTypeDB()
    database.add(info)
    return database


class ClonePrototypeTest(unittest.TestCase):
    def test_exact_prototypes_keep_unsupported_prefixes_and_zero_argument_counts(self):
        database = database_for([('partial.isra.0', [('fd', 'int'), ('payload', 'struct')], 'int'),
                                 ('empty.constprop.0', [], 'void')])
        self.assertEqual(database.argument_names('partial.isra.0', 6), ('fd',))
        self.assertEqual(database.function_types('partial.isra.0'),
                         {'args': [('sint', 32)], 'ret': ('sint', 32)})
        self.assertEqual(database.function_types('empty.constprop.0'),
                         {'args': [], 'argc': 0, 'ret': ('void', 0)})

    def test_exact_suffixed_prototypes_decode_names_counts_widths_and_returns(self):
        for suffix in ('.isra.0', '.constprop.3', '.part.1', '.llvm.deadbeef', '.clone.2',
                       '.cold', '.hot.1', '.lto_priv.5', '.isra.0.constprop.4'):
            with self.subTest(suffix=suffix):
                name = 'inner' + suffix
                database = database_for([(name, [('value', 's8'), ('address', 'ptr')], 's16')])
                self.assertEqual(database.argument_names(name, 6), ('value', 'address'))
                self.assertEqual(database.function_types(name),
                                 {'args': [('sint', 8), ('ptr', 64)], 'argc': 2, 'ret': ('sint', 16)})

    def test_exact_changed_signature_wins_over_original(self):
        database = database_for([('inner', [('buffer', 'ptr'), ('size', 'wide')], 's64'),
                                 ('inner.isra.0', [('value', 'u8')], 's16')], 'kernel.elf')
        with patch('fglib.btf_abi.verified_clones') as verify:
            self.assertEqual(database.argument_names('inner.isra.0', 6), ('value',))
            self.assertEqual(database.function_types('inner.isra.0'),
                             {'args': [('uint', 8)], 'argc': 1, 'ret': ('sint', 16)})
            verify.assert_not_called()

    def test_exact_conflict_does_not_fall_back_to_unambiguous_original(self):
        database = database_for([('inner', [('value', 'int')], 'int'),
                                 ('inner.isra.0', [('first', 'int')], 'int'),
                                 ('inner.isra.0', [('second', 'int')], 'int')], 'kernel.elf')
        with patch('fglib.btf_abi.verified_clones') as verify:
            self.assertEqual(database.argument_names('inner.isra.0', 6), ())
            self.assertIsNone(database.function_types('inner.isra.0'))
            verify.assert_not_called()

    def test_alias_verification_is_batched_cached_and_does_not_infer_returns(self):
        database = database_for([('inner', [('value', 'u8')], 's32')], 'kernel.elf')
        names = ('inner.constprop.0', 'inner.isra.1.llvm.abcd')
        with patch('fglib.btf_abi.verified_clones', return_value=set(names)) as verify:
            database.prepare_arguments((*names, names[0]))
            for _repeat in range(3):
                for name in names:
                    self.assertEqual(database.argument_names(name, 6), ('value',))
                    self.assertEqual(database.function_types(name), {'args': [('uint', 8)], 'argc': 1})
            verify.assert_called_once()
            self.assertEqual(set(verify.call_args[0][1]), set(names))

    def test_negative_results_are_cached_and_unknown_suffixes_are_not_guessed(self):
        database = database_for([('inner', [('value', 'int')], 'int')], 'kernel.elf')
        with patch('fglib.btf_abi.verified_clones', return_value=set()) as verify:
            for _repeat in range(3):
                for name in ('inner.isra.0', 'inner.unknown.0', 'inner.isra.x',
                             'inner.isra.0.more', 'other.constprop.0'):
                    self.assertEqual(database.argument_names(name, 6), ())
                    self.assertIsNone(database.function_types(name))
            verify.assert_called_once()
            self.assertEqual(set(verify.call_args[0][1]), {'inner.isra.0'})

    def test_exact_lookups_never_iterate_the_function_index(self):
        class IndexedOnly(dict):
            def __iter__(self):
                raise AssertionError('BTF index must not be scanned for a lookup')

        database = database_for([('inner.isra.0', [('value', 'int')], 'int')])
        database.params = IndexedOnly(database.params)
        database.funcs = IndexedOnly(database.funcs)
        for _repeat in range(10):
            self.assertEqual(database.argument_names('inner.isra.0', 6), ('value',))
            self.assertEqual(database.function_types('inner.isra.0')['argc'], 1)
            self.assertEqual(database.lookup('inner.isra.0'), (('sint', 4), False))

    def test_raw_btf_and_unsupported_layouts_never_use_unverified_aliases(self):
        for fields, returned, source in [([('value', 'int')], 'int', None),
                                         ([('value', 'float')], 'int', 'kernel.elf'),
                                         ([('value', 'struct')], 'int', 'kernel.elf'),
                                         ([('', 'int')], 'int', 'kernel.elf'),
                                         ([('value', 'int'), ('', 'varargs')], 'int', 'kernel.elf'),
                                         ([('value', 'int')], 'struct', 'kernel.elf')]:
            with self.subTest(fields=fields, returned=returned, source=source):
                database = database_for([('inner', fields, returned)], source)
                with patch('fglib.btf_abi.verified_clones') as verify:
                    self.assertEqual(database.argument_names('inner.isra.0', 6), ())
                    self.assertIsNone(database.function_types('inner.isra.0'))
                    verify.assert_not_called()

    def test_adding_conflicting_module_invalidates_verified_alias(self):
        database = database_for([('inner', [('value', 'int')], 'int')], 'kernel.elf')
        with patch('fglib.btf_abi.verified_clones', return_value={'inner.isra.0'}):
            self.assertEqual(database.argument_names('inner.isra.0', 6), ('value',))
            module = BtfBuilder()
            arguments(module, 'inner', [('address', 'ptr')])
            database.add(btf.BtfInfo(module.blob(), word_size=8))
            self.assertEqual(database.argument_names('inner.isra.0', 6), ())

    def test_every_candidate_source_must_agree(self):
        database = database_for([('inner', [('value', 'int')], 'int')], 'kernel.elf')
        module = BtfBuilder()
        arguments(module, 'inner', [('value', 'int')], 'int')
        info = btf.BtfInfo(module.blob(), word_size=8)
        info.elf_source = 'module.elf'
        database.add(info)
        with patch('fglib.btf_abi.verified_clones',
                   side_effect=lambda path, names: {'inner.isra.0'} if path == 'kernel.elf' else set()):
            self.assertEqual(database.argument_names('inner.isra.0', 6), ())

    def test_import_keeps_raw_identity_slots_and_return_bits(self):
        for exact in (False, True):
            with self.subTest(exact=exact):
                name = 'inner.constprop.0'
                database = database_for([(name if exact else 'inner', [('value', 'u8')], 's16')], 'kernel.elf')
                records = capture()
                records[5].update(name=name, args=['0xdeadbeefffffffff', '0x55', '0x66'])
                records[6].update(name=name, retval='0xffffffffffff8001')
                with patch('fglib.btf_abi.verified_clones', return_value={name}):
                    builder = inputs.UftraceInput(io.StringIO(stream(records))).build(enable_fold=True, rettypes=database)
                data = builder.to_data()
                index = next(index for index, symbol in enumerate(builder.s_name) if builder.strings[symbol] == name)
                line = builder.s_line[index]
                self.assertEqual(builder.parsed[line - 1].params, 'value=0xff')
                self.assertEqual(builder.strings[builder.s_args[index]], '0xdeadbeefffffffff, 0x55, 0x66')
                self.assertEqual(builder._formatted_return(index), '-32767' if exact else '0xffffffffffff8001')
                self.assertEqual(data['strings'][data['cols']['name'][index]], name)
                self.assertEqual(builder.s_ret[index], records[6]['retval'])

    def test_split_module_exact_clone_uses_its_own_optimized_prototype(self):
        base = BtfBuilder()
        arguments(base, 'inner', [('value', 'int'), ('address', 'ptr')])
        info = btf.BtfInfo(base.blob(), word_size=8)
        module = BtfBuilder(info)
        arguments(module, 'inner.isra.0', [('address', 'ptr')], 'void')
        database = btf.ReturnTypeDB()
        database.add(info)
        database.add(btf.BtfInfo(module.blob(), base=info))
        self.assertEqual(database.argument_names('inner.isra.0', 6), ('address',))
        self.assertEqual(database.function_types('inner.isra.0'),
                         {'args': [('ptr', 64)], 'argc': 1, 'ret': ('void', 0)})

    def test_missing_optional_dependency_is_a_raw_fallback(self):
        original = builtins.__import__

        def without_elftools(name, *args, **kwargs):
            if name.startswith('elftools'):
                raise ImportError(name)
            return original(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=without_elftools):
            self.assertEqual(btf_abi.verified_clones('missing.elf', {'inner.isra.0': ()}), set())

    def test_invalid_or_missing_elf_does_not_break_report(self):
        self.assertEqual(btf_abi.verified_clones('missing.elf', {'inner.isra.0': ()}), set())
        with tempfile.NamedTemporaryFile() as source:
            source.write(b'not an ELF')
            source.flush()
            self.assertEqual(btf_abi.verified_clones(source.name, {'inner.isra.0': ()}), set())


SOURCE = '''
__attribute__((noinline)) unsigned int safe_clone(volatile unsigned char *buffer, unsigned char value)
    __asm__("safe_clone.constprop.0");
unsigned int safe_clone(volatile unsigned char *buffer, unsigned char value) { return *buffer + value; }
__attribute__((noinline)) unsigned int chained_clone(volatile unsigned char *buffer, unsigned char value)
    __asm__("chained_clone.isra.0.llvm.abcd");
unsigned int chained_clone(volatile unsigned char *buffer, unsigned char value) { return *buffer - value; }
__attribute__((noinline)) unsigned int reduced_clone(volatile unsigned char *buffer)
    __asm__("reduced_clone.isra.0");
unsigned int reduced_clone(volatile unsigned char *buffer) { return *buffer; }
__attribute__((noinline)) unsigned int reordered_clone(unsigned char value, volatile unsigned char *buffer)
    __asm__("reordered_clone.constprop.0");
unsigned int reordered_clone(unsigned char value, volatile unsigned char *buffer) { return *buffer + value; }
__attribute__((noinline)) unsigned int widened_clone(volatile unsigned char *buffer, unsigned long value)
    __asm__("widened_clone.llvm.abcd");
unsigned int widened_clone(volatile unsigned char *buffer, unsigned long value) { return *buffer + value; }
int main(int argc, char **argv) { volatile unsigned char buffer = argc; return safe_clone(&buffer, argc); }
'''


@unittest.skipUnless(elftools and shutil.which('gcc') and shutil.which('objcopy'),
                     'requires pyelftools, gcc and objcopy')
class CloneDwarfTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='fg-btf-clones-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / 'clones.c'
        self.source.write_text(SOURCE)
        builder = BtfBuilder()
        for name in ('safe_clone', 'chained_clone', 'reduced_clone', 'reordered_clone', 'widened_clone'):
            arguments(builder, name, [('buffer', 'ptr'), ('value', 'u8')], 'u32')
        self.blob = self.root / 'kernel.btf'
        self.blob.write_bytes(builder.blob())

    def image(self, version=4, prefix=''):
        target = self.root / ('kernel' + prefix + str(version))
        subprocess.run([prefix + 'gcc', '-O2', '-gdwarf-' + str(version), '-o', str(target), str(self.source)],
                       check=True, capture_output=True)
        subprocess.run([prefix + 'objcopy', '--add-section', '.BTF=' + str(self.blob), str(target)],
                       check=True, capture_output=True)
        return target

    def test_real_dwarf4_and_dwarf5_prove_only_unchanged_entry_layouts(self):
        for version in (4, 5):
            with self.subTest(version=version):
                database = btf.build_return_db(self.image(version))
                names = ('safe_clone.constprop.0', 'chained_clone.isra.0.llvm.abcd',
                         'reduced_clone.isra.0', 'reordered_clone.constprop.0', 'widened_clone.llvm.abcd')
                database.prepare_arguments(names)
                for name in names[:2]:
                    self.assertEqual(database.argument_names(name, 6), ('buffer', 'value'))
                    self.assertEqual(database.function_types(name),
                                     {'args': [('ptr', 64), ('uint', 8)], 'argc': 2})
                for name in names[2:]:
                    self.assertEqual(database.argument_names(name, 6), ())
                    self.assertIsNone(database.function_types(name))

    def test_gcc_generated_clones_with_removed_or_scalarized_parameters_stay_raw(self):
        from elftools.elf.elffile import ELFFile

        self.source.write_text('''
struct pair { unsigned int first; unsigned int second; };
__attribute__((noinline)) static unsigned int optimized_pair(struct pair *item) { return item->second; }
__attribute__((noinline)) static unsigned int optimized_value(volatile unsigned char *buffer, unsigned char value)
{ return *buffer + value; }
int main(int argc, char **argv) {
    struct pair item = {argc, argc + 1};
    volatile unsigned char buffer = argc;
    return optimized_pair(&item) + optimized_value(&buffer, 7);
}
''')
        builder = BtfBuilder()
        arguments(builder, 'optimized_pair', [('item', 'ptr')], 'u32')
        arguments(builder, 'optimized_value', [('buffer', 'ptr'), ('value', 'u8')], 'u32')
        self.blob.write_bytes(builder.blob())
        target = self.image()
        with target.open('rb') as source:
            elf = ELFFile(source)
            names = [symbol.name for symbol in elf.get_section_by_name('.symtab').iter_symbols()
                     if symbol.name.startswith(('optimized_pair.', 'optimized_value.'))]
        self.assertEqual(len(names), 2, names)
        database = btf.build_return_db(target)
        database.prepare_arguments(names)
        for name in names:
            self.assertEqual(database.argument_names(name, 6), ())
            self.assertIsNone(database.function_types(name))

    def test_absent_debug_aranges_or_symbol_table_never_guesses(self):
        original = self.image()
        for options in (['--strip-debug'], ['--remove-section=.debug_aranges'], ['--strip-all']):
            with self.subTest(options=options):
                target = self.root / 'stripped'
                subprocess.run(['objcopy', *options, str(original), str(target)], check=True, capture_output=True)
                database = btf.build_return_db(target)
                self.assertEqual(database.argument_names('safe_clone.constprop.0', 6), ())
                self.assertIsNone(database.function_types('safe_clone.constprop.0'))

    @unittest.skipUnless(shutil.which('aarch64-linux-gnu-gcc') and shutil.which('aarch64-linux-gnu-objcopy'),
                         'requires the AArch64 cross toolchain')
    def test_aarch64_dwarf4_and_dwarf5_verify_actual_target_registers(self):
        for version in (4, 5):
            with self.subTest(version=version):
                database = btf.build_return_db(self.image(version, 'aarch64-linux-gnu-'))
                database.prepare_arguments(('safe_clone.constprop.0', 'reduced_clone.isra.0'))
                self.assertEqual(database.argument_names('safe_clone.constprop.0', 6), ('buffer', 'value'))
                self.assertEqual(database.function_types('safe_clone.constprop.0'),
                                 {'args': [('ptr', 64), ('uint', 8)], 'argc': 2})
                self.assertEqual(database.argument_names('reduced_clone.isra.0', 6), ())

    def test_only_single_entry_register_locations_are_accepted(self):
        from elftools.dwarf.locationlists import BaseAddressEntry, LocationEntry, LocationExpr

        parameter = SimpleNamespace(attributes={'DW_AT_location': object()})
        cu = {'version': 4}

        class CompilationUnit(dict):
            def get_top_DIE(self):
                return SimpleNamespace(attributes={'DW_AT_low_pc': SimpleNamespace(value=0x1000)})

        cu = CompilationUnit(cu)
        cases = [(LocationExpr([0x55]), 5), (LocationExpr([0x54]), 4),
                 (LocationExpr([0x91, 0]), None), (LocationExpr([0x30, 0x9f]), None),
                 (LocationExpr([0xa3, 1, 0x55, 0x9f]), None),
                 ([LocationEntry(0, 0, 0, 16, [0x55], False)], 5),
                 ([BaseAddressEntry(0, 0, 0x2000), LocationEntry(0, 0, 0, 16, [0x55], False)], None),
                 ([LocationEntry(0, 0, 0x1000, 0x1010, [0x55], True)], 5),
                 ([LocationEntry(0, 0, 1, 16, [0x55], False)], None),
                 ([LocationEntry(0, 0, 0, 16, [0x55], False),
                   LocationEntry(0, 0, 0, 16, [0x54], False)], None)]
        for parsed, expected in cases:
            with self.subTest(parsed=parsed):
                locations = SimpleNamespace(parse_from_attribute=lambda *args, **kwargs: parsed)
                self.assertEqual(btf_abi._entry_register(parameter, 0x1000, locations, cu), expected)
        parameter.attributes.clear()
        self.assertIsNone(btf_abi._entry_register(parameter, 0x1000, None, cu))

    def test_cyclic_abstract_origins_do_not_loop_forever(self):
        die = SimpleNamespace(attributes={'DW_AT_abstract_origin': object()})
        die.get_DIE_from_attribute = lambda name: die
        self.assertEqual(btf_abi._attribute(die, 'DW_AT_name'), (None, None))


@unittest.skipUnless(os.environ.get('FG_SAMPLE_VMLINUX'), 'set FG_SAMPLE_VMLINUX to the mixed sample kernel')
class RealKernelCloneTest(unittest.TestCase):
    def test_sample_constprop_functions_and_missing_isra_prototype(self):
        database = btf.build_return_db(os.environ['FG_SAMPLE_VMLINUX'])
        expected = {'__napi_poll.constprop.0': ('n', 'repoll'),
                    '__netif_receive_skb_core.constprop.0': ('pskb', 'pfmemalloc', 'ppt_prev'),
                    'selinux_parse_skb.constprop.0': ('skb', 'ad', '_addrp', 'src', 'proto')}
        database.prepare_arguments(expected)
        for name, names in expected.items():
            self.assertEqual(database.argument_names(name, 6), names)
            self.assertNotIn('ret', database.function_types(name))
        self.assertEqual(database.argument_names('inet_addr_hash.isra.0', 6), ())


if __name__ == '__main__':
    unittest.main()
