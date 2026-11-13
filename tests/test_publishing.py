from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from githug.core import Project, git, GitError
from githug.publishing import Change, choose_files, selection_counts, inspect_push, preview_changes, publish, remote_head, RemoteChanged
from githug.updating import inventory, UpdateConflict

class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.seed = self.root / 'seed'
        git('init', '-b', 'main', self.seed)
        git('config', 'user.name', 'Tester', cwd=self.seed)
        git('config', 'user.email', 'tester@example.com', cwd=self.seed)
        (self.seed / 'code.py').write_text('old = True\n')
        (self.seed / 'delete.txt').write_text('old\n')
        (self.seed / '.gitignore').write_text('*.db\n.env\n')
        git('add', '.', cwd=self.seed)
        git('commit', '-m', 'base', cwd=self.seed)
        self.base = git('rev-parse', 'HEAD', cwd=self.seed).strip()
        self.remote = self.root / 'remote.git'
        git('clone', '--bare', self.seed, self.remote)
        self.project = Project(self.root / 'managed', 'https://github.com/test/project')
        self.project.url = str(self.remote)
        self.project.base.mkdir(parents=True)
        git('clone', '--mirror', self.remote, self.project.cache)
        self.folder = Path(self.project.snapshot(self.base))
        (self.folder / 'code.py').write_text('new = True\n')
        (self.folder / 'add.txt').write_text('optional\n')
        (self.folder / 'private.db').write_bytes(b'private data')
        (self.folder / '.env').write_text('API_KEY=private')
        (self.folder / 'delete.txt').unlink()

    def tearDown(self): self.temp.cleanup()

    def plan(self): return inspect_push(self.project, self.folder)

    def send(self, plan, selected=('code.py',), branch='work/test', is_new=True):
        return publish(self.project, plan, selected, branch, is_new, 'change code', 'Tester', 'tester@example.com')

    def test_sensitive_modified_files_are_not_preselected(self):
        for name in ['settings.json', 'madobe.json', '.env', 'private.db', 'api_key.txt',
                     'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519', '.npmrc', '.pypirc',
                     'release.keystore', 'release.jks', 'service-account-prod.json',
                     'keys/SERVICE_ACCOUNT.JSON']:
            self.assertFalse(Change(name, 'M', 'hash').initially_selected)
        self.assertTrue(Change('core.py', 'M', 'hash').initially_selected)
        self.assertTrue(Change('id_rsa.pub', 'A', 'hash').initially_selected)
        self.assertTrue(Change('service-account-guide.md', 'A', 'hash').initially_selected)

    def test_filename_with_brackets_is_literal_in_git_and_preview(self):
        for name in ('a.txt', 'b.txt', '[ab].txt'):
            (self.seed / name).write_bytes(b'base\n')
        git('add', '.', cwd=self.seed)
        git('-c', 'commit.gpgsign=false', 'commit', '-m', 'bracket files', cwd=self.seed)
        git('push', self.remote, 'main', cwd=self.seed)
        git('fetch', 'origin', cwd=self.project.cache)
        sha = git('rev-parse', 'refs/heads/main', cwd=self.project.cache).strip()
        folder = Path(self.project.snapshot(sha))
        for name in ('a.txt', 'b.txt', '[ab].txt'):
            (folder / name).write_bytes(b'edited\n')
        plan = inspect_push(self.project, folder)
        preview = preview_changes(self.project, plan, ['[ab].txt'])
        self.assertEqual(preview['summary'], '1\t1\t[ab].txt\n')

    def test_bulk_selection_and_counts(self):
        changes = [Change('code.py','M','x'),Change('added.py','A','y'),Change('old.txt','D',None),Change('private.db','A','z')]
        default = choose_files(changes, 'default')
        self.assertEqual(default, {'code.py','added.py'})
        self.assertEqual(selection_counts(changes, default), {'M':1,'A':1,'D':0})
        all_paths = choose_files(changes, 'all')
        self.assertEqual(selection_counts(changes, all_paths), {'M':1,'A':2,'D':1})
        self.assertEqual(choose_files(changes,'none'), set())

    def test_personal_notes_are_outside_source_and_never_in_preview(self):
        from githug.notes import Notes
        notes = Notes(self.project.base)
        notes.set('history', self.base, 'private history comment')
        notes.set('folders', str(self.folder), 'private folder comment')
        plan = self.plan()
        self.assertNotIn('personal_notes.json', [change.path for change in plan.changes])
        preview = preview_changes(self.project, plan, ['code.py'])
        self.assertNotIn('private history comment', preview['patch'])
        with self.assertRaises(ValueError): inspect_push(self.project, self.project.base, self.base)

    def test_review_defaults_and_preview_do_not_change_source(self):
        before = inventory(self.folder)
        plan = self.plan()
        self.assertEqual({c.path:c.kind for c in plan.changes}, {'code.py':'M','delete.txt':'D','add.txt':'A'})
        self.assertEqual([c.path for c in plan.changes if c.initially_selected], ['code.py', 'add.txt'])
        preview = preview_changes(self.project, plan, ['code.py'])
        self.assertIn('+new = True', preview['patch'])
        self.assertNotIn('optional', preview['patch'])
        self.assertEqual(inventory(self.folder), before)

    def test_new_branch_sends_only_selected_changes(self):
        before = inventory(self.folder)
        result = self.send(self.plan())
        self.assertEqual(remote_head(self.project, 'main'), self.base)
        self.assertEqual(remote_head(self.project, 'work/test'), result['sha'])
        self.assertEqual(git('show', result['sha']+':code.py', cwd=self.remote), 'new = True\n')
        self.assertEqual(git('show', result['sha']+':delete.txt', cwd=self.remote), 'old\n')
        with self.assertRaises(GitError): git('show', result['sha']+':add.txt', cwd=self.remote)
        self.assertEqual(inventory(self.folder), before)
        again = self.plan()
        self.assertNotIn('code.py', [c.path for c in again.changes])

    def test_explicit_addition_and_deletion(self):
        result = self.send(self.plan(), ['add.txt','delete.txt'])
        self.assertEqual(git('show', result['sha']+':add.txt', cwd=self.remote), 'optional\n')
        with self.assertRaises(GitError): git('show', result['sha']+':delete.txt', cwd=self.remote)
        self.assertEqual(git('show', result['sha']+':code.py', cwd=self.remote), 'old = True\n')

    def test_existing_branch_fast_forward(self):
        result = self.send(self.plan(), branch='main', is_new=False)
        self.assertEqual(remote_head(self.project, 'main'), result['sha'])
        self.assertEqual(git('rev-parse', result['sha']+'^', cwd=self.remote).strip(), self.base)

    def advance_remote(self):
        (self.seed / 'else.txt').write_text('someone else\n')
        git('add','.',cwd=self.seed)
        git('commit','-m','other update',cwd=self.seed)
        git('push', self.remote, 'main:refs/heads/main', cwd=self.seed)
        return git('rev-parse','HEAD',cwd=self.seed).strip()

    def test_remote_advance_before_send_proposes_new_branch(self):
        plan = self.plan()
        other = self.advance_remote()
        with self.assertRaises(RemoteChanged): self.send(plan, branch='main', is_new=False)
        self.assertEqual(remote_head(self.project, 'main'), other)
        self.send(plan, branch='work/safe')
        self.assertEqual(remote_head(self.project, 'main'), other)

    def test_remote_advance_during_push_is_not_overwritten(self):
        plan = self.plan()
        original = git
        advanced = []
        def command(*args, **kwargs):
            if args[0] == 'push': advanced.append(self.advance_remote())
            return original(*args, **kwargs)
        with patch('githug.publishing.git', side_effect=command):
            with self.assertRaises(GitError): self.send(plan, branch='main', is_new=False)
        self.assertEqual(remote_head(self.project,'main'), advanced[0])
        self.assertTrue(list((self.project.base/'outbox').glob('*/repo/.git')))

    def test_new_branch_collision_is_not_replaced(self):
        plan = self.plan()
        original = self.send(plan)
        with self.assertRaises(RemoteChanged): self.send(plan)
        self.assertEqual(remote_head(self.project, 'work/test'), original['sha'])

    def test_changed_selected_file_refused(self):
        plan = self.plan()
        (self.folder/'code.py').write_text('changed again')
        with self.assertRaises(UpdateConflict): self.send(plan)
        self.assertIsNone(remote_head(self.project,'work/test'))

    def test_lost_response_is_checked_without_second_push(self):
        plan = self.plan()
        original = git
        calls = []
        def command(*args, **kwargs):
            if args[0] == 'push':
                calls.append(args)
                original(*args, **kwargs)
                raise GitError('connection lost after send')
            return original(*args, **kwargs)
        with patch('githug.publishing.git', side_effect=command): result = self.send(plan)
        self.assertEqual(len(calls), 1)
        self.assertEqual(remote_head(self.project,'work/test'), result['sha'])
