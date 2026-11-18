"""Entry excerpts supplement missing native call sites without replacing evidence."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import helpers
from fglib import report
from fglib.model import FtraceLine
from fglib.report import HtmlGenerator
from fglib.uftrace import UftraceLine


SOURCE = 'int entry(int value)\n{\n    return value + 1;\n}\n'


class KernelEntrySourceTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='fg-entry-source-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'kernel').mkdir()
        (self.root / 'kernel/entry.c').write_text(SOURCE)
        self.generator = HtmlGenerator([], None, None, kernel_src=str(self.root),
                                       base_url='https://kernel.invalid/src')

    def line(self, **fields):
        values = dict(raw_line=' 0)   1.000 us    |  entry(value=5);', expandable=True,
                      raw_func_name='entry', display_func_name='entry',
                      func_name_info=('kernel/entry.c', 2), cpu=0)
        values.update(fields)
        return FtraceLine(**values)

    def test_native_entry_without_recorded_caller_has_clickable_source(self):
        line = self.line()
        before = {name: copy.deepcopy(getattr(line, name)) for name in line.__slots__}
        self.generator.parsed_lines = [line]
        self.generator.timeline_json = {}
        content = ''.join(self.generator._iter_content_lines())
        self.assertIn('class="line-container expandable', content)
        self.assertIn('data-src="0"', content)
        self.assertEqual(self.generator._src_lines, {1: 0})
        block = self.generator._src_pool[0]
        self.assertIn('kernel function entry', block)
        self.assertIn('return value + 1;', block)
        self.assertIn('href="https://kernel.invalid/src/kernel/entry.c#L2"', block)
        self.assertEqual({name: getattr(line, name) for name in line.__slots__}, before)

    def test_native_recorded_caller_and_partial_inline_evidence_keep_precedence(self):
        selector = 'caller+0x1/0x20'
        for position in ('caller at kernel/caller.c:7',
                         'caller at kernel/caller.c:7\n (inlined by) unresolved at ??:0'):
            with self.subTest(position=position):
                generator = HtmlGenerator([self.line(func_info=selector)], None, None,
                                          kernel_src=str(self.root), base_url='https://kernel.invalid/src')
                generator.func_locations_map[generator._adjust_func_info(selector, remove_suffix=False)] = (
                    selector + ':\n' + position + '\n')
                expected = generator._src_pool[generator._source_index(selector)]
                ''.join(generator._iter_content_lines())
                self.assertEqual(generator._src_pool, [expected])
                self.assertIn('caller', expected)
                self.assertNotIn('kernel function entry', expected)

    def test_entry_position_is_resolved_once_across_repeated_native_rows(self):
        line = self.line()
        with mock.patch.object(self.generator, '_timeline_position', wraps=self.generator._timeline_position) as position:
            indices = [self.generator._kernel_source_index(line) for index in range(100)]
        self.assertEqual(indices, [0] * 100)
        position.assert_called_once_with('kernel/entry.c', 2, None)
        self.assertEqual(len(self.generator._src_pool), 1)

    def test_cached_entry_sources_keep_distinct_module_links(self):
        self.generator.combined_module_url_map = {'one': 'https://one.invalid/src',
                                                  'two': 'https://two.invalid/src'}
        indices = [self.generator._kernel_source_index(self.line(module_name=module))
                   for module in ('one', 'two', 'one')]
        self.assertEqual(indices, [0, 1, 0])
        for index, module in enumerate(('one', 'two')):
            self.assertIn(f'https://{module}.invalid/src/kernel/entry.c#L2', self.generator._src_pool[index])

    def test_absent_unresolved_and_unreadable_entry_sources_do_not_invent_blocks(self):
        outside = self.root.parent / (self.root.name + '-outside.c')
        outside.write_text('int outside;\n')
        self.addCleanup(outside.unlink)
        (self.root / 'escape.c').symlink_to(outside)
        positions = [None, ('??', 1), ('kernel/entry.c', 0), ('kernel/entry.c', -1),
                     ('kernel/entry.c', '??'), ('kernel/missing.c', 1), ('kernel/entry.c', 99),
                     ('escape.c', 1), (str(outside), 1)]
        for position in positions:
            with self.subTest(position=position):
                self.assertIsNone(self.generator._kernel_source_index(self.line(func_name_info=position)))
        self.assertEqual(self.generator._src_pool, [])

    def test_userspace_never_uses_kernel_entry_sources(self):
        line = UftraceLine(record_type='entry', space='user', raw_func_name='entry',
                           func_name_info=('kernel/entry.c', 2))
        self.assertIsNone(self.generator._kernel_source_index(line))
        self.assertEqual(self.generator._src_pool, [])

    def test_native_return_rows_do_not_gain_duplicate_entry_source_controls(self):
        line = self.line(raw_line=' 0)   2.000 us    |  } /* entry ret=0 */', expandable=False)
        self.generator.parsed_lines = [line]
        content = ''.join(self.generator._iter_content_lines())
        self.assertNotIn('expand-btn', content)
        self.assertEqual(self.generator._src_pool, [])

    def test_structured_call_site_validation_is_reused_without_changing_precedence(self):
        selector = 'caller+0x1/0x20'
        self.generator.func_locations_map[self.generator._adjust_func_info(selector, remove_suffix=False)] = (
            selector + ':\ncaller at kernel/caller.c:7\n')
        line = UftraceLine(record_type='entry', space='kernel', raw_func_name='entry', expandable=True,
                           func_info=selector, func_name_info=('kernel/entry.c', 2))
        with mock.patch('fglib.report.parse_chain', wraps=report.parse_chain) as chain:
            indices = [self.generator._line_source_index(line) for index in range(100)]
        self.assertEqual(indices, [0] * 100)
        self.assertEqual(chain.call_count, 1)
        self.assertIn('caller', self.generator._src_pool[0])
        self.assertNotIn('kernel function entry', self.generator._src_pool[0])

    def test_structured_unresolved_caller_keeps_existing_entry_fallback_and_cache(self):
        line = UftraceLine(record_type='entry', space='kernel', raw_func_name='entry', expandable=True,
                           func_info='missing+0x1/0x20', func_name_info=('kernel/entry.c', 2))
        with mock.patch('fglib.report.parse_chain', wraps=report.parse_chain) as chain:
            indices = [self.generator._line_source_index(line) for index in range(100)]
        self.assertEqual(indices, [0] * 100)
        self.assertEqual(chain.call_count, 1)
        self.assertIn('kernel function entry', self.generator._src_pool[0])
