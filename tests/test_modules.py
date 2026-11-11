"""Exact, bounded module discovery shared by source and BTF resolution."""
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import helpers
from fglib import btf, cli
from fglib.modules import ModuleIndex
from fglib.parser import FtraceParser
from fglib.report import HtmlGenerator
from fglib.util import find_module_path
from test_btf import BtfBuilder, make_elf


class _ModuleTree(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='fg-module-lookup-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def module(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return str(path)


class ModuleLookupTest(_ModuleTree):
    def test_missing_names_terminate_without_recursion_or_external_find(self):
        previous = sys.getrecursionlimit()
        self.addCleanup(sys.setrecursionlimit, previous)
        sys.setrecursionlimit(200)
        with patch('fglib.util.subprocess.run', return_value=SimpleNamespace(stdout='')) as run:
            for name in ['missing_module', 'missing-module', 'missing_plain']:
                with self.subTest(name=name):
                    self.assertIsNone(find_module_path(name, [str(self.root)]))
            run.assert_not_called()

    def test_substring_matches_cannot_supply_an_unrelated_module(self):
        self.module('demo_extra.ko')
        self.module('prefix_demo.ko.debug')
        with patch('fglib.util.subprocess.run', return_value=SimpleNamespace(stdout='')):
            self.assertIsNone(find_module_path('demo', [str(self.root)]))

    def test_nested_exact_module_wins_over_a_shallow_substring(self):
        self.module('demo_extra.ko')
        expected = self.module('kernel/drivers/demo.ko')
        self.assertEqual(find_module_path('demo', [str(self.root)]), expected)

    def test_aliases_are_exact_after_normalizing_mixed_hyphens_and_underscores(self):
        self.module('mixed_module_name_extra.ko')
        expected = self.module('kernel/mixed-module_name.ko')
        with patch('fglib.util.subprocess.run', return_value=SimpleNamespace(stdout='')):
            self.assertEqual(find_module_path('mixed_module_name', [str(self.root)]), expected)

    def test_module_suffixes_remain_supported(self):
        for suffix in ['.ko', '.ko.debug', '.ko.gz', '.ko.xz', '.ko.zst']:
            with self.subTest(suffix=suffix):
                expected = self.module('format' + suffix)
                self.assertEqual(find_module_path('format', [str(self.root)]), expected)
                Path(expected).unlink()

    def test_plain_module_is_preferred_to_debug_and_compressed_variants(self):
        self.module('format.ko.debug')
        self.module('format.ko.xz')
        expected = self.module('kernel/format.ko')
        self.assertEqual(find_module_path('format', [str(self.root)]), expected)

    def test_directories_and_broken_symlinks_are_not_module_files(self):
        (self.root / 'demo.ko').mkdir()
        (self.root / 'broken.ko').symlink_to(self.root / 'absent')
        with patch('fglib.util.subprocess.run', return_value=SimpleNamespace(stdout='')):
            self.assertIsNone(find_module_path('demo', [str(self.root)]))
            self.assertIsNone(find_module_path('broken', [str(self.root)]))

    def test_supplied_directory_order_is_preserved(self):
        expected = self.module('first/kernel/demo.ko.debug')
        self.module('second/demo.ko')
        self.assertEqual(find_module_path('demo', [self.root / 'first', self.root / 'second']), expected)

    def test_duplicate_locations_have_a_stable_shallow_then_lexical_order(self):
        self.module('z/demo.ko')
        self.module('deep/nested/demo.ko')
        expected = self.module('a/demo.ko')
        self.assertEqual(find_module_path('demo', [str(self.root)]), expected)

    def test_requested_names_do_not_expand_globs_or_escape_the_search_root(self):
        self.module('outside.ko')
        inner = self.root / 'inner'
        inner.mkdir()
        with patch('fglib.util.subprocess.run', return_value=SimpleNamespace(stdout='')):
            for name in ['../outside', '*', 'outside?']:
                with self.subTest(name=name):
                    self.assertIsNone(find_module_path(name, [str(inner)]))


class ModuleIndexTest(_ModuleTree):
    def test_best_possible_direct_module_does_not_scan_a_large_tree(self):
        expected = self.module('demo.ko')
        with patch('fglib.modules.os.walk', side_effect=AssertionError('unnecessary recursive scan')):
            index = ModuleIndex([self.root])
            self.assertEqual(index.find('demo'), expected)
            self.assertEqual(index.find('demo'), expected)

    def test_all_positive_and_negative_lookups_share_one_lazy_scan(self):
        first = self.module('a/first-module.ko')
        second = self.module('b/second.ko.debug')
        with patch('fglib.modules.os.walk', wraps=os.walk) as walk, \
                patch('fglib.util.subprocess.run') as run:
            index = ModuleIndex([self.root])
            walk.assert_not_called()
            for iteration in range(3):
                self.assertEqual(index.find('first_module'), first)
                self.assertEqual(index.find('second'), second)
                self.assertIsNone(index.find('missing_module'))
            self.assertEqual(walk.call_count, 1)
            run.assert_not_called()

    def test_overlapping_roots_and_directory_aliases_reuse_scanned_trees(self):
        expected = self.module('child/deeper/demo.ko')
        alias = self.root / 'alias'
        alias.symlink_to(self.root / 'child', target_is_directory=True)
        with patch('fglib.modules.os.walk', wraps=os.walk) as walk:
            index = ModuleIndex([self.root, self.root / 'child', alias, self.root])
            self.assertEqual(index.find('demo'), expected)
            self.assertEqual(walk.call_count, 1)

    def test_new_reports_do_not_reuse_stale_global_negative_results(self):
        first = ModuleIndex([self.root])
        self.assertIsNone(first.find('new_module'))
        expected = self.module('new-module.ko')
        self.assertIsNone(first.find('new_module'))
        self.assertEqual(ModuleIndex([self.root]).find('new_module'), expected)

    def test_symlink_loops_are_not_followed_and_file_aliases_are_usable(self):
        target = Path(self.module('target/unrelated.bin'))
        linked = self.root / 'demo.ko'
        linked.symlink_to(target)
        (self.root / 'loop').symlink_to(self.root, target_is_directory=True)
        index = ModuleIndex([self.root])
        self.assertEqual(index.find('demo'), str(linked))
        self.assertIsNone(index.find('missing'))

    def test_exact_spelling_precedes_normalized_aliases_in_the_same_root(self):
        self.module('demo-module.ko')
        expected = self.module('demo_module.ko.debug')
        self.assertEqual(ModuleIndex([self.root]).find('demo_module'), expected)


class ModulePipelineTest(unittest.TestCase):
    def setUp(self):
        self.kernel = helpers.FakeKernel(inline_source=True)
        self.addCleanup(self.kernel.close)
        self.root = Path(self.kernel.dir)
        self.modules = self.root / 'modules'
        self.modules.mkdir()
        base = BtfBuilder()
        integer = base.int_('int', 4, signed=True)
        base.func('kernel_fn', integer)
        Path(self.kernel.vmlinux).write_bytes(make_elf({'.BTF': base.blob()}))
        module = BtfBuilder(btf.BtfInfo(base.blob()))
        module.func('mod_fn', integer)
        self.expected = self.modules / 'nested' / 'mixed-module_name.ko'
        self.expected.parent.mkdir()
        self.expected.write_bytes(make_elf({'.BTF': module.blob()}))
        wrong = BtfBuilder(btf.BtfInfo(base.blob()))
        wrong.func('mod_fn', 0)
        (self.modules / 'mixed_module_name_extra.ko').write_bytes(make_elf({'.BTF': wrong.blob()}))
        self.trace = self.root / 'trace.txt'
        self.trace.write_text('# tracer: function_graph\n'
                              ' 2) mod_fn [mixed_module_name](); /* <-caller+0x1/0x20 [mixed_module_name] */\n')

    def test_btf_function_entries_and_call_sites_use_the_same_module_snapshot(self):
        from fglib.report import call_faddr2line_batch, call_faddr2line_for_func_names

        lines = FtraceParser().parse(str(self.trace))
        index = ModuleIndex([self.modules])
        arguments = SimpleNamespace(no_btf=False, btf=None, verbose=False)
        environment = dict(FAKE_SRC=self.kernel.ksrc, FAKE_SOURCE_FILE=self.kernel.source_file)
        with patch.dict(os.environ, environment), patch('fglib.modules.os.walk', wraps=os.walk) as walk, \
                patch('fglib.report.call_faddr2line_for_func_names', wraps=call_faddr2line_for_func_names) as names, \
                patch('fglib.report.call_faddr2line_batch', wraps=call_faddr2line_batch) as addresses:
            database = cli.load_rettypes(lines, self.kernel.vmlinux, [str(self.modules)], arguments,
                                         module_index=index)
            generator = HtmlGenerator(lines, self.kernel.vmlinux,
                                      str(Path(self.kernel.ksrc) / 'scripts/faddr2line'),
                                      module_dirs=[str(self.modules)], module_index=index,
                                      kernel_src=self.kernel.ksrc, func_links=True, use_list=True)
            report = generator.generate()[0]
        self.assertEqual(database.lookup('mod_fn'), (('sint', 4), False))
        self.assertIn(str(self.expected), database.sources)
        self.assertEqual(names.call_args.kwargs['module_paths'], {'mixed_module_name': str(self.expected)})
        self.assertEqual([call.args[1] for call in addresses.call_args_list], [str(self.expected)])
        self.assertEqual(walk.call_count, 1)
        self.assertIn('mod_fn', generator.func_name_results)
        self.assertTrue(generator.func_locations_map)
        self.assertIn('mod_fn', report)

    def test_cli_reuses_one_index_and_thin_reports_do_not_scan_unused_trees(self):
        for thin in [False, True]:
            with self.subTest(thin=thin):
                indexes = []

                def make_index(*args, **kwargs):
                    index = ModuleIndex(*args, **kwargs)
                    indexes.append(index)
                    return index

                arguments = ['funcgraph', '--no-config', '--vmlinux', self.kernel.vmlinux,
                             '--kernel-src', self.kernel.ksrc, '--module-dirs', str(self.modules),
                             '--timeline', '--func-links', '--compare', str(self.trace),
                             '--export', str(self.root / 'stats.json'), '--output', str(self.root / 'report.html')]
                if thin:
                    arguments += ['--no-btf', '--no-text-view']
                arguments.append(str(self.trace))
                environment = dict(FAKE_SRC=self.kernel.ksrc, FAKE_SOURCE_FILE=self.kernel.source_file,
                                   PATH=str(Path(self.kernel.ksrc) / 'scripts') + os.pathsep + os.environ['PATH'],
                                   CROSS_COMPILE='', LLVM='')
                with patch.object(sys, 'argv', arguments), patch.dict(os.environ, environment), \
                        patch('fglib.cli.ModuleIndex', side_effect=make_index), \
                        patch('fglib.report.ModuleIndex', side_effect=AssertionError('unshared module index')), \
                        patch('fglib.modules.os.walk', wraps=os.walk) as walk, \
                        contextlib.redirect_stdout(io.StringIO()):
                    cli.main()
                self.assertEqual(len(indexes), 1)
                self.assertEqual(walk.call_count, 0 if thin else 1)
                self.assertTrue((self.root / 'report.html').is_file())
                self.assertTrue((self.root / 'stats.json').is_file())

    def test_missing_modules_do_not_stop_a_complete_report_or_synthesize_sources(self):
        text = self.trace.read_text().replace('mixed_module_name', 'missing_module')
        self.trace.write_text(text)
        report, process = self.kernel.run(str(self.trace), '--no-config', '--module-dirs', str(self.modules),
                                          '--timeline', '--func-links')
        self.assertEqual(process.returncode, 0)
        self.assertIn('mod_fn', report)
        self.assertNotIn('RecursionError', process.stderr)
        timeline = helpers.json_script(report, 'tlData')
        self.assertEqual(timeline['n'], 1)
        self.assertEqual(timeline['sources']['functions'], {})
        self.assertTrue(all(not frames for frames in timeline['sources']['callers'].values()))


if __name__ == '__main__':
    unittest.main()
