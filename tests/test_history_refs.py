"""Show branch/tag history while preserving copies made from older PR refs."""
from pathlib import Path
import tempfile
import unittest

from githug.core import Project, git
from githug.publishing import inspect_push, preview_changes, publish
from githug.notes import Notes


class HistoryRefsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.seed = self.root / 'seed'
        git('init', '-b', 'main', self.seed)
        git('config', 'user.name', 'Test', cwd=self.seed)
        git('config', 'user.email', 'test@example.invalid', cwd=self.seed)
        (self.seed / 'file.txt').write_bytes(b'base\n')
        self.commit('base')
        self.base = git('rev-parse', 'HEAD', cwd=self.seed).strip()
        git('checkout', '-b', 'pull-only', cwd=self.seed)
        (self.seed / 'file.txt').write_bytes(b'PR contents\n')
        self.commit('PR only')
        self.pr = git('rev-parse', 'HEAD', cwd=self.seed).strip()
        git('update-ref', 'refs/pull/1/head', self.pr, cwd=self.seed)
        git('update-ref', 'refs/pull/2/head', self.base, cwd=self.seed)
        git('checkout', 'main', cwd=self.seed)
        git('branch', '-D', 'pull-only', cwd=self.seed)
        git('checkout', '--orphan', 'tag-only', cwd=self.seed)
        git('rm', '-rf', '.', cwd=self.seed)
        (self.seed / 'tag.txt').write_bytes(b'tag contents\n')
        self.commit('tag only')
        self.tag = git('rev-parse', 'HEAD', cwd=self.seed).strip()
        git('tag', '-a', 'release-tag', '-m', 'tag', cwd=self.seed)
        git('checkout', 'main', cwd=self.seed)
        git('branch', '-D', 'tag-only', cwd=self.seed)
        self.project = Project(self.root / 'managed', 'https://github.com/example/test')
        self.project.url = str(self.seed)

    def commit(self, message):
        git('add', '.', cwd=self.seed)
        git('-c', 'commit.gpgsign=false', 'commit', '-m', message, cwd=self.seed)

    def make_old_mirror(self):
        self.project.base.mkdir(parents=True)
        git('clone', '--mirror', self.seed, self.project.cache)

    def test_new_registration_only_fetches_and_displays_branches_and_tags(self):
        history = self.project.refresh()
        self.assertEqual({row['sha'] for row in history}, {self.base, self.tag})
        refs = git('for-each-ref', '--format=%(refname)', cwd=self.project.cache)
        self.assertNotIn('refs/pull/', refs)
        self.assertIn('refs/tags/release-tag', refs)
        self.assertTrue(all('refs/pull/' not in row['refs'] for row in history))
        self.assertIn('HEAD -> main', next(row['refs'] for row in history if row['sha'] == self.base))

    def test_old_mirror_hides_pr_history_and_pr_names_on_shared_commits(self):
        self.make_old_mirror()
        history = self.project.history()
        self.assertEqual({row['sha'] for row in history}, {self.base, self.tag})
        self.assertTrue(all('refs/pull/' not in row['refs'] for row in history))
        self.assertEqual(git('rev-parse', 'refs/pull/1/head', cwd=self.project.cache).strip(), self.pr)

    def test_refresh_preserves_pr_copy_notes_and_comparison_after_remote_pr_deletion(self):
        self.make_old_mirror()
        folder = Path(self.project.snapshot(self.pr))
        notes = Notes(self.project.base)
        notes.set('folders', str(folder), 'my copy note')
        notes.set('history', self.pr, 'my history note')
        records = self.project.folder_versions()
        git('update-ref', '-d', 'refs/pull/1/head', cwd=self.seed)
        git('branch', 'new-route', self.base, cwd=self.seed)
        history = self.project.refresh()
        self.assertEqual({row['sha'] for row in history}, {self.base, self.tag})
        self.assertEqual(git('rev-parse', 'refs/pull/1/head', cwd=self.project.cache).strip(), self.pr)
        self.assertEqual(self.project.compare(self.pr, folder), [])
        self.assertEqual(self.project.folder_versions(), records)
        notes.reload()
        self.assertEqual(notes.get('folders', str(folder)), 'my copy note')
        self.assertEqual(notes.get('history', self.pr), 'my history note')
        (folder / 'file.txt').write_bytes(b'edited\n')
        plan = inspect_push(self.project, folder)
        self.assertEqual(plan.base, self.pr)
        self.assertIn('+edited', preview_changes(self.project, plan, ['file.txt'])['patch'])
        recreated = Path(self.project.snapshot(self.pr))
        self.assertEqual((recreated / 'file.txt').read_text(), 'PR contents\n')

    def test_later_fetch_and_post_publish_refresh_do_not_import_new_pr_refs(self):
        self.make_old_mirror()
        git('update-ref', 'refs/pull/3/head', self.pr, cwd=self.seed)
        self.project.refresh()
        refs = git('for-each-ref', '--format=%(refname)', cwd=self.project.cache)
        self.assertNotIn('refs/pull/3/head', refs)
        folder = Path(self.project.snapshot(self.base))
        (folder / 'file.txt').write_bytes(b'published\n')
        result = publish(self.project, inspect_push(self.project, folder), ['file.txt'],
                         'published-route', True, 'change', 'Test', 'test@example.invalid')
        refs = git('for-each-ref', '--format=%(refname)', cwd=self.project.cache)
        self.assertNotIn('refs/pull/3/head', refs)
        self.assertIn('refs/heads/published-route', refs)
        self.assertIn(result['sha'], {row['sha'] for row in self.project.history()})

    def test_deleted_branches_and_tags_are_pruned_but_saved_pr_refs_are_retained(self):
        self.make_old_mirror()
        git('branch', 'temporary-route', self.base, cwd=self.seed)
        self.project.refresh()
        git('branch', '-D', 'temporary-route', cwd=self.seed)
        git('tag', '-d', 'release-tag', cwd=self.seed)
        self.project.refresh()
        refs = git('for-each-ref', '--format=%(refname)', cwd=self.project.cache)
        self.assertNotIn('refs/heads/temporary-route', refs)
        self.assertNotIn('refs/tags/release-tag', refs)
        self.assertIn('refs/pull/1/head', refs)
        self.assertEqual({row['sha'] for row in self.project.history()}, {self.base})

    def test_empty_repository_has_empty_history(self):
        self.project.base.mkdir(parents=True)
        git('init', '--bare', '-b', 'main', self.project.cache)
        self.assertEqual(self.project.history(), [])
