import copy
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import helpers
from fglib.cli import build_parser
from fglib.report import HtmlGenerator
from fglib.util import format_args_info


class TimelineSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kernel = helpers.FakeKernel(inline_source=True)
        trace = Path(cls.kernel.dir, 'source_trace.txt')
        original = Path(helpers.fixture('pi4_full')).read_text().splitlines()
        original.insert(23, '# interleaved trace comment')
        trace.write_text('\n'.join(original) + '\n')
        cls.trace = str(trace)
        options = ('--timeline', '--highlight-code', '--base-url', 'https://example.org/src')
        cls.full, _ = cls.kernel.run(cls.trace, *options, out='full.html')
        cls.kept, _ = cls.kernel.run(cls.trace, *options, '--no-text-view', '--keep-source', out='kept.html')
        cls.thin, _ = cls.kernel.run(cls.trace, *options, '--no-text-view', out='thin.html')

    @classmethod
    def tearDownClass(cls):
        cls.kernel.close()

    def test_source_index_matches_original_text_placeholders(self):
        expected = {str(int(row) + 1): int(source) for row, source in re.findall(
            r'id="line_(\d+)_content" data-src="(\d+)"', self.full)}
        self.assertGreater(len(expected), 100)
        self.assertEqual(helpers.json_script(self.full, 'fgSrcLines'), expected)
        self.assertNotEqual(min(map(int, expected)), 1)

    def test_keep_source_retains_identical_pool_and_original_line_index(self):
        for name in ['fgSrc', 'fgSrcLines']:
            self.assertEqual(helpers.json_script(self.kept, name), helpers.json_script(self.full, name))
        pool = helpers.json_script(self.kept, 'fgSrc')
        self.assertTrue(any('source-line' in block and 'inline_helper' in block for block in pool))
        ids = set(map(int, re.findall(r'class="line-container[^\"]*" id="L(\d+)"', self.kept)))
        self.assertLess(len(ids), 50)
        source_lines = set(map(int, helpers.json_script(self.kept, 'fgSrcLines')))
        self.assertTrue(source_lines.isdisjoint(ids))
        original = Path(self.trace).read_text().splitlines()
        self.assertEqual(ids, {index + 1 for index, line in enumerate(original) if line.startswith('#')})

    def test_complete_timeline_and_inline_data_survive_without_text_dom(self):
        full = helpers.json_script(self.full, 'tlData')
        kept = helpers.json_script(self.kept, 'tlData')
        self.assertTrue(kept.pop('no_text'))
        for data in [full, kept]:
            data['stats'].pop('build_time')
        self.assertGreater(len(kept['inline']), 0)
        self.assertEqual(kept, full)

    def test_default_thin_mode_stays_source_free(self):
        self.assertEqual(helpers.json_script(self.thin, 'fgSrc'), [])
        self.assertEqual(helpers.json_script(self.thin, 'fgSrcLines'), {})
        data = helpers.json_script(self.thin, 'tlData')
        self.assertEqual(data['inline'], {})
        self.assertEqual(data['n'], helpers.json_script(self.full, 'tlData')['n'])

    def test_keep_source_requires_both_timeline_and_no_text_before_output(self):
        output = Path(self.kernel.dir, 'previous.html')
        for options in [[], ['--timeline'], ['--no-text-view'],
                        ['--no-text-view', '--compare', self.trace]]:
            with self.subTest(options=options):
                output.write_text('previous report')
                process = subprocess.run([sys.executable, os.path.join(helpers.ROOT, 'funcgraph.py'),
                                          '--no-config', '--keep-source', '--output', str(output)] +
                                         options + [self.trace], capture_output=True, text=True)
                self.assertNotEqual(process.returncode, 0)
                self.assertIn('--keep-source needs --timeline and --no-text-view', process.stderr)
                self.assertEqual(output.read_text(), 'previous report')

    def test_unused_keep_source_is_absent_from_report_metadata(self):
        parser = build_parser()
        defaults = dict(format_args_info(parser.parse_args([self.trace])))
        self.assertNotIn('Keep Source', defaults)
        explicit = dict(format_args_info(parser.parse_args(
            [self.trace, '--timeline', '--no-text-view', '--keep-source'])))
        self.assertIn('Keep Source', explicit)


class SourceBlockTest(unittest.TestCase):
    def test_kernel_entry_excerpt_rejects_unresolved_missing_and_out_of_root_sources(self):
        from fglib.uftrace import UftraceLine

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            outside = root / 'outside.c'
            outside.write_text('int private_file;\n', encoding='utf-8')
            (source / 'escape.c').symlink_to(outside)
            generator = HtmlGenerator([], None, None, kernel_src=str(source))
            for position in [('??', 1), ('missing.c', 1), ('escape.c', 1), ('../outside.c', 1),
                             (str(outside), 1), ('missing.c', 0), ('missing.c', '??')]:
                with self.subTest(position=position):
                    line = UftraceLine(record_type='entry', space='kernel', raw_func_name='work', func_name_info=position)
                    self.assertIsNone(generator._kernel_source_index(line))
            self.assertEqual(generator._src_pool, [])

    def test_kernel_entry_excerpt_reads_selected_provider_not_modified_worktree(self):
        from fglib.sources import Provider
        from fglib.uftrace import UftraceLine

        provider = Provider()
        provider.files['kernel/fake.c'] = ['int pinned_revision;', 'int selected_line;']
        generator = HtmlGenerator([], None, None, source_provider=provider, base_url='https://kernel.invalid/src')
        line = UftraceLine(record_type='entry', space='kernel', raw_func_name='work',
                           func_name_info=('kernel/fake.c', 2))
        self.assertEqual(generator._kernel_source_index(line), 0)
        self.assertEqual(generator._kernel_source_index(line), 0)
        self.assertEqual(len(generator._src_pool), 1)
        self.assertIn('int selected_line;', generator._src_pool[0])
        self.assertIn('kernel function entry', generator._src_pool[0])
        self.assertIn('https://kernel.invalid/src/kernel/fake.c#L2', generator._src_pool[0])
        generator.func_links = True
        generator.base_url = None
        self.assertIn('<span class="hl-func">work</span>',
                      generator._uftrace_markup(line, '0 | 0 | 1 | K | work() {'))

    def test_cache_uses_full_function_identity_and_accepts_source_id_zero(self):
        generator = HtmlGenerator([], None, None)
        original = generator._adjust_func_info
        with mock.patch.object(generator, '_adjust_func_info', wraps=original) as adjust:
            for _ in range(30):
                self.assertEqual(generator._source_index('caller+0x1/0x20'), 0)
            self.assertEqual(adjust.call_count, 2)
            self.assertEqual(generator._source_index('other+0x1/0x20 [module]'), 1)
        self.assertEqual(len(generator._src_pool), 2)

    def test_structured_locations_preserve_url_module_fallback_and_escaping(self):
        for module_url, base_url in [('https://module.example/src', 'https://kernel.example/src'),
                                     (None, None)]:
            with self.subTest(module_url=module_url):
                generator = HtmlGenerator([], None, None, base_url=base_url, kernel_src='/source')
                generator.combined_module_url_map = {'demo': module_url}
                selector = 'caller+0x1/0x20 [demo]'
                key = generator._adjust_func_info(selector, remove_suffix=False)
                generator.func_locations_map[key] = {key: [
                    {'func': 'inline<&>', 'file': '/source/kernel/fake.c', 'line': 2, 'inlined': True}]}
                before = copy.deepcopy(generator.func_locations_map)
                block = generator._src_pool[generator._source_index(selector)]
                self.assertIn('(inlined)&nbsp;inline&lt;&amp;&gt;&nbsp;at&nbsp;kernel/fake.c:2', block)
                if module_url:
                    self.assertIn('href="https://module.example/src/kernel/fake.c#L2"', block)
                else:
                    self.assertTrue(block.startswith('<div class="location-link">'))
                self.assertEqual(generator.func_locations_map, before)
