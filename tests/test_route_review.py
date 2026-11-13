from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from githug.core import Project,git
from githug.route_review import routes,default_routes,compare_routes,file_patch,proposal_url,checked_proposal_url,RoutesChanged
from githug.updating import inventory

class RouteReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.seed=self.root/'seed'
        git('init','-b','main',self.seed);git('config','user.name','Test',cwd=self.seed);git('config','user.email','test@example.invalid',cwd=self.seed)
        (self.seed/'code.py').write_text('original\n');self.commit('base')
        self.common=git('rev-parse','HEAD',cwd=self.seed).strip()
        git('checkout','-b','feature/gui',cwd=self.seed)
        (self.seed/'code.py').write_text('feature\n');self.commit('feature')
        self.feature=git('rev-parse','HEAD',cwd=self.seed).strip()
        git('checkout','main',cwd=self.seed);(self.seed/'main-only.txt').write_text('main edit\n');self.commit('main edit')
        self.project=Project(self.root/'managed','https://github.com/example/test');self.project.base.mkdir(parents=True)
        git('clone','--mirror',self.seed,self.project.cache)

    def commit(self,message):
        git('add','.',cwd=self.seed);git('-c','commit.gpgsign=false','commit','-m',message,cwd=self.seed)

    def test_direction_and_common_ancestor_diff_without_working_folder_changes(self):
        before=inventory(self.seed);refs=routes(self.project)
        plan=compare_routes(self.project,'main','feature/gui')
        self.assertEqual(plan.common,self.common);self.assertEqual(plan.incoming,1);self.assertEqual(plan.destination_only,1)
        self.assertEqual(plan.files,(('M','code.py'),));self.assertTrue(plan.can_propose)
        patch=file_patch(self.project,plan,'code.py');self.assertIn('+feature',patch);self.assertIn('-original',patch)
        self.assertEqual(inventory(self.seed),before);self.assertEqual(routes(self.project),refs)
        reversed_plan=compare_routes(self.project,'feature/gui','main')
        self.assertEqual(reversed_plan.files,(('A','main-only.txt'),))

    def test_contained_routes_cannot_create_duplicate_proposal(self):
        git('update-ref','refs/heads/already',self.common,cwd=self.project.cache)
        plan=compare_routes(self.project,'main','already')
        self.assertFalse(plan.can_propose);self.assertEqual(plan.files,());self.assertIn('すでに',plan.summary)
        with self.assertRaises(ValueError):proposal_url(self.project,plan)

    def test_unrelated_history_has_clear_result(self):
        other=self.root/'other';git('init','-b','other',other)
        git('config','user.name','Test',cwd=other);git('config','user.email','test@example.invalid',cwd=other)
        (other/'unrelated.txt').write_text('separate\n');git('add','.',cwd=other);git('-c','commit.gpgsign=false','commit','-m','separate',cwd=other)
        git('fetch',other,'refs/heads/other:refs/heads/unrelated',cwd=self.project.cache)
        plan=compare_routes(self.project,'main','unrelated')
        self.assertIsNone(plan.common);self.assertFalse(plan.can_propose);self.assertIn('共通の出発点',plan.summary)

    def test_refresh_required_if_either_tip_changes_or_branch_disappears(self):
        plan=compare_routes(self.project,'main','feature/gui')
        git('update-ref','refs/heads/main',self.common,cwd=self.project.cache)
        with self.assertRaises(RoutesChanged):checked_proposal_url(self.project,plan)
        git('update-ref','refs/heads/main',plan.base_sha,cwd=self.project.cache)
        git('update-ref','-d','refs/heads/feature/gui',cwd=self.project.cache)
        with self.assertRaises(RoutesChanged):checked_proposal_url(self.project,plan)

    def test_url_preserves_direction_and_encodes_branch_characters(self):
        plan=compare_routes(self.project,'main','feature/gui')
        self.assertEqual(checked_proposal_url(self.project,plan),'https://github.com/example/test/compare/main...feature%2Fgui?quick_pull=1')
        odd=replace(plan,head='topic/a#b%plus')
        self.assertIn('topic%2Fa%23b%25plus',proposal_url(self.project,odd))
        self.project.url='file:///unexpected'
        with self.assertRaises(ValueError):proposal_url(self.project,plan)

    def test_same_or_deleted_route_is_rejected_and_defaults_are_explicit(self):
        with self.assertRaises(ValueError):compare_routes(self.project,'main','main')
        with self.assertRaises(RoutesChanged):compare_routes(self.project,'main','missing')
        self.assertEqual(default_routes(routes(self.project),self.feature),('main','feature/gui'))
        with self.assertRaises(ValueError):default_routes({'main':self.common})

    def test_patch_only_accepts_selected_files_and_limits_display(self):
        plan=compare_routes(self.project,'main','feature/gui')
        with self.assertRaises(ValueError):file_patch(self.project,plan,'main-only.txt')
        self.assertIn('表示量',file_patch(self.project,plan,'code.py',limit=10))

    def test_patch_for_bracket_filename_only_contains_selected_file(self):
        git('checkout','feature/gui',cwd=self.seed)
        for name,content in (('[ab].txt','CHOSEN_FILE_CONTENT'),
                             ('a.txt','NOT_CHOSEN_A'),('b.txt','NOT_CHOSEN_B')):
            (self.seed/name).write_text(content+'\n')
        self.commit('bracket filenames')
        git('fetch','origin',cwd=self.project.cache)
        plan=compare_routes(self.project,'main','feature/gui')
        text=file_patch(self.project,plan,'[ab].txt')
        self.assertIn('+CHOSEN_FILE_CONTENT',text)
        self.assertNotIn('NOT_CHOSEN_A',text)
        self.assertNotIn('NOT_CHOSEN_B',text)
