"""fgcapture.sh: static checks (capturing itself needs root + tracefs; tested manually on a Pi 4)."""
import os
from pathlib import Path
import shlex
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
        for word in ('--args', '--sched', '--event', '--calibrate', 'set_graph_function', 'Examples'):
            self.assertIn(word, p.stdout)

    def test_refuses_without_root(self):
        if os.geteuid() == 0:
            self.skipTest('running as root')
        p = subprocess.run(['bash', SCRIPT, '-d', '0'], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('root', p.stderr)

    def test_help_documents_arguments_enabled_when_supported(self):
        result = subprocess.run(['bash', SCRIPT, '--help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('enable function arguments when supported (default)', result.stdout)
        self.assertNotIn('probe', result.stdout)

    @unittest.skipUnless(helpers.have('shellcheck'), 'shellcheck not installed')
    def test_shellcheck(self):
        p = subprocess.run(['shellcheck', '-S', 'error', SCRIPT], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout)


class FgcaptureOptionsTest(unittest.TestCase):
    def add_event(self, name, event_filter='common_pid == 123'):
        path = self.tracefs / 'events' / name
        path.mkdir(parents=True, exist_ok=True)
        (path / 'enable').write_text('0\n')
        (path / 'filter').write_text(event_filter + '\n')
        return path

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='fg-kernel-options-')
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
        self.trace_reads = self.root / 'trace-reads'
        self.output = self.root / 'trace.txt'
        shims = {
            'id': '#!/bin/sh\necho 0\n',
            'cat': '''#!/bin/bash
if [ "$#" = 1 ] && [ "$1" = "$FG_TRACEFS/trace" ]; then
    printf 'read\\n' >> "$FG_TRACE_READS"
    printf 'capture args=%s retaddr=%s exec=%s exit=%s retval=%s latency=%s\\n' "$(/bin/cat "$FG_TRACEFS/options/funcgraph-args" 2>/dev/null)" "$(/bin/cat "$FG_TRACEFS/options/funcgraph-retaddr" 2>/dev/null)" "$(/bin/cat "$FG_TRACEFS/events/sched/sched_process_exec/enable")" "$(/bin/cat "$FG_TRACEFS/events/sched/sched_process_exit/enable")" "$(/bin/cat "$FG_TRACEFS/options/funcgraph-retval")" "$(/bin/cat "$FG_TRACEFS/options/latency-format")"
else
    exec /bin/cat "$@"
fi
''',
        }
        for name, content in shims.items():
            path = self.binary / name
            path.write_text(content)
            path.chmod(0o755)

    def run_capture(self, *arguments):
        self.trace_reads.write_text('')
        environment = dict(os.environ, FG_TRACEFS=str(self.tracefs),
                           FG_CAPTURE_LOCK=str(self.root / 'lock'),
                           FG_TRACE_READS=str(self.trace_reads),
                           PATH=str(self.binary) + ':' + os.environ['PATH'])
        return subprocess.run(['bash', SCRIPT, '-o', str(self.output), '-d', '0', *arguments],
                              env=environment, text=True, capture_output=True, timeout=10)

    def assert_restored(self):
        for name, expected in self.values.items():
            if name in ('trace', 'available_tracers'):
                continue
            self.assertEqual((self.tracefs / name).read_text().strip(), expected, name)

    def test_default_enables_supported_options_together_and_restores_state(self):
        result = self.run_capture('--sched')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('args=1 retaddr=1 exec=1 exit=1 retval=1 latency=1', self.output.read_text())
        command = result.stdout.split('generate HTML with: ')[1].strip()
        self.assertEqual(shlex.split(command), ['fgreport', str(self.output)])
        self.assertEqual(self.trace_reads.read_text().splitlines(), ['read'])
        self.assert_restored()

    def test_default_and_explicit_args_need_no_scope_or_probe(self):
        for arguments in ((), ('--args',)):
            with self.subTest(arguments=arguments):
                result = self.run_capture(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('args=1 retaddr=1 exec=0 exit=0 retval=1 latency=1', self.output.read_text())
                self.assertEqual(self.trace_reads.read_text().splitlines(), ['read'])
                self.assertNotIn('probe', result.stdout + result.stderr)
                self.assert_restored()

    def test_arguments_do_not_require_vfs_read_or_change_selected_graphs(self):
        command = 'grep -qx vfs_write "$FG_TRACEFS/set_graph_function" && ' \
                  'grep -qx vfs_read "$FG_TRACEFS/set_graph_notrace" && ' \
                  'test -z "$(cat "$FG_TRACEFS/set_ftrace_filter")"'
        result = self.run_capture('-g', 'vfs_write', '-n', 'vfs_read', '--', '/bin/sh', '-c', command)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('command exited with 0', result.stdout)
        self.assertIn('args=1 retaddr=1', self.output.read_text())
        self.assertEqual(self.trace_reads.read_text().splitlines(), ['read'])
        self.assert_restored()

    def test_no_args_disables_only_arguments_and_restores_previous_enabled_state(self):
        self.values['options/funcgraph-args'] = '1'
        (self.tracefs / 'options/funcgraph-args').write_text('1\n')
        result = self.run_capture('--no-args')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('args=0 retaddr=1 exec=0 exit=0 retval=1 latency=1', self.output.read_text())
        self.assertEqual(self.trace_reads.read_text().splitlines(), ['read'])
        self.assert_restored()

    def test_explicit_argument_controls_preserve_last_option_wins(self):
        for arguments, expected in ((('--no-args', '--args'), '1'), (('--args', '--no-args'), '0')):
            with self.subTest(arguments=arguments):
                result = self.run_capture(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('args=' + expected + ' retaddr=1', self.output.read_text())
                self.assert_restored()

    def test_extra_events_group_glob_and_duplicates_keep_all_functions_and_restore_filters(self):
        for name in ('kvm/kvm_entry', 'kvm/kvm_exit', 'kvm/vcpu_match_mmio'):
            self.add_event(name)
        command = 'for event in kvm_entry kvm_exit vcpu_match_mmio; do ' \
                  'test "$(cat "$FG_TRACEFS/events/kvm/$event/enable")" = 1 || exit 21; ' \
                  'test "$(cat "$FG_TRACEFS/events/kvm/$event/filter")" = 0 || exit 22; done; ' \
                  'test -z "$(cat "$FG_TRACEFS/set_graph_function")" || exit 23'
        result = self.run_capture('--no-args', '-e', 'kvm', '--event=kvm:kvm_*',
                                  '-e', 'kvm:kvm_entry', '--', '/bin/sh', '-c', command)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('command exited with 0', result.stdout)
        header = next(line for line in self.output.read_text().splitlines() if 'extra_events=' in line)
        self.assertEqual(header.count('kvm:kvm_entry'), 1)
        for name in ('kvm_entry', 'kvm_exit', 'vcpu_match_mmio'):
            self.assertEqual((self.tracefs / 'events/kvm' / name / 'enable').read_text().strip(), '0')
            self.assertEqual((self.tracefs / 'events/kvm' / name / 'filter').read_text().strip(), 'common_pid == 123')
        self.assert_restored()

    def test_explicit_scheduler_event_is_enabled_without_sched_bundle(self):
        result = self.run_capture('--no-args', '-e', 'sched:sched_switch', '--', '/bin/sh', '-c',
                                  'test "$(cat "$FG_TRACEFS/events/sched/sched_switch/enable")" = 1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('command exited with 0', result.stdout)
        self.assert_restored()

    def test_bad_event_selector_fails_before_mutating_tracefs(self):
        for selector in ('kvm:absent', '../kvm', 'kvm:entry@kernel', 'kvm:entry;exit', ''):
            with self.subTest(selector=selector):
                result = self.run_capture('--no-args', '--event=' + selector)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())
                self.assert_restored()

    def test_extra_event_filters_are_restored_when_output_fails(self):
        event = self.add_event('kvm/kvm_entry')
        self.output = self.root / 'missing' / 'trace.txt'
        result = self.run_capture('-e', 'kvm:kvm_entry')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((event / 'enable').read_text().strip(), '0')
        self.assertEqual((event / 'filter').read_text().strip(), 'common_pid == 123')
        self.assert_restored()

    def test_extra_event_state_is_restored_after_interrupt(self):
        event = self.add_event('kvm/kvm_entry')
        result = self.run_capture('-e', 'kvm:kvm_entry', '--', '/bin/sh', '-c', 'kill -TERM "$PPID"')
        self.assertEqual(result.returncode, 130, result.stderr)
        self.assertEqual((event / 'enable').read_text().strip(), '0')
        self.assertEqual((event / 'filter').read_text().strip(), 'common_pid == 123')
        self.assert_restored()

    def test_missing_retaddr_does_not_disable_supported_arguments(self):
        del self.values['options/funcgraph-retaddr']
        (self.tracefs / 'options/funcgraph-retaddr').unlink()
        result = self.run_capture()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('option funcgraph-retaddr not supported', result.stderr)
        self.assertIn('args=1 retaddr= exec=0 exit=0 retval=1 latency=1', self.output.read_text())
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

    def test_missing_args_option_is_skipped_for_default_and_explicit_controls(self):
        del self.values['options/funcgraph-args']
        (self.tracefs / 'options/funcgraph-args').unlink()
        for arguments in ((), ('--args',), ('--no-args',)):
            with self.subTest(arguments=arguments):
                result = self.run_capture(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('option funcgraph-args not supported', result.stderr)
                self.assertFalse((self.tracefs / 'options/funcgraph-args').exists())
                self.assertIn('args= retaddr=1 exec=0 exit=0 retval=1 latency=1', self.output.read_text())
                self.assertEqual(self.trace_reads.read_text().splitlines(), ['read'])
                self.assert_restored()


if __name__ == '__main__':
    unittest.main()
