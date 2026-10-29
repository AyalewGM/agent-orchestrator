# PR #5 approval-integrity review

Independent native review of `02f51ea90d9a8c802b584f6fb8bd9c0c0da086bd`
rejected the requested claim that agents cannot forge independent approvals.
Session `/root/pr5_independent_security` reproduced a single caller inventing
implementation, test, six specialist and integration identities, fabricated check
results and a nonexistent commit, then reaching local `completed`. All 54 existing
tests passed. This is a trust-boundary limitation, not proof of hostile activity.

`bind` checks recorded identity strings and `submit` derives a role assertion from
them. Neither authenticates a Desktop session. All local agents share the host user
and tools; worktrees, prompts, delivery tokens and SQLite constraints do not prevent
a process with ledger access from impersonating callers or modifying the database.
The always-denied sensitive-action function does not constrain unrelated host tools.

This follow-up adds explicit `UNVERIFIED_SHARED_HOST` reporting and separate append-only
evidence observations. `verified_independent_approvals` remains empty. Real native
review/test events can now be preserved without representing them as authenticated
release approvals, and synthetic evidence remains separate. These changes improve
report accuracy; they do not claim to provide the missing security boundary.

## Merge and unattended-operation blockers

- The owner must identify trusted independent GitHub reviewers/teams and the protected
  approval authority. Do not fabricate a policy or treat model-generated identity labels
  as verified reviewers. The connected account is also the PR author.
- A separately protected service must own ledger mutation credentials, authenticate
  session/role provenance, fetch CI directly, verify exact GitHub commit/check producers,
  exclude all contributors from reviewing their own work, and bind decisions to current
  task attempts. Engineering agents must not have its credentials or ledger write access.
  It may use the existing queue; no second orchestrator is needed.
- A repository administrator must verify required reviews/checks and branch restrictions.
  On this review the connector returned HTTP 403 for main branch protection, the visible
  ruleset inventory was empty, and PR #5 had no submitted GitHub reviews. A clean mergeable
  state and green checks are not evidence that the requested policy is satisfied.

The owner subsequently reviewed and merged PR #5 at `0f5f0898a3f2c661468d10f4edc59124c2c06dd4`. That owner decision is recorded separately and does not resolve this deployment trust boundary. A fresh native review may accept bounded reporting changes while
continuing to reject approval-forgery resistance. Never relabel that as full security
acceptance. No merge, production deployment, learner-data modification, or question
activation is authorized by a local readiness status.

The real pilot may create safe code proposals and run isolated pure tests under the
owner's instruction. Its actual worker session, worktree, Git SHA, executed results,
independent source reviews and correction history are observations. They do not close
Math/Pedagogy/Standards/Security/QA release gates without verified authority.
