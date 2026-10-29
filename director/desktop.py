"""Durable Desktop agent handoffs on the existing task queue.

The active Codex Director owns spawning/binding sessions and verifying evidence.
This local trusted bridge is not an identity provider or an unattended model service.
"""
from __future__ import annotations

import argparse
import json
import time
import uuid

from .core import claim, connect, MAX_ATTEMPTS
from .governance import REVIEW_GATES, finish, record_ci, report, review, sha, transaction
from .policy import projects

REVIEWERS = {'engineering': 'engineering_reviewer', 'mathematics': 'math_reviewer',
             'pedagogy': 'pedagogy_reviewer', 'standards': 'standards_reviewer',
             'security': 'security_reviewer', 'qa': 'qa_reviewer'}
IMPLEMENTERS = {'backend', 'frontend', 'content', 'implementation', 'devops'}
ASSIGNMENT_TTL = 900
MAX_DELIVERIES = 3


def schema(db):
    with transaction(db):
        db.execute('''CREATE TABLE IF NOT EXISTS assignments (
            id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id), fence TEXT NOT NULL,
            role TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', subject TEXT,
            token TEXT, lease_until INTEGER, deliveries INTEGER NOT NULL DEFAULT 0,
            response TEXT, UNIQUE(task_id,fence,role))''')


def enroll(db, task_id, base, implementer='backend', *, required_checks=None):
    """Explicit operator opt-in. Does not create or approve a task."""
    sha(base)
    if implementer not in IMPLEMENTERS:
        raise ValueError('unknown implementation lane')
    with transaction(db):
        task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not task or not task['managed'] or task['status'] not in ('pending', 'review'):
            raise ValueError('enroll a pending/review managed task')
        policy = projects().get(task['project'], {})
        if not policy.get('enabled'):
            raise ValueError('project disabled or unknown')
        required = required_checks if required_checks is not None else policy['required_checks']
        if not required or not all(isinstance(n, str) and n.strip() for n in required):
            raise ValueError('nonempty operator-owned CI policy required')
        values = (task_id, base, implementer, json.dumps(required))
        old = db.execute('SELECT * FROM desktop_tasks WHERE task_id=?', (task_id,)).fetchone()
        if old and tuple(old)[:4] != values:
            raise ValueError('conflicting enrollment')
        db.execute('INSERT OR IGNORE INTO desktop_tasks VALUES (?,?,?,?,NULL)', values)


def _offer(db, task, role):
    db.execute('INSERT OR IGNORE INTO assignments(id,task_id,fence,role) VALUES (?,?,?,?)',
               (uuid.uuid4().hex, task['id'], task['fence'], role))
    if role == 'integration' and task['status']=='integration':
        # A fresh canonical gate reconciliation may restore this same-head state.
        # Never rearm while an old execution/result is still in flight, nor reset its budget.
        has_runs=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_runs'").fetchone()
        pending_run=has_runs and db.execute("SELECT 1 FROM agent_runs r JOIN assignments a ON a.id=r.assignment_id WHERE a.task_id=? AND a.fence=? AND a.role=? AND r.state IN ('starting','running','returned','failed','uncertain')",(task['id'],task['fence'],role)).fetchone()
        if not pending_run:
            db.execute("UPDATE assignments SET state='queued',subject=NULL,token=NULL,lease_until=NULL WHERE task_id=? AND fence=? AND role=? AND state='cancelled' AND deliveries<?",(task['id'],task['fence'],role,MAX_DELIVERIES))
            exhausted=db.execute("SELECT 1 FROM assignments WHERE task_id=? AND fence=? AND role=? AND state='cancelled' AND deliveries>=?",(task['id'],task['fence'],role,MAX_DELIVERIES)).fetchone()
            if exhausted:
                db.execute("UPDATE tasks SET status='blocked',blocker='Integration delivery budget exhausted after gate restoration' WHERE id=?",(task['id'],))




def _cancel(db, task_id, fence):
    db.execute("UPDATE assignments SET state='cancelled',token=NULL WHERE task_id=? AND fence=? AND state!='done'",
               (task_id, fence))


def _retry(db, task, reason):
    db.execute('DELETE FROM accepted WHERE task_id=?', (task['id'],))
    _cancel(db, task['id'], task['fence'])
    db.execute("UPDATE tasks SET status=?,lease_until=NULL,completed_at=NULL,blocker=? WHERE id=?",
               ('pending' if task['attempts'] < MAX_ATTEMPTS else 'blocked', reason, task['id']))


def tick(db, now=None, *, claim_new=True):
    """Idempotently derive handoffs. No model execution or GitHub mutation."""
    current = int(time.time()) if now is None else now
    with transaction(db):
        rows = db.execute('SELECT t.* FROM tasks t JOIN desktop_tasks d ON d.task_id=t.id').fetchall()
        for task in rows:
            if not projects().get(task['project'], {}).get('enabled'):
                if task['status'] in ('running', 'review', 'integration'):
                    _retry(db, task, 'Project dispatch disabled by operator')
                continue
            # Revoked or exhausted leases must not leave usable old assignments.
            db.execute("UPDATE assignments SET state='cancelled',token=NULL WHERE task_id=? AND fence!=? AND state NOT IN ('done','cancelled')",
                       (task['id'], task['fence']))
            if task['status'] in ('pending', 'blocked'):
                _cancel(db, task['id'], task['fence'])
            if task['status'] == 'running' and task['lease_until'] <= current:
                _retry(db, task, 'Implementation lease expired')
            exhausted = db.execute("SELECT 1 FROM assignments WHERE task_id=? AND fence=? AND state IN ('running','reserved') AND lease_until<=? AND deliveries>=?",
                                   (task['id'], task['fence'], current, MAX_DELIVERIES)).fetchone()
            if exhausted and task['status'] not in ('completed', 'blocked', 'pending'):
                _retry(db, task, 'Agent delivery budget exhausted')
            db.execute("UPDATE assignments SET state='queued',subject=NULL,token=NULL,lease_until=NULL "
                       "WHERE task_id=? AND state IN ('running','reserved') AND lease_until<=? AND deliveries<?",
                       (task['id'], current, MAX_DELIVERIES))
    # Existing core.claim is the sole scheduler. Limit selection to opted-in work.
    for project, policy in projects().items():
        if not claim_new or not policy.get('enabled'):
            continue
        claim(db, f'desktop:{project}', now=current, lease_seconds=policy['lease_seconds'],
                    managed_only=True, max_running=policy['max_running'], project=project,
                    desktop_only=True)
        # A crash after claiming is recoverable: next tick reconstructs the offer.
    with transaction(db):
        for task in db.execute('SELECT t.*,d.implementer,d.integrated_sha FROM tasks t JOIN desktop_tasks d ON d.task_id=t.id').fetchall():
            if not projects().get(task['project'], {}).get('enabled'):
                continue
            if task['status'] == 'running':
                _offer(db, task, task['implementer'])
            elif task['status'] == 'review':
                tests = db.execute("SELECT state FROM assignments WHERE task_id=? AND fence=? AND role='test_runner'",
                                   (task['id'], task['fence'])).fetchone()
                if not tests:
                    _offer(db, task, 'test_runner')
                elif tests['state'] == 'done':
                    for role in REVIEWERS.values():
                        _offer(db, task, role)
            elif task['status'] == 'integration':
                _offer(db, task, 'integration')
    return pending(db)


def pending(db):
    return [dict(r) for r in db.execute("SELECT a.id,a.task_id,a.role,a.state,a.deliveries,t.head_sha,d.base_sha,t.blocker "
            "FROM assignments a JOIN tasks t ON t.id=a.task_id JOIN desktop_tasks d ON d.task_id=a.task_id "
            "WHERE a.state='queued' ORDER BY t.priority,a.role")]


def bind(db, assignment_id, subject, now=None, *, reserved_delivery=None):
    """Trusted Director binds a real tool-returned session, not a model's role label."""
    if not subject.strip():
        raise ValueError('session identity required')
    current = int(time.time()) if now is None else now
    with transaction(db):
        a = db.execute('SELECT * FROM assignments WHERE id=?', (assignment_id,)).fetchone()
        if reserved_delivery is None:
            available=a and a['state']=='queued' and a['deliveries']<MAX_DELIVERIES
        else:
            available=a and a['state']=='reserved' and a['deliveries']==reserved_delivery and reserved_delivery<=MAX_DELIVERIES and a['lease_until']>current
        if not available:
            raise ValueError('assignment unavailable')
        task = db.execute('SELECT * FROM tasks WHERE id=?', (a['task_id'],)).fetchone()
        if task['fence'] != a['fence'] or task['status'] not in ('running', 'review', 'integration'):
            raise ValueError('stale assignment')
        if not projects().get(task['project'], {}).get('enabled'):
            raise ValueError('project dispatch disabled')
        # One session cannot switch from author/tester/reviewer role to another.
        prior = db.execute('SELECT 1 FROM session_roles WHERE task_id=? AND subject=? AND role!=?',
                           (a['task_id'], subject, a['role'])).fetchone()
        author = db.execute('SELECT 1 FROM contributors WHERE task_id=? AND subject=?', (a['task_id'], subject)).fetchone()
        if prior or (author and a['role'] not in IMPLEMENTERS):
            raise ValueError('independent session boundary violated')
        limit = projects()[task['project']]['max_running']
        active = db.execute("SELECT count(*) FROM assignments WHERE state IN ('running','reserved') AND lease_until>? AND id!=?", (current,assignment_id)).fetchone()[0]
        if active >= limit:
            raise ValueError('active Desktop agent limit reached')
        token = uuid.uuid4().hex
        until = current + ASSIGNMENT_TTL
        if a['role'] in IMPLEMENTERS:
            until = min(until, task['lease_until'])
        if until <= current:
            raise ValueError('task lease expired')
        db.execute('INSERT OR IGNORE INTO session_roles VALUES (?,?,?)', (a['task_id'], subject, a['role']))
        if a['role'] in IMPLEMENTERS:
            db.execute('INSERT OR IGNORE INTO contributors VALUES (?,?)', (a['task_id'], subject))
        delivery=a['deliveries']+1 if reserved_delivery is None else reserved_delivery
        db.execute("UPDATE assignments SET state='running',subject=?,token=?,lease_until=?,deliveries=? WHERE id=?",
                   (subject, token, until, delivery,assignment_id))
        return dict(db.execute('SELECT * FROM assignments WHERE id=?', (assignment_id,)).fetchone())


def submit(db, assignment_id, subject, token, response, now=None):
    """Trusted Director verifies artifact/CI provenance before submitting an agent result."""
    current = int(time.time()) if now is None else now
    encoded = json.dumps(response, sort_keys=True, separators=(',', ':'), allow_nan=False)
    with transaction(db):
        a = db.execute('SELECT * FROM assignments WHERE id=?', (assignment_id,)).fetchone()
        if not a or a['subject'] != subject or a['token'] != token:
            raise ValueError('unowned delivery')
        if a['state'] == 'done':
            if a['response'] != encoded:
                raise ValueError('conflicting result replay')
            return
        task = db.execute('SELECT * FROM tasks WHERE id=?', (a['task_id'],)).fetchone()
        if not projects().get(task['project'], {}).get('enabled'):
            raise ValueError('project dispatch disabled')
        if a['state'] != 'running' or a['lease_until'] <= current or a['fence'] != task['fence']:
            raise ValueError('expired or stale delivery')
        head = sha(response.get('head'))
        # Mark consumed inside the transaction before a rejection cancels siblings.
        db.execute("UPDATE assignments SET state='done',response=? WHERE id=?", (encoded, assignment_id))
        if a['role'] in IMPLEMENTERS:
            finish(db, task['id'], task['worker'], a['fence'], head,
                   {'desktop_session': subject, 'evidence': response}, now=current)
        elif a['role'] == 'test_runner':
            required = json.loads(db.execute('SELECT required_checks FROM desktop_tasks WHERE task_id=?', (task['id'],)).fetchone()[0])
            passed = record_ci(db, task_id=task['id'], head=head, checks=response.get('checks'),
                               required=required, source=response.get('source'), now=current)
            if not passed:
                _retry(db, task, 'Required exact-head tests failed')
        elif a['role'] in REVIEWERS.values():
            tested = db.execute("SELECT 1 FROM assignments WHERE task_id=? AND fence=? AND role='test_runner' AND state='done'", (task['id'], a['fence'])).fetchone()
            if not tested:
                raise ValueError('tests required before reviews')
            gate = next(g for g,r in REVIEWERS.items() if r == a['role'])
            review(db, event_id=a['id'] + ':' + token, task_id=task['id'], head=head,
                   gate=gate, subject=subject, verdict=response.get('verdict'),
                   source=response.get('source'), identities={subject: gate}, now=current)
            if response.get('verdict') == 'reject':
                _cancel(db, task['id'], task['fence'])
        elif a['role'] == 'integration':
            if task['status'] != 'integration' or task['head_sha'] != head:
                raise ValueError('integration is not current-head ready')
            if response.get('verdict') != 'accept' or not response.get('source'):
                _retry(db, task, 'Integration rejected; correction required')
            else:
                db.execute('UPDATE desktop_tasks SET integrated_sha=? WHERE task_id=?', (head, task['id']))
                db.execute("UPDATE tasks SET status='completed',completed_at=?,blocker=NULL WHERE id=?", (current, task['id']))
        else:
            raise ValueError('unknown assignment role')
        db.execute("UPDATE assignments SET state='done',response=? WHERE id=?", (encoded, assignment_id))


def status(db):
    value = report(db)
    value['assignments'] = [dict(r) for r in db.execute('SELECT id,task_id,role,state,subject,deliveries FROM assignments ORDER BY task_id,rowid')]
    value['integration_ready'] = [dict(r) for r in db.execute('SELECT task_id,integrated_sha FROM desktop_tasks WHERE integrated_sha IS NOT NULL')]
    value['execution_environment'] = 'Active Codex Desktop Director; no standalone model runner'
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', default='director.sqlite')
    commands = p.add_subparsers(dest='command', required=True)
    commands.add_parser('tick')
    commands.add_parser('status')
    en = commands.add_parser('enroll')
    en.add_argument('--task', required=True)
    en.add_argument('--base', required=True)
    en.add_argument('--agent', default='backend', choices=sorted(IMPLEMENTERS))
    binding = commands.add_parser('bind')
    binding.add_argument('--assignment', required=True)
    binding.add_argument('--session', required=True)
    result = commands.add_parser('submit')
    result.add_argument('--assignment', required=True)
    result.add_argument('--session', required=True)
    result.add_argument('--token', required=True)
    result.add_argument('--result', required=True, help='Verified JSON result file')
    args = p.parse_args()
    with connect(args.db) as db:
        schema(db)
        if args.command == 'tick':
            value = tick(db)
        elif args.command == 'enroll':
            enroll(db, args.task, args.base, args.agent)
            value = status(db)
        elif args.command == 'bind':
            value = bind(db, args.assignment, args.session)
        elif args.command == 'submit':
            from pathlib import Path
            submit(db, args.assignment, args.session, args.token, json.loads(Path(args.result).read_text()))
            value = tick(db)
        else:
            value = status(db)
        print(json.dumps(value, indent=2))


if __name__ == '__main__':
    main()
