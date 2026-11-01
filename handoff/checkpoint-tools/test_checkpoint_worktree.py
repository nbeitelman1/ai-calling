"""Synthetic local Git tests. No network, providers or production repositories."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import checkpoint_worktree as cp


class TestCheckpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)
        self.root = self.parent / 'repo'
        self.root.mkdir()
        subprocess.run(['git', 'init', '-q', '-b', 'offline-test', str(self.root)], check=True)
        (self.root / 'file.py').write_text('version = 1\n')
        cp.git(self.root, 'add', 'file.py')
        cp.git(self.root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
               'commit', '-qm', 'fixture')
        self.output = self.parent / 'snapshot.zip'

    def test_clean_snapshot_has_clonable_history_and_verified_files(self):
        cp.save_checkpoint(self.root, self.output)
        manifest = cp.verify_archive(self.output)
        self.assertFalse(manifest['dirty'])
        with zipfile.ZipFile(self.output) as z:
            bundle = self.parent / 'recovered.bundle'
            bundle.write_bytes(z.read('repository.bundle'))
        clone = self.parent / 'clone'
        subprocess.run(['git', 'clone', '-q', '-b', 'offline-test', str(bundle), str(clone)], check=True)
        self.assertEqual((clone / 'file.py').read_text(), 'version = 1\n')

    def test_dirty_contents_are_preserved_not_just_head(self):
        (self.root / 'file.py').write_text('version = 2\n')
        manifest = cp.save_checkpoint(self.root, self.output)
        self.assertTrue(manifest['dirty'])
        with zipfile.ZipFile(self.output) as z:
            self.assertEqual(z.read('working-tree/file.py'), b'version = 2\n')
        self.assertEqual(cp.git(self.root, 'show', 'HEAD:file.py'), b'version = 1\n')

    def test_untracked_source_requires_explicit_inclusion(self):
        (self.root / 'new.py').write_text('value = 3\n')
        with self.assertRaisesRegex(ValueError, 'Untracked'):
            cp.save_checkpoint(self.root, self.output)
        self.assertFalse(self.output.exists())
        manifest = cp.save_checkpoint(self.root, self.output, ['new.py'])
        self.assertEqual(manifest['explicit_untracked_files'], ['new.py'])
        with zipfile.ZipFile(self.output) as z:
            self.assertEqual(z.read('working-tree/new.py'), b'value = 3\n')

    def test_deleted_file_is_recorded(self):
        (self.root / 'file.py').unlink()
        manifest = cp.save_checkpoint(self.root, self.output)
        self.assertEqual(manifest['deleted_tracked_files'], ['file.py'])
        self.assertEqual(manifest['files_captured'], 0)

    def test_symlink_and_parent_traversal_are_rejected(self):
        (self.root / 'link.py').symlink_to(self.root / 'file.py')
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            cp.save_checkpoint(self.root, self.output, ['link.py'])
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            cp.safe_relative('../escape')

    def test_secret_like_content_is_refused_without_publishing(self):
        (self.root / 'file.py').write_text('token = ' + 'github_' + 'pat_' + 'x' * 35)
        with self.assertRaisesRegex(ValueError, 'Secret pattern'):
            cp.save_checkpoint(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_existing_checkpoint_is_not_overwritten(self):
        cp.save_checkpoint(self.root, self.output)
        original = self.output.read_bytes()
        with self.assertRaises(FileExistsError):
            cp.save_checkpoint(self.root, self.output)
        self.assertEqual(self.output.read_bytes(), original)

    def test_mid_capture_change_is_detected(self):
        collect = cp.collect
        calls = 0
        def moving(root, include):
            nonlocal calls
            calls += 1
            if calls == 2:
                (root / 'file.py').write_text('changed during capture\n')
            return collect(root, include)
        with patch.object(cp, 'collect', moving), self.assertRaisesRegex(RuntimeError, 'changed'):
            cp.save_checkpoint(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_hash_tampering_is_detected(self):
        cp.save_checkpoint(self.root, self.output)
        broken = self.parent / 'broken.zip'
        with zipfile.ZipFile(self.output) as source, zipfile.ZipFile(broken, 'w') as dest:
            for name in source.namelist():
                dest.writestr(name, b'tampered' if name == 'working-tree/file.py' else source.read(name))
        with self.assertRaisesRegex(ValueError, 'Hash mismatch'):
            cp.verify_archive(broken)


if __name__ == '__main__':
    unittest.main(verbosity=2)
