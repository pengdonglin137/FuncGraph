"""Address resolver invocation from checkouts and installed Python packages."""
import os
import sys
import unittest
from unittest.mock import Mock, patch

import helpers
from fglib.resolver import AddressResolver


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


if __name__ == '__main__':
    unittest.main()
