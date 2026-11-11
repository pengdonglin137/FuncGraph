"""Offline tests for dependency locks, gitlinks and native build prerequisites."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import helpers


SPEC = importlib.util.spec_from_file_location(
    'funcgraph_dependency_pins', Path(helpers.ROOT) / 'third_party/check_pins.py')
PINS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PINS)


class DependencyPinsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='fg-dependency-pins-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / 'project'
        self.root.mkdir()
        self.environment = dict(os.environ, GIT_CONFIG_NOSYSTEM='1',
                                GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
                                GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid')
        self.git(self.root, 'init', '-q')
        self.pins = {}
        for name in ['libtraceevent', 'libtracefs', 'trace-cmd', 'uftrace']:
            checkout = self.root / 'third_party' / name
            checkout.mkdir(parents=True)
            self.git(checkout, 'init', '-q')
            (checkout / 'source').write_text(name + '\n')
            self.git(checkout, 'add', 'source')
            self.git(checkout, 'commit', '-qm', 'pinned source')
            self.pins[name] = self.git(checkout, 'rev-parse', 'HEAD')
            self.git(self.root, 'update-index', '--add', '--cacheinfo',
                     '160000,' + self.pins[name] + ',third_party/' + name)
        self.lock = self.root / 'third_party/versions.json'
        self.write_lock(self.pins)
        self.script = self.root / 'third_party/check_pins.py'
        shutil.copyfile(Path(helpers.ROOT) / 'third_party/check_pins.py', self.script)

    def git(self, directory, *arguments, input_text=None):
        result = subprocess.run(['git', '-C', str(directory), *arguments],
                                env=self.environment, input=input_text,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def write_lock(self, pins):
        self.lock.write_text(json.dumps(pins), encoding='utf-8')

    def test_all_current_commits_and_cli_match(self):
        self.assertEqual(PINS.check(self.root), self.pins)
        result = subprocess.run([sys.executable, str(self.script)], cwd=self.root.parent,
                                env=self.environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(),
                         [name + ' ' + commit for name, commit in self.pins.items()])

    def test_uninitialized_requires_explicit_opt_in(self):
        shutil.rmtree(self.root / 'third_party/uftrace')
        with self.assertRaisesRegex(ValueError, 'submodule update --init --recursive'):
            PINS.check(self.root)
        self.assertEqual(PINS.check(self.root, allow_uninitialized=True), self.pins)

    def test_empty_uninitialized_directory_is_allowed_in_python_only_jobs(self):
        checkout = self.root / 'third_party/uftrace'
        shutil.rmtree(checkout)
        checkout.mkdir()
        self.assertEqual(PINS.check(self.root, allow_uninitialized=True), self.pins)

    def test_wrong_initialized_head_is_rejected_even_with_opt_in(self):
        checkout = self.root / 'third_party/libtraceevent'
        (checkout / 'source').write_text('upstream changed\n')
        self.git(checkout, 'commit', '-qam', 'different source')
        for allow_uninitialized in [False, True]:
            with self.subTest(allow_uninitialized=allow_uninitialized):
                with self.assertRaisesRegex(ValueError, 'libtraceevent HEAD differs'):
                    PINS.check(self.root, allow_uninitialized=allow_uninitialized)

    def test_tracked_edits_and_staged_edits_are_rejected(self):
        checkout = self.root / 'third_party/libtracefs'
        (checkout / 'source').write_text('local changes\n')
        with self.assertRaisesRegex(ValueError, 'tracked submodule sources are modified: libtracefs'):
            PINS.check(self.root)
        self.git(checkout, 'add', 'source')
        with self.assertRaisesRegex(ValueError, 'tracked submodule sources are modified: libtracefs'):
            PINS.check(self.root, allow_uninitialized=True)

    def test_untracked_build_artifacts_are_allowed(self):
        (self.root / 'third_party/trace-cmd/build-output').write_text('generated\n')
        self.assertEqual(PINS.check(self.root), self.pins)

    def test_only_checks_selected_checkouts_but_every_gitlink(self):
        shutil.rmtree(self.root / 'third_party/libtracefs')
        self.assertEqual(PINS.check(self.root, only=['uftrace']),
                         {'uftrace': self.pins['uftrace']})
        self.git(self.root, 'update-index', '--cacheinfo',
                 '160000,' + 'f' * 40 + ',third_party/libtracefs')
        with self.assertRaisesRegex(ValueError, 'every indexed submodule commit exactly'):
            PINS.check(self.root, only=['uftrace'], allow_uninitialized=True)

    def test_unknown_dependency_selection_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unlocked submodule: unknown'):
            PINS.check(self.root, only=['unknown'])

    def test_lock_requires_every_gitlink_and_no_extra_dependencies(self):
        for pins in [dict(self.pins, extra='e' * 40),
                     {name: commit for name, commit in self.pins.items() if name != 'uftrace'}]:
            with self.subTest(pins=pins):
                self.write_lock(pins)
                with self.assertRaisesRegex(ValueError, 'every indexed submodule commit exactly'):
                    PINS.check(self.root, allow_uninitialized=True)

    def test_full_lowercase_sha_and_valid_names_are_required(self):
        for name, commit in [('uftrace', '842ed731248a'), ('uftrace', 'A' * 40),
                             ('uftrace', 'v0.20'), ('uftrace', None), ('uftrace', 123),
                             ('../uftrace', 'a' * 40), ('uftrace/child', 'a' * 40)]:
            with self.subTest(name=name, commit=commit):
                self.write_lock({name: commit})
                with self.assertRaisesRegex(ValueError, 'full lowercase commit SHAs'):
                    PINS.check(self.root)

    def test_invalid_json_shapes_are_rejected_without_traceback(self):
        for contents in ['[]', 'null', '"uftrace"', '{', '{"uftrace": []}']:
            with self.subTest(contents=contents):
                self.lock.write_text(contents, encoding='utf-8')
                result = subprocess.run([sys.executable, str(self.script)],
                                        env=self.environment, capture_output=True, text=True)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn('Dependency pin check failed:', result.stderr)
                self.assertNotIn('Traceback', result.stderr)

    def test_missing_lock_is_rejected_without_traceback(self):
        self.lock.unlink()
        result = subprocess.run([sys.executable, str(self.script)],
                                env=self.environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('Dependency pin check failed:', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_unmerged_gitlink_is_rejected(self):
        self.git(self.root, 'update-index', '--force-remove', 'third_party/uftrace')
        self.git(self.root, 'update-index', '--index-info',
                 input_text='160000 ' + self.pins['uftrace'] + ' 1\tthird_party/uftrace\n')
        with self.assertRaisesRegex(ValueError, 'unmerged submodule: third_party/uftrace'):
            PINS.check(self.root, allow_uninitialized=True)


if __name__ == '__main__':
    unittest.main()
