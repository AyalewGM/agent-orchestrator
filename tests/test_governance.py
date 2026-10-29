"""Real SQLite recovery, identity separation, SHA binding and retry tests."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from director.core import approve, claim, complete, connect
from director.governance import REVIEW_GATES, finish, initialize, record_ci, report, review, sensitive_action


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'queue.sqlite')
        self.db = connect(self.path)
        initialize(self.db)
        self.identities = {f'reviewer-{g}': g for g in REVIEW_GATES}
        self.head = 'a' * 40

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def lease(self, worker='author', now=10):
        tid = claim(self.db, worker, now=now, managed_only=True, lease_seconds=10)
        return dict(self.db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone())

    def result(self, task, now=11):
        return finish(self.db, task['id'], task['worker'], task['fence'], self.head,
                      {'ci': 'NOT_VERIFIED'}, now=now)

    def verdict(self, task, gate='mathematics', verdict='accept', **kwargs):
        args = dict(event_id=f"{task['fence']}:{gate}:{verdict}", task_id=task['id'],
                    head=self.head, gate=gate, subject=f'reviewer-{gate}', verdict=verdict,
                    source='https://github.com/review/1', identities=self.identities, now=12)
        args.update(kwargs)
        review(self.db, **args)

    def test_migration_is_idempotent_and_preserves_development_approvals(self):
        initialize(self.db)
        self.assertEqual(self.db.execute('SELECT count(*) FROM tasks').fetchone()[0], 12)
        self.assertEqual(self.db.execute('SELECT count(*) FROM dependencies').fetchone()[0], 11)
        self.assertEqual(self.db.execute('SELECT count(*) FROM tasks WHERE managed=1').fetchone()[0], 12)

    def test_restart_and_same_worker_fencing(self):
        old = self.lease()
        self.db.close()
        self.db = connect(self.path)
        current = self.lease(now=20)
        self.assertNotEqual(current['fence'], old['fence'])
        with self.assertRaises(ValueError):
            self.result(old, now=21)
        self.result(current, now=21)
        self.assertEqual(report(self.db)['tasks'][0]['head_sha'], self.head)

    def test_idempotent_result_and_conflicting_retry(self):
        task = self.lease()
        digest = self.result(task)
        self.assertEqual(self.result(task, now=100), digest)
        with self.assertRaises(ValueError):
            finish(self.db, task['id'], task['worker'], task['fence'], self.head, {'other': True})

    def test_legacy_completion_and_approval_cannot_bypass_managed_reviews(self):
        task = self.lease()
        with self.assertRaises(ValueError):
            complete(self.db, task['id'], 'author', now=11)
        self.result(task)
        with self.assertRaises(ValueError):
            approve(self.db, task['id'], 'qa', 'someone')

    def test_all_independent_gates_required_for_dependency_progress(self):
        task = self.lease()
        self.result(task)
        for gate in REVIEW_GATES[:-1]:
            self.verdict(task, gate)
        self.assertIsNone(claim(self.db, 'next', now=12, managed_only=True))
        self.verdict(task, REVIEW_GATES[-1])
        self.assertIsNone(claim(self.db, 'next', now=13, managed_only=True))
        checks = [{'name': 'test', 'head_sha': self.head, 'status': 'completed', 'conclusion': 'success'}]
        record_ci(self.db, task_id=task['id'], head=self.head, checks=checks, required=['test'], source='synthetic-ci', now=13)
        self.assertEqual(claim(self.db, 'next', now=13, managed_only=True), 'mihur:ontario-9:content')
        self.assertFalse(report(self.db)['merge_allowed'])
        self.assertFalse(report(self.db)['release_authorized'])

    def test_self_approval_even_previous_attempt_author_is_rejected(self):
        first = self.lease()
        second = self.lease(worker='new-author', now=20)
        self.result(second, now=21)
        identities = {'author': 'mathematics'}
        with self.assertRaises(ValueError):
            self.verdict(second, subject='author', identities=identities)

    def test_wrong_sha_wrong_role_and_missing_source_rejected(self):
        task = self.lease()
        self.result(task)
        for values in ({'head': 'b' * 40}, {'subject': 'impostor'}, {'source': ''}):
            with self.assertRaises(ValueError):
                self.verdict(task, **values)

    def test_review_rejection_routes_back_and_exhausts_without_manual_reset(self):
        for index in range(3):
            task = self.lease(now=10 + index * 10)
            self.result(task, now=11 + index * 10)
            self.verdict(task, verdict='reject')
        self.assertEqual(self.db.execute('SELECT status FROM tasks WHERE id=?', (task['id'],)).fetchone()[0], 'blocked')
        self.assertIsNone(claim(self.db, 'author', now=50, managed_only=True))
        self.assertIn('rejection', report(self.db)['tasks'][0]['blocker'])

    def test_reject_invalidates_other_approvals_and_replay_is_idempotent(self):
        task = self.lease()
        self.result(task)
        self.verdict(task)
        self.verdict(task)
        with self.assertRaises(ValueError):
            self.verdict(task, source='changed')
        self.verdict(task, gate='qa', verdict='reject')
        self.assertEqual(self.db.execute('SELECT count(*) FROM accepted').fetchone()[0], 0)
        self.assertEqual(self.lease(now=13)['id'], task['id'])

    def test_explicit_dependency_and_global_concurrency_limit(self):
        self.db.execute("INSERT INTO tasks (id,project,stage,phase,priority,managed) VALUES ('other','other','one','audit',0,1)")
        self.db.execute("INSERT INTO dependencies VALUES ('other','mihur:ontario-9:audit')")
        self.lease()
        self.assertIsNone(claim(self.db, 'parallel', now=11, managed_only=True))
        self.db.execute("DELETE FROM dependencies WHERE task_id='other'")
        self.assertIsNone(claim(self.db, 'parallel', now=11, managed_only=True, max_running=1))
        self.assertEqual(claim(self.db, 'parallel', now=11, managed_only=True), 'other')

    def test_production_operations_always_denied(self):
        for action in ('merge', 'deploy', 'student_activation', 'sensitive_data'):
            with self.assertRaises(PermissionError):
                sensitive_action(action)

    def test_existing_database_additive_migration(self):
        legacy = sqlite3.connect(':memory:', isolation_level=None)
        legacy.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY, project TEXT, stage TEXT, phase TEXT, priority INTEGER, status TEXT, attempts INTEGER, worker TEXT, lease_until INTEGER, completed_at INTEGER)')
        legacy.execute("INSERT INTO tasks VALUES ('old','mihur','one','audit',0,'completed',1,'dev',NULL,1)")
        legacy.row_factory = sqlite3.Row
        from director.governance import schema
        schema(legacy)
        self.assertEqual(legacy.execute('SELECT managed FROM tasks').fetchone()[0], 0)
        legacy.close()


    def test_failed_or_wrong_head_ci_blocks_after_all_reviews(self):
        task = self.lease()
        self.result(task)
        for gate in REVIEW_GATES:
            self.verdict(task, gate)
        for checks in ([{'name': 'test', 'head_sha': self.head, 'status': 'completed', 'conclusion': 'failure'}],
                       [{'name': 'test', 'head_sha': 'b' * 40, 'status': 'completed', 'conclusion': 'success'}]):
            self.assertFalse(record_ci(self.db, task_id=task['id'], head=self.head, checks=checks,
                                       required=['test'], source='synthetic-ci'))
            self.assertIsNone(claim(self.db, 'next', now=13, managed_only=True))

    def test_expired_legacy_lease_migrates_and_preserves_author(self):
        self.db.execute("UPDATE tasks SET managed=0,status='running',worker='previous-author',lease_until=5 WHERE id='mihur:ontario-9:audit'")
        initialize(self.db, now=6)
        row = self.db.execute("SELECT * FROM tasks WHERE id='mihur:ontario-9:audit'").fetchone()
        self.assertEqual(row['managed'], 1)
        self.assertEqual(row['status'], 'pending')
        self.assertTrue(self.db.execute("SELECT 1 FROM contributors WHERE subject='previous-author'").fetchone())

    def test_simultaneous_schema_startup_is_serialized(self):
        from concurrent.futures import ThreadPoolExecutor
        path = str(Path(self.tmp.name) / 'simultaneous.sqlite')
        def startup(_):
            connection = connect(path)
            connection.close()
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(startup, range(12)))
