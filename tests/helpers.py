"""Shared helpers for the FuncGraph self-tests (standard library only)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'tests', 'data')
sys.path.insert(0, ROOT)

# Captured on a Raspberry Pi 4 (6.18, arm64) with different option sets, first 1500 lines each.
FIXTURES = {
    'pi4_full': 'abstime + proc + latency-format + retval + retaddr + tail',
    'pi4_default': 'kernel defaults: cpu + duration + overhead + irqs',
    'pi4_sched': 'full + sched_switch/sched_wakeup events',
    'pi4_noproc': 'abstime + latency-format + retval, no funcgraph-proc',
    'pi4_nodur': 'abstime + proc + retval, no funcgraph-duration',
    'pi4_args': 'abstime + proc + retval + args, no retaddr',
}

FAKE_FADDR2LINE = """#!/bin/sh
# Fake faddr2line: resolves every func+off/len to kernel/fake.c:2
for a in "$@"; do
  case "$a" in
    --*|*vmlinux*) ;;
    *) echo "$a:"; echo "${a%%+*} at $FAKE_SRC/kernel/fake.c:2" ;;
  esac
done
"""


def fixture(name):
    return os.path.join(ROOT, 'ftrace.txt') if name == 'x86' else os.path.join(DATA, name + '.txt')


class FakeKernel:
    """A temp dir with an empty vmlinux and kernel-src/scripts/faddr2line stub."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix='fgtest_')
        self.ksrc = os.path.join(self.dir, 'ksrc')
        os.makedirs(os.path.join(self.ksrc, 'scripts'))
        os.makedirs(os.path.join(self.ksrc, 'kernel'))
        with open(os.path.join(self.ksrc, 'kernel', 'fake.c'), 'w') as f:
            f.write('int a;\nint b;\nint c;\n')
        tool = os.path.join(self.ksrc, 'scripts', 'faddr2line')
        with open(tool, 'w') as f:
            f.write(FAKE_FADDR2LINE)
        os.chmod(tool, 0o755)
        self.vmlinux = os.path.join(self.dir, 'vmlinux')
        with open(self.vmlinux, 'wb') as f:
            f.write(b'\0' * 64)

    def run(self, trace, *opts, out='out.html'):
        """Run funcgraph.py, return (html_text, completed_process)."""
        out = os.path.join(self.dir, out)
        cmd = [sys.executable, os.path.join(ROOT, 'funcgraph.py'), '--vmlinux', self.vmlinux,
               '--kernel-src', self.ksrc, '--output', out] + list(opts) + [trace]
        env = dict(os.environ, FAKE_SRC=self.ksrc)
        env.pop('CROSS_COMPILE', None)
        p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)
        if p.returncode != 0:
            raise AssertionError(f'funcgraph.py failed ({p.returncode}):\n{p.stdout}\n{p.stderr}')
        with open(out, encoding='utf-8') as f:
            return f.read(), p

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def json_script(html_text, elem_id):
    m = re.search(r'<script type="application/json" id="%s">(.*?)</script>' % re.escape(elem_id), html_text, re.S)
    return json.loads(m.group(1)) if m else None


def have(cmd):
    return shutil.which(cmd) is not None
