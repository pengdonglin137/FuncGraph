"""Small structured source indexes reuse existing resolver output without new lookups."""
import unittest
from unittest.mock import patch

import helpers
from fglib.report import HtmlGenerator


class TimelineSourcesTest(unittest.TestCase):
    @staticmethod
    def structured(locations):
        from fglib.inline import parse_chain

        return [{'func': function, 'file': path, 'line': str(number), 'inlined': index > 0}
                for index, (function, path, number) in enumerate(reversed(parse_chain(locations)))]

    def test_structured_and_text_positions_produce_identical_inline_and_source_indexes(self):
        listed = self.generator()
        plain = self.generator()
        plain.func_locations_map = {selector: self.structured(locations)
                                    for selector, locations in plain.func_locations_map.items()}
        with patch('fglib.report.call_faddr2line_batch', side_effect=AssertionError('new lookup')):
            for generator in [listed, plain]:
                generator._attach_inline()
                generator._attach_timeline_sources()
        self.assertEqual(plain.timeline_json['inline'], listed.timeline_json['inline'])
        self.assertGreater(len(plain.timeline_json['inline']), 0)
        self.assertEqual(plain.timeline_json['sources'], listed.timeline_json['sources'])

    def test_structured_tail_jump_source_keeps_exact_instruction_offset(self):
        generator = HtmlGenerator([], None, None, kernel_src='/tree', base_url='https://kernel.test/src',
            timeline_json={'tail_calls': {'pairs': {'pair': {'sites': [{'symbol': 'parent+0x14/0x20'}]}}}})
        generator.func_locations_map = {
            'parent+0x13/0x20': self.structured('parent at /tree/kernel/parent.c:10'),
            'parent+0x14/0x20': self.structured('helper at /tree/include/helper.h:30\n'
                                               ' (inlined by) parent at /tree/kernel/parent.c:20'),
        }
        generator._attach_tail_sources()
        site = generator.timeline_json['tail_calls']['pairs']['pair']['sites'][0]
        self.assertEqual(site['source'], ['include/helper.h', 30, 'https://kernel.test/src/include/helper.h#L30'])
        self.assertEqual(site['source_function'], 'helper')

    def test_tail_jump_source_uses_exact_instruction_offset_not_return_address_minus_one(self):
        generator = HtmlGenerator([], None, None, kernel_src='/tree', base_url='https://kernel.test/src',
            timeline_json={'tail_calls': {'pairs': {'pair': {'sites': [{'symbol': 'parent+0x14/0x20'}]}}}})
        generator.func_locations_map = {
            'parent+0x13/0x20': 'parent at /tree/kernel/parent.c:10',
            'parent+0x14/0x20': 'helper at /tree/include/helper.h:30\n (inlined by) parent at /tree/kernel/parent.c:20',
        }
        generator._attach_tail_sources()
        site = generator.timeline_json['tail_calls']['pairs']['pair']['sites'][0]
        self.assertEqual(site['source'], ['include/helper.h', 30, 'https://kernel.test/src/include/helper.h#L30'])
        self.assertEqual(site['source_function'], 'helper')

    def generator(self):
        timeline = {'strings': ['parent', 'leaf', 'mod', 'parent+0x1/0x20', 'leaf+0x1/0x20 [mod]'],
                    'cols': {'name': [0, 1, 1], 'module': [-1, 2, 2], 'caller': [-1, 3, 4]}}
        generator = HtmlGenerator([], None, None, kernel_src='/tree', base_url='https://kernel.test/src',
                                  timeline_json=timeline)
        generator.combined_module_url_map = {'mod': 'https://module.test/src'}
        generator.func_name_results = {'parent': ('/tree/kernel/parent.c', 10), 'leaf': ('/tree/drivers/leaf.c', 50)}
        generator.func_locations_map = {
            generator._adjust_func_info('parent+0x1/0x20'): 'helper at /tree/include/helper.h:30\n (inlined by) parent at /tree/kernel/parent.c:20',
            generator._adjust_func_info('leaf+0x1/0x20 [mod]'): 'leaf at /tree/drivers/leaf.c:60',
        }
        return generator

    def test_unique_entry_and_call_site_indexes_preserve_modules_and_inline_order(self):
        generator = self.generator()
        with patch('fglib.report.call_faddr2line_batch', side_effect=AssertionError('new address lookup')):
            generator._attach_timeline_sources()
        sources = generator.timeline_json['sources']
        self.assertEqual(sources['functions'], {
            '0:-1': ['kernel/parent.c', 10, 'https://kernel.test/src/kernel/parent.c#L10'],
            '1:2': ['drivers/leaf.c', 50, 'https://module.test/src/drivers/leaf.c#L50'],
        })
        self.assertEqual(sources['callers']['3'], [
            ['parent', 'kernel/parent.c', 20, 'https://kernel.test/src/kernel/parent.c#L20'],
            ['helper', 'include/helper.h', 30, 'https://kernel.test/src/include/helper.h#L30'],
        ])
        self.assertEqual(sources['callers']['4'][0][3], 'https://module.test/src/drivers/leaf.c#L60')

    def test_missing_sources_are_not_invented_and_url_is_optional(self):
        generator = self.generator()
        generator.base_url = None
        generator.func_name_results = {}
        generator.func_locations_map = {}
        generator._attach_timeline_sources()
        self.assertEqual(generator.timeline_json['sources'], {'functions': {}, 'callers': {'3': [], '4': []}})
        snapshot = generator.timeline_json['sources']
        generator.func_locations_map[generator._adjust_func_info('parent+0x1/0x20')] = 'parent at /tree/kernel/parent.c:20'
        generator._attach_timeline_sources()
        self.assertIs(generator.timeline_json['sources'], snapshot)
        self.assertEqual(snapshot['callers']['3'], [])
        generator = self.generator()
        generator.base_url = None
        generator._attach_timeline_sources()
        self.assertEqual(generator.timeline_json['sources']['callers']['3'][0], ['parent', 'kernel/parent.c', 20, ''])

    def test_missing_resolver_output_is_explicit_but_unparsed_output_stays_unknown(self):
        generator = self.generator()
        missing = generator._adjust_func_info('parent+0x1/0x20')
        unsupported = generator._adjust_func_info('leaf+0x1/0x20 [mod]')
        generator.func_locations_map = {missing: '', unsupported: 'unparsed provider output'}
        with patch('fglib.report.call_faddr2line_batch', side_effect=AssertionError('new lookup')):
            generator._attach_timeline_sources()
        callers = generator.timeline_json['sources']['callers']
        self.assertEqual(callers, {'3': []})
        self.assertNotIn('4', callers)
