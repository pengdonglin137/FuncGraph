"""fgcapture.sh: static checks (capturing itself needs root + tracefs; tested manually on a Pi 4)."""
import os
from pathlib import Path
import subprocess
import tempfile
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
        src = Path(SCRIPT).read_text()
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


class FgcaptureProbeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='fg-kernel-probe-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tracefs = self.root / 'tracefs'
        self.binary = self.root / 'bin'
        self.binary.mkdir()
        self.values = {'current_tracer': 'nop', 'tracing_on': '0', 'buffer_size_kb': '1234',
                       'available_tracers': 'nop function_graph', 'trace': '',
                       'set_ftrace_pid': '', 'set_graph_function': '', 'set_graph_notrace': '',
                       'set_ftrace_filter': '', 'per_cpu/cpu0/stats': 'overrun: 0'}
        for name in ('funcgraph-abstime', 'funcgraph-proc', 'funcgraph-retval', 'funcgraph-retaddr',
                     'funcgraph-tail', 'funcgraph-args', 'funcgraph-duration', 'funcgraph-cpu',
                     'funcgraph-overhead', 'funcgraph-irqs', 'latency-format', 'function-fork'):
            self.values['options/' + name] = '1' if name == 'function-fork' else '0'
        for event in ('sched_switch', 'sched_wakeup', 'sched_waking', 'sched_process_exec', 'sched_process_exit'):
            self.values['events/sched/' + event + '/enable'] = '0'
        for name, value in self.values.items():
            path = self.tracefs / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value + '\n')
        self.probe = self.root / 'probe'
        self.probe.write_text('  vfs_read(file=0x1, buf=0x2, count=1) <- ksys_read+0x1/0x2;\n')
        self.output = self.root / 'trace.txt'
        shims = {
            'id': '#!/bin/sh\necho 0\n',
            'cat': '''#!/bin/bash
if [ "$#" = 1 ] && [ "$1" = "$FG_TRACEFS/trace" ]; then
    if /bin/grep -q vfs_read "$FG_TRACEFS/set_ftrace_filter"; then
        exec /bin/cat "$FG_PROBE_FILE"
    fi
    printf 'capture args=%s retaddr=%s exec=%s exit=%s\\n' "$(/bin/cat "$FG_TRACEFS/options/funcgraph-args" 2>/dev/null)" "$(/bin/cat "$FG_TRACEFS/options/funcgraph-retaddr")" "$(/bin/cat "$FG_TRACEFS/events/sched/sched_process_exec/enable")" "$(/bin/cat "$FG_TRACEFS/events/sched/sched_process_exit/enable")"
else
    exec /bin/cat "$@"
fi
''',
            'date': '''#!/bin/bash
if [ "$1" = +%s%N ]; then
    if [ -f "$FG_DATE_STATE" ]; then echo "$FG_PROBE_NS"; else touch "$FG_DATE_STATE"; echo 0; fi
else
    exec /bin/date "$@"
fi
''',
        }
        for name, content in shims.items():
            path = self.binary / name
            path.write_text(content)
            path.chmod(0o755)

    def run_capture(self, *arguments, nanoseconds='1000'):
        environment = dict(os.environ, FG_TRACEFS=str(self.tracefs),
                           FG_CAPTURE_LOCK=str(self.root / 'lock'),
                           FG_PROBE_FILE=str(self.probe), FG_PROBE_NS=nanoseconds,
                           FG_DATE_STATE=str(self.root / 'date-state'),
                           PATH=str(self.binary) + ':' + os.environ['PATH'])
        return subprocess.run(['bash', SCRIPT, '-o', str(self.output), '-d', '0', *arguments],
                              env=environment, text=True, capture_output=True, timeout=10)

    def assert_restored(self):
        for name, expected in self.values.items():
            if name in ('trace', 'available_tracers'):
                continue
            self.assertEqual((self.tracefs / name).read_text().strip(), expected, name)

    def test_default_auto_enables_fast_compatible_arguments_and_restores_state(self):
        result = self.run_capture('--sched')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('arguments enabled automatically', result.stdout)
        self.assertIn('args=1 retaddr=1 exec=1 exit=1', self.output.read_text())
        self.assert_restored()

    def test_default_auto_safely_falls_back_on_slow_kernel(self):
        result = self.run_capture(nanoseconds='2000000')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('automatic arguments disabled', result.stderr)
        self.assertIn('args=0 retaddr=1', self.output.read_text())
        self.assert_restored()

    def test_default_auto_preserves_call_sites_when_args_and_retaddr_are_incompatible(self):
        self.probe.write_text('  vfs_read() <- ksys_read+0x1/0x2;\n')
        result = self.run_capture()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('args=0 retaddr=1', self.output.read_text())
        self.assert_restored()

    def test_empty_probe_is_inconclusive_not_compatible(self):
        self.probe.write_text('# empty trace\n\n')
        result = self.run_capture()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('probe saw no vfs_read call', result.stderr)
        self.assertIn('args=0 retaddr=1', self.output.read_text())
        self.assert_restored()

    def test_no_args_skips_probe_entirely(self):
        result = self.run_capture('--no-args')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'date-state').exists())
        self.assertIn('args=0 retaddr=1', self.output.read_text())
        self.assert_restored()

    def test_explicit_args_on_old_kernel_requires_scope_and_restores_on_error(self):
        self.probe.write_text('  vfs_read(file=0x1);\n')
        result = self.run_capture('--args', nanoseconds='2000000')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('needs -g, -p or a command', result.stderr)
        self.assert_restored()

    def test_explicit_scoped_args_keep_arguments_on_old_kernel_without_retaddr(self):
        self.probe.write_text('  vfs_read(file=0x1);\n')
        result = self.run_capture('--args', '-g', 'vfs_read', '-p', str(os.getpid()), nanoseconds='2000000')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('args=1 retaddr=0', self.output.read_text())
        self.assert_restored()

    def test_command_restores_existing_function_fork_option(self):
        result = self.run_capture('--no-args', '--', '/bin/true')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_restored()

    def test_active_capture_is_refused_without_changing_settings(self):
        self.values['tracing_on'] = '1'
        (self.tracefs / 'tracing_on').write_text('1\n')
        result = self.run_capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('refusing to interrupt', result.stderr)
        self.assert_restored()

    def test_missing_args_capability_falls_back_or_fails_if_explicitly_required(self):
        del self.values['options/funcgraph-args']
        (self.tracefs / 'options/funcgraph-args').unlink()
        result = self.run_capture()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('automatic arguments unavailable', result.stderr)
        self.assertFalse((self.root / 'date-state').exists())
        result = self.run_capture('--args')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires kernel support', result.stderr)
        self.assert_restored()


if __name__ == '__main__':
    unittest.main()
