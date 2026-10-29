"""Head-bound governance on the existing queue. Trusted local service API, not authentication."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import re
import time
import uuid

from .core import MAX_ATTEMPTS, seed_mihur

REVIEW_GATES = ('engineering', 'mathematics', 'pedagogy', 'standards', 'security', 'qa')


@contextmanager
def transaction(db):
    nested = db.in_transaction
    savepoint = 'nested_' + uuid.uuid4().hex
    db.execute('SAVEPOINT ' + savepoint if nested else 'BEGIN IMMEDIATE')
    try:
        yield
        db.execute('RELEASE SAVEPOINT ' + savepoint if nested else 'COMMIT')
    except BaseException:
        if nested:
            db.execute('ROLLBACK TO SAVEPOINT ' + savepoint)
            db.execute('RELEASE SAVEPOINT ' + savepoint)
        elif db.in_transaction:
            db.execute('ROLLBACK')
        raise


def schema(db):
    with transaction(db):
        # Additive migration preserves the original tasks and development approvals.
        columns = {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}
        for name, kind in [('managed', 'INTEGER NOT NULL DEFAULT 0'), ('fence', 'TEXT'),
                           ('head_sha', 'TEXT'), ('blocker', 'TEXT')]:
            if name not in columns:
                db.execute(f'ALTER TABLE tasks ADD COLUMN {name} {kind}')
        statements = '''
            CREATE TABLE IF NOT EXISTS desktop_tasks (
                task_id TEXT PRIMARY KEY REFERENCES tasks(id), base_sha TEXT NOT NULL,
                implementer TEXT NOT NULL, required_checks TEXT NOT NULL, integrated_sha TEXT);
            CREATE TABLE IF NOT EXISTS session_roles (
                task_id TEXT REFERENCES tasks(id), subject TEXT NOT NULL, role TEXT NOT NULL,
                PRIMARY KEY(task_id,subject,role));
            CREATE TABLE IF NOT EXISTS dependencies (
                task_id TEXT REFERENCES tasks(id), requires TEXT REFERENCES tasks(id),
                PRIMARY KEY(task_id, requires), CHECK(task_id != requires));
            CREATE TABLE IF NOT EXISTS contributors (
                task_id TEXT REFERENCES tasks(id), subject TEXT NOT NULL,
                PRIMARY KEY(task_id, subject));
            CREATE TABLE IF NOT EXISTS results (
                task_id TEXT REFERENCES tasks(id), fence TEXT NOT NULL, sha TEXT NOT NULL,
                digest TEXT NOT NULL, payload TEXT NOT NULL, created_at INTEGER NOT NULL,
                PRIMARY KEY(task_id, fence));
            CREATE TABLE IF NOT EXISTS verdicts (
                event_id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id), sha TEXT NOT NULL,
                gate TEXT NOT NULL, subject TEXT NOT NULL, verdict TEXT NOT NULL,
                source TEXT NOT NULL, created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS ci_evidence (
                task_id TEXT REFERENCES tasks(id), sha TEXT NOT NULL, passed INTEGER NOT NULL,
                source TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(task_id,sha));
            CREATE TABLE IF NOT EXISTS accepted (
                task_id TEXT REFERENCES tasks(id), gate TEXT NOT NULL, sha TEXT NOT NULL,
                subject TEXT NOT NULL, PRIMARY KEY(task_id, gate));
        '''
        for statement in statements.split(';'):
            if statement.strip():
                db.execute(statement)

def initialize(db, now=None):
    current = int(time.time()) if now is None else now
    with transaction(db):
        seed_mihur(db)
        # Never silently trust approvals from the unauthenticated development CLI.
        db.execute("INSERT OR IGNORE INTO contributors SELECT id,worker FROM tasks WHERE worker IS NOT NULL AND project='mihur'")
        db.execute("UPDATE tasks SET managed=1, status='pending', worker=NULL, lease_until=NULL, "
                   "completed_at=NULL, blocker='Awaiting exact-SHA audit and independent review' "
                   "WHERE project='mihur' AND managed=0 AND (status!='running' OR lease_until<=?)", (current,))
        if db.execute("SELECT 1 FROM tasks WHERE project='mihur' AND managed=0").fetchone():
            raise ValueError('active legacy worker: wait for its lease before migration')
        rows = db.execute("SELECT id FROM tasks WHERE project='mihur' ORDER BY priority").fetchall()
        for prior, task in zip(rows, rows[1:]):
            db.execute('INSERT OR IGNORE INTO dependencies VALUES (?,?)', (task['id'], prior['id']))


def sha(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}', value):
        raise ValueError('full lowercase commit SHA required')
    return value


def finish(db, task_id, worker, fence, head, payload, now=None):
    sha(head)
    current = int(time.time()) if now is None else now
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    with transaction(db):
        prior = db.execute('SELECT * FROM results WHERE task_id=? AND fence=?', (task_id, fence)).fetchone()
        if prior:
            if prior['digest'] == digest and prior['sha'] == head:
                return digest
            raise ValueError('conflicting retry result')
        row = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not row or not row['managed'] or row['status'] != 'running' or row['worker'] != worker or row['fence'] != fence or row['lease_until'] <= current:
            raise ValueError('stale or unowned lease')
        db.execute('INSERT INTO results VALUES (?,?,?,?,?,?)',
                   (task_id, fence, head, digest, encoded, current))
        db.execute('DELETE FROM accepted WHERE task_id=?', (task_id,))
        db.execute("UPDATE tasks SET status='review', head_sha=?, lease_until=NULL, "
                   "blocker='Independent specialist review required' WHERE id=?", (head, task_id))
    return digest


def review(db, *, event_id, task_id, head, gate, subject, verdict, source,
           identities, now=None):
    """Gateway supplies verified subject/role; agents must not control identities.

    Local caller and database are trusted. This API does not turn a typed name into
    an authenticated reviewer. A production identity gateway is still required.
    """
    sha(head)
    if gate not in REVIEW_GATES or verdict not in ('accept', 'reject'):
        raise ValueError('invalid review')
    if not event_id or not source or identities.get(subject) != gate:
        raise ValueError('trusted role identity and evidence required')
    current = int(time.time()) if now is None else now
    values = (event_id, task_id, head, gate, subject, verdict, source)
    with transaction(db):
        old = db.execute('SELECT * FROM verdicts WHERE event_id=?', (event_id,)).fetchone()
        if old:
            if tuple(old)[:7] != values:
                raise ValueError('conflicting event replay')
            return
        task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not task or task['status'] != 'review' or task['head_sha'] != head:
            raise ValueError('review is not for current reviewable head')
        if db.execute('SELECT 1 FROM contributors WHERE task_id=? AND subject=?', (task_id, subject)).fetchone():
            raise ValueError('contributor cannot approve or review own task')
        db.execute('INSERT INTO verdicts VALUES (?,?,?,?,?,?,?,?)', values + (current,))
        if verdict == 'reject':
            db.execute('DELETE FROM accepted WHERE task_id=?', (task_id,))
            db.execute("UPDATE tasks SET status=?, completed_at=NULL, blocker=? WHERE id=?",
                       ('pending' if task['attempts'] < MAX_ATTEMPTS else 'blocked',
                        f'{gate} rejection: {source}', task_id))
        else:
            db.execute('INSERT INTO accepted VALUES (?,?,?,?) ON CONFLICT(task_id,gate) '
                       'DO UPDATE SET sha=excluded.sha, subject=excluded.subject',
                       (task_id, gate, head, subject))
            count = db.execute('SELECT count(*) FROM accepted WHERE task_id=? AND sha=?', (task_id, head)).fetchone()[0]
            if count == len(REVIEW_GATES):
                _advance(db, task_id, head, current)


def _advance(db, task_id, head, current):
    reviews = db.execute('SELECT count(*) FROM accepted WHERE task_id=? AND sha=?', (task_id, head)).fetchone()[0]
    ci = db.execute('SELECT passed FROM ci_evidence WHERE task_id=? AND sha=?', (task_id, head)).fetchone()
    if reviews == len(REVIEW_GATES) and ci and ci['passed']:
        desktop = db.execute('SELECT 1 FROM desktop_tasks WHERE task_id=?', (task_id,)).fetchone()
        db.execute("UPDATE tasks SET status=?, completed_at=?, blocker=? WHERE id=?",
                   ('integration' if desktop else 'completed', None if desktop else current,
                    'Independent integration check required' if desktop else None, task_id))
    elif reviews == len(REVIEW_GATES):
        db.execute("UPDATE tasks SET blocker='Exact-head CI evidence required' WHERE id=?", (task_id,))


def record_ci(db, *, task_id, head, checks, required, source, now=None):
    """Trusted collector only. Required policy/producer identity must be verified upstream."""
    sha(head)
    if not source or not required or not checks:
        raise ValueError('CI policy, checks and source required')
    passed = all(any(c.get('name') == name and c.get('head_sha') == head
                     and c.get('status') == 'completed' and c.get('conclusion') == 'success'
                     for c in checks) for name in required)
    current = int(time.time()) if now is None else now
    with transaction(db):
        task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not task or task['status'] != 'review' or task['head_sha'] != head:
            raise ValueError('CI is not for current reviewable head')
        db.execute('INSERT INTO ci_evidence VALUES (?,?,?,?,?) ON CONFLICT(task_id,sha) '
                   'DO UPDATE SET passed=excluded.passed, source=excluded.source, payload=excluded.payload',
                   (task_id, head, int(passed), source, json.dumps(checks, sort_keys=True)))
        _advance(db, task_id, head, current)
    return passed


def report(db):
    rows = []
    for r in db.execute('SELECT * FROM tasks ORDER BY project, priority, id'):
        row = dict(r)
        if row['status'] == 'running' and row['attempts'] >= MAX_ATTEMPTS and row['lease_until'] <= int(time.time()):
            row['blocker'] = 'Attempts exhausted after lease expiry'
        ci = db.execute('SELECT passed,source FROM ci_evidence WHERE task_id=? AND sha=?', (r['id'], r['head_sha'])).fetchone()
        row['ci'] = dict(ci) if ci else {'passed': False, 'source': None}
        row['missing_reviews'] = [g for g in REVIEW_GATES if not db.execute(
            'SELECT 1 FROM accepted WHERE task_id=? AND gate=? AND sha=?', (r['id'], g, r['head_sha'])).fetchone()]
        latest = db.execute('SELECT payload,digest FROM results WHERE task_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1', (r['id'],)).fetchone()
        row['evidence'] = json.loads(latest['payload']) if latest else None
        row['evidence_digest'] = latest['digest'] if latest else None
        row['dependencies'] = [d['requires'] for d in db.execute('SELECT requires FROM dependencies WHERE task_id=? ORDER BY requires', (r['id'],))]
        row['review_verdicts'] = [dict(v) for v in db.execute('SELECT event_id,sha,gate,subject,verdict,source,created_at FROM verdicts WHERE task_id=? ORDER BY created_at,rowid', (r['id'],))]
        row.pop('fence')  # lease fences are for workers, not public reports
        rows.append(row)
    return {'tasks': rows, 'merge_allowed': False, 'release_authorized': False,
            'learner_available': 'NOT_VERIFIED',
            'blockers': ['Authenticated review gateway and GitHub protection not verified',
                         'Deployment, student activation and sensitive-data changes require explicit authorization']}


def sensitive_action(action):
    raise PermissionError(f'{action} unavailable: separate scoped human authorization and release service required')
