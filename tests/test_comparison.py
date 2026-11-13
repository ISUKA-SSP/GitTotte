"""Comparison must use Git's text rules without changing the user's index."""
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from githug.core import Project, git, GitError, ComparisonCancelled
from githug.updating import remove_temporary


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.seed = self.root / 'seed'
        git('init', '-b', 'main', self.seed)
        git('config', 'user.name', 'Test', cwd=self.seed)
        git('config', 'user.email', 'test@example.invalid', cwd=self.seed)
        git('config', 'core.autocrlf', 'false', cwd=self.seed)
        (self.seed / '.gitattributes').write_bytes(b'*.txt text\n*.bin -text\n')
        for name in ('text.txt', 'binary.bin', 'plain.dat', '日本語 [test].txt'):
            (self.seed / name).write_bytes(b'first\nsecond\n')
        git('add', '.', cwd=self.seed)
        git('-c', 'commit.gpgsign=false', 'commit', '-m', 'base', cwd=self.seed)
        self.sha = git('rev-parse', 'HEAD', cwd=self.seed).strip()
        self.project = Project(self.root / 'managed', 'https://github.com/example/test')
        self.project.base.mkdir(parents=True)
        git('clone', '--mirror', self.seed, self.project.cache)
        git('config', 'core.autocrlf', 'false', cwd=self.project.cache)
        self.folder = self.root / 'files'
        shutil.copytree(self.seed, self.folder, ignore=shutil.ignore_patterns('.git'))

    def test_plain_folder_normalizes_text_but_preserves_binary_differences(self):
        (self.folder / 'text.txt').write_bytes(b'first\r\nsecond\r\n')
        (self.folder / 'binary.bin').write_bytes(b'first\r\nsecond\r\n')
        self.assertEqual(self.project.compare(self.sha, self.folder),
                         [('内容が異なる', 'binary.bin')])
        (self.folder / 'text.txt').write_bytes(b'edited\r\nsecond\r\n')
        self.assertIn(('内容が異なる', 'text.txt'), self.project.compare(self.sha, self.folder))

    def test_missing_attributes_fall_back_to_selected_revision(self):
        (self.folder / '.gitattributes').unlink()
        (self.folder / 'text.txt').write_bytes(b'first\r\nsecond\r\n')
        self.assertEqual(self.project.compare(self.sha, self.folder),
                         [('手元にない', '.gitattributes')])

    def test_folder_attributes_override_selected_revision(self):
        (self.folder / '.gitattributes').write_bytes(b'*.txt -text\n*.bin -text\n')
        (self.folder / 'text.txt').write_bytes(b'first\r\nsecond\r\n')
        self.assertEqual(set(self.project.compare(self.sha, self.folder)),
                         {('内容が異なる', '.gitattributes'), ('内容が異なる', 'text.txt')})

    def test_local_config_is_used_and_staged_changes_are_untouched(self):
        folder = Path(self.project.snapshot(self.sha))
        git('config', 'core.autocrlf', 'true', cwd=folder)
        (folder / 'plain.dat').write_bytes(b'first\r\nsecond\r\n')
        (folder / 'text.txt').write_bytes(b'staged\n')
        git('add', '--', 'text.txt', cwd=folder)
        index = folder / '.git' / 'index'
        before_index = index.read_bytes()
        before_files = {name: (folder / name).read_bytes()
                        for name in ('plain.dat', 'text.txt', '.gitattributes')}
        self.assertEqual(self.project.compare(self.sha, folder), [('内容が異なる', 'text.txt')])
        self.assertEqual(index.read_bytes(), before_index)
        self.assertEqual({name: (folder / name).read_bytes() for name in before_files}, before_files)
        self.assertFalse((self.project.cache / 'index').exists())

    def test_git_process_count_does_not_grow_with_file_count(self):
        for i in range(32):
            name = f'file-{i}.txt'
            (self.seed / name).write_bytes(b'original\n')
            (self.folder / name).write_bytes(b'original\r\n')
        git('add', '.', cwd=self.seed)
        git('-c', 'commit.gpgsign=false', 'commit', '-m', 'more files', cwd=self.seed)
        sha = git('rev-parse', 'HEAD', cwd=self.seed).strip()
        git('fetch', self.seed, 'main:refs/heads/main', cwd=self.project.cache)
        with patch('githug.core.git', wraps=git) as calls:
            self.assertEqual(self.project.compare(sha, self.folder), [])
        self.assertEqual(calls.call_count, 3)

    def test_cancel_before_batch_comparison(self):
        cancelled = False
        def progress(stage, *args):
            nonlocal cancelled
            if stage == '履歴と内容を一括比較':
                cancelled = True
        with patch('githug.core.git', wraps=git) as calls:
            with self.assertRaises(ComparisonCancelled):
                self.project.compare(self.sha, self.folder, progress=progress, cancelled=lambda: cancelled)
        self.assertFalse(any('diff' in call.args for call in calls.call_args_list))

    def test_failed_download_removes_only_its_partial_directory(self):
        remove_temporary(self.project.cache)
        unrelated = self.project.base / 'keep.txt'
        unrelated.write_text('keep')
        def fail_clone(*args, **kwargs):
            partial = Path(args[-1])
            partial.mkdir()
            (partial / 'partial.pack').write_bytes(b'incomplete')
            raise GitError('download failed')
        with patch('githug.core.git', side_effect=fail_clone):
            with self.assertRaisesRegex(GitError, 'download failed'):
                self.project.refresh()
        self.assertFalse(list(self.project.base.glob('download-*')))
        self.assertFalse(self.project.cache.exists())
        self.assertEqual(unrelated.read_text(), 'keep')
        self.project.url = str(self.seed)
        self.assertTrue(self.project.refresh())

    def test_cleanup_failure_preserves_download_error_and_reports_leftover(self):
        remove_temporary(self.project.cache)
        original = GitError('original download error')
        def fail_clone(*args, **kwargs):
            Path(args[-1]).mkdir()
            raise original
        with patch('githug.core.git', side_effect=fail_clone), \
                patch('githug.updating.remove_temporary', side_effect=PermissionError('cleanup denied')):
            with self.assertRaises(GitError) as caught:
                self.project.refresh()
        self.assertIs(caught.exception.__cause__, original)
        self.assertIn('original download error', str(caught.exception))
        self.assertIn('cleanup denied', str(caught.exception))
        leftovers = list(self.project.base.glob('download-*'))
        self.assertEqual(len(leftovers), 1)
        self.assertIn(str(leftovers[0]), str(caught.exception))
        self.assertFalse(self.project.cache.exists())
