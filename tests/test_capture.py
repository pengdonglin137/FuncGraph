"""Unified capture commands and failure-safe tracefs ownership without root."""
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import helpers
from fglib import capture
from test_uftrace_export import AVAILABLE, COMPILE_FLAGS, EXECUTABLE


class CaptureTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='fg-capture-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tracefs = self.root / 'tracefs'
        self.tracefs.mkdir()
        self.evidence = self.root / 'evidence'
        self.evidence.mkdir()
        self.lock = self.root / 'capture.lock'
        self.values = {name: '\n' for name in capture.FILTERS}
        self.values.update(tracing_on='0', current_tracer='function_graph',
                           trace_clock='[mono] local', max_graph_depth='7',
                           tracing_cpumask='ff', set_event='', buffer_size_kb='1410',
                           available_tracers='nop function_graph', **{'events/enable': '0'})
        for name in ('funcgraph-args', 'funcgraph-retaddr', 'funcgraph-retval', 'function-fork', 'event-fork'):
            self.values['options/' + name] = '0'
        for event in capture.SCHED_EVENTS + capture.LIFECYCLE_EVENTS:
            self.values['events/sched/' + event + '/enable'] = '0'
        for name, value in self.values.items():
            path = self.tracefs / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value + '\n')

    def session(self):
        return capture.TracefsSession(self.tracefs, self.evidence, self.lock)

    def arguments(self, *values):
        result = capture.parser().parse_args(list(values) + ['--', './demo', 'with spaces', '--kernel'])
        result.command = result.command[1:]
        result.output = self.root / 'capture with spaces.data'
        return result

    def test_user_defaults_record_arguments_callsite_source_and_shared_clock(self):
        command = capture.build_command(self.arguments('--user'), ['/recorder'])
        self.assertEqual(command[:9], ['/recorder', 'record', '--clock=mono', '--srcline',
                                      '-T', '.@callsite', '--auto-args', '--no-sched', '-d'])
        self.assertEqual(command[-3:], ['./demo', 'with spaces', '--kernel'])
        self.assertNotIn('-K', command)

    def test_mixed_defaults_include_kernel_outside_main_and_real_lifecycle_events(self):
        command = capture.build_command(self.arguments('--mixed'), ['/recorder'], self.tracefs)
        for value in ('-K', '8', '--kernel-full', '--kernel-buffer=16384k', '--no-sched'):
            self.assertIn(value, command)
        self.assertNotIn('--kernel-full-info', command)
        for event in capture.SCHED_EVENTS + capture.LIFECYCLE_EVENTS:
            self.assertIn('sched:' + event + '@kernel', command)
        self.assertTrue(any(value.endswith('_sys_.*@kernel') for value in command))

    def test_no_sched_keeps_exec_exit_and_no_args_disables_automatic_arguments(self):
        command = capture.build_command(self.arguments('--mixed', '--no-sched', '--no-args'),
                                        ['/recorder'], self.tracefs)
        self.assertNotIn('--auto-args', command)
        for event in capture.SCHED_EVENTS:
            self.assertNotIn('sched:' + event + '@kernel', command)
        for event in capture.LIFECYCLE_EVENTS:
            self.assertIn('sched:' + event + '@kernel', command)

    def test_user_sched_uses_native_perf_without_kernel_events(self):
        command = capture.build_command(self.arguments('--user', '--sched'), ['/recorder'])
        self.assertNotIn('--no-sched', command)
        self.assertNotIn('-E', command)

    def test_explicit_specs_buffers_depth_and_graphs_are_preserved(self):
        command = capture.build_command(self.arguments('--mixed', '-A', 'work@arg1/i32',
            '-R', 'work@retval/i32', '-b', '32768', '--kernel-depth', '64', '-g', 'read,write',
            '-n', 'noisy'), ['/recorder'], self.tracefs)
        for value in ('work@arg1/i32', 'work@retval/i32', '--kernel-buffer=32768k',
                      '64', 'read@kernel;write@kernel', 'noisy@kernel'):
            self.assertIn(value, command)

    def test_arm64_adds_patchable_entry_support(self):
        with patch.object(capture.platform, 'machine', return_value='aarch64'):
            command = capture.build_command(self.arguments('--user'), ['/recorder'])
        self.assertIn('-P', command)

    def test_missing_optional_event_reports_unavailability_without_inventing_it(self):
        (self.tracefs / 'events/sched/sched_process_exec/enable').unlink()
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            command = capture.build_command(self.arguments('--mixed'), ['/recorder'], self.tracefs)
        self.assertNotIn('sched:sched_process_exec@kernel', command)
        self.assertIn('event unavailable: sched_process_exec', errors.getvalue())

    def test_normalization_does_not_restore_comments_or_no_pid_as_filters(self):
        self.assertEqual(capture.normalized('set_ftrace_pid', '# comment\nno pid\n'), '')
        self.assertEqual(capture.normalized('trace_clock', 'local [mono] global'), 'mono')

    def test_state_is_restored_after_success_and_zero_is_not_written_as_empty(self):
        state = self.session()
        with state:
            state.configure(self.arguments('--mixed', '--no-args'))
            self.assertEqual((self.tracefs / 'options/funcgraph-args').read_text(), '0\n')
            state.write('tracing_on', '1')
            state.write('current_tracer', 'nop')
            state.write('options/function-fork', 1)
            state.write('options/event-fork', 1)
            state.write('set_ftrace_pid', 123)
            state.write('events/sched/sched_process_exec/enable', 1)
        self.assertEqual(state.snapshot(), state.before)
        self.assertEqual(json.loads((self.evidence / 'tracefs-restore.json').read_text()),
                         {'errors': [], 'differences': {}})

    def test_state_is_restored_after_recording_error_and_interrupt(self):
        for exception in (RuntimeError('record failed'), capture.CaptureInterrupted(signal.SIGTERM)):
            with self.subTest(exception=type(exception).__name__):
                state = self.session()
                with self.assertRaises(type(exception)):
                    with state:
                        state.configure(self.arguments('--mixed'))
                        state.write('tracing_on', '1')
                        raise exception
                self.assertEqual(state.snapshot(), state.before)

    def test_active_tracing_enabled_events_and_filters_are_refused_without_changes(self):
        for name, value in (('tracing_on', '1'), ('set_event', 'sched:sched_switch'),
                            ('events/enable', 'X'), ('set_ftrace_pid', '123'),
                            ('set_graph_function', 'vfs_read')):
            with self.subTest(name=name):
                path = self.tracefs / name
                original = path.read_text()
                path.write_text(value)
                state = self.session()
                before = state.snapshot()
                with self.assertRaises(OSError):
                    with state:
                        self.fail('unsafe session entered')
                self.assertEqual(state.snapshot(), before)
                self.assertIsNone(state.lock)
                path.write_text(original)

    def test_missing_required_setting_or_unsupported_tracer_clock_is_refused(self):
        for name, value in (('current_tracer', None), ('available_tracers', 'nop'),
                            ('trace_clock', '[local] global')):
            with self.subTest(name=name):
                path = self.tracefs / name
                original = path.read_text()
                if value is None:
                    path.unlink()
                else:
                    path.write_text(value)
                with self.assertRaises(OSError):
                    with self.session():
                        self.fail('unsupported session entered')
                path.write_text(original)

    def test_lock_contention_refuses_before_mutation(self):
        with self.lock.open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(OSError, 'another fgcapture'):
                with self.session():
                    self.fail('lock contention ignored')

    def test_symlink_lock_is_not_followed(self):
        victim = self.root / 'victim'
        victim.write_text('untouched')
        self.lock.symlink_to(victim)
        with self.assertRaises(OSError):
            with self.session():
                self.fail('symlink lock accepted')
        self.assertEqual(victim.read_text(), 'untouched')

    def test_restore_failure_is_reported_and_other_settings_still_restore(self):
        state = self.session()
        original = state.write

        def failing_write(name, value):
            if name == 'max_graph_depth':
                raise OSError('injected write failure')
            original(name, value)

        with self.assertRaisesRegex(OSError, 'restoration failed'):
            with state:
                state.write('options/function-fork', 1)
                state.write = failing_write
        self.assertEqual((self.tracefs / 'options/function-fork').read_text(), '0\n')
        report = json.loads((self.evidence / 'tracefs-restore.json').read_text())
        self.assertIn('max_graph_depth', report['errors'][0])
        with self.lock.open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_missing_optional_argument_option_is_recorded_not_simulated(self):
        (self.tracefs / 'options/funcgraph-args').unlink()
        with contextlib.redirect_stderr(io.StringIO()) as errors, self.session() as state:
            state.configure(self.arguments('--mixed'))
        self.assertIn('kernel option unavailable: funcgraph-args', errors.getvalue())
        self.assertNotIn('options/funcgraph-args', state.before)

    def test_legacy_kernel_delegation_preserves_command_arguments(self):
        with patch.object(capture.subprocess, 'call', return_value=7) as run:
            result = capture.main(['--kernel', '-d', '0', '--', '/app', '--kernel'])
        self.assertEqual(result, 7)
        self.assertEqual(run.call_args.args[0][-5:], ['-d', '0', '--', '/app', '--kernel'])

    def test_user_main_never_opens_tracefs_and_resolves_bundled_recorder(self):
        output = self.root / 'user.data'
        with patch.object(capture.uftrace_runtime, 'command', return_value=['/bundled', '--libmcount-path=/libs']) as locate, \
                patch.object(capture, 'TracefsSession', side_effect=AssertionError('tracefs accessed')), \
                patch.object(capture, 'run_record', return_value=0) as run:
            self.assertEqual(capture.main(['--user', '-o', str(output), '--', '/app', 'an argument']), 0)
        locate.assert_called_once_with(None)
        self.assertEqual(run.call_args.args[0][:2], ['/bundled', '--libmcount-path=/libs'])
        self.assertEqual(run.call_args.args[0][-2:], ['/app', 'an argument'])
        self.assertTrue((output.with_name('user.data.fgcapture') / 'command.json').exists())

    def test_main_rejects_missing_command_existing_output_and_nonroot_mixed(self):
        existing = self.root / 'existing'
        existing.mkdir()
        for arguments, message in ((['--user'], 'requires -- command'),
                (['--user', '-o', str(existing), '--', '/app'], 'overwrite'),
                (['--mixed', '-o', str(self.root / 'new'), '--', '/app'], 'requires root'),
                (['--user', '-g', 'read', '--', '/app'], 'select kernel graphs')):
            with self.subTest(message=message), contextlib.redirect_stderr(io.StringIO()) as errors, \
                    patch.object(capture.uftrace_runtime, 'command', return_value=['/recorder']), \
                    patch.object(capture.os, 'geteuid', return_value=1000), \
                    patch.object(capture, 'run_record', side_effect=AssertionError('record started')):
                self.assertEqual(capture.main(arguments), 1)
                self.assertIn(message, errors.getvalue())

    def test_main_preserves_failure_and_interruption_exit_codes(self):
        for result in (9, -signal.SIGTERM, capture.CaptureInterrupted(signal.SIGINT)):
            with self.subTest(result=result), patch.object(capture.uftrace_runtime, 'command', return_value=['/recorder']):
                output = self.root / ('failed-' + str(result))
                with patch.object(capture, 'run_record', **({'side_effect': result} if isinstance(result, Exception)
                                                           else {'return_value': result})):
                    code = capture.main(['--user', '-o', str(output), '--', '/app'])
                self.assertEqual(code, 130 if isinstance(result, Exception) else (143 if result < 0 else result))

    def test_mixed_main_restores_on_native_failure_missing_data_and_cancellation(self):
        for result in (0, 5, capture.CaptureInterrupted(signal.SIGTERM)):
            with self.subTest(result=result):
                output = self.root / ('mixed-' + str(result))
                state = self.session()
                factory = lambda *arguments: state
                effect = {'side_effect': result} if isinstance(result, Exception) else {'return_value': result}
                with patch.object(capture, 'TracefsSession', side_effect=factory), \
                        patch.object(capture, 'tracefs_root', return_value=self.tracefs), \
                        patch.object(capture.os, 'geteuid', return_value=0), \
                        patch.object(capture.uftrace_runtime, 'command', return_value=['/recorder']), \
                        patch.object(capture, 'build_command', return_value=['/recorder', 'record']), \
                        patch.object(capture, 'run_record', **effect), \
                        contextlib.redirect_stderr(io.StringIO()) as errors:
                    code = capture.main(['--mixed', '-o', str(output), '--', '/app'])
                self.assertEqual(code, 143 if isinstance(result, Exception) else (1 if result == 0 else result))
                self.assertEqual(state.snapshot(), state.before)
                if result == 0:
                    self.assertIn('did not save kernel data', errors.getvalue())

    def test_duplicate_capture_metadata_never_overwrites_an_earlier_run(self):
        output = self.root / 'duplicate'
        evidence = output.with_name('duplicate.fgcapture')
        evidence.mkdir()
        (evidence / 'command.json').write_text('keep')
        with patch.object(capture.uftrace_runtime, 'command', return_value=['/recorder']), \
                patch.object(capture, 'run_record', side_effect=AssertionError('record started')):
            self.assertEqual(capture.main(['--user', '-o', str(output), '--', '/app']), 1)
        self.assertEqual((evidence / 'command.json').read_text(), 'keep')

    def test_invalid_numeric_controls_are_rejected_by_argparse(self):
        for option, value in (('-b', '0'), ('--kernel-depth', '-2'), ('-b', 'abc')):
            with self.subTest(option=option, value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    self.arguments('--mixed', option, value)

    def test_real_owned_process_group_is_stopped_before_interrupt_returns(self):
        child_file = self.root / 'child.pid'
        command = [sys.executable, '-c',
                   'import os,time; open(__import__("sys").argv[1],"w").write(str(os.getpid())); time.sleep(30)',
                   str(child_file)]
        original_wait = subprocess.Popen.wait
        invoked = []

        def interrupted_wait(process, *arguments, **keywords):
            if not invoked:
                invoked.append(process)
                raise capture.CaptureInterrupted(signal.SIGINT)
            return original_wait(process, *arguments, **keywords)

        with patch.object(subprocess.Popen, 'wait', interrupted_wait):
            with self.assertRaises(capture.CaptureInterrupted):
                capture.run_record(command)
        self.assertIsNotNone(invoked[0].poll())

    def test_signal_during_launch_is_deferred_until_child_ownership_is_known(self):
        original = subprocess.Popen
        started = []

        def interrupted_launch(*arguments, **keywords):
            process = original(*arguments, **keywords)
            started.append(process)
            os.kill(os.getpid(), signal.SIGINT)
            return process

        with capture.interruptible(), patch.object(capture.subprocess, 'Popen', interrupted_launch):
            with self.assertRaises(capture.CaptureInterrupted):
                capture.run_record([sys.executable, '-c', 'import time; time.sleep(30)'])
        self.assertIsNotNone(started[0].poll())

    def test_root_launcher_and_shell_compatibility_help(self):
        result = subprocess.run([str(Path(helpers.ROOT) / 'fgcapture'), '--help'],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for option in ('--user', '--mixed', '--no-args', '--uftrace-bin'):
            self.assertIn(option, result.stdout)
        result = subprocess.run(['bash', str(Path(helpers.ROOT) / 'fgcapture.sh'), '--user', '--help'],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--mixed', result.stdout)


@unittest.skipUnless(AVAILABLE, 'patched uftrace and gcc are required')
class NativeCaptureTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='fg-native-capture-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        source = self.root / 'workload.c'
        source.write_text('int work(int value) { return value + 1; }\n'
                          'int main(void) { return work(7) != 8; }\n')
        self.binary = self.root / 'app with spaces'
        subprocess.run(['gcc', *COMPILE_FLAGS, '-g', '-O0', '-o', str(self.binary), str(source)],
                       check=True, capture_output=True)

    def record(self, *options):
        output = self.root / 'capture with spaces.data'
        with patch.object(capture.uftrace_runtime, 'command', return_value=[str(EXECUTABLE)]):
            self.assertEqual(capture.main(['--user', '-o', str(output), *options, '--', str(self.binary)]), 0)
        result = subprocess.run([str(EXECUTABLE), 'dump', '--fgraph', '--srcline', '-d', str(output)],
                                text=True, capture_output=True, check=True)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def test_simple_user_capture_preserves_names_values_returns_and_bounded_call_site(self):
        records = self.record()
        entry = next(item for item in records if item.get('name') == 'work' and item['type'] == 'entry')
        exit_record = next(item for item in records if item.get('name') == 'work' and item['type'] == 'exit')
        site = next(item['user_callsite'] for item in records
                    if item.get('user_callsite', {}).get('name') == 'main')
        self.assertEqual(entry['user_args'], ['7'])
        self.assertEqual(entry['user_arg_names'], ['value'])
        self.assertEqual(exit_record['user_retval'], '8')
        self.assertGreaterEqual(site['symbol']['offset'], 0)
        self.assertLess(site['symbol']['offset'], site['symbol']['size'])
        self.assertEqual(Path(site['source']['file']), self.root / 'workload.c')
        self.assertEqual(records[-1], {'type': 'end', 'complete': True})

    def test_no_args_keeps_user_source_and_call_sites_without_automatic_payloads(self):
        records = self.record('--no-args')
        entry = next(item for item in records if item.get('name') == 'work' and item['type'] == 'entry')
        self.assertNotIn('user_args', entry)
        self.assertEqual(Path(entry['source']['file']), self.root / 'workload.c')
        self.assertTrue(any('user_callsite' in item for item in records))

    def test_explicit_argument_and_return_specs_remain_available(self):
        records = self.record('--no-args', '-A', 'work@arg1/i32', '-R', 'work@retval/i32')
        entry = next(item for item in records if item.get('name') == 'work' and item['type'] == 'entry')
        exit_record = next(item for item in records if item.get('name') == 'work' and item['type'] == 'exit')
        self.assertEqual(entry['user_args'], ['7'])
        self.assertEqual(entry['user_arg_names'], ['value'])
        self.assertEqual(exit_record['user_retval'], '8')


if __name__ == '__main__':
    unittest.main()
