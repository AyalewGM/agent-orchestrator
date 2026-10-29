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
