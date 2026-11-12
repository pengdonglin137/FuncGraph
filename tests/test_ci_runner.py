"""CI failures retain their exit status and expose safely escaped diagnostics."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import helpers


class CiRunnerTest(unittest.TestCase):
    def run_command(self, source, github=True):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(['bash', str(Path(helpers.ROOT, 'tests/ci.sh')), sys.executable,
                                     '-c', source], capture_output=True, text=True,
                                    env=dict(os.environ, RUNNER_TEMP=directory,
                                             GITHUB_ACTIONS='true' if github else 'false'))
            logs = list(Path(directory).glob('funcgraph-ci.*.log'))
            self.assertEqual(len(logs), 1)
            return result, logs[0].read_text()

    def test_success_keeps_stdout_stderr_and_does_not_emit_failure_annotation(self):
        result, log = self.run_command('import sys; print("out"); print("err", file=sys.stderr)')
        self.assertEqual(result.returncode, 0)
        self.assertIn('out\n', result.stdout)
        self.assertIn('err\n', result.stdout)
        self.assertNotIn('::error', result.stdout)
        self.assertEqual(result.stdout, log)

    def test_failure_preserves_exit_status_and_escapes_annotation_payload(self):
        result, log = self.run_command('import sys; print("bad%value\\n::warning::not an annotation"); sys.exit(7)')
        self.assertEqual(result.returncode, 7)
        self.assertIn('bad%value\n::warning::not an annotation', log)
        self.assertIn('::error title=Command failed (exit 7)::bad%25value%0A::warning::not an annotation', result.stdout)

    def test_local_failures_are_not_wrapped_in_github_command_markup(self):
        result, log = self.run_command('import sys; print("failed"); sys.exit(9)', github=False)
        self.assertEqual(result.returncode, 9)
        self.assertEqual(result.stdout, log)
        self.assertNotIn('::error', result.stdout)

    def test_annotation_tail_is_bounded_but_the_full_log_is_retained(self):
        result, log = self.run_command('import sys; print("x" * 20000); sys.exit(1)')
        self.assertEqual(result.returncode, 1)
        self.assertGreater(len(log), 20000)
        annotation = result.stdout.split('::error title=', 1)[1]
        self.assertLess(len(annotation), 16100)

    def test_missing_command_has_a_helpful_usage_error(self):
        result = subprocess.run(['bash', str(Path(helpers.ROOT, 'tests/ci.sh'))], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Usage:', result.stderr)

