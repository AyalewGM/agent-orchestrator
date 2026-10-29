"""Notification-driven execution on director.core tasks and desktop assignments.

One foreground supervisor owns SQLite. Async wakeups come from app-server completion,
webhooks and operator commands; bounded timers reconcile missed notifications.
No schedule, infinite polling, auto-merge, deployment or activation capability.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
import tomllib
import uuid

from .app_server import AppServerAdapter
from .core import connect
from . import desktop
from .evidence import observe
from .governance import sha, transaction
from .native import git, worktree
from .webhooks import GitHubReconciler, decode, validate_policy


ROOT = Path(__file__).resolve().parents[1]
PHASES = {'pending': 'READY', 'running': 'IMPLEMENTING', 'integration': 'INTEGRATING',
          'completed': 'RELEASE_READY', 'blocked': 'BLOCKED'}


def native_adapter(role):
    profile = tomllib.loads((ROOT / '.codex/agents' / (role + '.toml')).read_text())
    features = {'apps':False, 'plugins':False, 'request_permissions_tool':False}
    features.update(profile.get('features', {}))
    command = ['codex','app-server','--listen','stdio://','-c','agents.enabled=false']
    for key,value in features.items():
        if value is not False or key not in {'apps','plugins','request_permissions_tool','shell_tool','memories'}:
            raise ValueError('worker features may only reduce capabilities')
        command.extend(['-c',f'features.{key}=false'])
    return AppServerAdapter(sandbox='workspace-write' if role in desktop.IMPLEMENTERS or role == 'test_runner' else 'read-only',
                            timeout_seconds=240,command=tuple(command))


def schema(db):
    desktop.schema(db)
    with transaction(db):
        for sql in [
            "CREATE TABLE IF NOT EXISTS state_history(id INTEGER PRIMARY KEY,task_id TEXT REFERENCES tasks(id),state TEXT NOT NULL,sha TEXT,recorded_at INTEGER NOT NULL)",
            "CREATE TABLE IF NOT EXISTS event_plans(task_id TEXT PRIMARY KEY REFERENCES tasks(id), policy TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS dispatcher_events(id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, payload TEXT NOT NULL, processed INTEGER NOT NULL DEFAULT 0)",
            "CREATE TABLE IF NOT EXISTS agent_runs(assignment_id TEXT PRIMARY KEY REFERENCES assignments(id), state TEXT NOT NULL, thread_id TEXT, turn_id TEXT, cwd TEXT, result TEXT)",
            "CREATE TABLE IF NOT EXISTS dispatcher_control(id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL, turns INTEGER NOT NULL DEFAULT 0)",
        ]:
            db.execute(sql)
        columns={r['name'] for r in db.execute('PRAGMA table_info(dispatcher_control)')}
        if 'stop_reason' not in columns:
            db.execute('ALTER TABLE dispatcher_control ADD COLUMN stop_reason TEXT')
        db.execute("INSERT OR IGNORE INTO dispatcher_control(id,mode,turns) VALUES(1,'RUNNING',0)")


def register(db, task_id, policy):
    """Operator-owned source/worktree/check/reviewer policy. No credential fields."""
    validate_policy(policy)
    source = Path(policy['source']).resolve()
    if not source.is_dir() or not policy.get('objective'):
        raise ValueError('existing dedicated source and bounded objective required')
    task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    if not task or not task['managed']:
        raise ValueError('existing managed task required')
    encoded = json.dumps(policy, sort_keys=True)
    with transaction(db):
        prior = db.execute('SELECT policy FROM event_plans WHERE task_id=?', (task_id,)).fetchone()
        if prior and prior['policy'] != encoded:
            raise ValueError('plan changes require explicit operator reconciliation')
        enrolled = db.execute('SELECT required_checks FROM desktop_tasks WHERE task_id=?',(task_id,)).fetchone()
        if not enrolled:
            desktop.enroll(db,task_id,policy['base_sha'],policy.get('implementer','backend'),required_checks=list(policy['checks']))
        elif set(json.loads(enrolled['required_checks'])) != set(policy['checks']):
            raise ValueError('event check policy must preserve enrolled requirements')
        db.execute('INSERT OR IGNORE INTO event_plans VALUES(?,?)', (task_id, encoded))


def phase(db, task):
    if task['status'] == 'review':
        row = db.execute("SELECT state FROM assignments WHERE task_id=? AND fence=? AND role='test_runner'", (task['id'], task['fence'])).fetchone()
        return 'REVIEWING' if row and row['state'] == 'done' else 'TESTING'
    return PHASES.get(task['status'], task['status'].upper())


class Dispatcher:
    def __init__(self, db, *, adapter_factory=None, reconciler=None, max_active=3,
                 budget_seconds=900, max_turns=20, reconcile_interval=120, max_reconciliations=3):
        if not 1 <= max_active <= 3 or not 0 < budget_seconds <= 3600 or not 1 <= max_turns <= 100:
            raise ValueError('bounded execution limits required')
        if not 10 <= reconcile_interval <= 3600 or not 0 <= max_reconciliations <= 20:
            raise ValueError('bounded missed-event reconciliation required')
        self.db = db
        schema(db)
        self.adapter_factory = adapter_factory or native_adapter
        self.reconciler = reconciler or GitHubReconciler(token=os.getenv('MIHUR_GITHUB_TOKEN', ''))
        self.max_active, self.budget_seconds, self.max_turns = max_active, budget_seconds, max_turns
        self.interval, self.max_reconciliations = reconcile_interval, max_reconciliations
        self.wakeup = asyncio.Event()
        self.active = {}
        self.stopped = False
        self.service_running = False
        self.deadline = None
        self.event_errors = []

    def plans(self):
        return {r['task_id']: json.loads(r['policy']) for r in self.db.execute('SELECT * FROM event_plans')}

    def emit(self, event_id, kind, payload):
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        digest = hashlib.sha256((kind + ':' + encoded + ('' if kind == 'github' else ':' + event_id)).encode()).hexdigest()
        with transaction(self.db):
            prior = self.db.execute('SELECT * FROM dispatcher_events WHERE id=?', (event_id,)).fetchone()
            if prior and (prior['digest'] != digest or prior['kind'] != kind):
                raise ValueError('conflicting event delivery')
            self.db.execute('INSERT OR IGNORE INTO dispatcher_events(id,digest,kind,payload) VALUES(?,?,?,?)',
                            (event_id, digest, kind, encoded))
        self.wakeup.set()

    def webhook(self, headers, raw, secret):
        value = decode(secret, headers.get('x-hub-signature-256', ''), raw)
        kind = headers.get('x-github-event', '')
        if kind not in {'pull_request', 'pull_request_review', 'check_run', 'check_suite', 'workflow_run'}:
            raise ValueError('unsupported webhook event')
        repo = value.get('repository', {}).get('full_name')
        if repo not in {p['repository'] for p in self.plans().values()}:
            raise PermissionError('repository not enrolled')
        # Dedupe also by signed content, since delivery ID headers are not signed.
        self.emit('github:' + headers.get('x-github-delivery', ''), 'github', {'repository': repo, 'body': value})

    async def reconcile(self, repository=None):
        for task_id, policy in self.plans().items():
            if self.stopped or (self.deadline is not None and time.monotonic() >= self.deadline):
                return
            if repository and policy['repository'] != repository:
                continue
            try:
                self.reconciler.deadline=self.deadline
                snapshot = await asyncio.to_thread(self.reconciler.snapshot, policy)
                if self.stopped or (self.deadline is not None and time.monotonic() >= self.deadline):
                    return
                result = self.reconciler.apply(self.db, task_id, policy, snapshot, int(time.time()))
                task = self.db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
                observe(self.db, event_id='github-snapshot:' + hashlib.sha256((task_id + ':' + str(task['fence']) + ':' + result + ':' + phase(self.db,task) + ':' + json.dumps(snapshot, sort_keys=True)).encode()).hexdigest(),
                        task_id=task_id, repository=policy['repository'], head=snapshot['head'],
                        kind='authenticated_github_snapshot', actor='github-rest-collector',
                        source=f"https://github.com/{policy['repository']}/pull/{policy['pr']}",
                        payload={'result': result, 'phase': phase(self.db, task), 'snapshot':snapshot})
            except Exception as exc:
                self.event_errors.append(type(exc).__name__)
                self.event_errors = self.event_errors[-20:]
                # Failed network/provenance reconciliation never grants a gate.

    def recover(self):
        for run in self.db.execute("SELECT assignment_id FROM agent_runs WHERE state IN ('returned','failed')").fetchall():
            self.emit('agent:' + run['assignment_id'], 'agent_complete', {'assignment_id':run['assignment_id']})
        # Durable results are replayed by events. Unknown starts are quarantined,
        # never blindly replayed (server may have begun before its ID was saved).
        with transaction(self.db):
            for r in self.db.execute("SELECT * FROM agent_runs WHERE state IN ('starting','running')").fetchall():
                a = self.db.execute('SELECT * FROM assignments WHERE id=?', (r['assignment_id'],)).fetchone()
                self.db.execute("UPDATE agent_runs SET state='uncertain' WHERE assignment_id=?", (r['assignment_id'],))
                self.db.execute("UPDATE tasks SET status='blocked',blocker='Uncertain app-server start: inspect stored thread/turn before resuming' WHERE id=?", (a['task_id'],))
                desktop._cancel(self.db, a['task_id'], a['fence'])

    async def drain(self):
        rows = self.db.execute('SELECT * FROM dispatcher_events WHERE processed=0 ORDER BY rowid LIMIT 100').fetchall()
        for event in rows:
            if self.stopped or (self.deadline is not None and time.monotonic() >= self.deadline):
                return
            value = json.loads(event['payload'])
            if event['kind'] in ('github', 'reconcile'):
                await self.reconcile(value.get('repository'))
            elif event['kind'] == 'agent_complete':
                self.complete(value['assignment_id'])
                self.emit('post-agent:' + value['assignment_id'], 'reconcile', {})
            with transaction(self.db):
                self.db.execute('UPDATE dispatcher_events SET processed=1 WHERE id=?', (event['id'],))
        if len(rows) == 100:
            self.wakeup.set()

    def complete(self, assignment_id):
        with transaction(self.db):
            self._complete_transaction(assignment_id)

    def _complete_transaction(self, assignment_id):
        run = self.db.execute('SELECT * FROM agent_runs WHERE assignment_id=?', (assignment_id,)).fetchone()
        a = self.db.execute('SELECT * FROM assignments WHERE id=?', (assignment_id,)).fetchone()
        if not run or run['state'] not in ('returned', 'failed'):
            return
        task = self.db.execute('SELECT * FROM tasks WHERE id=?', (a['task_id'],)).fetchone()
        if a['state']=='cancelled' or task['fence']!=a['fence']:
            self.db.execute("UPDATE agent_runs SET state='consumed' WHERE assignment_id=?",(assignment_id,))
            return
        result = json.loads(run['result'])
        policy = self.plans()[a['task_id']]
        try:
            if run['state'] == 'failed' or not result.get('completed'):
                raise ValueError('Codex execution did not complete')
            response = result['response']
            if task['fence'] != a['fence'] or a['state'] != 'running':
                raise ValueError('stale worker result')
            head = sha(response['head'])
            if head != git(run['cwd'], 'rev-parse', 'HEAD') or git(run['cwd'], 'status', '--porcelain'):
                raise ValueError('result is not clean exact worktree commit')
            if a['role'] in desktop.IMPLEMENTERS:
                git(run['cwd'], 'merge-base', '--is-ancestor', policy['base_sha'], head)
                desktop.submit(self.db, a['id'], a['subject'], a['token'], response)
            elif a['role'] == 'integration':
                if head != task['head_sha'] or response.get('integration_passed') is not True:
                    raise ValueError('integration rejected')
                # Only GitHub collector may have filled these gates; model prose cannot.
                if self.db.execute('SELECT count(*) FROM accepted WHERE task_id=? AND sha=? AND subject LIKE ?',
                                   (task['id'], head, 'github:%')).fetchone()[0] != len(desktop.REVIEWERS):
                    raise ValueError('authenticated specialist gates missing')
                desktop.submit(self.db, a['id'], a['subject'], a['token'],
                               {'head': head, 'verdict': 'accept', 'source': 'app-server://' + run['thread_id']})
            else:
                # Tests/review agent output is evidence only, never authentication.
                if head != task['head_sha']:
                    raise ValueError('evidence head mismatch')
                with transaction(self.db):
                    self.db.execute("UPDATE assignments SET state='done',response=? WHERE id=?", (json.dumps(response), a['id']))
                    if response.get('verdict') in ('blocked','pending'):
                        self.db.execute("UPDATE tasks SET status='blocked',blocker='Codex execution environment or evidence prerequisite blocked; inspect recorded result' WHERE id=?", (task['id'],))
                        desktop._cancel(self.db,task['id'],task['fence'])
                    elif response.get('tests_passed') is False or response.get('verdict') == 'reject':
                        desktop._retry(self.db, task, 'Native test/review rejection; correction required')
            observe(self.db, event_id='app-server-result:' + a['id'], task_id=a['task_id'], repository=policy['repository'],
                    head=head, kind='native_execution', actor=run['thread_id'], source='app-server://' + run['thread_id'] + '/' + run['turn_id'],
                    payload={'role': a['role'], 'response': response, 'authenticated_approval': False})
            self.db.execute("UPDATE agent_runs SET state='consumed' WHERE assignment_id=?", (a['id'],))
        except Exception as exc:
            with transaction(self.db):
                if task['fence'] == a['fence'] and task['status'] not in ('blocked', 'completed'):
                    desktop._retry(self.db, task, 'Invalid/failed app-server result: ' + type(exc).__name__)
                self.db.execute("UPDATE agent_runs SET state='consumed' WHERE assignment_id=?", (a['id'],))

    async def execute(self, offer, policy, cwd):
        aid = offer['id']
        async def persist(thread_id, turn_id):
            with transaction(self.db):
                run = self.db.execute('SELECT * FROM agent_runs WHERE assignment_id=?', (aid,)).fetchone()
                if not run['thread_id']:
                    desktop.bind(self.db, aid, thread_id)
                elif run['thread_id'] != thread_id:
                    raise ValueError('thread identity changed')
                self.db.execute("UPDATE agent_runs SET state='running',thread_id=?,turn_id=COALESCE(?,turn_id) WHERE assignment_id=?", (thread_id, turn_id, aid))
        try:
            profile = tomllib.loads((ROOT / '.codex/agents' / (offer['role'] + '.toml')).read_text())
            prompt = profile['developer_instructions'] + '\nTask: ' + policy['objective'] + '\n'
            prompt += 'Return only JSON with head (actual clean committed HEAD), evidence and tests_passed or verdict as appropriate. Integration uses integration_passed. No nested agents, merge, deploy, learner-data changes, activation, credentials or governance writes. Agent prose is not authenticated approval.'
            result = await self.adapter_factory(offer['role']).run(prompt=prompt, cwd=cwd, persist_ids=persist)
            response = json.loads(result.final_message)
            value = {'completed': result.succeeded, 'response': response}
            with transaction(self.db):
                self.db.execute("UPDATE agent_runs SET state='returned',result=? WHERE assignment_id=?", (json.dumps(value), aid))
                self.emit('agent:' + aid, 'agent_complete', {'assignment_id':aid})
        except asyncio.CancelledError:
            self.db.execute("UPDATE agent_runs SET state='running' WHERE assignment_id=? AND state!='returned'", (aid,))
            raise
        except Exception as exc:
            with transaction(self.db):
                self.db.execute("UPDATE agent_runs SET state='failed',result=? WHERE assignment_id=?", (json.dumps({'completed': False, 'error': type(exc).__name__}), aid))
                self.emit('agent:' + aid, 'agent_complete', {'assignment_id':aid})
        finally:
            self.active.pop(aid, None)
            if self.db.execute('SELECT state FROM agent_runs WHERE assignment_id=?', (aid,)).fetchone()[0] in ('returned', 'failed'):
                self.emit('agent:' + aid, 'agent_complete', {'assignment_id': aid})

    async def dispatch(self):
        mode, turns = self.db.execute('SELECT mode,turns FROM dispatcher_control WHERE id=1').fetchone()
        if mode != 'RUNNING' or self.stopped or (self.deadline is not None and time.monotonic() >= self.deadline):
            return
        plans = self.plans()
        offers=desktop.tick(self.db)
        occupied={r['id'] for r in self.db.execute("SELECT id FROM assignments WHERE state='running' AND lease_until>?",(int(time.time()),))} | set(self.active)
        slots=self.max_active-len(occupied)
        for offer in offers:
            if offer['task_id'] not in plans or slots<=0 or turns >= self.max_turns:
                continue
            if self.db.execute('SELECT 1 FROM agent_runs WHERE assignment_id=?', (offer['id'],)).fetchone():
                continue
            task = self.db.execute('SELECT * FROM tasks WHERE id=?', (offer['task_id'],)).fetchone()
            policy = plans[offer['task_id']]
            base = (task['head_sha'] or policy['base_sha'])
            cwd = await asyncio.to_thread(worktree,policy['source'],policy['worktrees'],base,task['id'],task['fence']+':'+offer['role'],deadline=self.deadline)
            if self.stopped or self.db.execute('SELECT mode FROM dispatcher_control').fetchone()[0]!='RUNNING' or (self.deadline is not None and time.monotonic()>=self.deadline):
                return
            with transaction(self.db):
                self.db.execute("INSERT INTO agent_runs(assignment_id,state,cwd) VALUES(?,'starting',?)", (offer['id'], str(cwd)))
                self.db.execute('UPDATE dispatcher_control SET turns=turns+1 WHERE id=1')
            turns += 1
            slots -= 1
            self.active[offer['id']] = asyncio.create_task(self.execute(offer, policy, str(cwd)))

    def record_states(self):
        with transaction(self.db):
            for task in self.db.execute('SELECT * FROM tasks WHERE id IN (SELECT task_id FROM event_plans)').fetchall():
                state = phase(self.db,task)
                prior = self.db.execute('SELECT state,sha FROM state_history WHERE task_id=? ORDER BY id DESC LIMIT 1',(task['id'],)).fetchone()
                if not prior or (prior['state'],prior['sha']) != (state,task['head_sha']):
                    self.db.execute('INSERT INTO state_history(task_id,state,sha,recorded_at) VALUES(?,?,?,?)',
                                    (task['id'],state,task['head_sha'],int(time.time())))

    def status(self):
        tasks = [dict(r) for r in self.db.execute('SELECT * FROM tasks WHERE id IN (SELECT task_id FROM event_plans)')]
        return {'mode': self.db.execute('SELECT mode FROM dispatcher_control').fetchone()[0],
                'service_running':self.service_running,
                'stop_reason':self.db.execute('SELECT stop_reason FROM dispatcher_control').fetchone()[0],
                'turns_used': self.db.execute('SELECT turns FROM dispatcher_control').fetchone()[0],
                'active': len(self.active), 'tasks': [{'id':t['id'], 'state':phase(self.db,t), 'sha':t['head_sha'], 'blocker':t['blocker']} for t in tasks],
                'event_errors': self.event_errors, 'release_authorized':False, 'merge_allowed':False}

    async def run(self):
        self.service_running=True
        stop_reason='OPERATOR_STOPPED'
        self.recover()
        self.record_states()
        self.emit('startup:' + uuid.uuid4().hex, 'reconcile', {})
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.budget_seconds
        self.deadline=deadline
        reconciliations = 0
        timer = None
        def missed():
            nonlocal reconciliations
            reconciliations += 1
            self.emit('missed:' + uuid.uuid4().hex, 'reconcile', {})
        try:
            while not self.stopped:
                self.wakeup.clear()
                remaining=deadline-loop.time()
                if remaining<=0:
                    stop_reason='EXECUTION_BUDGET_EXHAUSTED'
                    break
                await asyncio.wait_for(self.drain(),timeout=remaining)
                self.record_states()
                remaining=deadline-loop.time()
                if remaining<=0:
                    stop_reason='EXECUTION_BUDGET_EXHAUSTED'
                    break
                await asyncio.wait_for(self.dispatch(),timeout=remaining)
                self.record_states()
                remaining = deadline - loop.time()
                if remaining <= 0:
                    stop_reason='EXECUTION_BUDGET_EXHAUSTED'
                    break
                if self.db.execute('SELECT 1 FROM dispatcher_events WHERE processed=0 LIMIT 1').fetchone():
                    continue
                if self.db.execute('SELECT turns FROM dispatcher_control').fetchone()[0] >= self.max_turns and not self.active:
                    stop_reason='EXECUTION_BUDGET_EXHAUSTED'
                    break
                states = [r['status'] for r in self.db.execute('SELECT status FROM tasks WHERE id IN (SELECT task_id FROM event_plans)')]
                if not self.active and all(x in ('completed','blocked') for x in states):
                    stop_reason='DEPENDENCY_BLOCKED' if 'blocked' in states else 'ALL_ELIGIBLE_WORK_COMPLETE'
                    break
                if self.active:
                    await asyncio.wait_for(self.wakeup.wait(), timeout=remaining)
                elif reconciliations < self.max_reconciliations:
                    timer = loop.call_later(min(self.interval, remaining), missed)
                    await asyncio.wait_for(self.wakeup.wait(), timeout=remaining)
                    timer.cancel()
                else:
                    mode=self.db.execute('SELECT mode FROM dispatcher_control').fetchone()[0]
                    awaiting_external=any(set(p.get('reviewers',{}))==set(desktop.REVIEWERS) for p in self.plans().values())
                    if mode=='RUNNING' and awaiting_external and not self.event_errors:
                        # Remain an event receiver after finite missed-event reconciliation;
                        # no polling or scheduled work. A real webhook wakes this wait.
                        await asyncio.wait_for(self.wakeup.wait(),timeout=remaining)
                    else:
                        stop_reason='OPERATOR_PAUSED' if mode=='PAUSED' else 'DEPENDENCY_OR_AUTHORIZATION_BLOCKED'
                        break
        except asyncio.TimeoutError:
            stop_reason='EXECUTION_BUDGET_EXHAUSTED'
        finally:
            if timer:
                timer.cancel()
            running = list(self.active.values())
            for job in running:
                job.cancel()
            await asyncio.gather(*running, return_exceptions=True)
            self.service_running=False
            self.db.execute('UPDATE dispatcher_control SET stop_reason=? WHERE id=1',(stop_reason,))

    async def control(self, reader, writer):
        try:
            raw = await asyncio.wait_for(reader.readline(), 5)
            if len(raw) > 1024:
                raise ValueError('control request too large')
            command = json.loads(raw)['command']
            if command not in ('status', 'stop', 'pause', 'resume'):
                raise ValueError('invalid operator command')
            if command != 'status':
                mode = {'stop':'STOPPED','pause':'PAUSED','resume':'RUNNING'}[command]
                self.db.execute('UPDATE dispatcher_control SET mode=? WHERE id=1', (mode,))
                if command == 'stop':
                    self.stopped = True
                    self.reconciler.deadline=time.monotonic()
                self.wakeup.set()
            writer.write((json.dumps(self.status()) + '\n').encode())
            await writer.drain()
        except Exception:
            writer.write(b'{"error":"invalid operator request"}\n')
        finally:
            writer.close()
            await writer.wait_closed()

    async def http(self, reader, writer):
        code = 202
        try:
            headers = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
            if len(headers) > 16384:
                raise ValueError('headers too large')
            lines = headers.decode('ascii').split('\r\n')
            if lines[0] != 'POST /github HTTP/1.1':
                raise ValueError('only GitHub POST supported')
            pairs = [line.split(':', 1) for line in lines[1:] if line]
            values = {key.lower(): value.strip() for key,value in pairs}
            if len(values) != len(pairs) or 'transfer-encoding' in values:
                raise ValueError('ambiguous request framing')
            length = int(values.get('content-length', '0'))
            if not 0 < length <= 1024 * 1024:
                raise ValueError('invalid body length')
            body = await asyncio.wait_for(reader.readexactly(length), 10)
            self.webhook(values, body, os.getenv('DIRECTOR_WEBHOOK_SECRET', '').encode())
        except PermissionError:
            code = 403
        except Exception:
            code = 400
        writer.write(f'HTTP/1.1 {code} Response\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'.encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()


async def serve(args):
    db = connect(args.db)
    schema(db)
    lock_path = Path(str(Path(args.db).resolve()) + '.dispatcher.lock')
    with lock_path.open('a') as lock:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('dispatcher already running') from None
        policy = json.loads(Path(args.config).read_text())
        dispatcher = Dispatcher(db, **policy.get('limits', {}))
        for task_id, config in policy.get('tasks', {}).items():
            register(db, task_id, config)
        # Explicit start honors a prior pause; an operator resume is required.
        db.execute("UPDATE dispatcher_control SET mode='RUNNING' WHERE mode='STOPPED'")
        socket = Path(args.socket).resolve()
        if socket.exists():
            if not socket.is_socket():
                raise ValueError('control path must be a socket')
            socket.unlink()
        server = await asyncio.start_unix_server(dispatcher.control, str(socket))
        os.chmod(socket, 0o600)
        webhook_server = await asyncio.start_server(dispatcher.http, '127.0.0.1', args.webhook_port)
        try:
            async with server, webhook_server:
                await dispatcher.run()
            print(json.dumps(dispatcher.status(), indent=2))
        finally:
            socket.unlink(missing_ok=True)
            db.close()


async def command(args):
    try:
        reader,writer = await asyncio.open_unix_connection(args.socket)
        writer.write((json.dumps({'command':args.command}) + '\n').encode())
        await writer.drain()
        print((await asyncio.wait_for(reader.readline(), 5)).decode().strip())
        writer.close()
        await writer.wait_closed()
    except (FileNotFoundError, ConnectionRefusedError):
        db = connect(args.db)
        try:
            d = Dispatcher(db)
            if args.command != 'status':
                db.execute('UPDATE dispatcher_control SET mode=?', ({'pause':'PAUSED','resume':'RUNNING','stop':'STOPPED'}[args.command],))
            print(json.dumps(d.status(), indent=2))
        finally:
            db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default='director.sqlite')
    parser.add_argument('--socket', default='/tmp/engineering-director.sock')
    sub = parser.add_subparsers(dest='command', required=True)
    family = sub.add_parser('add-family')
    family.add_argument('--family',required=True)
    family.add_argument('--stage',default='ontario-9')
    start = sub.add_parser('start')
    start.add_argument('--config', required=True)
    start.add_argument('--webhook-port', type=int, default=8787)
    for name in ('stop','pause','resume','status'):
        sub.add_parser(name)
    args = parser.parse_args()
    if args.command == 'add-family':
        from .core import enqueue_family
        db=connect(args.db)
        try:
            print(json.dumps({'task_id':enqueue_family(db,args.family,args.stage)}))
        finally:
            db.close()
    else:
        asyncio.run(serve(args) if args.command == 'start' else command(args))


if __name__ == '__main__':
    main()
