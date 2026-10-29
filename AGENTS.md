# Unified Engineering Director

This repository is the permanent cross-repository coordination layer. Extend
`director.core` and its SQLite ledger; do not add another queue or orchestrator.
Read `docs/ARCHITECTURE_AUDIT.md`, `projects.toml`, and the target repository's
AGENTS.md before implementation. Reuse open integration PRs and canonical families.

Delegate bounded implementation to `.codex/agents/implementation.toml`; delegate
independent reviews to the six named reviewer profiles. Use separate Codex sessions
and isolated Git worktrees for concurrent assignments. No author, contributor, or
Director may supply their own specialist acceptance. Existing Mihur specialty charters
remain authoritative; profile creation does not establish reviewer acceptance.

Use `.agents/skills/mihur-delivery/SKILL.md` for Mihur assignments. Priorities are
Ontario Grade 9 MTH1W, Maryland Algebra I, then Ontario and Maryland Grade 8.
Never infer student availability from generated files, static review, mocks or CI.

Run `python3 -m unittest discover -s tests -v` and `python3 -m compileall -q director tests`.
Use exact main/PR head SHAs and report commands actually executed, counts, CI URLs,
review identities, missing evidence and blockers. Failed review requeues the same
milestone with its existing identity and bounded attempt budget. Never clear a blocker
by inventing evidence or reset attempts to bypass exhaustion.

No agent may merge, deploy production, activate student content, alter sensitive data,
modify learner history, or obtain production credentials. These operations require
explicit scoped owner authorization and a separate authorized service. Local SQLite
review APIs trust their caller; typed role names are never authenticated acceptance.
Worktrees isolate Git changes, not host credentials or processes. Product code/tests
must execute only in a disposable synthetic environment without production access.

Single persistent runner owns SQLite; no network-filesystem/shared Actions-cache DB.
Scheduled GitHub Actions collect read-only reports only. Do not claim an unattended
Codex service, authenticated gateway, deployed integration or independent approval
unless it has actually been provisioned and verified.
