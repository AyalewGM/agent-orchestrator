# Agent Orchestrator

A shared, **fail-closed** engineering-goal coordinator for Mihur, Gasha, and future projects.

## Current implementation (MVP)

- Standard-library Python CLI and SQLite persistence for project-scoped, durable work items.
- A seeded, ordered Mihur goal: Ontario Grade 9 MTH1W → Maryland Algebra I → Grade 8.
- Atomic task claiming, expiry-based crash recovery, bounded attempts, and explicit review gates.
- No automatic agent execution, GitHub write access, model calls, server deployment, or production activation yet. Those are future integrations and must not be claimed as implemented.

Run from repository root (Python 3.11+):

```bash
python -m director.cli --db ./director.sqlite init-mihur
python -m director.cli --db ./director.sqlite status
python -m director.cli --db ./director.sqlite claim --worker local-dev
python -m unittest discover -s tests -v
```

The `claim` command grants a time-limited lease and prints the task ID. Work is not automatically executed. Complete the work and record a review outcome with:

```bash
python -m director.cli --db ./director.sqlite complete --task-id mihur:ontario-9:audit --worker local-dev
python -m director.cli --db ./director.sqlite approve --task-id mihur:ontario-9:audit --gate engineering --reviewer independent-reviewer
```

Passing review requires an actual reviewer with independent evidence; the CLI does **not** authenticate reviewers, so approvals here are development placeholders, never production authorization. The current tasks are coordination milestones, not generated or activated questions.

## Safety boundaries

Use an isolated execution runner for model-written code. Do not mount production learner data, tokens or deployment credentials into that runner. A later production implementation requires PostgreSQL or another concurrency-safe shared datastore, authenticated authorization, GitHub App credentials, CI evidence, independent reviewers, budgets, observability, per-project configuration, and explicitly authorized releases. Never enable direct student activation from agent claims or self-attested approvals.
