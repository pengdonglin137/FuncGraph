"""Return types stay local to a report and to each comparison's module set."""
import contextlib
import gc
import gzip
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

import helpers
from fglib import btf, cli, compare, inputs, timeline
from fglib.modules import ModuleIndex
from test_btf import BtfBuilder, make_elf
from test_uftrace_import import call, stream

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


def trace(function, module=None):
    suffix = ' [' + module + ']' if module else ''
    return ('# tracer: function_graph\n'
            f'1.000000 |   0)   task-42   |  ...1. |   1.000 us   |  {function}{suffix}(); /* ret=-22 */\n')


class BtfSessionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='fg-btf-session-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.base = BtfBuilder()
        self.integer = self.base.int_('int', 4, signed=True)
        self.base.func('kernel_fn', self.integer)
        self.image = self.root / 'vmlinux'
        self.image.write_bytes(make_elf({'.BTF': self.base.blob()}))
        self.modules = self.root / 'modules'
        self.modules.mkdir()
        self.signed = self.module('signed', 'shared_fn', self.integer)
        self.void = self.module('void', 'shared_fn', 0)

    def module(self, name, function, return_type):
        source = BtfBuilder(btf.BtfInfo(self.base.blob()))
        source.func(function, return_type)
        path = self.modules / (name + '.ko')
        path.write_bytes(make_elf({'.BTF': source.blob()}))
        return str(path)

    def test_sources_are_lazy_and_shared_but_module_views_are_isolated(self):
        with patch('fglib.btf.load_btf', wraps=btf.load_btf) as load:
            loader = btf.ReturnTypeLoader(str(self.image))
            load.assert_not_called()
            signed = loader.build([self.signed])
            void = loader.build([self.void])
            plain = loader.build()
            self.assertIs(loader.build([self.signed, self.signed]), signed)
            self.assertEqual(load.call_count, 3)
        self.assertEqual(signed.lookup('shared_fn')[0], ('sint', 4))
        self.assertEqual(void.lookup('shared_fn')[0], ('void', 0))
        self.assertEqual(plain.lookup('shared_fn'), (None, False))
        self.assertNotIn(self.void, signed.sources)
        self.assertNotIn(self.signed, void.sources)

    def test_missing_modules_are_cached_without_leaking_diagnostics_between_views(self):
        missing = str(self.modules / 'missing.ko')
        with patch('fglib.btf.load_btf', wraps=btf.load_btf) as load:
            loader = btf.ReturnTypeLoader(str(self.image))
            failed = loader.build([missing])
            clean = loader.build([self.signed])
            again = loader.build([missing, self.void])
            self.assertEqual(sum(call.args[0] == missing for call in load.call_args_list), 1)
        self.assertEqual(len(failed.errors), 1)
        self.assertEqual(clean.errors, [])
        self.assertEqual(again.errors, failed.errors)
        self.assertEqual(again.lookup('shared_fn')[0], ('void', 0))

    def test_extra_base_and_split_sources_are_reused(self):
        raw = self.root / 'vmlinux.btf'
        raw.write_bytes(self.base.blob())
        self.image.write_bytes(make_elf({'.text': b'\0'}))
        with patch('fglib.btf.load_btf', wraps=btf.load_btf) as load:
            loader = btf.ReturnTypeLoader(str(self.image), [str(raw), self.void])
            first = loader.build()
            second = loader.build([self.signed])
            self.assertEqual(load.call_count, 4)
        self.assertEqual(first.lookup('shared_fn')[0], ('void', 0))
        self.assertEqual(second.lookup('kernel_fn')[0], ('sint', 4))

    def test_new_loader_does_not_reuse_negative_source_results(self):
        missing = str(self.root / 'late.btf')
        first = btf.ReturnTypeLoader(extra_paths=[missing])
        self.assertEqual(len(first.build()), 0)
        Path(missing).write_bytes(self.base.blob())
        self.assertEqual(len(first.build()), 0)
        self.assertEqual(btf.ReturnTypeLoader(extra_paths=[missing]).build().lookup('kernel_fn')[0], ('sint', 4))

    def test_direct_cli_helpers_never_reuse_another_kernel_database(self):
        arguments = SimpleNamespace(no_btf=False, btf=None, verbose=False)
        first = cli.load_rettypes([], str(self.image), [], arguments)
        replacement = BtfBuilder()
        replacement.func('kernel_fn', 0)
        self.image.write_bytes(make_elf({'.BTF': replacement.blob()}))
        second = cli.load_rettypes([], str(self.image), [], arguments)
        self.assertEqual(first.lookup('kernel_fn')[0], ('sint', 4))
        self.assertEqual(second.lookup('kernel_fn')[0], ('void', 0))

    def test_model_resolves_types_from_recorded_module_identity_before_error_analysis(self):
        seen = []

        def resolve(names):
            seen.append(set(name for name in names if name))
            return btf.build_return_db(str(self.image), module_paths=[self.void])

        data = timeline.build_timeline(trace('shared_fn', 'void').splitlines(), rettype_resolver=resolve).to_data()
        self.assertEqual(seen, [{'void'}])
        self.assertEqual(data['chains'], [])
        self.assertEqual(data['btf'], 2)

    def test_explicit_database_and_resolver_are_not_silently_combined(self):
        database = btf.build_return_db(str(self.image))
        with self.assertRaises(ValueError):
            timeline.build_timeline([], rettypes=database, rettype_resolver=lambda names: database)

    def test_caller_module_does_not_override_the_recorded_function_module(self):
        seen = []
        text = trace('shared_fn', 'void').replace('/* ret=', '/* <-caller+0x1/0x20 [signed] ret=')
        timeline.build_timeline(text.splitlines(), rettype_resolver=lambda names: seen.append(set(names)))
        self.assertEqual(seen, [{'void'}])

    def test_orphan_exit_module_is_available_to_type_resolution(self):
        text = ('# tracer: function_graph\n'
                '1.000000 |   0)   task-42   |  ...1. |   1.000 us   |  } /* shared_fn [void] ret=-22 */\n')
        seen = []
        timeline.build_timeline(text.splitlines(), rettype_resolver=lambda names: seen.append(set(names)))
        self.assertEqual(seen, [{'void'}])

    def test_structured_mixed_input_and_comparison_share_only_the_kernel_base(self):
        arguments = SimpleNamespace(no_btf=False, btf=None, verbose=False)
        resolver = cli.ReturnTypeResolver(str(self.image), [str(self.modules)], arguments)
        current = timeline.build_timeline(trace('shared_fn', 'void').splitlines(), rettype_resolver=resolver).to_data()
        records = [call('entry', 'user', 0, 0, 'kernel_fn', 1),
                   call('exit', 'user', 400, 0, 'kernel_fn', 1, user_retval='-22'),
                   call('entry', 'kernel', 100, 0, 'kernel_fn', 2),
                   call('exit', 'kernel', 300, 0, 'kernel_fn', 2, retval='0xffffffffffffffea')]
        baseline = inputs.UftraceInput(io.StringIO(stream(records)))
        with patch('fglib.btf.load_btf', wraps=btf.load_btf) as load:
            data = compare.compare(current, baseline, rettype_resolver=resolver)
            self.assertEqual(load.call_count, 0)
        rows = {(row[11], row[0]): row for row in data['rows']}
        self.assertEqual(data['errors'], [1, 0])
        self.assertEqual(rows['user', 'kernel_fn'][5], 0)
        self.assertEqual(rows['kernel', 'kernel_fn'][5], 1)


class BtfComparisonTest(unittest.TestCase):
    def setUp(self):
        self.kernel = helpers.FakeKernel()
        self.addCleanup(self.kernel.close)
        self.root = Path(self.kernel.dir)
        self.modules = self.root / 'modules'
        self.modules.mkdir()
        self.base = BtfBuilder()
        self.integer = self.base.int_('int', 4, signed=True)
        self.base.func('kernel_fn', self.integer)
        Path(self.kernel.vmlinux).write_bytes(make_elf({'.BTF': self.base.blob()}))
        for name, return_type in [('signed', self.integer), ('void', 0)]:
            source = BtfBuilder(btf.BtfInfo(self.base.blob()))
            source.func('shared_fn', return_type)
            (self.modules / (name + '.ko')).write_bytes(make_elf({'.BTF': source.blob()}))

    def test_comparison_resolves_each_side_without_loading_the_kernel_twice(self):
        for current_module, baseline_module in [('signed', 'void'), ('void', 'signed'), ('void', 'void'), ('signed', 'signed')]:
            for with_timeline in [False, True]:
                with self.subTest(current=current_module, baseline=baseline_module, timeline=with_timeline):
                    current = self.root / 'current.txt'
                    baseline = self.root / 'baseline.txt'
                    current.write_text(trace('shared_fn', current_module))
                    baseline.write_text(trace('shared_fn', baseline_module))
                    output = self.root / 'report.html'
                    exported = self.root / 'export.json'
                    arguments = ['funcgraph', '--no-config', '--vmlinux', self.kernel.vmlinux,
                                 '--kernel-src', self.kernel.ksrc, '--module-dirs', str(self.modules),
                                 '--compare', str(baseline), '--export', str(exported), '--output', str(output)]
                    if with_timeline:
                        arguments.append('--timeline')
                    arguments.append(str(current))
                    with patch.object(sys, 'argv', arguments), patch('fglib.btf.load_btf', wraps=btf.load_btf) as load, \
                            patch('fglib.modules.os.walk', wraps=os.walk) as walk, \
                            contextlib.redirect_stdout(io.StringIO()):
                        cli.main()
                    data = helpers.json_script(output.read_text(), 'cmpData')
                    expected = [int(baseline_module == 'signed'), int(current_module == 'signed')]
                    self.assertEqual(data['errors'], expected)
                    self.assertEqual([data['rows'][0][5], data['rows'][0][10]], expected)
                    self.assertEqual(len(json.loads(exported.read_text())['errors']), expected[1])
                    self.assertEqual(load.call_count, 2 + int(current_module != baseline_module))
                    self.assertLessEqual(walk.call_count, 1)

    def test_module_only_present_in_baseline_does_not_create_false_errors(self):
        current = self.root / 'current.txt'
        baseline = self.root / 'baseline.txt'
        current.write_text(trace('kernel_fn'))
        baseline.write_text(trace('shared_fn', 'void'))
        report, _ = self.kernel.run(str(current), '--no-config', '--timeline', '--module-dirs', str(self.modules),
                                    '--compare', str(baseline))
        self.assertEqual(helpers.json_script(report, 'cmpData')['errors'], [0, 1])

    def test_repeated_cli_calls_reload_replaced_kernel_btf_and_missing_btf(self):
        source = self.root / 'trace.txt'
        source.write_text(trace('kernel_fn'))
        output = self.root / 'report.html'
        arguments = ['funcgraph', '--no-config', '--vmlinux', self.kernel.vmlinux,
                     '--kernel-src', self.kernel.ksrc, '--timeline', '--output', str(output), str(source)]
        for kind, errors, functions in [('void', 0, 1), ('signed', 1, 1), ('missing', 1, 0)]:
            with self.subTest(kind=kind):
                replacement = BtfBuilder()
                integer = replacement.int_('int', 4, signed=True)
                replacement.func('kernel_fn', 0 if kind == 'void' else integer)
                sections = {'.text': b'\0'} if kind == 'missing' else {'.BTF': replacement.blob()}
                Path(self.kernel.vmlinux).write_bytes(make_elf(sections))
                with patch.object(sys, 'argv', arguments), contextlib.redirect_stdout(io.StringIO()):
                    cli.main()
                data = helpers.json_script(output.read_text(), 'tlData')
                self.assertEqual(len(data['chains']), errors)
                self.assertEqual(data['btf'], functions)

    def test_raw_extra_base_and_gzip_baseline_work_without_vmlinux(self):
        current = self.root / 'current.txt'
        baseline = self.root / 'baseline.txt.gz'
        raw = self.root / 'vmlinux.btf'
        output = self.root / 'comparison.html'
        raw.write_bytes(self.base.blob())
        current.write_text(trace('shared_fn', 'signed'))
        with gzip.open(baseline, 'wt') as stream_file:
            stream_file.write(trace('shared_fn', 'void'))
        result = subprocess.run([sys.executable, str(Path(helpers.ROOT) / 'funcgraph.py'), '--no-config',
                                 '--btf', str(raw), '--module-dirs', str(self.modules), '--timeline',
                                 '--compare', str(baseline), '--output', str(output), str(current)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(helpers.json_script(output.read_text(), 'cmpData')['errors'], [0, 1])

    @unittest.skipIf(sync_playwright is None, 'playwright not installed')
    def test_browser_comparison_displays_correct_typed_error_counts(self):
        current = self.root / 'current.txt'
        baseline = self.root / 'baseline.txt'
        current.write_text(trace('shared_fn', 'signed').replace('ret=-22', 'ret=0xffffffffffffffea'))
        baseline.write_text(trace('shared_fn', 'void').replace('ret=-22', 'ret=0xffffffffffffffea'))
        self.kernel.run(str(current), '--no-config', '--timeline', '--module-dirs', str(self.modules),
                        '--compare', str(baseline), out='comparison.html')
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception as error:
                self.skipTest(str(error))
            try:
                page = browser.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto((self.root / 'comparison.html').as_uri(), wait_until='load')
                page.evaluate("async () => { await fgSwitchView('compare'); }")
                self.assertIn('0 → 1', page.locator('.cmp-pill').filter(has_text='error chains').inner_text())
                cells = page.locator('#compareView tbody tr').first.locator('td').all_text_contents()
                self.assertEqual(cells[0], 'shared_fn')
                self.assertEqual(cells[-3:], ['0', '1', '+1'])
                self.assertEqual(errors, [])
            finally:
                browser.close()

    def test_disabled_btf_does_not_resolve_or_read_modules(self):
        arguments = SimpleNamespace(no_btf=True, btf=None, verbose=False)
        with patch('fglib.btf.load_btf', side_effect=AssertionError('BTF read')), \
                patch('fglib.modules.os.walk', side_effect=AssertionError('module scan')):
            resolver = cli.ReturnTypeResolver(self.kernel.vmlinux, [str(self.modules)], arguments,
                                              module_index=ModuleIndex([self.modules]))
            self.assertIsNone(resolver(['void']))

    def test_cli_releases_parsed_type_sources_before_rendering_html(self):
        current = self.root / 'current.txt'
        baseline = self.root / 'baseline.txt'
        current.write_text(trace('shared_fn', 'signed'))
        baseline.write_text(trace('shared_fn', 'void'))
        arguments = ['funcgraph', '--no-config', '--vmlinux', self.kernel.vmlinux,
                     '--kernel-src', self.kernel.ksrc, '--module-dirs', str(self.modules),
                     '--compare', str(baseline), '--output', str(self.root / 'report.html'), '--timeline', str(current)]
        sources = []
        load_source = btf.load_btf
        render = cli.write_html

        def observed_source(*args, **kwargs):
            source = load_source(*args, **kwargs)
            if source is not None:
                sources.append(weakref.ref(source))
            return source

        def observed_render(*args, **kwargs):
            gc.collect()
            self.assertEqual(len(sources), 3)
            self.assertTrue(all(source() is None for source in sources))
            return render(*args, **kwargs)

        with patch.object(sys, 'argv', arguments), patch('fglib.btf.load_btf', new=observed_source), \
                patch('fglib.cli.write_html', new=observed_render), contextlib.redirect_stdout(io.StringIO()):
            cli.main()


if __name__ == '__main__':
    unittest.main()
