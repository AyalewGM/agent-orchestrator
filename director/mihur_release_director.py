"""Read-only Mihur engineering director: exact-head release dependency snapshot.

This is a coordinator, not an independent reviewer, release signer or deployer.
GitHub comments/checks are evidence pointers, never automatically approval.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

REPO = "AyalewGM/ai-tutor"
PRIORITIES = ("ontario-grade-9", "maryland-algebra-i", "grade-8")
TRACK = {
    501: ("ontario-grade-9", "authenticated learning journey / signed publisher"),
    505: ("ontario-grade-9", "accessible rational frontend"),
    511: ("ontario-grade-9", "atomic attempt persistence"),
    514: ("ontario-grade-9", "authenticated published learning flow"),
}
GATES = ("math", "pedagogy", "standards", "security", "architecture", "muse_qa",
         "authenticated_journey", "frontend_accessibility", "publisher_signatures",
         "deployment", "learner_selectability")


def fetch_json(path: str, token: str, *, base: str = "https://api.github.com") -> dict | list:
    if not path.startswith("/") or "//" in path or not base.startswith("https://"):
        raise ValueError("invalid request location")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "mihur-release-director/0.1",
               "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(base + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GitHub read failed (HTTP {exc.code})") from exc


def snapshot(fetch=fetch_json, *, token: str = "") -> dict:
    """Fetch current GitHub truth. A missing review/CI signal is always pending."""
    head = fetch(f"/repos/{REPO}/git/ref/heads/main", token)
    if not isinstance(head, dict) or not head.get("object", {}).get("sha"):
        raise RuntimeError("missing main ref: cannot report release readiness")
    issue = fetch(f"/repos/{REPO}/issues/488", token)
    if not isinstance(issue, dict) or issue.get("number") != 488:
        raise RuntimeError("missing coordination issue")
    results = []
    for number, (course, responsibility) in TRACK.items():
        pr = fetch(f"/repos/{REPO}/pulls/{number}", token)
        if not isinstance(pr, dict) or pr.get("number") != number:
            raise RuntimeError(f"missing pull request {number}")
        commit_sha = pr["head"]["sha"]
        if not re.fullmatch('[0-9a-f]{40}', commit_sha):
            raise RuntimeError('invalid PR head SHA')
        checks = fetch(f"/repos/{REPO}/commits/{commit_sha}/check-runs?per_page=100", token)
        raw_checks = checks.get("check_runs", []) if isinstance(checks, dict) else []
        ci = [{"id": c.get("id"), "name": c.get("name"), "status": c.get("status"),
               "conclusion": c.get("conclusion"), "head_sha": c.get("head_sha"),
               "url": c.get("html_url"), "app_id": c.get("app", {}).get("id")}
              for c in raw_checks]
        reviews = []
        for page in range(1, 11):
            batch = fetch(f"/repos/{REPO}/pulls/{number}/reviews?per_page=100&page={page}", token)
            if not isinstance(batch, list):
                raise RuntimeError('invalid review inventory')
            reviews.extend({"id": r.get('id'), "sha": r.get('commit_id'),
                            "subject": r.get('user', {}).get('login'),
                            "verdict": r.get('state'), "url": r.get('html_url')}
                           for r in batch)
            if len(batch) < 100:
                break
        else:
            raise RuntimeError('review inventory exceeds bounded pagination')
        from .policy import projects
        required = projects()['mihur']['required_checks']
        complete_checks = isinstance(checks, dict) and checks.get('total_count', len(ci)) <= 100
        checks_passed = complete_checks and all(
            any(c['name'] == name and c['head_sha'] == commit_sha
                and c['status'] == 'completed' and c['conclusion'] == 'success' for c in ci)
            for name in required)
        # Names alone cannot authenticate the check producer or required-check policy.
        confirmed = fetch(f"/repos/{REPO}/pulls/{number}", token)
        if confirmed['head']['sha'] != commit_sha:
            raise RuntimeError('PR moved during collection; retry a fresh snapshot')
        # Even a successful CI is not independent approval or activation.
        results.append({
            "pr": number, "url": pr.get("html_url"), "course": course,
            "responsibility": responsibility, "sha": commit_sha,
            "state": pr.get("state"), "merged": bool(pr.get("merged_at")),
            "draft": bool(pr.get("draft")), "checks": ci,
            "checks_complete": complete_checks, "configured_checks_passed": checks_passed,
            "ci_policy_verified": False, "required_checks": required, "reviews": reviews,
            "gates": {gate: "UNVERIFIED" for gate in GATES},
            "activation": "NOT_VERIFIED",
            "next_owner": {
                501: "Assessment Backend / Content Publishing + independent Pedagogy",
                505: "Frontend/MVE + Muse accessibility QA",
                511: "Assessment Backend + independent Security/Architecture/DB QA",
                514: "Frontend / Assessment Backend + independent Muse QA",
            }[number],
        })
    return {
        "schema": "MIHUR_RELEASE_LEDGER_SNAPSHOT_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repository": REPO, "coordination_issue": "https://github.com/AyalewGM/ai-tutor/issues/488",
        "main_sha": head["object"]["sha"], "curriculum_priority": list(PRIORITIES),
        "pr_dependencies": results,
        "release_authorized": False, "learner_available": "NOT_VERIFIED",
        "note": "Read-only evidence inventory. Missing authenticated independent verdict, signed release, staging, seeded/enabled and synthetic learner availability proofs are never inferred.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="mihur-release-snapshot.json")
    args = parser.parse_args()
    report = snapshot(token=os.getenv("MIHUR_GITHUB_TOKEN", ""))
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    print(f"Recorded {len(report['pr_dependencies'])} release dependencies; activation UNVERIFIED")


if __name__ == "__main__":
    main()
