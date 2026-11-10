"""Small structured source indexes reuse existing resolver output without new lookups."""
import unittest
from unittest.mock import patch

import helpers
from fglib.report import HtmlGenerator


class TimelineSourcesTest(unittest.TestCase):
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
        self.assertNotIn('sources', generator.timeline_json)
        generator.func_locations_map[generator._adjust_func_info('parent+0x1/0x20')] = 'parent at /tree/kernel/parent.c:20'
        generator._attach_timeline_sources()
        self.assertEqual(generator.timeline_json['sources']['callers']['3'][0], ['parent', 'kernel/parent.c', 20, ''])
