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


## Active Codex Desktop execution

Use the persistent bridge, not manually rotated chats. Read docs/CODEX_DESKTOP.md.
The trusted Director owns SQLite and binds actual tool-returned session IDs. Agent
results are untrusted evidence until the Director verifies the exact Git commit,
changed scope and command/CI provenance; agent role strings are not authentication.
Do not give author sessions the ledger, binding tokens or governance mutation tools.

1. Enroll an existing pending or review task with `director.desktop enroll`; this is
   explicit task opt-in. Do not bypass missing prerequisites or clone the pilot queue.
2. Run `python3 -m director.desktop --db director.sqlite tick`. For each queued
   assignment, read the matching `.codex/agents/ROLE.toml`, prepare its SHA-pinned
   isolated worktree and spawn a native subagent. Pass only the bounded assignment,
   raw source/artifacts and that role's instructions. Review sessions start without
   author conversation history, suggested verdicts, or other reviewers' conclusions.
3. Bind the assignment to the actual spawned session with `bind`. Keep the returned
   token in the Director context. Never bind one session to two roles for a task.
   Maximum three active assignments; execute review lanes in waves within this cap.
4. Wait for the native session. Independently verify the returned artifact SHA and
   evidence. For test_runner, verify executed synthetic test logs or authenticated
   exact-head GitHub checks against the operator-owned required policy. Do not
   substitute a model's assertion of green CI. Write the verified result to JSON,
   then `submit` using the Director-held token. Submission returns the next offers.
5. Continue automatically through testing, independent reviews, correction and
   integration. Rejections cancel sibling deliveries and requeue the same milestone.
   Persist all handoffs; a restarted Director runs tick/status to resume. Expired
   deliveries receive fresh tokens and at most three deliveries per role/attempt.
   Total implementation attempts are also capped at three. Stop at exhaustion or a
   real missing prerequisite, and report the blocker without resetting budgets.

An integration accept establishes local engineering readiness only. It does not grant
merge, deployment, learner-data mutation, production access or student activation.
No automated sensitive-action path is supplied. The offline demo uses synthetic actor
fixtures and must never be ingested as real Mihur specialist acceptance.
