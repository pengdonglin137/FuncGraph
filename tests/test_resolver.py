"""Address resolver invocation from checkouts and installed Python packages."""
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import helpers
from fglib.resolver import AddressResolver
from fglib.inline import parse_chain
from test_inline import PLAIN, TWO


class ResolverCommandTest(unittest.TestCase):
    def test_bundled_resolver_uses_current_python(self):
        script = os.path.join(helpers.ROOT, 'fastfaddr2line.py')
        resolver = AddressResolver(script, '/tmp/vmlinux')
        command = resolver._build_cmd('/tmp/vmlinux', use_list=True)
        self.assertEqual(command[:3], [sys.executable, script, '--list'])
        self.assertEqual(command[-1], '/tmp/vmlinux')

    def test_external_resolver_runs_directly(self):
        resolver = AddressResolver('/tmp/scripts/faddr2line', '/tmp/vmlinux')
        self.assertEqual(resolver._build_cmd('/tmp/vmlinux'),
                         ['/tmp/scripts/faddr2line', '/tmp/vmlinux'])

    def test_function_name_resolution_uses_current_python(self):
        script = os.path.join(helpers.ROOT, 'fastfaddr2line.py')
        resolver = AddressResolver(script, '/tmp/vmlinux')
        with patch('fglib.resolver.subprocess.run', return_value=Mock(returncode=0, stdout='')) as run:
            resolver._resolve_func_list('/tmp/vmlinux', ['vfs_read'])
        self.assertEqual(run.call_args[0][0][:2], [sys.executable, script])


class ResolverOutputTest(unittest.TestCase):
    def resolve(self, output, selector, use_list=False):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'vmlinux')
            tool = os.path.join(directory, 'faddr2line')
            for path in [target, tool]:
                with open(path, 'w'):
                    pass
            resolver = AddressResolver(tool, target)
            with patch('fglib.resolver.subprocess.run', return_value=Mock(stdout=output)):
                return resolver.resolve_batch([selector], use_list=use_list)[selector]

    def test_plain_positions_keep_indented_inlining_markers(self):
        rows = self.resolve(PLAIN, 'arch_stack_walk+0x2cf/0x490')
        self.assertEqual([row['func'] for row in rows],
                         ['kunwind_next_frame_record', 'kunwind_next', 'arch_stack_walk'])
        self.assertEqual([row['inlined'] for row in rows], [False, True, True])
        self.assertEqual(parse_chain(rows), parse_chain(PLAIN))

    def test_batch_modes_keep_first_duplicate_candidate_and_module_identity(self):
        for listed in [False, True]:
            with self.subTest(listed=listed):
                positions = self.resolve(TWO, 'dup+0x10/0x40 [demo]', use_list=listed)
                self.assertEqual(parse_chain(positions), [['dup', 'a.c', 1]])

    def test_source_paths_with_numeric_colons_match_in_both_modes(self):
        path = '/build with spaces/version:123/include/helper.c'
        output = ('parent+0x1/0x20:\nhelper at ' + path + ':27 (discriminator 4)\n'
                  ' (inlined by) parent at /another tree/main.c:18\n')
        expected = [['parent', '/another tree/main.c', 18], ['helper', path, 27]]
        for listed in [False, True]:
            with self.subTest(listed=listed):
                positions = self.resolve(output, 'parent+0x1/0x20', use_list=listed)
                self.assertEqual(parse_chain(positions), expected)
        block = AddressResolver.parse_list_output(output, base_url='https://example.org/src',
                                                  kernel_src='/build with spaces')
        self.assertIn('href="https://example.org/src/version:123/include/helper.c#L27"', block)
        self.assertNotIn('#L123', block)


if __name__ == '__main__':
    unittest.main()
