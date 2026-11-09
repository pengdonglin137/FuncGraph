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

    def test_args_probe_ignores_graph_filter(self):
        # with -g, vfs_read is only traced inside the graph functions: the args probe must clear
        # set_graph_function first (otherwise it sees no line and wrongly drops retaddr), then restore it
        src = open(SCRIPT).read()
        a = src.index('# the probe must not be limited by -g/-n')
        b = src.index('vfs_read > $T/set_ftrace_filter', a)
        self.assertIn('echo > $T/set_graph_function', src[a:b])
        c = src.index('echo nop > $T/current_tracer; echo > $T/trace; echo > $T/set_ftrace_filter', b)
        self.assertIn('setlist set_graph_function "$GRAPH"', src[c:c + 200])
        self.assertIn('probe saw no vfs_read call', src[b:c])

    @unittest.skipUnless(helpers.have('shellcheck'), 'shellcheck not installed')
    def test_shellcheck(self):
        p = subprocess.run(['shellcheck', '-S', 'error', SCRIPT], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout)


if __name__ == '__main__':
    unittest.main()
