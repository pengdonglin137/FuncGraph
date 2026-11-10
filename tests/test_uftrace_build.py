"""Hermetic uftrace patch/build workflow tests; no tracing or compiler required."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import helpers


class UftraceBuildTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fg-uftrace-build-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        self.environment = dict(os.environ, GIT_CONFIG_NOSYSTEM='1',
                                GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
                                GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid')
        self.git(self.root, 'init', '-q')
        (self.root / 'scripts').mkdir()
        shutil.copy(Path(helpers.ROOT) / 'scripts/uftrace.sh', self.root / 'scripts/uftrace.sh')
        self.patches = self.root / 'patches/uftrace'
        self.patches.mkdir(parents=True)
        (self.patches / 'series').write_text('')
        upstream = Path(self.temp.name) / 'upstream'
        upstream.mkdir()
        self.git(upstream, 'init', '-q')
        (upstream / 'value').write_text('base\n')
        (upstream / 'configure').write_text(
            '#!/bin/bash\nset -eu\nprintf "%s\\n" "$@" > configured\n'
            'if git rev-parse --show-toplevel > parent-repo 2>/dev/null; then exit 5; fi\n'
            'printf "all:\\n\\ttrue\\ninstall:\\n\\ttrue\\n" > Makefile\n')
        (upstream / 'configure').chmod(0o755)
        self.git(upstream, 'add', '.')
        self.git(upstream, 'commit', '-qm', 'base')
        self.git(self.root, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q',
                 str(upstream), 'third_party/uftrace')
        self.source = self.root / 'third_party/uftrace'

    def git(self, directory, *arguments):
        result = subprocess.run(['git', '-C', str(directory), *arguments],
                                env=self.environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def run_script(self, *arguments, **environment):
        return subprocess.run(['bash', str(self.root / 'scripts/uftrace.sh'), *arguments],
                              env=dict(self.environment, **environment), capture_output=True,
                              text=True)

    def make_patch(self):
        development = self.root / 'build/uftrace-dev'
        development.parent.mkdir(exist_ok=True)
        self.git(self.source, 'worktree', 'add', '--detach', str(development), 'HEAD')
        (development / 'value').write_text('patched\n')
        self.git(development, 'commit', '-qam', 'extend recorded metadata')
        result = self.run_script('refresh')
        self.assertEqual(result.returncode, 0, result.stderr)
        return development

    def test_help_without_initialized_submodule(self):
        shutil.rmtree(self.source)
        result = self.run_script('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('CROSS_COMPILE', result.stdout)

    def test_missing_submodule_cannot_archive_parent(self):
        shutil.rmtree(self.source)
        self.source.mkdir()
        self.git(self.root, 'commit', '-qm', 'parent')
        result = self.run_script('check')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('submodule update', result.stderr)

    def test_wrong_pin_is_rejected(self):
        (self.source / 'value').write_text('different\n')
        self.git(self.source, 'commit', '-qam', 'different revision')
        result = self.run_script('check')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('indexed submodule pin', result.stderr)

    def test_refresh_check_build_and_pristine_pin(self):
        self.make_patch()
        series = (self.patches / 'series').read_text().splitlines()
        self.assertEqual(len(series), 1)
        result = self.run_script('check')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_script('build', '--arch', 'arm64',
                                 CROSS_COMPILE='aarch64-linux-gnu-', JOBS='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        tree = self.root / 'build/uftrace-src-aarch64'
        self.assertEqual((tree / 'value').read_text(), 'patched\n')
        self.assertIn('--arch=aarch64', (tree / 'configured').read_text())
        self.assertIn('--cross-compile=aarch64-linux-gnu-', (tree / 'configured').read_text())
        self.assertEqual((self.source / 'value').read_text(), 'base\n')
        self.assertEqual(self.git(self.source, 'status', '--porcelain'), '')
        result = self.run_script('build', '--arch', 'arm64')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('explicitly', result.stderr)

    def test_bad_patch_does_not_destroy_existing_series(self):
        development = self.make_patch()
        original = (self.patches / 'series').read_bytes()
        (development / 'value').write_text('uncommitted\n')
        result = self.run_script('refresh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Commit development changes', result.stderr)
        self.assertEqual((self.patches / 'series').read_bytes(), original)

    def test_invalid_series_names_and_duplicates(self):
        self.make_patch()
        original = (self.patches / 'series').read_text()
        for invalid in ('../outside.patch\n', original + original, 'missing.patch\n'):
            with self.subTest(invalid=invalid):
                (self.patches / 'series').write_text(invalid)
                result = self.run_script('check')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Invalid or duplicate patch', result.stderr)

    def test_invalid_architecture_and_jobs(self):
        for arguments in (('build', '--arch', '../bad'), ('check', '--arch'), ('bad',)):
            with self.subTest(arguments=arguments):
                self.assertNotEqual(self.run_script(*arguments).returncode, 0)
        result = self.run_script('build', JOBS='0')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('JOBS must be positive', result.stderr)


if __name__ == '__main__':
    unittest.main()
