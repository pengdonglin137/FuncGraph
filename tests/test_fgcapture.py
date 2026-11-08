"""fgcapture.sh: static checks (capturing itself needs root + tracefs; tested manually on a Pi 4)."""
import os
import subprocess
import unittest

import helpers

SCRIPT = os.path.join(helpers.ROOT, 'fgcapture.sh')


class FgcaptureTest(unittest.TestCase):
    def test_bash_syntax(self):
        p = subprocess.run(['bash', '-n', SCRIPT], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_help(self):
        p = subprocess.run(['bash', SCRIPT, '--help'], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        for word in ('--args', '--sched', '--calibrate', 'set_graph_function', 'Examples'):
            self.assertIn(word, p.stdout)

    def test_refuses_without_root(self):
        if os.geteuid() == 0:
            self.skipTest('running as root')
        p = subprocess.run(['bash', SCRIPT, '-d', '0'], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('root', p.stderr)

    @unittest.skipUnless(helpers.have('shellcheck'), 'shellcheck not installed')
    def test_shellcheck(self):
        p = subprocess.run(['shellcheck', '-S', 'error', SCRIPT], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout)


if __name__ == '__main__':
    unittest.main()
