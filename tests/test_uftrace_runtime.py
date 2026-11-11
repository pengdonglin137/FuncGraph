"""Native recorder discovery, overrides and signal-preserving forwarding."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fglib import inputs, uftrace, uftrace_runtime


class UftraceRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='fg-uftrace-runtime-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.payload = self.root / 'payload'
        self.patch = patch.object(uftrace_runtime, 'PAYLOAD', self.payload)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def bundled(self):
        executable = self.payload / 'bin/uftrace'
        executable.parent.mkdir(parents=True)
        executable.write_text('#!/bin/sh\nexit 0\n')
        executable.chmod(0o755)
        library = self.payload / 'lib/uftrace/libmcount.so'
        library.parent.mkdir(parents=True)
        library.touch()
        return executable

    def test_bundled_executable_precedes_path_and_uses_relocated_libraries(self):
        executable = self.bundled()
        with patch.object(uftrace_runtime.shutil, 'which', side_effect=AssertionError('PATH fallback')):
            self.assertEqual(uftrace_runtime.command(), [str(executable),
                             '--libmcount-path=' + str(self.payload / 'lib/uftrace')])

    def test_explicit_override_precedes_bundle_without_bundled_library_options(self):
        self.bundled()
        with patch.object(uftrace_runtime.shutil, 'which', return_value='/override/uftrace'):
            self.assertEqual(uftrace_runtime.command('custom'), ['/override/uftrace'])

    def test_invalid_override_and_incomplete_bundle_do_not_silently_fall_back(self):
        with patch.object(uftrace_runtime.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(OSError, 'override is not executable'):
                uftrace_runtime.command('missing')
        executable = self.bundled()
        for problem in ('not_executable', 'missing_binary', 'missing_library'):
            with self.subTest(problem=problem):
                if problem == 'not_executable':
                    executable.chmod(0o644)
                elif problem == 'missing_binary':
                    executable.unlink()
                else:
                    (self.payload / 'lib/uftrace/libmcount.so').unlink()
                with self.assertRaisesRegex(OSError, 'incomplete'):
                    uftrace_runtime.command()

    def test_path_fallback_and_actionable_missing_executable(self):
        with patch.object(uftrace_runtime.shutil, 'which', return_value='/usr/bin/uftrace'):
            self.assertEqual(uftrace_runtime.command(), ['/usr/bin/uftrace'])
        with patch.object(uftrace_runtime.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(OSError, 'Linux wheel'):
                uftrace_runtime.command()

    def test_launcher_exec_preserves_arguments_and_environment(self):
        executable = self.bundled()
        with patch.object(uftrace_runtime.os, 'execv') as execute:
            uftrace_runtime.main(['record', '-d', 'capture with spaces', './program', '--arg'])
        arguments = uftrace_runtime.command() + ['record', '-d', 'capture with spaces', './program', '--arg']
        execute.assert_called_once_with(str(executable), arguments)

    def test_launcher_reports_failure_without_traceback(self):
        errors = io.StringIO()
        with patch.object(uftrace_runtime.shutil, 'which', return_value=None), contextlib.redirect_stderr(errors):
            self.assertEqual(uftrace_runtime.main(['--version']), 1)
        self.assertIn('fguftrace: uftrace is unavailable', errors.getvalue())

    def test_directory_reader_uses_bundled_executable_without_explicit_path(self):
        executable = self.bundled()
        fixture = Path(__file__).parent.parent / 'samples/sample_mixed.jsonl'
        executable.write_text('#!/bin/sh\nexec cat "' + str(fixture) + '"\n')
        imported = inputs.read_uftrace_directory(str(self.root))
        with fixture.open(encoding='utf-8') as source:
            _, expected = uftrace.read_export(source)
        self.assertEqual(imported.records, expected)


if __name__ == '__main__':
    unittest.main()
