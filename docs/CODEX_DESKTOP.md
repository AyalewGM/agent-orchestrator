# Running the Engineering Director in Codex Desktop

## Existing system and this increment

The existing Python/SQLite task queue, curriculum dependencies, three-attempt limit,
lease recovery, exact-SHA release collector, isolated worktree helper and independent
review gates remain the authority. This follow-up targets current main and preserves
the existing integration commits.
No competing queue, Agents API service or recursive `codex exec` process is added.

The new `director.desktop` bridge persists role assignments *for an existing leased
task*. Codex Desktop's active Director spawns native subagents, binds their actual
sessions, verifies results and submits them. A tick recreates missing offers after a
crash. Submissions and governance changes commit together using nested savepoints.
Each delivery has a session owner, token, deadline, attempt fence and idempotent result.
Old deliveries cannot submit after reassignment or task correction. Tokens remain
in the Director; implementation agents should not have ledger access.

Flow: existing dependency-ready task → Backend/Frontend/Content/DevOps implementation
→ test runner → Engineering, Math, Pedagogy, Standards, Security and QA reviews →
independent Integration → local readiness. Test or review rejection automatically
requeues implementation and invalidates unfinished sibling assignments. Integration
rejection also requeues. Three implementation attempts and three deliveries per lane
bound retries. Six accepts and exact-head CI are both mandatory before Integration.
Only Integration acceptance unblocks dependent tasks. Release authorization stays false.

Profiles under `.codex/agents/` cover Director, Backend, Frontend, Content, Math,
Pedagogy, Standards, Security, QA, Engineering, Integration, DevOps and test execution.
Keep the generic implementation agent for other bounded engineering work. Native
profiles inherit parent permissions: role instructions and Git worktrees are not a host
sandbox. Run product commands only against disposable synthetic environments.

## Run the working demonstration

Open this repository as a local project in Codex Desktop, then run in its terminal:

```bash
python3 -m unittest discover -s tests -v
python3 -m director.demo --root /tmp/mihur-desktop-demo
python3 -m director.desktop --db /tmp/mihur-desktop-demo/demo.sqlite status
```

Choose a fresh root if that path already contains an incomplete demonstration.
A completed demo can be rerun without duplicating work. It creates a synthetic Git
repository, commits eight original draft equations in attempt-specific worktrees,
executes the existing content validator in a real Python subprocess and evaluates
six independent deterministic contract oracles. The first attempt has a leaking hint:
Pedagogy rejects it, the same task is automatically retried, and attempt two repairs it.
A fresh database connection resumes the flow before reviews. The final independent
integration check yields `integration_ready`, with `release_authorized=false` and
`learner_available=NOT_VERIFIED`. The report includes both exact commit SHAs, test
commands, exit codes, rejection and review history.

**Demo actors are synthetic fixtures, not live Codex models or authenticated Mihur
specialists.** Standards checks only preserve UNVERIFIED status; no official mapping
is accepted. No real product code, student record, GitHub write or activation is used.
Do not copy demo verdicts into the pilot ledger.

## Enable the Mihur pilot

1. Review and land the Desktop follow-up PR against main. #3 has landed; #4 was
   merged into its former feature base, so the follow-up preserves those changes
   that are still absent from main. The feature branch can be used for an isolated
   local demonstration before merge. Keep the existing
   `director.sqlite` as the single pilot ledger; do not replace it with the demo DB.
2. Verify the current Mihur main SHA, open #488 and #501/#505/#511/#514, review source
   guidance, and refresh the release snapshot through the connected GitHub tools.
   Historical evidence in docs/evidence is not current authorization. Have an
   administrator verify required checks, trusted check producers and merge protection.
3. Provision a dedicated Mihur checkout and disposable test stack accessible to the
   Desktop task. Fetch the exact pinned base commit. Do not mount production secrets
   or real learner data. Use the existing PostgreSQL and browser suites and existing
   family/publisher implementations. Enable no database seed/publisher activation.
4. Initialize or resume the persistent ledger:

   ```bash
   python3 -m director.native --db director.sqlite init
   python3 -m director.native --db director.sqlite status
   ```

   If the first audit is pending, run the existing read-only audit first:

   ```bash
   python3 -m director.native --db director.sqlite audit-once \
     --source /isolated/ai-tutor --worktrees /isolated/worktrees --worker audit-session
   ```

5. Enroll the current pending/review milestone, substituting its verified full SHA:

   ```bash
   python3 -m director.desktop --db director.sqlite enroll \
     --task mihur:ontario-9:audit --base FULL_40_CHARACTER_SHA --agent implementation
   python3 -m director.desktop --db director.sqlite tick
   ```

   Existing audit results are retained; a review-state audit proceeds to test evidence
   and independent reviews. No approval is copied from the legacy CLI. The initial
   audit can remain blocked if genuine current-head CI/specialist evidence is missing.
6. Give the active Desktop Director this prompt:

   > Use the Mihur Delivery skill and docs/CODEX_DESKTOP.md. Resume director.sqlite.
   > Process ready enrolled assignments using native specialized subagents. Keep
   > at most three active assignments. Bind actual spawned session identities; keep
   > tokens and the ledger private to the Director. Verify exact Git artifact and CI
   > provenance before submitting. Start reviewers in separate contexts without the
   > author's conclusions. Continue tests, reviews and bounded correction loops
   > automatically. Stop at real blockers or integration readiness. Do not merge,
   > deploy, modify learner data, activate students or claim product acceptance.

7. As each prerequisite completes, the Director enrolls the next bounded milestone
   with its then-current verified base: Ontario content → integration → verification,
   then Maryland Algebra I, then Ontario/Maryland Grade 8. This opt-in prevents a
   schedule from blindly dispatching the entire backlog at stale base commits. Select
   `content`, `backend`, `frontend` or `devops` according to the existing task's scope.
   Preserve canonical family IDs and reuse open product PRs; do not create competing PRs.
8. Inspect `python3 -m director.desktop --db director.sqlite status`. Check assignment
   owners, retries, exact SHAs, CI, review verdicts, missing gates and integration
   readiness. To resume after Desktop interruption, run tick and rebind expired offers.
   A prior session cannot change roles; do not reset attempt counters after exhaustion.

## Desktop result contract

`bind --assignment ID --session TOOL_RETURNED_SESSION` returns a delivery token to
the trusted Director. `submit --assignment ID --session SESSION --token TOKEN
--result VERIFIED_RESULT_JSON` atomically stores the result and returns next offers.
Tokens are not authorization for external systems and must not enter Git or public
reports. Store temporary result files outside the repository or in ignored state paths.

- Implementation: `head` plus artifact/scope/command evidence, verified against Git.
- Test runner: `head`, `source`, `checks` list with name/head_sha/status/conclusion.
  Required names come from operator-owned projects.toml, never the agent result.
  The Director must verify check producer and current-head provenance upstream.
- Reviewer: `head`, `verdict` (`accept` or `reject`), `source` evidence pointer.
  A pending verdict is a blocker, never an acceptance.
- Integration: same verdict contract; readiness only, never a release authorization.

The bridge trusts the local Director process. A typed session name is not cryptographic
identity: use actual native tool-returned sessions and independently verified evidence.
Authenticated GitHub specialist identities/CI provenance and a protected ledger service
remain necessary before unattended production governance can be trusted.

## Event-driven operation replaces scheduled execution

Use `director.dispatcher` and docs/EVENT_DISPATCHER.md as the primary execution
path. The hourly Director heartbeat was deleted at the owner's request. Do not
create an hourly job, cron wakeup or schedule-driven engineering loop. The older
Desktop bind/submit interface remains a compatibility/manual inspection path;
stored assignments alone are not running agents. Native app-server notifications,
signed GitHub webhook reconciliation and operator events drive the supervisor.
Existing read-only daily Actions reports are historical monitoring, not agent dispatch.

Keep student activation separate. After all required independent gates, real synthetic
PostgreSQL/browser/AT evidence, signed package verification and persisted before/after
student-visible depth are available, request explicit scoped owner authorization for
that exact release. This version supplies no production activation command, so it
cannot execute that final step or claim the primary product objective complete.

Official Codex references: [subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents),
[worktrees](https://learn.chatgpt.com/docs/environments/git-worktrees).
