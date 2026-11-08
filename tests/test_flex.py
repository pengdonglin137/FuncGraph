"""Command line flexibility: config files/presets, inputs (stdin, compressed, merged), no vmlinux."""
import gzip
import os
import subprocess
import sys
import tempfile
import unittest

import helpers
from fglib import inputs

PROG = os.path.join(helpers.ROOT, 'funcgraph.py')


def run(args, cwd=None, stdin=None, check=True):
    env = dict(os.environ)
    env.pop('CROSS_COMPILE', None)
    p = subprocess.run([sys.executable, PROG] + args, capture_output=True, text=True, cwd=cwd, input=stdin, env=env)
    if check and p.returncode != 0:
        raise AssertionError(p.stdout + p.stderr)
    return p


class FlexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='fgflex_')
        self.trace = helpers.fixture('pi4_sched')

    def out(self, name='o.html'):
        return os.path.join(self.tmp, name)

    def test_no_vmlinux(self):
        p = run(['--timeline', '--enable-fold', '--output', self.out(), self.trace])
        self.assertIn('no --vmlinux given', p.stdout)
        h = open(self.out()).read()
        self.assertGreater(helpers.json_script(h, 'tlData')['n'], 100)
        self.assertNotIn('data-src=', h)

    def test_stdin_and_gzip_match_plain(self):
        run(['--timeline', '--output', self.out('a.html'), self.trace])
        with open(self.trace) as f:
            text = f.read()
        run(['--timeline', '--output', self.out('b.html'), '-'], stdin=text)
        gz = os.path.join(self.tmp, 't.txt.gz')
        with gzip.open(gz, 'wt') as f:
            f.write(text)
        run(['--timeline', '--output', self.out('c.html'), gz])
        a, b, c = (helpers.json_script(open(self.out(n)).read(), 'tlData') for n in ('a.html', 'b.html', 'c.html'))
        for d in (a, b, c):
            d['stats'].pop('build_time')
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_merge_by_timestamp(self):
        lines = inputs.split_lines(open(self.trace).read())
        on0 = [inputs.RE_TS.match(l) is not None and ' 0) ' in l[:30] for l in lines]
        cpu0 = [l for l, k in zip(lines, on0) if k]
        rest = [l for l, k in zip(lines, on0) if not k]
        p0, p1 = os.path.join(self.tmp, 'c0.txt'), os.path.join(self.tmp, 'c1.txt')
        open(p0, 'w').write('\n'.join(cpu0) + '\n')
        open(p1, 'w').write('\n'.join(rest) + '\n')
        merged, note = inputs.read_lines([p0, p1])
        self.assertIn('merged 2 inputs', note)
        ts = [inputs.RE_TS.match(l) for l in merged]
        vals = [int(m.group(1)) * 10**6 + int(m.group(2)) for m in ts if m]
        self.assertEqual(vals, sorted(vals))
        self.assertEqual(len(merged), len(lines))

    def test_export_json_and_csv(self):
        import csv as _csv
        import json as _json
        j, c = os.path.join(self.tmp, 'a.json'), os.path.join(self.tmp, 'a.csv')
        run(['--export', j, '--output', self.out(), helpers.fixture('vm72_full')])
        d = _json.load(open(j))
        self.assertEqual(set(d), {'meta', 'functions', 'errors', 'sections', 'sched_latency', 'atomic_sleep', 'off_cpu'})
        self.assertTrue(d['meta']['caps']['retval_btf'])
        self.assertTrue(any(e['name'] == 'ENOENT' and e['fate'] == 'user' for e in d['errors']))
        f = d['functions'][0]
        self.assertLessEqual(f['p50'], f['p99'])
        self.assertLessEqual(f['p99'], f['max'])
        run(['--export', c, '--output', self.out(), helpers.fixture('vm72_full')])
        rows = list(_csv.DictReader(open(c)))
        self.assertEqual(len(rows), len(d['functions']))

    def test_config_and_preset(self):
        cfg = os.path.join(self.tmp, 'funcgraph.ini')
        open(cfg, 'w').write('[default]\nenable-fold = true\n\n[preset t]\ntimeline = true\npid = 131108\n')
        run(['--preset', 't', '--output', self.out(), self.trace], cwd=self.tmp)
        h = open(self.out()).read()
        d = helpers.json_script(h, 'tlData')
        self.assertIsNotNone(d)                                   # timeline from the preset
        self.assertIn('fold-entry', h)                            # [default] applied
        self.assertEqual({t['pid'] for t in d['tasks'] if t['kind'] == 0}, {131108})   # pre-filter from config
        # command line wins: --no-config ignores the file
        run(['--no-config', '--output', self.out('n.html'), self.trace], cwd=self.tmp)
        self.assertNotIn('id="tlData"', open(self.out('n.html')).read())

    def test_config_errors(self):
        cfg = os.path.join(self.tmp, 'funcgraph.ini')
        open(cfg, 'w').write('[default]\nno-such-option = 1\n')
        p = run(['--output', self.out(), self.trace], cwd=self.tmp, check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("unknown option 'no-such-option'", p.stderr)
        p = run(['--config', cfg, '--preset', 'x', '--output', self.out(), self.trace], check=False)
        self.assertIn("preset 'x'", p.stderr)


if __name__ == '__main__':
    unittest.main()
