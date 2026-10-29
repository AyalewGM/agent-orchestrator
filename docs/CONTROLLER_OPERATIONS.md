# Controller operational handoff

## PR6 and exhausted Mihur pilot

The starting head `c45685b978940bb1b4ea107d7eb48a0b66628009` passed exact-head
CI but fresh independent reviews rejected it: delivery expiry could orphan a live
worker, and same-head gate restoration could leave Integration cancelled forever.
The fixes prevent overlapping deliveries of one assignment, retain execution ownership
until completion, and rearm Integration only after fresh gate restoration, no uncertain
or pending old execution, and remaining delivery budget. No retry count is reset.

The original Mihur task is `mihur:ontario-9:audit`, head
`c03ee1098f1006b11dccbf268d389be80a2e4297`, implementation attempts 3,
test deliveries 3. Original SHA/fence, results and blockers remain intact.

| Delivery | Preserved evidence | Disposition |
| --- | --- | --- |
| 1, `2a8de2783b48476aa9aae257dcd3af26` | Explicit no-temp-directory traceback, zero tests | Confirmed infrastructure/sandbox failure |
| 2, `d5bdbe6a616044c4a07c4fd7f7883e1a` | Only exception class `TestRunnerError` | Unknown stage; cannot prove zero tests |
| 3, `b4274524cc8a4e6ab787df3f5f4dc8ef` | Only exception class `TestRunnerError` | Unknown stage; cannot prove zero tests |

The later exception type also covers post-launch timeout or HEAD movement. Their
historical stage cannot be reconstructed from that field. A current sandbox-denial
reproduction or passing tests does not retroactively prove what those runs executed.
The overall exhausted delivery is therefore **unknown**, with one confirmed
infrastructure failure; no product assertion failure is established.

A linked, non-executable recovery milestone is stored as an idempotent observation in
the **same ledger**, not a new task/PR/queue. Inspect or regenerate it with:

```bash
python3 -m director.recovery --db director.sqlite --task mihur:ontario-9:audit
```

Its manifest digest, original result hashes and one-test/one-review/300-second proposed
scope are in `evidence/mihur-recovery-disposition-20261029.json`. Its state is
`HOLD_UNKNOWN_FAILURE_STAGE`; `authorized` and `executable` are false. No execute or
authorize CLI is supplied. The operator must resolve the missing evidence or explicitly
approve a bounded diagnostic continuation retaining that uncertainty, referencing the
manifest digest. This does **not** authorize resetting original counters, clearing the
parent blocker, granting specialist approvals, code changes or activation. A trusted
controller must admit that narrowly scoped authorization before any recovery execution.
No such authorization has been received. The conditional infrastructure-only recovery
criterion has not been met, so no new execution budget has been granted.

The corrected Seatbelt adapter genuinely ran the two fixed Mihur test files again:
22 passed; credential-read, outside-write and network denial probes passed. The new
report is `evidence/confined-mihur-operational-recheck-20261029.json`; older reports
were not overwritten. It is diagnostic evidence, not an exhausted queue redelivery,
Codex identity, authenticated approval or successful live pipeline transition.

## macOS with Codex Desktop: minimal safe local procedure

Native macOS protected-controller execution is **not supported by this launcher**.
Its protected path requires Linux namespaces and distinct UIDs; a Desktop assignment
or read-only model session is not an authenticated controller boundary. macOS
Seatbelt confines the fixed pytest subprocess, not the same-user Director or its
Codex app-server process. A same-user process can access the ledger, control socket,
configuration and sign-in; chmod0600 does not protect against that identity. Do not
put GitHub write credentials/webhook secrets into this local diagnostic environment.
Resource limits on a shared Mac are also not a service cgroup boundary.

For local diagnostics only, use a manually opened Terminal associated with this
workspace (no launchd job, cron or hourly automation):

1. Verify trusted, pinned controller source and Codex sign-in. Preserve the single
   existing ledger; do not seed a second pilot queue. Back it up with SQLite's backup
   API while the owner is stopped, keeping database/WAL state consistent.
2. Unset `MIHUR_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN` and
   `DIRECTOR_WEBHOOK_SECRET` in that terminal. Never copy their values into prompts.
3. Inspect status and the recovery disposition. The provided diagnostic configuration
   enrolls no new tasks; persisted plans remain present and their counters still govern.
4. A local bounded diagnostic service can be started with:

```bash
python3 -m director.dispatcher --db director.sqlite start \
  --local-evidence-only --config config/controller/macos-diagnostic.json
python3 -m director.dispatcher --db director.sqlite status
python3 -m director.dispatcher --db director.sqlite pause
python3 -m director.dispatcher --db director.sqlite resume
python3 -m director.dispatcher --db director.sqlite stop
```

These commands do not admit the exhausted pilot, configure the fixed pytest adapter,
or make the controller protected. Start may immediately stop because the persisted
turn budget is exhausted. Do not increase limits to bypass it. For an authorized
confined diagnostic, the operator must run the trusted fixed adapter from an outer
context permitting Seatbelt creation, after denial probes pass; never fall back to
unconfined product execution when Desktop's outer sandbox rejects nested confinement.

For a genuinely protected **local** controller on a Mac, provision a dedicated Linux
VM and use the Linux configuration below inside it. Keep controller secrets and the
ledger inside the guest, separate from shared host folders. Desktop can remain the
operator interface; workers and the controller must run under the guest identities.
No VM, accounts, provider gateway or service was installed by this PR.

OpenAI documents app-server's thread/turn notification interface and permission
requests, plus OS-specific command sandboxes. These facilities do not authenticate
this project's specialist gates or install its controller:
[App Server](https://learn.chatgpt.com/docs/app-server),
[Agent approvals and security](https://learn.chatgpt.com/docs/agent-approvals-security).

## Prepared Linux service (not installed)

Templates: `config/controller/linux.json` and `config/controller/director.service`.
The controller uses only Python's standard library; `/usr/bin/python3` must be 3.11+.
Install trusted Codex on an accessible system path, not under controller-private HOME.

Operator prerequisites, narrowly scoped to this controller:

- Private root-owned mode0700 `/opt/director`, `/var/lib/director`,
  `/var/lib/director/control` and `/etc/director`. Ledger/config/env files mode0600.
  Copy this reviewed repository to `/opt/director`, the existing ledger to
  `/var/lib/director/director.sqlite`, and the JSON to `/etc/director/dispatcher.json`.
- Distinct unprivileged worker UID/GID2001. Root-owned stable ancestors, with
  `/srv/director-workers/job` and `/srv/director-workers/temp` worker-owned mode0700.
  Worker HOME and CODEX_HOME live under temp; provision separate worker sign-in,
  never copy controller authentication. Product source clones/worktrees stay in job.
- Root-owned trusted `/usr/bin/bwrap`, user namespaces available. Actual protected
  file coverage and dropped-UID access probes must pass. Do not remove preflight.
- A reviewed restricted provider gateway/namespace launch policy. The supplied
  launcher uses `--unshare-all` and has **no provider network path**. This remains a
  hard live-Codex blocker; the template is not ready to enable. It also shares one
  worker UID/job root: per-job OS protection from sibling workers is not established.
  Git worktrees and read-only reviewer tools alone do not establish that protection.
- Controller-only `/etc/director/controller.env` with least-privilege GitHub read
  token and a fresh webhook secret (32+ bytes). Configure separate numeric reviewer
  IDs for all six specialist gates; authors/contributors remain ineligible.
- HTTPS proxy to loopback8787, subscribed PR/review/check/workflow events, with
  GitHub repository webhook administration. Independently verify branch protections
  and rulesets; this connector gets403 for branch-protection administration reads.

The JSON has **empty tasks** and does not opt in new work. Existing event plans in the
copied ledger still require the boundary preflight. Enroll only operator-reviewed
existing task IDs and preserve policy/counters. The service template caps three
workers, memory2GiB, CPU200%, tasks96 and one900-second run; systemd kills the whole
cgroup on stop/OOM. `Restart=no` prevents an unlimited service restart/budget loop.
An always-on host is appropriate, but this bounded service deliberately requires
operator budget renewal after exhaustion; it is not an always-running listener.
It is prepared configuration, not a deployed continuous controller.

After all prerequisites are independently verified, the operator may install the
unit and start one bounded run. Use existing pause/resume/status/stop controls with
`--db /var/lib/director/director.sqlite --socket /var/lib/director/control/director.sock`.
Test shutdown and restart on the provisioned host before unattended use. Returned
results replay idempotently; uncertain live starts quarantine rather than blindly
repeat. No service installation or broad infrastructure permission is requested here.

## Merge and release gates

Independent Codex engineering/security findings are development evidence. GitHub
approval must come from a real authorized independent reviewer at the final head.
Required checks and branch protection/ruleset requirements must be verified by an
operator with administration read access; no bypass or self-approval is acceptable.
No merge is performed by an agent under AGENTS.md. Ontario's Math, Pedagogy, Standards,
Security, QA, trusted scoring, accessibility, deployed synthetic journey, staging and
release gates remain separate. Production deployment, learner-data changes and
student activation require explicit scoped owner authorization through a separate
service. Maryland Algebra I and Grade8 remain behind Ontario.
