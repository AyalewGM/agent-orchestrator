---
name: mihur-delivery
description: Execute or review a bounded Mihur question-bank integration milestone through the shared Director queue, preserving exact-SHA evidence and independent acceptance.
---

Read repository AGENTS.md, projects.toml and docs/ARCHITECTURE_AUDIT.md. Resolve
current ai-tutor main and relevant PR heads, then read #488 and the existing integration
PRs #501/#505/#511/#514. Their status can change; inspect rather than assume.

Use `python3 -m director.native --db /persistent/director.sqlite status` to recover
existing assignments. Initialize once with `init`; run `audit-once --source CHECKOUT
--worktrees WORKTREE_ROOT --worker SUBJECT` for the deterministic first milestone.
The product checkout must have the exact remote commit locally and the configured
HTTPS origin. No product code runs during this audit. Keep SQLite on a persistent
single host; read-only scheduled Actions artifacts are not a queue backup strategy.

Take the stored delegation packet into a separate Codex implementation session and
its SHA-pinned worktree. Reviewers receive the exact resulting SHA and evidence in
separate sessions. The trusted gateway, not an agent-supplied role label, submits
verdicts to `director.governance.review`. Rejection automatically requeues the same
task until its attempt limit. Do not invoke legacy approve on managed tasks.

Ontario first: reconcile persisted depth, canonical families, mode metadata, rational
scorer, signed publisher, rational input and real authenticated journey. Then Maryland
Algebra I, then Ontario/Maryland Grade 8. Original content only; build once/map many.
Report distinct mathematical task counts separately from row/generator counts.

No student availability claim without real synthetic deployed-journey evidence with
exact deployment SHA, route, before/after per-skill depth and no guided mastery credit.
Return missing evidence and blockers. Owner release authorization remains separate.


## Event-driven continuous execution

Read docs/EVENT_DISPATCHER.md. The primary runtime is `director.dispatcher`, using
the same tasks, dependencies and Desktop assignment ledger. No hourly automation,
cron wakeup or manual chat rotation. The hourly heartbeat was deleted by owner request.

Use start/stop/pause/resume/status. A persistent signed-event inbox and real Codex
app-server notifications trigger immediate next-state reconciliation. Configure a
bounded execution budget, at most three active workers and finite missed-event
reconciliation. Workers may not recursively spawn agents. Completed/failed results
and completion events commit together; replay is idempotent. Ambiguous crash starts
are quarantined for operator inspection instead of blindly rerunning side effects.

Use add-family to create independent Ontario content lanes under the existing audit
and aggregate-content milestones; do not bypass shared dependencies or advance
Maryland/Grade 8 before Ontario. Register only existing managed tasks in an
operator-owned event configuration. Do not reset task or delivery budgets.

Only the protected GitHub REST collector can populate authenticated gates. Webhooks
must pass HMAC-SHA256 and trigger fresh canonical head/check/review reads. Reviewers
are operator-configured numeric GitHub IDs, separate from all PR contributors. Agent
review prose is evidence only. Current-head APPROVED states, trusted check App IDs,
all independent lanes, local evidence and independent Integration are separate gates.
A head change or withdrawn gate revokes readiness and blocks dependent work.

Controller secrets and ledger write authority must be protected from workers. Worktrees
and local role names are not authentication. The app-server adapter strips controller,
GitHub, deployment and provider secrets from worker environment, enforces profile
feature restrictions and denies approval requests; it does not establish an OS/user
isolation boundary. Provision protected controller and disposable worker isolation
before claiming unattended production approval integrity.

Persist real native execution separately from synthetic fixtures. PR515 is the existing
real pilot; reuse its branch and PR, do not duplicate it. Its genuine correction loop
ended at c03ee1098f1006b11dccbf268d389be80a2e4297 with exact-head CI and bounded
engineering acceptance. The existing audit task has used all three implementation
attempts and remains blocked on broader audit/authenticated gates; do not reset it.

RELEASE_READY is local engineering readiness, never deployment or student activation.
No merge, production deploy, learner-data mutation, question activation or credential
acquisition without explicit scoped owner authorization and separate trusted service.
