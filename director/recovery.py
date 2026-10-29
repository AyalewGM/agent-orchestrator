"""Prepare a non-executable recovery disposition in the existing evidence ledger.

This is an operator handoff, not a retry mechanism or a second queue. It never
changes tasks, assignments, budgets, accepted gates, or prior execution records.
"""
import argparse
import hashlib
import json

from .core import connect
from .dispatcher import schema
from .evidence import observe
from .governance import transaction


def prepare(db, task_id):
    schema(db)
    with transaction(db):
        task=db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
        if not task or task['status']!='blocked' or not task['head_sha']:
            raise ValueError('existing blocked exact-head task required')
        rows=db.execute('SELECT r.* FROM agent_runs r JOIN assignments a ON a.id=r.assignment_id WHERE a.task_id=? AND a.fence=? AND a.role=? ORDER BY r.delivery',
                        (task_id,task['fence'],'test_runner')).fetchall()
        if not rows or any(r['state'] not in ('consumed','expired','superseded') for r in rows):
            raise ValueError('terminal preserved test history required')
        runs=[]
        for row in rows:
            value=json.loads(row['result'] or '{}')
            evidence=value.get('response',{}).get('evidence',{})
            # Exception type alone is NOT proof tests never started.
            infrastructure=(value.get('response',{}).get('verdict')=='blocked'
                            and evidence.get('tests_executed')==0
                            and bool(evidence.get('blocker')))
            runs.append({'run_id':row['run_id'],'delivery':row['delivery'],
                         'result_sha256':hashlib.sha256((row['result'] or '').encode()).hexdigest(),
                         'classification':'infrastructure' if infrastructure else 'unknown',
                         'tests_executed':0 if infrastructure else None})
        unknown=any(r['classification']=='unknown' for r in runs)
        proposal={'parent_task':task_id,'head':task['head_sha'],'fence':task['fence'],
                  'original_implementation_attempts':task['attempts'],'runs':runs,
                  'classification':'unknown' if unknown else 'infrastructure',
                  'status':'HOLD_UNKNOWN_FAILURE_STAGE' if unknown else 'AWAITING_OPERATOR_AUTHORIZATION',
                  'authorized':False,'executable':False,
                  'scope':'Exact-SHA confined pytest and separate independent Codex source review only; zero product changes',
                  'proposed_limits':{'test_executions':1,'review_turns':1,'max_active':1,'budget_seconds':300,'automatic_retries':0},
                  'prohibited':['reset counters','clear original blocker','create duplicate product task or PR','populate approvals','merge','deploy','learner-data changes','student activation'],
                  'requirements':['resolve unknown failure stages or explicitly retain uncertainty in operator recovery disposition',
                                  'explicit scoped human authorization referencing this manifest digest',
                                  'verified confinement preflight before execution','no implicit recovery dispatch']}
        digest=hashlib.sha256(json.dumps(proposal,sort_keys=True).encode()).hexdigest()
        observe(db,event_id='recovery-proposal:'+digest,task_id=task_id,repository='AyalewGM/ai-tutor',
                head=task['head_sha'],kind='recovery_milestone_proposal',actor='director-diagnosis',
                source='existing-ledger://'+task_id,payload=proposal)
        return {'manifest_sha256':digest,**proposal}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True)
    parser.add_argument('--task',required=True)
    args=parser.parse_args()
    db=connect(args.db)
    try:print(json.dumps(prepare(db,args.task),indent=2))
    finally:db.close()


if __name__=='__main__': main()
