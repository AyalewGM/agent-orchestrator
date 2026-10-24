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

## OpenAI-backed Ontario draft worker

The branch also includes `director.agent_worker` and `.github/workflows/mihur-worker.yml`.
The worker reads `AyalewGM/ai-tutor#488`, requests exactly eight original Ontario Grade 9
linear-equation examples, and checks every unique integer solution with a deterministic
oracle. It does not execute LLM-written code or grant mathematical/release approval.
The `--write` option creates one fixed-name branch and **draft** PR in Mihur, preventing
silent proliferation of new branches. It deliberately fails if that branch already exists.

Set the following repository Actions secrets in **agent-orchestrator**:
- `OPENAI_API_KEY`: OpenAI API credential with budget/rate-limit protections.
- `MIHUR_GITHUB_TOKEN`: fine-grained GitHub token restricted to `AyalewGM/ai-tutor`,
  with Contents read/write, Issues read, and Pull requests read/write. Keep this token
  separate from production Mihur credentials.

Do not place tokens in files or logs. On the workflow's **Actions → Mihur draft generation
worker → Run workflow** page, choose `write_pr=true` to create its draft PR.
`write_pr=false` performs an API-backed generation and validates without writing.
The schedule is validation-only. GitHub scheduled workflows run only from the default
branch, so they will not execute until this PR is reviewed and merged.

This is a small **working slice**, not a general unattended coding agent. The shared
ledger is not yet wired to this stateless generator, and a permanent VPS service,
authenticated review-gate collector, general task selection and secure coding sandbox
remain future work. The existence of this workflow does not establish that it is running.
