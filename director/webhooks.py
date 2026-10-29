"""Signed GitHub notifications trigger fresh, exact-head authoritative reconciliation.

Only authenticated GitHub APPROVED states from configured independent numeric user
IDs can fill gates. Agent text and webhook payload claims never grant approval.
Run controller credentials outside engineering workers and protect the ledger at OS level.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time

from .desktop import _cancel, _retry
from .governance import REVIEW_GATES, _advance, sha, transaction
from .mihur_release_director import fetch_json


def decode(secret: bytes, signature: str, body: bytes):
    if len(secret) < 32 or len(body) > 1024 * 1024:
        raise ValueError('webhook secret/body limits not satisfied')
    expected = 'sha256=' + hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise PermissionError('invalid webhook signature')
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError('webhook object required')
    return value


def validate_policy(policy):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', policy['repository']):
        raise ValueError('invalid repository')
    ids = []
    for gate, reviewers in policy.get('reviewers', {}).items():
        if gate not in REVIEW_GATES or any(type(i) is not int or i <= 0 for i in reviewers):
            raise ValueError('reviewers must be operator-configured GitHub numeric user IDs')
        ids.extend(reviewers)
    if len(ids) != len(set(ids)):
        raise ValueError('each independent reviewer may own only one gate')
    if not policy.get('checks') or any(type(v) is not int or v <= 0 for v in policy['checks'].values()):
        raise ValueError('trusted GitHub check App IDs required')


def invalidate_dependents(db, task_id, reason):
    dependents = db.execute("WITH RECURSIVE children(id) AS (SELECT task_id FROM dependencies WHERE requires=? "
                            "UNION SELECT d.task_id FROM dependencies d JOIN children c ON d.requires=c.id) SELECT id FROM children", (task_id,)).fetchall()
    for child in dependents:
        task = db.execute('SELECT * FROM tasks WHERE id=?', (child['id'],)).fetchone()
        desktop_reason = 'Dependency gate revoked: ' + reason
        _cancel(db, task['id'], task['fence'])
        db.execute('DELETE FROM accepted WHERE task_id=?', (task['id'],))
        db.execute("UPDATE tasks SET status='blocked',completed_at=NULL,blocker=? WHERE id=?", (desktop_reason, task['id']))
        db.execute('UPDATE desktop_tasks SET integrated_sha=NULL WHERE task_id=?', (task['id'],))


class GitHubReconciler:
    def __init__(self, fetch=fetch_json, token=''):
        self.fetch = fetch
        self.token = token
        self.deadline = None

    def get(self,path):
        remaining = 5 if self.deadline is None else min(5,self.deadline-time.monotonic())
        if remaining <= 0:
            raise TimeoutError('reconciliation deadline exhausted')
        if self.fetch is fetch_json:
            return self.fetch(path,self.token,timeout=remaining)
        return self.fetch(path,self.token)

    def snapshot(self, policy):
        validate_policy(policy)
        base = '/repos/' + policy['repository']
        number = int(policy['pr'])
        pr = self.get(f'{base}/pulls/{number}')
        head = sha(pr['head']['sha'])
        checks = self.get(f'{base}/commits/{head}/check-runs?per_page=100')
        reviews, commits = [], []
        for resource, output in [('reviews', reviews), ('commits', commits)]:
            for page in range(1, 4):
                batch = self.get(f'{base}/pulls/{number}/{resource}?per_page=100&page={page}')
                if not isinstance(batch, list):
                    raise ValueError('invalid GitHub inventory')
                output.extend(batch)
                if len(batch) < 100:
                    break
            else:
                raise ValueError('bounded GitHub inventory exceeded')
        if self.get(f'{base}/pulls/{number}')['head']['sha'] != head:
            raise ValueError('PR moved during reconciliation')
        return {'head': head, 'pr': pr, 'checks': checks, 'reviews': reviews, 'commits': commits}

    def apply(self, db, task_id, policy, data, now):
        """Persist a fresh REST snapshot, never the untrusted webhook claims."""
        head = sha(data['head'])
        task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not task:
            return 'NOT_ENROLLED'
        author_ids = set(policy.get('contributors', [])) | {data['pr']['user']['id']}
        unattributed = not bool(data['commits'])
        for c in data['commits']:
            if not c.get('author') or not c.get('committer'):
                unattributed = True
            author_ids.update(x['id'] for x in (c.get('author'), c.get('committer')) if x)
        with transaction(db):
            for subject in author_ids:
                db.execute('INSERT OR IGNORE INTO contributors VALUES (?,?)',(task_id,'github:'+str(subject)))
        author_ids.update(int(r['subject'].split(':',1)[1]) for r in db.execute(
            "SELECT subject FROM contributors WHERE task_id=? AND subject GLOB 'github:[0-9]*'", (task_id,)))
        if task['head_sha'] != head:
            if task['status'] in ('review','integration','completed'):
                with transaction(db):
                    _cancel(db, task_id, task['fence'])
                    db.execute('DELETE FROM accepted WHERE task_id=?', (task_id,))
                    db.execute('DELETE FROM ci_evidence WHERE task_id=?', (task_id,))
                    db.execute("UPDATE tasks SET status='blocked',completed_at=NULL,blocker='Remote PR head changed; reconcile worktree and scope' WHERE id=?", (task_id,))
                    db.execute('UPDATE desktop_tasks SET integrated_sha=NULL WHERE task_id=?', (task_id,))
                    invalidate_dependents(db, task_id, 'remote head changed')
            return 'HEAD_CHANGED'
        if task['status'] not in ('review', 'integration', 'completed'):
            return 'NOT_REVIEWABLE'
        latest_checks = {}
        for c in data['checks'].get('check_runs', []):
            key = (c.get('name'), c.get('app', {}).get('id'))
            if c.get('head_sha') == head and c.get('id', 0) > latest_checks.get(key, {}).get('id', -1):
                latest_checks[key] = c
        selected = [latest_checks.get((name, app), {}) for name, app in policy['checks'].items()]
        checks_passed = data['checks'].get('total_count', 101) <= 100 and all(
            c.get('status') == 'completed' and c.get('conclusion') == 'success' for c in selected)
        failed = any(c.get('status') == 'completed' and c.get('conclusion') in ('failure', 'cancelled', 'timed_out', 'action_required') for c in selected)
        latest_reviews = {}
        for r in data['reviews']:
            subject = r.get('user', {}).get('id')
            # Latest state for a reviewer governs; a newer dismissal/other SHA clears old acceptance.
            if subject is not None and r.get('id', 0) > latest_reviews.get(subject, {}).get('id', -1):
                latest_reviews[subject] = r
        accepted, rejected = {}, False
        for gate, reviewers in policy.get('reviewers', {}).items():
            for subject in reviewers:
                r = latest_reviews.get(subject, {})
                if subject in author_ids or r.get('commit_id') != head:
                    continue
                if r.get('state') == 'CHANGES_REQUESTED':
                    rejected = True
                if r.get('state') == 'APPROVED' and not unattributed:
                    accepted[gate] = (str(subject), r.get('html_url', ''))
        source = f"https://github.com/{policy['repository']}/pull/{policy['pr']}"
        with transaction(db):
            # Never allow pre-read state to overwrite a worker's newer commit.
            current = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if current['head_sha'] != head or current['fence'] != task['fence']:
                return 'STALE_RECONCILIATION'
            db.execute('DELETE FROM accepted WHERE task_id=?', (task_id,))
            for subject, r in latest_reviews.items():
                if r.get('commit_id') != head:
                    continue
                gate = next((g for g,ids in policy.get('reviewers',{}).items() if subject in ids),None)
                if not gate or subject in author_ids:
                    continue
                state = r.get('state','')
                event = 'github:' + str(r['id']) + ':' + state + ':' + head
                db.execute('INSERT OR IGNORE INTO verdicts VALUES(?,?,?,?,?,?,?,?)',
                           (event,task_id,head,gate,'github:'+str(subject),state,r.get('html_url',''),now))
            db.execute('INSERT INTO ci_evidence VALUES (?,?,?,?,?) ON CONFLICT(task_id,sha) '
                       'DO UPDATE SET passed=excluded.passed,source=excluded.source,payload=excluded.payload',
                       (task_id, head, int(checks_passed), source, json.dumps(selected)))
            for gate, (subject, review_source) in accepted.items():
                db.execute('INSERT INTO accepted VALUES (?,?,?,?)', (task_id, gate, head, 'github:' + subject))
            if failed or rejected:
                invalidate_dependents(db, task_id, 'authenticated gate rejected')
                _retry(db, current, 'Authenticated GitHub review or trusted CI rejected current head')
                db.execute('UPDATE desktop_tasks SET integrated_sha=NULL WHERE task_id=?', (task_id,))
                return 'REJECTED'
            if not checks_passed or len(accepted) != len(REVIEW_GATES):
                if current['status'] in ('integration', 'completed'):
                    invalidate_dependents(db, task_id, 'authenticated gate withdrawn')
                    # Completed Integration is invalidated too: restored gates require
                    # a fresh bounded delivery, never reuse its stale acceptance.
                    db.execute("UPDATE assignments SET state='cancelled',token=NULL WHERE task_id=? AND fence=? AND role='integration' AND state='done'",(task_id,current['fence']))
                    _cancel(db, task_id, current['fence'])
                    db.execute("UPDATE tasks SET status='review',completed_at=NULL,blocker='Fresh authenticated gates incomplete' WHERE id=?", (task_id,))
                    db.execute('UPDATE desktop_tasks SET integrated_sha=NULL WHERE task_id=?', (task_id,))
                return 'WAITING_FOR_AUTHENTICATED_GATES'
            if current['status'] == 'review':
                is_event = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_plans'").fetchone()
                if is_event and db.execute('SELECT 1 FROM event_plans WHERE task_id=?', (task_id,)).fetchone():
                    required_roles = ['test_runner', 'engineering_reviewer', 'math_reviewer', 'pedagogy_reviewer', 'standards_reviewer', 'security_reviewer', 'qa_reviewer']
                    done = {r['role'] for r in db.execute("SELECT role FROM assignments WHERE task_id=? AND fence=? AND state='done'", (task_id,current['fence']))}
                    if not set(required_roles).issubset(done):
                        return 'WAITING_FOR_LOCAL_EVIDENCE'
                _advance(db, task_id, head, now)
            return 'AUTHENTICATED_GATES_PASSED'
