"""Desktop bridge invariants with the existing real SQLite scheduler."""
from pathlib import Path
import tempfile
import unittest

from director.core import connect
from director.desktop import bind, enroll, REVIEWERS, schema, status, submit, tick
from director.demo import run


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'queue.sqlite')
        self.db = connect(self.path)
        schema(self.db)
        self.head = 'a' * 40
        self.db.execute("INSERT INTO tasks(id,project,stage,phase,priority,managed) VALUES ('task','mihur','test','content',0,1)")
        enroll(self.db, 'task', self.head, required_checks=['test'])

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def delivery(self, role, subject, now=10):
        offers = tick(self.db, now=now)
        offer = next(a for a in offers if a['role'] == role)
        return bind(self.db, offer['id'], subject, now=now)

    def send(self, delivery, response, now=11):
        submit(self.db, delivery['id'], delivery['subject'], delivery['token'], response, now=now)

    def implement(self):
        a = self.delivery('backend', 'author')
        self.send(a, {'head': self.head, 'scope': 'fixture'})
        return a

    def _tests_pass(self):
        self.implement()
        a = self.delivery('test_runner', 'tester', now=12)
        self.send(a, {'head': self.head, 'source': 'synthetic://tests',
                     'checks': [{'name': 'test', 'head_sha': self.head, 'status': 'completed', 'conclusion': 'success'}]}, now=13)

    def test_restart_reconstructs_offer_without_duplicate_implementation(self):
        first = tick(self.db, now=10)
        self.db.close()
        self.db = connect(self.path)
        self.assertEqual(tick(self.db, now=11), first)
        self.assertEqual(self.db.execute('SELECT attempts FROM tasks').fetchone()[0], 1)

    def test_author_cannot_bind_independent_test_or_review(self):
        self.implement()
        offer = next(a for a in tick(self.db, now=12) if a['role'] == 'test_runner')
        with self.assertRaises(ValueError):
            bind(self.db, offer['id'], 'author', now=12)

    def test_idempotent_submission_and_conflicting_replay(self):
        a = self.implement()
        self.send(a, {'head': self.head, 'scope': 'fixture'}, now=9000)
        with self.assertRaises(ValueError):
            self.send(a, {'head': self.head, 'scope': 'different'})

    def test_wrong_owner_and_token_leave_lease_intact(self):
        a = self.delivery('backend', 'author')
        for owner, token in [('other', a['token']), ('author', 'wrong')]:
            with self.assertRaises(ValueError):
                submit(self.db, a['id'], owner, token, {'head': self.head}, now=11)
        self.assertEqual(self.db.execute('SELECT status FROM tasks').fetchone()[0], 'running')

    def test_review_rejection_reroutes_and_invalidates_siblings(self):
        self._tests_pass()
        rejector = self.delivery('math_reviewer', 'math', now=14)
        sibling = self.delivery('security_reviewer', 'security', now=14)
        result = {'head': self.head, 'verdict': 'reject', 'source': 'synthetic://math-finding'}
        self.send(rejector, result, now=15)
        self.send(rejector, result, now=16)  # repeated delivery is harmless
        with self.assertRaises(ValueError):
            self.send(sibling, {'head': self.head, 'verdict': 'accept', 'source': 'synthetic://stale'}, now=16)
        offer = next(a for a in tick(self.db, now=16) if a['role'] == 'backend')
        self.assertIn('rejection', offer['blocker'])
        self.assertEqual(self.db.execute('SELECT attempts FROM tasks').fetchone()[0], 2)

    def test_failed_tests_automatically_requeue_before_reviews(self):
        self.implement()
        a = self.delivery('test_runner', 'tester', now=12)
        self.send(a, {'head': self.head, 'source': 'synthetic://test-failure',
                     'checks': [{'name': 'test', 'head_sha': self.head, 'status': 'completed', 'conclusion': 'failure'}]}, now=13)
        offers = tick(self.db, now=14)
        self.assertEqual([a['role'] for a in offers], ['backend'])

    def test_integration_separate_and_downstream_blocked_until_acceptance(self):
        self._tests_pass()
        for index, role in enumerate(REVIEWERS.values()):
            a = self.delivery(role, role, now=14 + index)
            self.send(a, {'head': self.head, 'verdict': 'accept', 'source': 'synthetic://' + role}, now=15 + index)
        self.assertEqual(self.db.execute('SELECT status FROM tasks').fetchone()[0], 'integration')
        self.db.execute("INSERT INTO tasks(id,project,stage,phase,priority,managed) VALUES ('next','mihur','test','verification',1,1)")
        enroll(self.db, 'next', self.head)
        a = self.delivery('integration', 'independent-integrator', now=30)
        self.assertEqual(self.db.execute("SELECT attempts FROM tasks WHERE id='next'").fetchone()[0], 0)
        self.send(a, {'head': self.head, 'verdict': 'accept', 'source': 'synthetic://integration'}, now=31)
        tick(self.db, now=32)
        self.assertEqual(self.db.execute("SELECT attempts FROM tasks WHERE id='next'").fetchone()[0], 1)
        self.assertFalse(status(self.db)['release_authorized'])

    def test_agent_concurrency_and_role_switch_rejected(self):
        self._tests_pass()
        offers = tick(self.db, now=14)
        for i in range(3):
            bind(self.db, offers[i]['id'], 'reviewer-' + str(i), now=14)
        with self.assertRaises(ValueError):
            bind(self.db, offers[3]['id'], 'reviewer-3', now=14)
        with self.assertRaises(ValueError):
            bind(self.db, offers[4]['id'], 'reviewer-0', now=14)

    def test_delivery_expiry_changes_token_and_rejects_stale_result(self):
        self.implement()
        a = self.delivery('test_runner', 'tester', now=12)
        offers = tick(self.db, now=913)
        offer = next(x for x in offers if x['role'] == 'test_runner')
        fresh = bind(self.db, offer['id'], 'new-tester', now=913)
        self.assertNotEqual(a['token'], fresh['token'])
        with self.assertRaises(ValueError):
            self.send(a, {'head': self.head}, now=914)

    def test_unenrolled_task_is_not_claimed(self):
        self.db.execute("INSERT INTO tasks(id,project,stage,phase,priority,managed) VALUES ('unenrolled','mihur','test','audit',-1,1)")
        self.assertEqual(tick(self.db, now=10), [])
        self.assertEqual(self.db.execute("SELECT attempts FROM tasks WHERE id='unenrolled'").fetchone()[0], 0)

    def test_offline_demo_runs_correction_and_no_release(self):
        root = Path(self.tmp.name) / 'demo'
        result = run(root)
        self.assertEqual(len(result['demonstration']['trace']), 2)
        self.assertEqual(result['demonstration']['trace'][0]['rejection'], 'pedagogy_reviewer')
        self.assertEqual(len(result['integration_ready']), 1)
        self.assertFalse(result['release_authorized'])
        recovered = run(root)
        self.assertEqual(recovered['integration_ready'], result['integration_ready'])

    def test_nested_submission_rolls_back_on_invalid_review_head(self):
        self._tests_pass()
        a = self.delivery('math_reviewer', 'math', now=14)
        with self.assertRaises(ValueError):
            self.send(a, {'head': 'b' * 40, 'verdict': 'accept', 'source': 'synthetic://wrong-head'}, now=15)
        state = self.db.execute('SELECT state FROM assignments WHERE id=?', (a['id'],)).fetchone()[0]
        self.assertEqual(state, 'running')

    def test_disabled_project_blocks_existing_offers_and_deliveries(self):
        from unittest.mock import patch
        offers = tick(self.db, now=10)
        with patch('director.desktop.projects', return_value={'mihur': {'enabled': False}}):
            with self.assertRaises(ValueError):
                bind(self.db, offers[0]['id'], 'author', now=11)
            self.assertEqual(tick(self.db, now=11), [])
            self.assertEqual(self.db.execute('SELECT status FROM tasks').fetchone()[0], 'pending')

    def test_revocation_rejects_a_running_delivery(self):
        from unittest.mock import patch
        a = self.delivery('backend', 'author')
        with patch('director.desktop.projects', return_value={'mihur': {'enabled': False}}):
            with self.assertRaises(ValueError):
                self.send(a, {'head': self.head}, now=11)
        self.assertEqual(self.db.execute('SELECT status FROM tasks').fetchone()[0], 'running')

    def test_bounded_implementation_expiry_blocks_after_three_attempts(self):
        for now in (10, 611, 1212):
            self.assertTrue(tick(self.db, now=now))
        self.assertEqual(tick(self.db, now=1813), [])
        self.assertEqual(self.db.execute('SELECT status FROM tasks').fetchone()[0], 'blocked')
        self.assertEqual(self.db.execute('SELECT attempts FROM tasks').fetchone()[0], 3)

    def test_delivery_reassignment_is_bounded_then_retries_implementation(self):
        self.implement()
        for index in range(3):
            now = 12 + index * 901
            offers = tick(self.db, now=now)
            offer = next(x for x in offers if x['role'] == 'test_runner')
            bind(self.db, offer['id'], f'tester-{index}', now=now)
        offers = tick(self.db, now=2715)
        self.assertEqual([a['role'] for a in offers], ['backend'])
        self.assertEqual(self.db.execute('SELECT attempts FROM tasks').fetchone()[0], 2)
