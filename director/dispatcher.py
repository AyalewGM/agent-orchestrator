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


def native_adapter(role, *, isolation=None, local_evidence_only=False):
    if isolation is None and not local_evidence_only:
        from .isolation import IsolationError
        raise IsolationError('protected worker launch not configured; explicit local-evidence-only mode is required for safe diagnostics')
    profile = tomllib.loads((ROOT / '.codex/agents' / (role + '.toml')).read_text())
    features = {'apps':False, 'plugins':False, 'request_permissions_tool':False}
    features.update(profile.get('features', {}))
    command = ['codex','app-server','--listen','stdio://','-c','agents.enabled=false']
    for key,value in features.items():
        if value is not False or key not in {'apps','plugins','request_permissions_tool','shell_tool','memories'}:
            raise ValueError('worker features may only reduce capabilities')
        command.extend(['-c',f'features.{key}=false'])
    return AppServerAdapter(sandbox='workspace-write' if role in desktop.IMPLEMENTERS or role == 'test_runner' else 'read-only',
                            timeout_seconds=240,command=tuple(command),launch_policy=isolation)


def schema(db):
    desktop.schema(db)
    with transaction(db):
        for sql in [
            "CREATE TABLE IF NOT EXISTS state_history(id INTEGER PRIMARY KEY,task_id TEXT REFERENCES tasks(id),state TEXT NOT NULL,sha TEXT,recorded_at INTEGER NOT NULL)",
            "CREATE TABLE IF NOT EXISTS event_plans(task_id TEXT PRIMARY KEY REFERENCES tasks(id), policy TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS dispatcher_events(id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, payload TEXT NOT NULL, processed INTEGER NOT NULL DEFAULT 0)",

            "CREATE TABLE IF NOT EXISTS dispatcher_control(id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL, turns INTEGER NOT NULL DEFAULT 0)",
        ]:
            db.execute(sql)
        old_columns={r['name'] for r in db.execute('PRAGMA table_info(agent_runs)')}
        if old_columns and 'run_id' not in old_columns:
            db.execute('ALTER TABLE agent_runs RENAME TO agent_runs_legacy')
        db.execute("""CREATE TABLE IF NOT EXISTS agent_runs(
            run_id TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(16)))),
            assignment_id TEXT NOT NULL REFERENCES assignments(id), delivery INTEGER NOT NULL DEFAULT 1,
            fence TEXT, lease_until INTEGER, state TEXT NOT NULL, thread_id TEXT,
            turn_id TEXT,cwd TEXT,result TEXT, UNIQUE(assignment_id,delivery))""")
        if old_columns and 'run_id' not in old_columns:
            db.execute("""INSERT INTO agent_runs(run_id,assignment_id,delivery,fence,lease_until,state,thread_id,turn_id,cwd,result)
                SELECT r.assignment_id,r.assignment_id,MAX(1,a.deliveries),a.fence,a.lease_until,
                       r.state,r.thread_id,r.turn_id,r.cwd,r.result
                FROM agent_runs_legacy r JOIN assignments a ON a.id=r.assignment_id""")
            # Legacy rows are retained as an immutable migration source, not overwritten.
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
                 budget_seconds=900, max_turns=20, reconcile_interval=120, max_reconciliations=3,
                 isolation=None,local_evidence_only=False):
        if not 1 <= max_active <= 3 or not 0 < budget_seconds <= 3600 or not 1 <= max_turns <= 100:
            raise ValueError('bounded execution limits required')
        if not 10 <= reconcile_interval <= 3600 or not 0 <= max_reconciliations <= 20:
            raise ValueError('bounded missed-event reconciliation required')
        self.db = db
        schema(db)
        self.local_evidence_only=local_evidence_only
        self.isolation=isolation
        if local_evidence_only and any(os.getenv(k) for k in ('MIHUR_GITHUB_TOKEN','GH_TOKEN','GITHUB_TOKEN','DIRECTOR_WEBHOOK_SECRET')):
            raise PermissionError('local evidence mode must not hold controller credentials')
        self.adapter_factory = adapter_factory or (lambda role:native_adapter(role,isolation=isolation,local_evidence_only=local_evidence_only))
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
        if self.local_evidence_only:
            raise PermissionError('local evidence mode cannot receive authenticated webhook events')
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
                result = ('LOCAL_EVIDENCE_ONLY_NO_APPROVAL_MUTATION' if self.local_evidence_only else self.reconciler.apply(self.db,task_id,policy,snapshot,int(time.time())))
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
        for run in self.db.execute("SELECT run_id FROM agent_runs WHERE state IN ('returned','failed')").fetchall():
            self.emit('agent:' + run['run_id'], 'agent_complete', {'run_id':run['run_id']})
        with transaction(self.db):
            for r in self.db.execute("SELECT * FROM agent_runs WHERE state IN ('starting','running')").fetchall():
                a=self.db.execute('SELECT * FROM assignments WHERE id=?',(r['assignment_id'],)).fetchone()
                task=self.db.execute('SELECT * FROM tasks WHERE id=?',(a['task_id'],)).fetchone()
                if a['state'] in ('cancelled','done') or task['fence']!=a['fence'] or (r['delivery']!=a['deliveries'] and not (r['state']=='starting' and a['state'] in ('queued','reserved') and r['delivery']==a['deliveries']+1)):
                    self.db.execute("UPDATE agent_runs SET state='superseded' WHERE run_id=?",(r['run_id'],))
                    continue
                self.db.execute("UPDATE agent_runs SET state='uncertain' WHERE run_id=?", (r['run_id'],))
                self.db.execute("UPDATE tasks SET status='blocked',completed_at=NULL,blocker='Uncertain app-server start: inspect stored thread/turn before resuming' WHERE id=?", (a['task_id'],))
                self.db.execute('UPDATE desktop_tasks SET integrated_sha=NULL WHERE task_id=?',(a['task_id'],))
                desktop._cancel(self.db,a['task_id'],a['fence'])

    async def drain(self):
        rows = self.db.execute('SELECT * FROM dispatcher_events WHERE processed=0 ORDER BY rowid LIMIT 100').fetchall()
        for event in rows:
            if self.stopped or (self.deadline is not None and time.monotonic() >= self.deadline):
                return
            value = json.loads(event['payload'])
            if event['kind'] in ('github', 'reconcile'):
                await self.reconcile(value.get('repository'))
            elif event['kind'] == 'agent_complete':
                run_id=value.get('run_id',value.get('assignment_id'))
                self.complete(run_id)
                self.emit('post-agent:' + run_id, 'reconcile', {})
            with transaction(self.db):
                self.db.execute('UPDATE dispatcher_events SET processed=1 WHERE id=?', (event['id'],))
        if len(rows) == 100:
            self.wakeup.set()

    def complete(self, run_id):
        with transaction(self.db):
            self._complete_transaction(run_id)

    def _complete_transaction(self, run_id):
        run = self.db.execute('SELECT * FROM agent_runs WHERE run_id=?', (run_id,)).fetchone()
        if not run:
            return
        a = self.db.execute('SELECT * FROM assignments WHERE id=?', (run['assignment_id'],)).fetchone()
        if not run or run['state'] not in ('returned', 'failed'):
            return
        task = self.db.execute('SELECT * FROM tasks WHERE id=?', (a['task_id'],)).fetchone()
        if a['state']=='cancelled' or task['fence']!=a['fence'] or run['delivery']!=a['deliveries']:
            self.db.execute("UPDATE agent_runs SET state='consumed' WHERE run_id=?",(run_id,))
            return
        result = json.loads(run['result'])
        policy = self.plans()[a['task_id']]
        try:
            if a['lease_until'] is None or a['lease_until']<=int(time.time()) or (run['lease_until'] is not None and run['lease_until']<=int(time.time())):
                self.db.execute("UPDATE agent_runs SET state='expired' WHERE run_id=?",(run_id,))
                return
            if run['state']=='failed' or not result.get('completed'):
                self.db.execute("UPDATE assignments SET state=?,subject=NULL,token=NULL,lease_until=NULL WHERE id=?",
                                ('queued' if a['deliveries']<desktop.MAX_DELIVERIES else 'cancelled',a['id']))
                if a['deliveries']>=desktop.MAX_DELIVERIES:
                    self.db.execute("UPDATE tasks SET status='blocked',blocker='Agent delivery budget exhausted; inspect preserved execution failures' WHERE id=?",(task['id'],))
                self.db.execute("UPDATE agent_runs SET state='consumed' WHERE run_id=?",(run_id,))
                return
            response=result['response']
            prelaunch_blocked=(a['state']=='reserved' and response.get('executor')=='os-confined-pytest' and response.get('verdict')=='blocked' and response.get('evidence',{}).get('tests_executed')==0)
            if task['fence'] != a['fence'] or (a['state'] != 'running' and not prelaunch_blocked):
                raise ValueError('stale worker result')
            head = sha(response['head'])
            if head != git(run['cwd'], 'rev-parse', 'HEAD',launch_policy=self.isolation) or git(run['cwd'], 'status', '--porcelain',launch_policy=self.isolation):
                raise ValueError('result is not clean exact worktree commit')
            if self.local_evidence_only and a['role'] in {*desktop.IMPLEMENTERS,'integration'}:
                raise PermissionError('local evidence mode cannot implement/integrate tasks')
            if a['role'] in desktop.IMPLEMENTERS:
                git(run['cwd'], 'merge-base', '--is-ancestor', policy['base_sha'], head,launch_policy=self.isolation)
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
            executor=response.get('executor','codex-app-server')
            source=('process://' if executor=='os-confined-pytest' else 'app-server://')+(run['thread_id'] or 'controller-prelaunch:'+run_id)+'/'+(run['turn_id'] or 'not-started')
            observe(self.db, event_id='app-server-result:' + run_id, task_id=a['task_id'], repository=policy['repository'],
                    head=head, kind='native_execution', actor=run['thread_id'] or 'controller-prelaunch:'+run_id, source=source,
                    payload={'role': a['role'], 'executor':executor,'response': response, 'authenticated_approval': False})
            self.db.execute("UPDATE agent_runs SET state='consumed' WHERE run_id=?", (run_id,))
        except Exception as exc:
            with transaction(self.db):
                current=self.db.execute('SELECT * FROM tasks WHERE id=?',(task['id'],)).fetchone()
                if current['fence'] == a['fence'] and current['status'] not in ('blocked', 'completed'):
                    desktop._retry(self.db, task, 'Invalid/failed app-server result: ' + type(exc).__name__)
                self.db.execute("UPDATE agent_runs SET state='consumed' WHERE run_id=?", (run_id,))

    async def execute(self, offer, policy, cwd, run_id):
        aid = offer['id']
        async def persist(thread_id, turn_id):
            with transaction(self.db):
                run = self.db.execute('SELECT * FROM agent_runs WHERE run_id=?', (run_id,)).fetchone()
                if not run['thread_id']:
                    bound=desktop.bind(self.db, aid, thread_id,reserved_delivery=run['delivery'])
                    if bound['deliveries']!=run['delivery']:
                        raise ValueError('delivery generation changed')
                    self.db.execute('UPDATE agent_runs SET lease_until=? WHERE run_id=?',(bound['lease_until'],run_id))
                elif run['thread_id'] != thread_id:
                    raise ValueError('thread identity changed')
                self.db.execute("UPDATE agent_runs SET state='running',thread_id=?,turn_id=COALESCE(?,turn_id) WHERE run_id=?", (thread_id, turn_id, run_id))
        try:
            profile = tomllib.loads((ROOT / '.codex/agents' / (offer['role'] + '.toml')).read_text())
            prompt = profile['developer_instructions'] + '\nRole: '+offer['role']+'\nTask: ' + policy['objective'] + '\n'
            prompt += 'Return only JSON with head (actual clean committed HEAD), evidence and tests_passed or verdict as appropriate. Integration uses integration_passed. No nested agents, merge, deploy, learner-data changes, activation, credentials or governance writes. Agent prose is not authenticated approval.'
            result = await self.adapter_factory(offer['role']).run(prompt=prompt, cwd=cwd, persist_ids=persist)
            response = json.loads(result.final_message)
            if getattr(result,'executor',None)=='os-confined-pytest':
                response={'head':result.head_sha,'tests_passed':result.succeeded,'evidence':response,'executor':'os-confined-pytest'}
            value = {'completed': True if getattr(result,'executor',None)=='os-confined-pytest' else result.succeeded, 'response': response}
            with transaction(self.db):
                self.db.execute("UPDATE agent_runs SET state='returned',result=? WHERE run_id=?", (json.dumps(value), run_id))
                self.emit('agent:' + run_id, 'agent_complete', {'run_id':run_id})
        except asyncio.CancelledError:
            self.db.execute("UPDATE agent_runs SET state='running' WHERE run_id=? AND state!='returned'", (run_id,))
            raise
        except Exception as exc:
            from .test_runner import TestRunnerError
            with transaction(self.db):
                if isinstance(exc,TestRunnerError):
                    value={'completed':True,'response':{'head':git(cwd,'rev-parse','HEAD',launch_policy=self.isolation),'verdict':'blocked','executor':'os-confined-pytest','evidence':{'tests_executed':exc.tests_executed,'blocker':str(exc)}}}
                    state='returned'
                else:
                    value={'completed':False,'error':type(exc).__name__}
                    state='failed'
                self.db.execute("UPDATE agent_runs SET state=?,result=? WHERE run_id=?", (state,json.dumps(value), run_id))
                self.emit('agent:' + run_id, 'agent_complete', {'run_id':run_id})
        finally:
            self.active.pop(aid, None)
            if self.db.execute('SELECT state FROM agent_runs WHERE run_id=?', (run_id,)).fetchone()[0] in ('returned', 'failed'):
                self.emit('agent:' + run_id, 'agent_complete', {'run_id':run_id})

    async def dispatch(self):
        mode, turns = self.db.execute('SELECT mode,turns FROM dispatcher_control WHERE id=1').fetchone()
        if mode != 'RUNNING' or self.stopped or (self.deadline is not None and time.monotonic() >= self.deadline):
            return
        plans = self.plans()
        offers=desktop.tick(self.db,claim_new=not self.local_evidence_only)
        occupied={r['id'] for r in self.db.execute("SELECT id FROM assignments WHERE state='running' AND lease_until>?",(int(time.time()),))} | set(self.active)
        slots=self.max_active-len(occupied)
        for offer in offers:
            if offer['task_id'] not in plans or slots<=0 or turns >= self.max_turns:
                continue
            if self.local_evidence_only and offer['role'] not in {'test_runner',*desktop.REVIEWERS.values()}:
                continue
            if self.db.execute('SELECT 1 FROM agent_runs WHERE assignment_id=? AND delivery=?', (offer['id'],offer['deliveries']+1)).fetchone():
                continue
            task = self.db.execute('SELECT * FROM tasks WHERE id=?', (offer['task_id'],)).fetchone()
            policy = plans[offer['task_id']]
            base = (task['head_sha'] or policy['base_sha'])
            cwd = await asyncio.to_thread(worktree,policy['source'],policy['worktrees'],base,task['id'],task['fence']+':'+offer['role']+':'+str(offer['deliveries']+1),deadline=self.deadline,launch_policy=self.isolation)
            if self.stopped or self.db.execute('SELECT mode FROM dispatcher_control').fetchone()[0]!='RUNNING' or (self.deadline is not None and time.monotonic()>=self.deadline):
                return
            with transaction(self.db):
                current=self.db.execute('SELECT * FROM assignments WHERE id=?',(offer['id'],)).fetchone()
                if current['state']!='queued' or current['deliveries']!=offer['deliveries']:
                    continue
                delivery=current['deliveries']+1
                self.db.execute("UPDATE assignments SET state='reserved',deliveries=?,lease_until=? WHERE id=?",(delivery,int(time.time())+desktop.ASSIGNMENT_TTL,offer['id']))
                run_id=uuid.uuid4().hex
                self.db.execute("INSERT INTO agent_runs(run_id,assignment_id,delivery,fence,state,cwd) VALUES(?,?,?,?,'starting',?)", (run_id,offer['id'],delivery,task['fence'],str(cwd)))
                self.db.execute('UPDATE dispatcher_control SET turns=turns+1 WHERE id=1')
            turns += 1
            slots -= 1
            self.active[offer['id']] = asyncio.create_task(self.execute(offer, policy, str(cwd),run_id))

    def revise_objective(self, task_id, objective, reason):
        """Explicit operator instruction repair; cannot change gate/source policy."""
        if not objective or len(objective)>32000 or not reason or len(reason)>500:
            raise ValueError('bounded objective and reason required')
        with transaction(self.db):
            task=self.db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
            if not task or task['status'] not in ('blocked','review'):
                raise ValueError('instruction repair requires an idle review/blocked task')
            if self.db.execute("SELECT 1 FROM agent_runs r JOIN assignments a ON a.id=r.assignment_id WHERE a.task_id=? AND r.state IN ('starting','running')",(task_id,)).fetchone():
                raise ValueError('execution still in flight')
            policy=self.plans()[task_id]
            if policy['objective']==objective:
                return
            digest=hashlib.sha256((json.dumps(policy,sort_keys=True)+objective+reason).encode()).hexdigest()
            observe(self.db,event_id='operator-objective:'+digest,task_id=task_id,repository=policy['repository'],
                    head=task['head_sha'],kind='operator_instruction_repair',actor='operator',source='director-control',
                    payload={'reason':reason,'prior_policy':policy,'objective':objective})
            policy['objective']=objective
            self.db.execute('UPDATE event_plans SET policy=? WHERE task_id=?',(json.dumps(policy,sort_keys=True),task_id))

    def resume_test(self, task_id, run_id, reason):
        """Operator-scoped retry of an unexecuted environment-blocked test only.

        Retains the task attempt/fence/head, every prior run/event/result, and the
        delivery counter. It cannot authorize implementation, review or activation.
        """
        if not reason or len(reason)>500:
            raise ValueError('bounded operator reason required')
        event_id='operator-test-resume:'+run_id
        prior=self.db.execute('SELECT payload FROM evidence_events WHERE event_id=?',(event_id,)).fetchone()
        if prior:
            payload=json.loads(prior['payload'])
            if payload['reason']!=reason or payload['task_id']!=task_id:
                raise ValueError('conflicting operator replay')
            return
        with transaction(self.db):
            run=self.db.execute('SELECT * FROM agent_runs WHERE run_id=?',(run_id,)).fetchone()
            if not run or run['state']!='consumed' or not run['result']:
                raise ValueError('preserved terminal execution required')
            a=self.db.execute('SELECT * FROM assignments WHERE id=?',(run['assignment_id'],)).fetchone()
            task=self.db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
            response=json.loads(run['result']).get('response',{})
            evidence=response.get('evidence',{})
            if not task or a['task_id']!=task_id or a['role']!='test_runner' or task['status']!='blocked':
                raise ValueError('only existing blocked test task may resume')
            if a['fence']!=task['fence'] or run['fence']!=task['fence'] or response.get('head')!=task['head_sha']:
                raise ValueError('scope/commit/fence moved')
            if response.get('verdict')!='blocked' or evidence.get('tests_executed')!=0 or not evidence.get('blocker'):
                raise ValueError('only proven unexecuted environment blocker is resumable')
            if a['deliveries']>=desktop.MAX_DELIVERIES or a['deliveries']!=run['delivery']:
                raise ValueError('test delivery budget exhausted or superseded')
            observe(self.db,event_id=event_id,task_id=task_id,repository=self.plans()[task_id]['repository'],
                    head=task['head_sha'],kind='operator_test_resume',actor='operator',source='run:'+run_id,
                    payload={'task_id':task_id,'reason':reason,'implementation_attempts':task['attempts'],
                             'prior_delivery':a['deliveries'],'prior_response':response})
            desktop._cancel(self.db,task_id,task['fence'])
            self.db.execute("UPDATE assignments SET state='queued',subject=NULL,token=NULL,lease_until=NULL,response=NULL WHERE id=?",(a['id'],))
            self.db.execute("UPDATE tasks SET status='review',completed_at=NULL,blocker='Environment-only test redelivery authorized; implementation budget unchanged' WHERE id=?",(task_id,))
            self.emit('resume-test:'+run_id,'operator_resume',{'task_id':task_id,'run_id':run_id})

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
                'execution_boundary':'LOCAL_EVIDENCE_ONLY' if self.local_evidence_only else ('PROTECTED_LAUNCH_CONFIGURED' if self.isolation else 'NOT_CONFIGURED'),
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
            request=json.loads(raw)
            command=request['command']
            if command not in ('status', 'stop', 'pause', 'resume','resume-test'):
                raise ValueError('invalid operator command')
            if command=='resume-test':
                self.resume_test(request['task'],request['run'],request['reason'])
            elif command != 'status':
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
        isolation=None
        local=getattr(args,'local_evidence_only',False)
        if policy.get('tasks') or db.execute('SELECT 1 FROM event_plans LIMIT 1').fetchone():
            if not local:
                from .isolation import IsolationConfig,IsolationError,preflight,require_coverage
                raw=policy.get('isolation')
                if not raw:
                    raise IsolationError('protected controller configuration required; no live service started')
                raw=dict(raw)
                for name in ('worktree','temp','worker_home','worker_codex_home','sandbox_executable'):
                    if name in raw: raw[name]=Path(raw[name])
                raw['protected_paths']=tuple(Path(p) for p in raw['protected_paths'])
                isolation=IsolationConfig(**raw)
                preflight(isolation)
                require_coverage(isolation,[Path(args.db).resolve(),Path(args.config).resolve(),lock_path,Path(args.socket).resolve().parent])
        dispatcher = Dispatcher(db, isolation=isolation,local_evidence_only=local, **policy.get('limits', {}))
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
        request={'command':args.command}
        if args.command=='resume-test':
            request.update(task=args.task,run=args.run,reason=args.reason)
        writer.write((json.dumps(request) + '\n').encode())
        await writer.drain()
        print((await asyncio.wait_for(reader.readline(), 5)).decode().strip())
        writer.close()
        await writer.wait_closed()
    except (FileNotFoundError, ConnectionRefusedError):
        db = connect(args.db)
        try:
            d = Dispatcher(db)
            if args.command=='resume-test':
                d.resume_test(args.task,args.run,args.reason)
            elif args.command != 'status':
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
    retry=sub.add_parser('resume-test')
    retry.add_argument('--task',required=True)
    retry.add_argument('--run',required=True)
    retry.add_argument('--reason',required=True)
    start = sub.add_parser('start')
    start.add_argument('--config', required=True)
    start.add_argument('--local-evidence-only',action='store_true',help='Unprotected diagnostics: no author/integration execution, secrets or authenticated approval mutation')
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
