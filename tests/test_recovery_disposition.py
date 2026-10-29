import json
import tempfile
import unittest
from pathlib import Path
from director.core import connect
from director.dispatcher import schema
from director.recovery import prepare

class RecoveryTests(unittest.TestCase):
    def test_proposal_preserves_exhaustion_and_unknown_execution(self):
        with tempfile.TemporaryDirectory() as root:
            db=connect(str(Path(root)/'queue.sqlite'));self.addCleanup(db.close);schema(db)
            db.execute("INSERT INTO tasks(id,project,stage,phase,priority,status,attempts,managed,head_sha,fence) VALUES('original','mihur','ontario-9','audit',0,'blocked',3,1,?,'f')",('a'*40,))
            db.execute("INSERT INTO assignments(id,task_id,fence,role,state,deliveries) VALUES('test','original','f','test_runner','cancelled',3)")
            results=[{'completed':True,'response':{'verdict':'blocked','evidence':{'tests_executed':0,'blocker':'No temporary directory'}}},{'completed':False,'error':'TestRunnerError'}]
            for n,result in enumerate(results,1):
                db.execute("INSERT INTO agent_runs(run_id,assignment_id,delivery,fence,state,result) VALUES(?, 'test',?,'f','consumed',?)",(str(n),n,json.dumps(result)))
            before=[tuple(r) for r in db.execute('SELECT * FROM agent_runs')]
            proposal=prepare(db,'original');again=prepare(db,'original')
            self.assertEqual(proposal,again)
            self.assertEqual(proposal['classification'],'unknown')
            self.assertEqual([r['classification'] for r in proposal['runs']],['infrastructure','unknown'])
            self.assertFalse(proposal['executable']);self.assertFalse(proposal['authorized'])
            self.assertEqual(db.execute('SELECT count(*) FROM evidence_events').fetchone()[0],1)
            self.assertEqual(tuple(db.execute('SELECT status,attempts FROM tasks').fetchone()),('blocked',3))
            self.assertEqual(db.execute('SELECT deliveries FROM assignments').fetchone()[0],3)
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM agent_runs')])
            self.assertEqual(db.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
