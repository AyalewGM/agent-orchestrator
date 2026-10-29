"""Bounded native delegation and read-only audit adapter using the existing queue."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

from .core import claim, connect
from .policy import projects
from .governance import finish, initialize, report, sha, transaction
from .mihur_release_director import snapshot


def git(source, *args, timeout=60):
    # Git config can otherwise run hooks, fsmonitor, and arbitrary checkout filters.
    prefix = ['git', '-C', str(source), '-c', 'core.hooksPath=/dev/null',
              '-c', 'core.fsmonitor=false']
    configured = subprocess.run(prefix + ['config', '--name-only', '--get-regexp', '^filter\\.'],
                                capture_output=True, text=True, timeout=min(10,timeout))
    if configured.returncode not in (0, 1):
        raise ValueError('cannot inspect Git filter configuration')
    drivers = {key.rsplit('.', 1)[0] for key in configured.stdout.splitlines()}
    for driver in sorted(drivers):
        for prop in ('process', 'smudge', 'clean'):
            prefix.extend(['-c', f'{driver}.{prop}='])
        prefix.extend(['-c', f'{driver}.required=false'])
    return subprocess.run(prefix + list(args), check=True,
                          capture_output=True, text=True, timeout=timeout).stdout.strip()


def worktree(source, root, head, task_id, fence, *, deadline=None):
    """Per-attempt path: a retry cannot reuse a dirty or stale agent workspace."""
    sha(head)
    def bounded_git(repo,*args):
        remaining=60 if deadline is None else min(60,deadline-time.monotonic())
        if remaining<=0:
            raise TimeoutError('worktree preparation budget exhausted')
        return git(repo,*args,timeout=remaining)
    key = hashlib.sha256(f'{task_id}:{fence}'.encode()).hexdigest()[:24]
    path = Path(root).resolve() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if bounded_git(path, 'rev-parse', 'HEAD') != head or bounded_git(path, 'status', '--porcelain', '--untracked-files=all'):
            raise ValueError('existing worktree is dirty or wrong head')
        common = Path(bounded_git(path, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
        if common != Path(bounded_git(source, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve():
            raise ValueError('worktree belongs to another repository')
    else:
        bounded_git(source, 'worktree', 'add', '--detach', str(path), head)
    return path


def packet(task, evidence):
    return {
        'task_id': task['id'], 'fence': task['fence'], 'agent': 'implementation',
        'project': task['project'], 'stage': task['stage'], 'phase': task['phase'],
        'base_sha': evidence['main_sha'], 'prior_blocker': task['blocker'],
        'objective': projects()[task['project']]['objective'],
        'dependencies': evidence['pr_dependencies'],
        'constraints': ['Reuse existing families and open PRs; do not create competing work',
                        'No approval of own work; return exact commit and test evidence',
                        'No merge, deployment, student activation or sensitive-data writes'],
        'review_agents': ['math_reviewer', 'pedagogy_reviewer', 'standards_reviewer',
                          'security_reviewer', 'qa_reviewer', 'engineering_reviewer'],
    }


def audit_once(db, *, worker, source, worktrees, fetch_snapshot=snapshot, now=None):
    """Working first milestone, no model call, source execution or remote writes."""
    current = int(time.time()) if now is None else now
    policy = projects()['mihur']
    if not policy['enabled']:
        raise ValueError('project dispatch disabled')
    tid = claim(db, worker, now=current, lease_seconds=policy['lease_seconds'],
                max_running=policy['max_running'], managed_only=True, project='mihur', phase='audit')
    if tid is None:
        return None
    task = dict(db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone())
    try:
        if task['project'] != 'mihur' or task['phase'] != 'audit':
            raise ValueError('deterministic adapter only handles Mihur audit; delegate other phases through Codex')
        evidence = fetch_snapshot()
        head = sha(evidence['main_sha'])
        if evidence['repository'] != projects()['mihur']['repository']:
            raise ValueError('snapshot repository mismatch')
        expected = 'https://github.com/' + evidence['repository'] + '.git'
        if git(source, 'remote', 'get-url', 'origin') != expected:
            raise ValueError('source origin must match configured repository HTTPS URL')
        path = worktree(source, worktrees, head, tid, task['fence'])
        # Read tracked names only. Never import or run product-repository code.
        files = git(path, 'ls-tree', '-r', '--name-only', 'HEAD').splitlines()
        inventory = [p for p in files if p.startswith(('.github/agents/', '.github/workflows/'))
                     or p in ('AGENTS.md', 'app/canonical_problem_families.py',
                              'scripts/audit_persisted_question_depth.py')]
        payload = {'release_snapshot': evidence, 'source_inventory': inventory,
                   'delegation': packet(task, evidence), 'execution': 'SOURCE_INSPECTION_ONLY',
                   'student_availability': 'NOT_VERIFIED',
                   'blockers': ['Independent review required', 'Persisted depth and authenticated synthetic E2E not executed']}
        finish(db, tid, worker, task['fence'], head, payload, now=now)
        return {'task_id': tid, 'sha': head, 'worktree': str(path), 'status': 'review'}
    except Exception as exc:
        # A failed attempt is retryable; never store untrusted exception text/secrets.
        with transaction(db):
            db.execute("UPDATE tasks SET status=CASE WHEN attempts>=3 THEN 'blocked' ELSE 'pending' END, "
                       "lease_until=NULL, blocker=? WHERE id=? AND fence=? AND status='running'",
                       (f'audit failed ({type(exc).__name__}); inspect runner privately', tid, task['fence']))
        raise


def main():
    parser = argparse.ArgumentParser(description='Codex-native integration on the existing ledger')
    parser.add_argument('--db', default='director.sqlite')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init')
    commands.add_parser('status')
    audit = commands.add_parser('audit-once')
    audit.add_argument('--source', required=True)
    audit.add_argument('--worktrees', required=True)
    audit.add_argument('--worker', required=True)
    args = parser.parse_args()
    with connect(args.db) as db:
        if args.command == 'init':
            initialize(db)
            value = report(db)
        elif args.command == 'audit-once':
            import os
            value = audit_once(db, worker=args.worker, source=args.source, worktrees=args.worktrees,
                               fetch_snapshot=lambda: snapshot(token=os.getenv('MIHUR_GITHUB_TOKEN', '')))
        else:
            value = report(db)
        print(json.dumps(value, indent=2))


if __name__ == '__main__':
    main()
