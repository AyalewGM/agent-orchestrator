"""Worktree and audit integration with real Git, synthetic source, no network/model."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from director.core import connect
from director.governance import initialize, report
from director.native import audit_once, git, worktree


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'product'
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        git(self.source, 'config', 'user.email', 'synthetic@example.test')
        git(self.source, 'config', 'user.name', 'Synthetic')
        git(self.source, 'remote', 'add', 'origin', 'https://github.com/AyalewGM/ai-tutor.git')
        (self.source / 'AGENTS.md').write_text('Synthetic source; never executed')
        git(self.source, 'add', 'AGENTS.md')
        git(self.source, 'commit', '-qm', 'fixture')
        self.head = git(self.source, 'rev-parse', 'HEAD')
        self.db = connect(str(self.root / 'ledger.sqlite'))
        initialize(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def snapshot(self):
        return {'repository': 'AyalewGM/ai-tutor', 'main_sha': self.head,
                'pr_dependencies': [], 'release_authorized': False}

    def audit(self, fetch=None):
        return audit_once(self.db, worker='audit-agent', source=self.source,
                          worktrees=self.root / 'worktrees', fetch_snapshot=fetch or self.snapshot, now=10)

    def test_real_worktree_audit_persists_snapshot_and_restart_report(self):
        result = self.audit()
        self.assertEqual(result['status'], 'review')
        self.assertEqual(git(result['worktree'], 'rev-parse', 'HEAD'), self.head)
        self.assertEqual(git(self.source, 'status', '--porcelain'), '')
        self.db.close()
        self.db = connect(str(self.root / 'ledger.sqlite'))
        data = report(self.db)
        payload = data['tasks'][0]['evidence']
        self.assertEqual(payload['source_inventory'], ['AGENTS.md'])
        self.assertEqual(payload['delegation']['base_sha'], self.head)
        self.assertEqual(data['learner_available'], 'NOT_VERIFIED')
        self.assertIsNone(self.audit())

    def test_attempt_worktrees_are_distinct_and_dirty_reuse_rejected(self):
        one = worktree(self.source, self.root / 'wt', self.head, 'task', 'fence1')
        two = worktree(self.source, self.root / 'wt', self.head, 'task', 'fence2')
        self.assertNotEqual(one, two)
        self.assertEqual(one, worktree(self.source, self.root / 'wt', self.head, 'task', 'fence1'))
        (one / 'dirty').write_text('change')
        with self.assertRaises(ValueError):
            worktree(self.source, self.root / 'wt', self.head, 'task', 'fence1')
        self.assertEqual(git(two, 'status', '--porcelain'), '')

    def test_failure_requeues_with_bounded_attempts(self):
        def unavailable():
            raise RuntimeError('synthetic secret should not enter reports')
        for _ in range(3):
            with self.assertRaises(RuntimeError):
                self.audit(unavailable)
        row = report(self.db)['tasks'][0]
        self.assertEqual(row['status'], 'blocked')
        self.assertNotIn('secret', row['blocker'])

    def test_wrong_repository_and_missing_commit_fail_closed(self):
        with self.assertRaises(ValueError):
            self.audit(lambda: dict(self.snapshot(), repository='other/repo'))
        with self.assertRaises(subprocess.CalledProcessError):
            self.audit(lambda: dict(self.snapshot(), main_sha='f' * 40))

    def test_profiles_parse_and_disabled_projects_are_explicit(self):
        import tomllib
        from director.policy import projects
        root = Path(__file__).resolve().parents[1]
        profiles = list((root / '.codex/agents').glob('*.toml'))
        self.assertGreaterEqual(len(profiles), 8)
        for path in profiles:
            profile = tomllib.loads(path.read_text())
            self.assertTrue(all(profile.get(k) for k in ('name', 'description', 'developer_instructions')))
        self.assertFalse(projects()['orbytics']['enabled'])
        self.assertFalse(projects()['gasha']['enabled'])


    def test_audit_never_executes_checkout_hooks_or_filters(self):
        marker = self.root / 'hook-ran'
        hook = self.source / '.git/hooks/post-checkout'
        hook.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\n')
        hook.chmod(0o755)
        filter_marker = self.root / 'filter-ran'
        git(self.source, 'config', 'filter.evil.smudge', 'touch "' + str(filter_marker) + '"')
        (self.source / '.gitattributes').write_text('AGENTS.md filter=evil\n')
        git(self.source, 'add', '.gitattributes')
        git(self.source, 'commit', '-qm', 'synthetic filter fixture')
        self.head = git(self.source, 'rev-parse', 'HEAD')
        self.audit()
        self.assertFalse(marker.exists())
        self.assertFalse(filter_marker.exists())
