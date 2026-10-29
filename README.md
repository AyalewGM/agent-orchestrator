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

## Incremental Codex-native integration (stacked on PR #3)

See [audit and gap analysis](docs/ARCHITECTURE_AUDIT.md). The queue remains
`director.core`; `director.governance` adds managed tasks, lease fences, contributor
separation, exact-SHA results and immutable review events. Rejection atomically
requeues the same task (three total attempts); exhaustion blocks downstream work.
The old `approve`/`complete` commands cannot bypass managed review gates.

```bash
python3 -m director.native --db /persistent/director.sqlite init
python3 -m director.native --db /persistent/director.sqlite audit-once \
  --source /isolated/ai-tutor --worktrees /isolated/worktrees --worker audit-session-id
python3 -m director.native --db /persistent/director.sqlite status
```

Use a dedicated product checkout with HTTPS origin `https://github.com/AyalewGM/ai-tutor.git`
and the current main commit fetched locally. The audit reads source names and the
existing release snapshot; it executes no product code, model call or GitHub write.
It records the snapshot and native delegation packet atomically before independent
review. Subsequent implementation runs in separate Codex sessions selected by the
Director using `.codex/agents/`, the stored packet, and SHA-pinned per-attempt worktrees.
Worktrees are retained for evidence and explicit operator cleanup; no automatic deletion.

`projects.toml` enables Mihur and reserves Orbytics/Gasha onboarding. Active Codex
sessions use the Director, implementation and six independent reviewer profiles;
the reusable Mihur delivery skill carries #488's operating contract. Reviewer
profiles disable shell/apps/plugins, but all profiles inherit parent permissions;
configure runner isolation separately. Syntax follows the [official custom-agent
format](https://learn.chatgpt.com/docs/agent-configuration/subagents).

The local service API `governance.review` accepts a trusted gateway's verified
subject-to-role mapping and evidence event; it does **not authenticate identities**.
Do not expose it to agent-controlled input. No external authenticated gateway,
unattended coding subprocess, merge/deployment service or production authorization
mechanism is provisioned. These remain blockers to permanent autonomous operation.
The service denies all merge/release/sensitive-data actions even if local reviews pass.
Branch protection must be configured/verified separately by a repository administrator.

Existing daily read-only Actions remain the schedule for remote evidence collection.
For durable queue execution, an operator's persistent isolated host can schedule
`audit-once` with the same database; overlapping ticks share atomic claims, cap three
running leases, and cannot execute later content phases. Do not put the ledger in
Actions caches or ephemeral artifacts. Model budgets must be enforced by the provider
and isolated runner before unattended model dispatch is added. Reports never infer
student availability from draft question counts, CI or mock browser tests.


## Codex Desktop Director and correction-loop demonstration

[Desktop runbook](docs/CODEX_DESKTOP.md) documents the supported active-session
workflow, exact pilot-enabling commands, evidence contracts and recommended thread
automation. Backend, Frontend, Content, Integration, DevOps and test-runner profiles
extend the existing Director and independent reviewers.

```bash
python3 -m director.demo --root /tmp/mihur-desktop-demo
python3 -m director.desktop --db /tmp/mihur-desktop-demo/demo.sqlite status
```

This offline contract demo makes real Git commits and executes content tests, then
uses **synthetic deterministic reviewer fixtures** to exercise a rejection, automatic
correction, restart, independent gates and integration readiness. It never supplies
Mihur specialist approval, student availability or release authorization.

For live Desktop use, `director.desktop enroll/tick/bind/submit` persist handoffs for
existing tasks. The active native Director spawns separate Codex subagents, verifies
actual session/artifact/CI provenance and submits results; failures route back without
manual chat rotation. Integration is a separate gate before dependent tasks can start.
This remains a trusted local bridge, not a cryptographic identity gateway or a
standalone background model service. No production activation path is provided.
