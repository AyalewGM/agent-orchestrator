# Event-driven Director

This is an incremental execution adapter for the existing `director.core` SQLite
queue, dependency ledger, `director.desktop` assignments and `director.governance`
gates. It is not a second task scheduler. PR #5 was reviewed and merged by the owner
at `0f5f0898a3f2c661468d10f4edc59124c2c06dd4`. Its shared-host identity limitation
remains documented in APPROVAL_BOUNDARY.md. The hourly heartbeat was deleted.

## State machine and events

| Existing durable state | Public phase | Completion trigger |
| --- | --- | --- |
| pending | READY | Dependency-ready atomic claim |
| running | IMPLEMENTING | Actual app-server turn plus verified clean Git commit |
| review, test assignment incomplete | TESTING | Native test evidence and trusted exact-head GitHub checks |
| review, test assignment complete | REVIEWING | Separate native reviewers plus authenticated GitHub approvals |
| integration | INTEGRATING | Independent app-server integration check |
| completed | RELEASE_READY | Engineering readiness only; never release authorization |

The service starts real `codex app-server --listen stdio://` processes. It initializes
JSON-RPC, creates separate threads, persists returned thread/turn IDs, consumes
`turn/completed` notifications and validates clean worktree HEADs. Server requests
for permission/approval are denied. Profile feature reductions reach app-server
configuration; nested agents are disabled so three managed workers cannot silently
create extra subagents. Test runners may write disposable test/temp files; independent
source-review workers are read-only. No worker may merge, deploy, activate content or
change learner data. These remain separate authorization boundaries.

A completion result and its inbox event commit together. Draining that event updates
the existing assignment/task, records evidence and immediately derives the next offers.
The supervisor waits on notifications, not an infinite polling loop. GitHub events,
app-server completion and operator controls wake it. At most three timer-based
reconciliations (configurable) repair missed notifications; after that, a configured
receiver can keep waiting for actual webhooks within its execution budget. There are
no recurring scheduled engineering runs. Start/restart performs reconciliation once.

Failures and genuine rejections invalidate unfinished siblings and return the same
task to implementation within its three-attempt budget. Pending evidence or an
execution-environment blocker is not a reason to spend another implementation attempt.
A native model's accept text is retained only as evidence and cannot populate an
authenticated approval gate. The existing local compatibility CLI still trusts its
caller; do not expose it or the database to untrusted workers.

## GitHub authentication and ordering

POST `/github` verifies HMAC-SHA256 over the **raw body**, using constant-time comparison
and a controller-only secret of at least 32 bytes. Body/header sizes and read times are
bounded. Repository enrollment is mandatory. Duplicate delivery IDs conflict-check and
signed-content hashes deduplicate replays with changed unsigned headers.

Notifications are signals, never authoritative approvals. Every notification fetches
canonical current PR head, checks, commits and reviews through GitHub REST, then rechecks
the head. Old/out-of-order notifications cannot restore an old decision. Required check
names must match configured GitHub App numeric IDs and exact commit; the newest run
for a given name/App governs. Missing, truncated or unknown-provider evidence fails closed.

Authenticated approval requires GitHub `APPROVED` state from an operator-configured
numeric user ID assigned to one independent gate. Comments containing “approved”,
model text, PR authors, all observed contributors across every attempt, unattributed
commits and older-SHA reviews cannot supply acceptance. Contributor identities survive
rebases and squashes. Dismissal, rejection and head changes revoke readiness and block
transitive dependents. Canonical snapshots and immutable head-bound review decisions
are preserved with source URLs before transitions. No GitHub write/merge API exists
in the dispatcher.

## Start and controls

Python 3.11+, Codex CLI with existing sign-in, and macOS/Linux Unix sockets are required.
Use a dedicated protected controller host/user and disposable engineering environment.
The adapter strips controller/GitHub/deployment/provider secrets from worker environment;
inherited user configuration and same-user filesystem access are **not** an OS security
boundary. Provision ledger and credential separation before claiming hostile-agent
approval-forgery resistance. This version does not install that infrastructure.

```bash
python3 -m director.native --db director.sqlite init
cp config/dispatcher.example.json director-event-config.local.json
python3 -m director.dispatcher --db director.sqlite start --config director-event-config.local.json
```

The example has **no enrolled execution plans** and safely launches no workers. Add
only existing managed task IDs after checking their remaining budget and dependencies.
Each plan has this operator-owned structure (replace paths, SHA and reviewer IDs):

```json
{
  "repository": "AyalewGM/ai-tutor",
  "pr": 515,
  "source": "/isolated/ai-tutor",
  "worktrees": "/isolated/worktrees",
  "base_sha": "FULL_40_CHARACTER_COMMIT_SHA",
  "implementer": "backend",
  "objective": "One bounded authorized correction; no learner data or activation",
  "checks": {"test":15368,"containers":15368,"Semgrep SAST":15368,"Trivy dependencies and secrets":15368,"CodeQL":57789},
  "reviewers": {}
}
```

Place it under `tasks[TASK_ID]`. The empty reviewers object intentionally cannot approve
anything. The owner must supply `engineering`, `mathematics`, `pedagogy`, `standards`,
`security`, `qa`, each mapped to distinct trusted GitHub numeric user IDs; never invent
them. Plans register existing tasks and preserve the enrolled required-check policy.
Plan changes are rejected rather than silently changing an in-flight authorization.

Provide `MIHUR_GITHUB_TOKEN` and `DIRECTOR_WEBHOOK_SECRET` only to the protected
controller through its service environment. Do not put credentials in the JSON or Git.
The listener binds loopback port 8787. A separately configured HTTPS reverse proxy and
GitHub webhook must forward PR/review/check/workflow events to `/github`; installation
requires repository administration, which the current connector lacks. No public
endpoint or webhook subscription was installed by this change.

In another terminal:

```bash
python3 -m director.dispatcher --db director.sqlite status
python3 -m director.dispatcher --db director.sqlite pause
python3 -m director.dispatcher --db director.sqlite resume
python3 -m director.dispatcher --db director.sqlite stop
```

The default control socket is `/tmp/engineering-director.sock`, mode 0600, with a
single-process file lock. Pausing stops new launches while in-flight evidence may
finish. Stop interrupts workers; unknown completion is quarantined. A stopped service
must be started again; `resume` alone does not spawn a process. Status distinguishes
service availability, operator intent and the last stop reason. Budgets and task
attempt counts survive restart; changing a run budget never resets task retries.

## Parallel families and recovery

```bash
python3 -m director.dispatcher --db director.sqlite add-family --family linear-fractions
python3 -m director.dispatcher --db director.sqlite add-family --family linear-integers
```

This reuses the existing Ontario audit and aggregate-content milestones. Families
have independent lanes and each requires the shared audit. The aggregate content
milestone depends on every family. Shared blockers remain global; later Maryland
Algebra I and Grade 8 cannot skip Ontario. Repeated initialization preserves this DAG.

Durable returned/failed turns are automatically replayed after restart. Eligible
queued work resumes without schedule ticks. Ambiguous starts (a crash after process
launch but before the server turn ID was durably captured) are quarantined instead
of blindly duplicated; an operator must inspect the recorded IDs/worktree. Timeouts
and stale fences cannot publish results. HTTP calls have short bounded timeouts and
check the run deadline between requests; one already-running network call may take
up to its five-second cancellation grace. Worktree preparation runs off the event
loop with deadline-bounded subprocesses. No launch intent is inserted after budget
expiry. The controller exits for completed work, blocked dependencies/authorization,
exhausted budget, pause or stop, recording the reason.

## Validation and limits of evidence

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q director tests
```

The full synthetic lifecycle test uses real temporary Git commits and fake Codex/
GitHub authorities, demonstrates all six phases, rejects the first implementation,
corrects it, and obtains fresh fixture reviews. It is not product approval. Tests also
cover signatures, duplicate/out-of-order source handling, current-head revocation,
check producers, lifetime contributor exclusion, crashes, deadlines and operator controls.

Real engineering evidence is separate: Mihur PR #515 changed the actual persisted
audit, failed first-head lint, automatically returned to the responsible native agent,
and passed exact-head CI and bounded independent engineering re-review at
`c03ee1098f1006b11dccbf268d389be80a2e4297`. A genuine app-server source review also
completed (see `docs/evidence/genuine-app-server-review-20261028.json`).

A genuine dispatcher-driven test turn was executed and persisted, but returned a
read-only temp-file environment blocker instead of tests. The test-runner sandbox
configuration was corrected; it was not rerun after the pilot exhausted its three
implementation attempts. See `genuine-dispatcher-execution-20261028.json`. No counters
were reset, no fake success was recorded and no new pilot PR was duplicated.

Live GitHub webhook delivery, protected-controller deployment and authenticated
specialist IDs are not configured. The real Ontario milestone remains blocked on its
broader persisted-depth audit and release gates; synthetic RELEASE_READY does not
establish real student availability. There is no deployment/activation capability.

References: [Codex app-server protocol](https://learn.chatgpt.com/docs/app-server),
[GitHub signature validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries).

## Reliability hardening (29 October 2026)

Each delivery now has an immutable `run_id` and its own result, fence, lease and
worktree. The SQLite migration retains `agent_runs_legacy`; redelivery never replaces
old execution evidence. Completion checks both leases and delivery generation.
Recovery quarantines uncertain current executions, while superseded runs cannot
alter newer work. Contributor exclusions are accumulated even before a task reaches
review. Known test-environment blockers stop the task; completed failing tests route
to implementation within the existing implementation budget.

An enrolled service now fails closed unless `isolation` is configured. Its fields
are documented in `director/isolation.py`: distinct Linux controller/worker UIDs,
root-owned private controller paths, worker-owned job/temp paths, and bubblewrap.
The actual database, configuration, lock and control directory must be covered by
`protected_paths`. Put the socket and lock inside `/var/lib/director/control`, not
shared `/tmp`; create the private directories/files before startup. Source clones
and worktree roots must be inside the worker job root. Git preparation runs as the
worker, including creation of worktree administrative files. No recursive ownership
changes or credential copying are performed.

The current launcher deliberately unshares networking. A reviewed restricted
provider gateway/namespace policy is still required before protected Codex work can
run. Do not enable host networking as a workaround. Configure a dedicated worker
sign-in; controller GitHub credentials and webhook secret stay in protected storage.
An operator must provision HTTPS forwarding and subscribe GitHub `pull_request`,
`pull_request_review`, `check_run`, `check_suite` and `workflow_run` events. Supply
separate numeric GitHub reviewer IDs for all six gates and GitHub App permission to
read branch protection. The available connector returns 403 for that administration
read. These requirements are not installed or live on the current shared-user Mac.

`start --local-evidence-only` explicitly permits bounded diagnostic evidence only.
It refuses controller credential environment variables, webhook/approval mutations,
new implementation claims, implementation execution and integration. It does not
provide a protected controller. The fixed macOS pytest adapter additionally uses
Seatbelt, isolated Python startup, a static test allowlist, no network, read-only
product/runtime paths and only a disposable writable temporary directory. Credential
read, outside-write and network denial probes must pass before product code runs.
It is identified as `os-confined-pytest`, never as a Codex reviewer.

`resume-test --task TASK --run RUN --reason TEXT` can redeliver only a preserved,
proven unexecuted environment-blocked test, under the same SHA/fence, while test
remaining deliveries permit it. It archives the authorization and old response;
it never resets implementation attempts or delivery counts. Exhaustion remains a
hard stop. Instruction repairs similarly archive the entire previous policy.

### Current Mihur evidence and blocker

PR515 remains at `c03ee1098f1006b11dccbf268d389be80a2e4297`. The hardened
confined executor genuinely ran both approved test files: **22 passed**, with all
three denial probes passing. See `evidence/confined-mihur-tests-20261029.json`.
This was a separate diagnostic execution, not a queue delivery or approval.
An existing independent Codex reviewer re-inspected the exact commit and accepted
the bounded prerequisite-scope correction; this is not authenticated specialist
release acceptance.

The attempted persisted redelivery encountered the outer host sandbox and consumed
test deliveries 2 and 3 before the known-blocker handling fix. All original results
and implementation attempts (3) remain unchanged. See
`evidence/genuine-recovery-20261029.json`. The task remains BLOCKED; no successful
real queued successor transition is claimed for this run. Synthetic lifecycle tests
cover immediate completion dispatch, rejection/correction, pause and crash recovery.
A live end-to-end continuation requires an explicit scoped operator recovery policy
for this exhausted task and the provisioned execution boundary. Do not reset its
counters, mark it accepted from diagnostic tests, or create a duplicate task/PR.
Maryland Algebra I and Grade 8 remain behind Ontario. No service or schedule is left
running; no merge, production deployment, learner-data change or activation occurred.
