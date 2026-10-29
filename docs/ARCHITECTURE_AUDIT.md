# Audit and incremental architecture — 2026-10-28

Audited local main `3bd6571b57052d24b4ee975d71acbc3e04a85f84` and open
orchestrator PR #3 head `d824ff02b4a96ef43d65384f88465157c84531a3`.
No AGENTS.md, .codex configuration, custom Codex agents, or reusable skills existed.
The standard-library Python coordinator already owns SQLite tasks, priority ordering,
leases, three attempts, and development approvals. Reuse it as the only queue.
The OpenAI draft worker validates eight equations but bypasses the ledger and is not
an integration worker. Its fixed branch fails rather than recovers after partial writes.
Do not promote that generator into the integration path.

PR #3 already owns exact-main/PR-head release snapshots and read-only scheduled
Actions. This change is stacked on #3 and extends that collector, rather than creating
a replacement. Existing CI covers Python 3.11–3.13. Scheduled artifacts are reports,
not durable queue storage. A single persistent host owns the SQLite file; never share
it over NFS or restore a stale Actions cache as queue state.

Mihur #488 explicitly requires Ontario MTH1W → Maryland Algebra I → Ontario and
Maryland Grade 8. Remote open PR inventory includes #501 signed staging publisher,
#505 accessible rational input, #511 atomic recorder, #514 published learner journey,
and #484/#485 factory/orchestration prototypes. Existing canonical families, exact
scoring, source fidelity records, and independent auditors must be reused. Mihur main
has six .github/agents profiles (Math, Pedagogy, Standards, Security, Muse QA, bug
hunter); these are Copilot format, not Codex profiles. Preserve their lane charters,
not their unsupported execution assumptions. Current remote inventory was inspected
through the GitHub connector; the local Mihur checkout is older and not release truth.
Mihur workflows already provide PostgreSQL, browser/container, and AppSec lanes.

| Gap | Smallest increment | Remaining boundary |
| --- | --- | --- |
| Stateless draft execution | Queue-backed read-only integration audit | Coding sandbox adapter must be separately provisioned |
| No exact-SHA review records | Immutable review events and head-bound CI/evidence | Trusted gateway authenticates role identity |
| Manual failed-review routing | Transactional retry with blockers, bounded attempts | Exhaustion remains visible and blocks dependencies |
| Worktree collisions | Detached SHA-pinned per-attempt worktrees | Worktrees isolate Git changes, not credentials/processes |
| Serial-only queue | Explicit DAG edges, independent projects can progress | Existing curriculum order stays strict |
| Self-attested approvals | Managed tasks reject legacy approval path | Never trust agent output as authenticated approval |
| Unverified release | Unified blockers + always-denied sensitive operations | No merge/deploy/activation API supplied |
| Multi-product scope | Mihur enabled, Orbytics/Gasha explicitly disabled | Exact repositories and policies need onboarding |

## Execution and trust

Unified Engineering Director delegates bounded assignments to named repository Codex
agents. Implementation and all five specialist review lanes have separate sessions.
Any implementation contributor is ineligible to review that task, even after retries.
Reviewer roles are supplied by a trusted service configuration, never by model output.
Review records include exact SHA, role, subject, source and verdict; rejection clears
current approvals and atomically requeues implementation. Dependency progress requires
all head-bound review gates. CI is a separate requirement and never a review verdict.
No local SQLite API authenticates a caller: database and runner access are trusted.
No profile or prompt is an OS security boundary. Merge protection must be enforced
by GitHub rulesets/required checks with a separate merge identity; no merge capability
is present here. Production deployment, student activation, and sensitive-data changes
are denied by this slice even when all local gates pass. Explicit scoped human
authorization and a separate release service are still required.

## Working vertical slice

The existing twelve Mihur milestones become managed tasks on explicit initialization.
The first task performs a read-only release dependency audit and records the exact
main SHA, all four PR heads and check results, source inventory, blockers, and artifact
hash. Repeat execution on an expired lease uses a new fence and worktree; stale
workers cannot publish results. Snapshot storage and task transition commit together.
A failed independent review automatically makes the same milestone claimable for a
bounded remediation attempt. Missing review/CI cannot advance content or integration.
Student availability remains NOT_VERIFIED until authenticated deployed synthetic
journey evidence exists; static files, mocks and green CI cannot establish it.

The Codex adapter prepares native delegation packets for an active Codex Director
session. An unattended coding runner is not provisioned by this slice. It never runs model-written
shell code with deployment credentials. The deterministic first audit can run without
a model or provider key. No new question family or production activation is warranted
until the existing integration PRs and persisted-depth evidence have been reconciled.

## Executed integration evidence

`docs/evidence/mihur-release-20261028.json` contains GitHub-connector readbacks
of main and #501/#505/#511/#514, their full current-head check lists and review
metadata. Heads were re-read after collection. GitHub review states remain evidence
pointers: they do not authenticate a Math/Pedagogy/Standards/Security/QA lane.
The local read-only audit ran against exact remote main
`288ff05f98a0feb1d02e101a9de74ac637bb0067` fetched into a temporary checkout,
then persisted the result in `director.sqlite`. That local durable database is ignored
by Git; its public execution summary is `docs/evidence/queue-audit-20261028.json`.
A fresh CLI process recovered the unified status. No product tests or data operations
were executed. Source inspection is not persisted-depth, deployment or student proof.

Observed remote blockers: #501's current-head CodeQL check failed; #514 has no
current-head AppSec/CodeQL checks in the readback. #505/#511 pass the configured
check-name inventory, but producer identity and actual branch-required policy remain
unverified. Configured names are never sufficient merge authorization. #3 must land
before this stacked integration can target main. GitHub branch-protection read returned
403 with the current connector, so administrator verification remains outstanding.

Independent development review found and reproduced CI-gate bypass, checkout hook
execution, concurrent migration races and expired-lease migration failure. These were
fixed with a separate head-bound CI gate, disabled Git hooks/fsmonitor/filter commands,
serialized schema migration and expiry-aware legacy adoption retaining contributors.
Regression tests cover these findings; this development review is not a specialist
release acceptance. A trusted local CI collector submits `record_ci`; agent-generated
claims cannot be used as its input policy. Completed local milestones are finalized;
changes to accepted artifacts require a new scoped task and fresh evidence rather than
reusing a historical acceptance.

## Codex Desktop follow-up audit and implementation

Re-audited the existing feature head `bf8f5f6a490f6a8b2e19592f5a947a10b89c8f91`,
AGENTS.md, eight existing profiles, skill, registry, Python modules, all three Actions
workflows, open orchestration PR #4 and current open Mihur PR inventory. #3/#4 remain
unmerged and must be reused. This follow-up updates #4 rather than adding another PR.

The concrete missing capability was persistent *agent handoff*, not task scheduling.
`director.desktop` now derives durable role assignments from the existing task state,
uses the same atomic claim/fence and review/CI APIs, and records bound Desktop sessions,
delivery deadlines, tokens and idempotent results. It never spawns another orchestration
service or independent scheduler. The active native Codex Director remains the executor.
Six additional profiles cover Backend, Frontend, Content, Integration, DevOps and test
execution; the original independent reviewers retain their separate lanes.

All six accepts plus head-bound CI move Desktop-enrolled tasks into a separate
`integration` state. An independent Integration accept is required before local queue
dependencies unblock. Failed tests, specialist rejection, integration rejection and
expired agent deliveries automatically route back to implementation within the existing
three-attempt budget. Each role delivery is separately capped at three; active bound
sessions are capped by project policy. Disabled projects reject both new and active
assignment execution. Ledger mutations roll back together using nested savepoints.

Validation: 54 tests passed, compilation passed, updated skill validated. The offline
flow uses real temporary Git commits and executed validator subprocesses; synthetic
independent contract fixtures reject a leaking hint on attempt one, resume after a DB
reconnect and accept the correction on attempt two. Exact SHAs, test commands and
verdict history are in `docs/evidence/desktop-demo-20261028.json`. This is explicitly
not product approval or a live-model acceptance run. The independent development
review reproduced a policy-revocation bypass, then verified the fix and all 54 tests,
plus complete three-by-three delivery/implementation exhaustion ending blocked.

Remaining limits: local Director/session/CI provenance is trusted, not cryptographically
authenticated; a malicious process with ledger access can impersonate callers. An
active Desktop host/session is required, recommended automation is not installed,
and provider cost/process limits are external. GitHub protection and authentic specialist
acceptance must still be verified. No product activation, real learner-data modification,
production deployment or merge capability was added. The full Ontario student-availability
objective and subsequent Maryland/Grade 8 milestones remain gated product work.

See `docs/CODEX_DESKTOP.md` for exact enabling commands, native session procedure,
result schemas, recommended thread automation and explicit release boundary.
