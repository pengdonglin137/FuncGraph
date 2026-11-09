"""fgreport: presets, the composed funcgraph.py command, output naming, big-trace switch, real run."""
import os
import subprocess
import sys
import tempfile
import unittest

import helpers

SCRIPT = os.path.join(helpers.ROOT, 'fgreport')


class FgreportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ, FG_CONFIG=os.path.join(self.tmp.name, 'config.ini'))
        self.env.pop('FG_PRESET', None)

    def tearDown(self):
        self.tmp.cleanup()

    def run_fg(self, *args, check=True):
        p = subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True, text=True, env=self.env)
        if check:
            self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def command(self, *args):
        """funcgraph.py arguments fgreport would run (dry run)."""
        line = [x for x in self.run_fg('-n', *args).stderr.splitlines() if 'funcgraph.py' in x][0]
        return line.split('funcgraph.py ', 1)[1].split()

    def test_init_presets_and_default(self):
        vml = os.path.join(self.tmp.name, 'vmlinux')
        open(vml, 'w').close()
        self.run_fg('init', 'a', '--vmlinux', vml, '--kernel-src', self.tmp.name, '--source-rev', 'HEAD', '--default')
        self.run_fg('init', 'b', '--vmlinux', vml, '--cross-compile', 'aarch64-linux-gnu-')
        out = self.run_fg('presets').stdout
        self.assertIn('* a', out)
        self.assertIn('  b', out)
        c = self.command('t.txt')
        self.assertEqual(c[c.index('--preset') + 1], 'a')          # default preset
        self.assertIn('--func-links', c)
        self.assertEqual(c[c.index('--output') + 1], 't.html')
        c = self.command('-p', 'b', 'dir/t.txt.gz')
        self.assertEqual(c[c.index('--preset') + 1], 'b')
        self.assertEqual(c[c.index('--output') + 1], 'dir/t.html')
        self.assertNotIn('--highlight-code', c)                     # preset b has no sources
        c = self.command('-P', '--lite', 't.txt')
        self.assertNotIn('--preset', c)
        self.assertNotIn('--func-links', c)
        p = self.run_fg('-p', 'zz', 't.txt', check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("no preset 'zz'", p.stderr)

    def test_passthrough_and_big_trace(self):
        c = self.command('t.txt', '--', '--pid', '7')
        self.assertEqual(c[-3:], ['--pid', '7', 't.txt'])
        big = os.path.join(self.tmp.name, 'big.txt')
        with open(big, 'w') as f:
            f.write('x\n' * 150001)
        self.assertIn('--no-text-view', self.command(big))
        self.assertNotIn('--no-text-view', self.command('--text', big))

    def test_real_report(self):
        out = os.path.join(self.tmp.name, 'r.html')
        self.run_fg('-P', '-o', out, helpers.fixture('pi4_sched'))
        with open(out, encoding='utf-8') as f:
            html = f.read()
        self.assertIn('id="tlData"', html)
        self.assertIn('id="filterInput"', html)


if __name__ == '__main__':
    unittest.main()
