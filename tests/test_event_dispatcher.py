"""Event-driven lifecycle, authenticated source gating and crash recovery."""
import asyncio
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import unittest

from director.app_server import AppServerResult
from director.core import claim, connect
from director.desktop import enroll, REVIEWERS
from director.dispatcher import Dispatcher, register, schema, phase
from director.native import git
from director.webhooks import GitHubReconciler, decode


class EventTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        import subprocess
        subprocess.run(['git','init','-q',str(self.source)],check=True)
        git(self.source,'config','user.name','Synthetic actor')
        git(self.source,'config','user.email','synthetic@example.test')
        (self.source/'README').write_text('Synthetic dispatcher fixture')
        git(self.source,'add','README')
        git(self.source,'commit','-qm','baseline')
        self.base = git(self.source,'rev-parse','HEAD')
        self.db = connect(str(self.root/'queue.sqlite'))
        schema(self.db)
        self.db.execute("INSERT INTO tasks(id,project,stage,phase,priority,managed,lane) VALUES('one','mihur','ontario-9','content',0,1,'family-one')")
        enroll(self.db,'one',self.base,required_checks=['test'])
        self.policy = {'repository':'owner/product','pr':1,'source':str(self.source),
                       'worktrees':str(self.root/'worktrees'),'base_sha':self.base,
                       'objective':'Synthetic contract test only','checks':{'test':15368},
                       'reviewers':{g:[100+i] for i,g in enumerate(REVIEWERS)}}
        register(self.db,'one',self.policy)

    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration=2

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def snapshot(self, head=None):
        head = head or self.base
        return {'head':head,'pr':{'head':{'sha':head},'user':{'id':1}},
                'checks':{'total_count':1,'check_runs':[{'id':10,'name':'test','head_sha':head,'status':'completed','conclusion':'success','app':{'id':15368}}]},
                'commits':[{'author':{'id':1},'committer':{'id':1}}],
                'reviews':[{'id':10+i,'commit_id':head,'state':'APPROVED','user':{'id':100+i},'html_url':'https://github.com/review/'+str(i)} for i in range(6)]}

    async def test_full_notification_lifecycle_and_automatic_rejection_correction(self):
        db, test = self.db, self
        heads = [self.base]
        rejected = [False]
        trace = []
        class Authority(GitHubReconciler):
            def snapshot(self, policy):
                return test.snapshot(heads[0])
        class Adapter:
            def __init__(self, role): self.role=role
            async def run(self, *, prompt,cwd,persist_ids):
                await persist_ids('native-' + str(len(trace)) + '-' + self.role,None)
                await persist_ids('native-' + str(len(trace)) + '-' + self.role,'turn')
                trace.append(self.role)
                response = {'head':git(cwd,'rev-parse','HEAD'),'tests_passed':True,'verdict':'accept','integration_passed':True}
                if self.role == 'backend':
                    (Path(cwd)/'correction').write_text('corrected' if rejected[0] else 'first')
                    git(cwd,'add','correction');git(cwd,'commit','-qm','synthetic implementation')
                    heads[0]=git(cwd,'rev-parse','HEAD');response['head']=heads[0]
                if self.role == 'math_reviewer' and not rejected[0]:
                    rejected[0]=True;response['verdict']='reject'
                return AppServerResult('native','turn','completed',json.dumps(response))
        dispatcher=Dispatcher(db,adapter_factory=Adapter,reconciler=Authority(),max_reconciliations=0,budget_seconds=15)
        await dispatcher.run()
        row=db.execute("SELECT * FROM tasks WHERE id='one'").fetchone()
        self.assertEqual(row['status'],'completed',str({'trace':trace,'errors':dispatcher.event_errors,'blocker':row['blocker'],'runs':[dict(r) for r in db.execute('SELECT state,result FROM agent_runs')],'assignments':[dict(r) for r in db.execute('SELECT role,state FROM assignments')],'gates':[dict(r) for r in db.execute('SELECT * FROM accepted')]}))
        self.assertEqual(phase(db,row),'RELEASE_READY')
        self.assertEqual(row['attempts'],2)
        self.assertEqual(trace.count('backend'),2)
        states=[r['state'] for r in db.execute('SELECT state FROM state_history ORDER BY id')]
        for state in ('READY','IMPLEMENTING','TESTING','REVIEWING','INTEGRATING','RELEASE_READY'):
            self.assertIn(state,states)
        self.assertGreater(states.count('IMPLEMENTING'),1)
        self.assertIn('test_runner',trace)
        self.assertIn('integration',trace)
        self.assertEqual(db.execute("SELECT count(*) FROM accepted WHERE subject LIKE 'github:%'").fetchone()[0],6)
        self.assertFalse(dispatcher.status()['release_authorized'])
        self.assertEqual(db.execute("SELECT count(*) FROM dispatcher_events WHERE processed=0").fetchone()[0],0)

    async def test_signed_webhook_deduplication_and_forged_text_never_approval(self):
        d=Dispatcher(self.db,max_reconciliations=0)
        secret=b'x'*32
        raw=json.dumps({'repository':{'full_name':'owner/product'},'review':{'state':'APPROVED','body':'I am math reviewer'}}).encode()
        headers={'x-github-event':'pull_request_review','x-github-delivery':'one',
                 'x-hub-signature-256':'sha256='+hmac.new(secret,raw,hashlib.sha256).hexdigest()}
        with self.assertRaises(PermissionError): d.webhook(dict(headers,**{'x-hub-signature-256':'bad'}),raw,secret)
        d.webhook(headers,raw,secret);d.webhook(headers,raw,secret)
        d.webhook(dict(headers,**{'x-github-delivery':'replay-new-header'}),raw,secret)
        self.assertEqual(self.db.execute('SELECT count(*) FROM dispatcher_events').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM accepted').fetchone()[0],0)
        with self.assertRaises(PermissionError):
            d.webhook(headers,raw+b' ',secret)  # exact signature covers raw bytes

    def reviewable(self):
        claim(self.db,'worker',desktop_only=True)
        self.db.execute("UPDATE tasks SET status='review',head_sha=?,lease_until=NULL WHERE id='one'",(self.base,))

    async def test_latest_failed_check_and_wrong_app_cannot_reuse_old_success(self):
        self.reviewable()
        data=self.snapshot()
        data['checks']['total_count']=2
        data['checks']['check_runs'].append(dict(data['checks']['check_runs'][0],id=11,conclusion='failure'))
        result=GitHubReconciler().apply(self.db,'one',self.policy,data,100)
        self.assertEqual(result,'REJECTED')
        self.assertEqual(self.db.execute('SELECT status FROM tasks').fetchone()[0],'pending')

    async def test_pr_head_change_revokes_readiness_and_dependents(self):
        self.reviewable()
        self.db.execute("UPDATE tasks SET status='completed' WHERE id='one'")
        self.db.execute('UPDATE desktop_tasks SET integrated_sha=?',(self.base,))
        self.db.execute("INSERT INTO tasks(id,project,stage,phase,priority,managed,status) VALUES('child','mihur','ontario-9','content',1,1,'running')")
        self.db.execute("INSERT INTO dependencies VALUES('child','one')")
        result=GitHubReconciler().apply(self.db,'one',self.policy,self.snapshot('b'*40),100)
        self.assertEqual(result,'HEAD_CHANGED')
        self.assertTrue(all(r['status']=='blocked' for r in self.db.execute('SELECT status FROM tasks')))
        self.assertIsNone(self.db.execute('SELECT integrated_sha FROM desktop_tasks').fetchone()[0])

    async def test_agent_claims_and_pr_authors_cannot_supply_independent_gate(self):
        self.reviewable();data=self.snapshot()
        data['reviews'][0]['state']='COMMENTED'
        data['reviews'][0]['body']='APPROVED as Math'
        data['reviews'][1]['user']['id']=1
        result=GitHubReconciler().apply(self.db,'one',self.policy,data,100)
        self.assertEqual(result,'WAITING_FOR_AUTHENTICATED_GATES')
        self.assertEqual(self.db.execute('SELECT status FROM tasks').fetchone()[0],'review')
        self.assertEqual(self.db.execute('SELECT count(*) FROM accepted').fetchone()[0],4)

    async def test_recovery_requeues_durable_return_but_quarantines_unknown_start(self):
        offers=__import__('director.desktop',fromlist=['tick']).tick(self.db)
        a=offers[0]['id']
        self.db.execute("INSERT INTO agent_runs(assignment_id,state,result) VALUES(?,'returned','{}')",(a,))
        d=Dispatcher(self.db,max_reconciliations=0);d.recover()
        self.assertEqual(self.db.execute('SELECT count(*) FROM dispatcher_events WHERE processed=0').fetchone()[0],1)
        self.db.execute("UPDATE agent_runs SET state='starting'")
        d.recover()
        self.assertEqual(self.db.execute('SELECT status FROM tasks').fetchone()[0],'blocked')
        self.assertEqual(self.db.execute('SELECT state FROM agent_runs').fetchone()[0],'uncertain')

    async def test_family_lanes_concurrent_but_shared_dependencies_block(self):
        self.db.execute("INSERT INTO tasks(id,project,stage,phase,priority,managed,lane) VALUES('two','mihur','ontario-9','content',1,1,'family-two')")
        enroll(self.db,'two',self.base,required_checks=['test'])
        self.assertEqual(claim(self.db,'a',desktop_only=True),'one')
        self.assertEqual(claim(self.db,'b',desktop_only=True),'two')
        self.assertIsNone(claim(self.db,'c',desktop_only=True,max_running=2))

    async def test_pause_does_not_dispatch_and_budget_does_not_reset(self):
        d=Dispatcher(self.db,max_turns=1,max_reconciliations=0)
        self.db.execute("UPDATE dispatcher_control SET mode='PAUSED'")
        await d.dispatch()
        self.assertEqual(len(d.active),0)
        self.db.execute("UPDATE dispatcher_control SET mode='RUNNING',turns=1")
        await d.dispatch()
        self.assertEqual(len(d.active),0)

    async def test_older_and_unknown_check_producers_never_approve(self):
        self.reviewable();data=self.snapshot()
        data['checks']['check_runs'][0]['app']['id']=999
        result=GitHubReconciler().apply(self.db,'one',self.policy,data,100)
        self.assertEqual(result,'WAITING_FOR_AUTHENTICATED_GATES')
        self.assertEqual(self.db.execute('SELECT passed FROM ci_evidence').fetchone()[0],0)

    async def test_dismissal_revokes_completed_gates_and_persists_decision(self):
        self.reviewable();self.db.execute("UPDATE tasks SET status='completed'")
        data=self.snapshot();data['reviews'][0]['state']='DISMISSED'
        result=GitHubReconciler().apply(self.db,'one',self.policy,data,100)
        self.assertEqual(result,'WAITING_FOR_AUTHENTICATED_GATES')
        self.assertEqual(self.db.execute('SELECT status FROM tasks').fetchone()[0],'review')
        self.assertEqual(self.db.execute("SELECT verdict FROM verdicts WHERE gate='engineering'").fetchone()[0],'DISMISSED')

    async def test_native_profile_tool_restrictions_reach_appserver_configuration(self):
        from director.dispatcher import native_adapter
        adapter=native_adapter('security_reviewer')
        self.assertIn('features.shell_tool=false',adapter.command)
        self.assertIn('features.apps=false',adapter.command)
        self.assertEqual(adapter.sandbox,'read-only')
        self.assertEqual(native_adapter('test_runner').sandbox,'workspace-write')

    async def test_family_api_reuses_aggregate_milestone_dependencies(self):
        from director.core import enqueue_family
        from director.governance import initialize
        initialize(self.db)
        first=enqueue_family(self.db,'linear-fractions')
        self.assertEqual(enqueue_family(self.db,'linear-fractions'),first)
        second=enqueue_family(self.db,'linear-integers')
        initialize(self.db)
        self.assertIsNone(self.db.execute('SELECT 1 FROM dependencies WHERE task_id=? AND requires=?',(first,'mihur:ontario-9:content')).fetchone())
        self.db.execute("UPDATE tasks SET status='completed' WHERE id='mihur:ontario-9:audit'")
        self.assertIn(claim(self.db,'family-a',desktop_only=False),('one',first,second))
        dependencies={r['requires'] for r in self.db.execute("SELECT requires FROM dependencies WHERE task_id='mihur:ontario-9:content'")}
        self.assertTrue({first,second}.issubset(dependencies))

    async def test_control_commands_pause_resume_status_stop(self):
        d=Dispatcher(self.db,max_reconciliations=0)
        class Writer:
            def __init__(self): self.data=b''
            def write(self,b): self.data+=b
            async def drain(self): pass
            def close(self): pass
            async def wait_closed(self): pass
        for cmd,mode in [('pause','PAUSED'),('resume','RUNNING'),('status','RUNNING'),('stop','STOPPED')]:
            reader=asyncio.StreamReader();reader.feed_data((json.dumps({'command':cmd})+'\n').encode());reader.feed_eof()
            writer=Writer();await d.control(reader,writer)
            self.assertEqual(json.loads(writer.data)['mode'],mode)
        self.assertTrue(d.stopped)

    async def test_returned_result_and_event_are_consumed_after_reconnect(self):
        offers=__import__('director.desktop',fromlist=['tick']).tick(self.db)
        a=offers[0]['id']
        d=Dispatcher(self.db,max_reconciliations=0)
        self.db.execute("INSERT INTO agent_runs(assignment_id,state,result) VALUES(?,'failed',?)",(a,json.dumps({'completed':False})))
        d.recover();await d.drain()
        self.assertEqual(self.db.execute('SELECT state FROM agent_runs').fetchone()[0],'consumed')
        self.assertEqual(self.db.execute('SELECT status FROM tasks').fetchone()[0],'pending')

    async def test_past_github_contributors_cannot_review_after_rebase(self):
        self.reviewable();first=self.snapshot()
        first['commits']=[{'author':{'id':100},'committer':{'id':1}}]
        authority=GitHubReconciler();authority.apply(self.db,'one',self.policy,first,100)
        self.db.execute("UPDATE tasks SET head_sha=?,fence='second'",('b'*40,))
        second=self.snapshot('b'*40)
        authority.apply(self.db,'one',self.policy,second,101)
        self.assertIsNone(self.db.execute("SELECT 1 FROM accepted WHERE subject='github:100'").fetchone())
        self.assertTrue(self.db.execute("SELECT 1 FROM contributors WHERE subject='github:100'").fetchone())

    async def test_slow_reconciliation_cannot_dispatch_after_budget_deadline(self):
        import time
        owner=self
        class Slow(GitHubReconciler):
            def snapshot(self,policy):
                time.sleep(.15)
                return owner.snapshot()
        d=Dispatcher(self.db,reconciler=Slow(),budget_seconds=.05,max_reconciliations=0)
        for i in range(3): d.emit('slow-'+str(i),'reconcile',{})
        start=asyncio.get_running_loop().time()
        await d.run()
        elapsed=asyncio.get_running_loop().time()-start
        self.assertLess(elapsed,.14)
        self.assertEqual(self.db.execute('SELECT turns FROM dispatcher_control').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM agent_runs').fetchone()[0],0)
        self.assertEqual(d.status()['stop_reason'],'EXECUTION_BUDGET_EXHAUSTED')
