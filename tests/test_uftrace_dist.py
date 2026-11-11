"""Validate native payload identity and include exact patched corresponding source."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import helpers

specification = importlib.util.spec_from_file_location('uftrace_dist', Path(helpers.ROOT) / 'scripts/uftrace_dist.py')
uftrace_dist = importlib.util.module_from_spec(specification)
specification.loader.exec_module(uftrace_dist)


class UftraceDistTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='fg-uftrace-dist-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.upstream = self.root / 'third_party/uftrace'
        self.upstream.mkdir(parents=True)
        self.patches = self.root / 'patches/uftrace'
        self.patches.mkdir(parents=True)
        self.prefix = self.root / 'prefix'
        self.prefix.mkdir()
        for repository in (self.root, self.upstream):
            self.git(repository, 'init', '-q')
            self.git(repository, 'config', 'user.email', 'test@example.invalid')
            self.git(repository, 'config', 'user.name', 'Test')
        (self.upstream / 'COPYING').write_text('Test license\n')
        (self.upstream / 'file.c').write_text('int original;\n')
        self.git(self.upstream, 'add', '.')
        self.git(self.upstream, 'commit', '-qm', 'base')
        self.pin = self.git(self.upstream, 'rev-parse', 'HEAD').decode().strip()
        (self.root / 'third_party/versions.json').write_text(json.dumps({'uftrace': self.pin}))
        self.git(self.root, 'update-index', '--add', '--cacheinfo', '160000,' + self.pin + ',third_party/uftrace')
        (self.upstream / 'file.c').write_text('int patched;\n')
        self.patch_bytes = self.git(self.upstream, 'diff')
        self.git(self.upstream, 'checkout', '--', 'file.c')
        (self.patches / '0001-test.patch').write_bytes(self.patch_bytes)
        (self.patches / 'series').write_text('0001-test.patch\n')
        (self.prefix / 'funcgraph-uftrace.pin').write_text(self.pin + '\n')
        (self.prefix / 'funcgraph-patches.sha256').write_text(
            hashlib.sha256(self.patch_bytes).hexdigest() + '  0001-test.patch\n')
        for name in ['bin/uftrace'] + ['lib/uftrace/' + name for name in uftrace_dist.LIBRARIES]:
            target = self.prefix / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'\x7fELF\x02\x01' + b'\x00' * 12 + struct.pack('<H', 62))
            target.chmod(0o755)
        self.architecture = patch.object(uftrace_dist, 'host_architecture', return_value='x86_64')
        self.architecture.start()
        self.addCleanup(self.architecture.stop)

    def git(self, directory, *arguments):
        return subprocess.check_output(['git', '-C', str(directory), *arguments], stderr=subprocess.PIPE)

    def stage(self, suffix='payload'):
        return uftrace_dist.stage_payload(self.prefix, self.root / suffix, self.root)

    def test_complete_payload_has_license_identity_hashes_and_patched_source(self):
        payload = self.stage()
        manifest = json.loads((payload / 'manifest.json').read_text())
        self.assertEqual(manifest['architecture'], 'x86_64')
        self.assertEqual(manifest['upstream'], self.pin)
        self.assertEqual((payload / 'COPYING').read_text(), 'Test license\n')
        for name, checksum in manifest['files'].items():
            self.assertEqual(uftrace_dist.digest(payload / name), checksum)
        self.assertTrue((payload / 'bin/uftrace').stat().st_mode & 0o111)
        with tarfile.open(payload / 'source.tar.gz') as archive:
            self.assertEqual(archive.extractfile('uftrace/file.c').read(), b'int patched;\n')
            self.assertEqual(archive.extractfile('uftrace/funcgraph/0001-test.patch').read(), self.patch_bytes)
            self.assertIn(b'--without-libpython', archive.extractfile('uftrace/funcgraph/BUILDING').read())
        self.assertEqual((payload / 'source.tar.gz').read_bytes(), (self.stage('second') / 'source.tar.gz').read_bytes())

    def test_stale_pin_or_patch_checksums_are_rejected(self):
        for filename, expected in [('funcgraph-uftrace.pin', 'pin'), ('funcgraph-patches.sha256', 'checksums')]:
            with self.subTest(filename=filename):
                target = self.prefix / filename
                original = target.read_text()
                target.write_text('stale\n')
                with self.assertRaisesRegex(ValueError, expected):
                    self.stage()
                target.write_text(original)
                self.assertFalse((self.root / 'payload').exists())

    def test_lock_mismatch_is_rejected_before_staging(self):
        (self.root / 'third_party/versions.json').write_text(json.dumps({'uftrace': '0' * 40}))
        with self.assertRaisesRegex(ValueError, 'gitlink differs'):
            self.stage()
        self.assertFalse((self.root / 'payload').exists())

    def test_tracked_upstream_edits_are_rejected_before_staging(self):
        (self.upstream / 'file.c').write_text('int unexpected;\n')
        for staged in (False, True):
            with self.subTest(staged=staged):
                if staged:
                    self.git(self.upstream, 'add', 'file.c')
                with self.assertRaisesRegex(ValueError, 'tracked edits'):
                    self.stage()
                self.assertFalse((self.root / 'payload').exists())

    def test_missing_libraries_wrong_architecture_symlink_and_permissions_are_rejected(self):
        target = self.prefix / 'bin/uftrace'
        target.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'not executable'):
            self.stage()
        target.chmod(0o755)
        for header in (b'not ELF', b'\x7fELF\x02\x01' + b'\x00' * 12 + struct.pack('<H', 183)):
            target.write_bytes(header)
            with self.assertRaisesRegex(ValueError, 'ELF architecture'):
                self.stage()
        target.unlink()
        target.symlink_to(self.upstream / 'file.c')
        with self.assertRaisesRegex(ValueError, 'regular'):
            self.stage()

    def test_source_failure_cleans_partial_destination(self):
        with patch.object(uftrace_dist, 'source_archive', side_effect=ValueError('source unavailable')):
            with self.assertRaisesRegex(ValueError, 'source unavailable'):
                self.stage()
        self.assertFalse((self.root / 'payload').exists())

    def test_existing_destination_is_not_overwritten(self):
        payload = self.root / 'payload'
        payload.mkdir()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.stage()


if __name__ == '__main__':
    unittest.main()
