"""Offline end-to-end contract demonstration with synthetic actors, not live approvals.

Produces real Git commits, runs exact-content tests, exercises a review rejection,
recovers the ledger in a fresh connection and reaches integration readiness.
No provider, GitHub, product data or deployment access is needed.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys

from .agent_worker import validate
from .core import connect
from .desktop import bind, enroll, REVIEWERS, schema, status, submit, tick
from .native import git, worktree

TASK = 'mihur:synthetic-demo:content'


def independent_review(role, candidate):
    """Separate deterministic contract oracles. Never official curriculum acceptance."""
    if role == 'math_reviewer':
        return all(Fraction(p['c'] - p['b'], p['a']) == p['answer'] for p in candidate['problems'])
    if role == 'pedagogy_reviewer':
        return all('answer is' not in p['hint'].lower() for p in candidate['problems'])
    if role == 'standards_reviewer':
        return candidate.get('standards_status') == 'UNVERIFIED'
    if role == 'security_reviewer':
        return candidate.get('runtime_activation') is False and candidate.get('synthetic') is True
    if role == 'qa_reviewer':
        return len({(p['a'], p['b'], p['c']) for p in candidate['problems']}) == 8
    if role == 'engineering_reviewer':
        return candidate.get('review_status') == 'DRAFT_UNVERIFIED'
    raise ValueError('unknown reviewer')


def run(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    ledger = root / 'demo.sqlite'
    if ledger.exists():
        # Re-run reports durable terminal state rather than duplicating work.
        from contextlib import closing
        with closing(connect(str(ledger))) as db:
            schema(db)
            if not db.execute("SELECT 1 FROM tasks WHERE id=? AND status='completed'", (TASK,)).fetchone():
                raise ValueError('incomplete demo: inspect Desktop status; choose a fresh directory for a fresh demonstration')
            return status(db)
    source = root / 'source'
    subprocess.run(['git', 'init', '-q', str(source)], check=True, capture_output=True)
    git(source, 'config', 'user.email', 'synthetic@example.test')
    git(source, 'config', 'user.name', 'Synthetic demo actor')
    (source / 'README.md').write_text('Synthetic Mihur orchestration fixture, not product content.\n')
    git(source, 'add', 'README.md')
    git(source, 'commit', '-qm', 'synthetic baseline')
    base = git(source, 'rev-parse', 'HEAD')
    db = connect(str(ledger))
    schema(db)
    db.execute("INSERT INTO tasks(id,project,stage,phase,priority,managed) VALUES (?,'mihur','synthetic-demo','content',0,1)", (TASK,))
    enroll(db, TASK, base, 'content', required_checks=['demo-content-tests'])
    trace = []
    try:
        for attempt in range(1, 4):
            offers = tick(db)
            author = next(a for a in offers if a['role'] == 'content')
            delivery = bind(db, author['id'], f'synthetic-author-{attempt}')
            path = worktree(source, root / 'worktrees', base, TASK, delivery['fence'])
            candidate = {'family_title': 'Synthetic linear equations', 'teaching_goal': 'Preserve equality',
                         'misconception': 'Changing only one side', 'review_status': 'DRAFT_UNVERIFIED',
                         'standards_status': 'UNVERIFIED', 'runtime_activation': False, 'synthetic': True,
                         'problems': [{'a': 1, 'b': n, 'c': n + 3, 'answer': 3,
                                       'reasoning': 'Subtract the constant from both sides',
                                       'hint': 'The answer is 3' if attempt == 1 else 'Preserve equality on both sides'}
                                      for n in range(8)]}
            (path / 'candidate.json').write_text(json.dumps(candidate, indent=2) + '\n')
            git(path, 'add', 'candidate.json')
            git(path, 'commit', '-qm', f'synthetic attempt {attempt}')
            head = git(path, 'rev-parse', 'HEAD')
            submit(db, delivery['id'], delivery['subject'], delivery['token'],
                   {'head': head, 'artifact': str(path / 'candidate.json'), 'scope': 'synthetic fixture only'})
            test_offer = next(a for a in tick(db) if a['role'] == 'test_runner')
            test = bind(db, test_offer['id'], f'synthetic-test-runner-{attempt}')
            command = [sys.executable, '-c',
                       'import json,sys; from director.agent_worker import validate; validate(json.load(open(sys.argv[1])))',
                       str(path / 'candidate.json')]
            executed = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                      text=True, timeout=30)
            source_evidence = f'synthetic://demo/{attempt}/tests'
            submit(db, test['id'], test['subject'], test['token'],
                   {'head': head, 'source': source_evidence,
                    'checks': [{'name': 'demo-content-tests', 'head_sha': head, 'status': 'completed',
                                'conclusion': 'success' if executed.returncode == 0 else 'failure'}]})
            trace.append({'attempt': attempt, 'sha': head, 'command': command, 'test_exit_code': executed.returncode})
            # A fresh connection proves that role routing survives Director restarts.
            db.close()
            db = connect(str(ledger))
            schema(db)
            rejected = False
            for role in REVIEWERS.values():
                offer = next(a for a in tick(db) if a['role'] == role)
                reviewer = bind(db, offer['id'], f'synthetic-{role}')
                verdict = 'accept' if independent_review(role, candidate) else 'reject'
                submit(db, reviewer['id'], reviewer['subject'], reviewer['token'],
                       {'head': head, 'verdict': verdict, 'source': f'synthetic://demo/{attempt}/{role}'})
                if verdict == 'reject':
                    trace[-1]['rejection'] = role
                    rejected = True
                    break
            if rejected:
                continue
            offer = next(a for a in tick(db) if a['role'] == 'integration')
            integrator = bind(db, offer['id'], 'synthetic-independent-integrator')
            validate(candidate)
            submit(db, integrator['id'], integrator['subject'], integrator['token'],
                   {'head': head, 'verdict': 'accept', 'source': 'synthetic://integration/compatibility'})
            break
        result = status(db)
        result['demonstration'] = {'actors': 'SYNTHETIC_DETERMINISTIC_FIXTURES', 'trace': trace,
                                   'product_approval': False, 'product_tests_executed': False}
        (root / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
        return result
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='Disposable demo directory; separate from the pilot database')
    args = parser.parse_args()
    result = run(args.root)
    print(json.dumps({'integration_ready': result['integration_ready'],
                      'release_authorized': result['release_authorized'],
                      'learner_available': result['learner_available'],
                      'report': str(Path(args.root).resolve() / 'report.json')}, indent=2))


if __name__ == '__main__':
    main()
