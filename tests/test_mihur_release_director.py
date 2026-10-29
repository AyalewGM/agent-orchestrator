import unittest

from director.mihur_release_director import GATES, snapshot


class ReleaseDirectorTests(unittest.TestCase):
    def fake(self, path, token):
        if path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": "a" * 40}}
        if path.endswith("/issues/488"):
            return {"number": 488}
        if "/reviews?" in path:
            return []
        if "/pulls/" in path:
            n = int(path.rsplit("/", 1)[-1])
            return {"number": n, "head": {"sha": str(n).zfill(40)},
                    "state": "open", "merged_at": None, "draft": True,
                    "html_url": f"https://github.com/AyalewGM/ai-tutor/pull/{n}"}
        if "/check-runs?" in path:
            return {"check_runs": [{"name": "CI", "status": "completed", "conclusion": "success"}]}
        raise AssertionError(path)

    def test_green_ci_never_implies_release_or_approval(self):
        data = snapshot(self.fake)
        self.assertFalse(data["release_authorized"])
        self.assertEqual(data["learner_available"], "NOT_VERIFIED")
        self.assertEqual([p["pr"] for p in data["pr_dependencies"]], [501, 505, 511, 514])
        for p in data["pr_dependencies"]:
            self.assertEqual(p["activation"], "NOT_VERIFIED")
            self.assertEqual(set(p["gates"]), set(GATES))
            self.assertTrue(all(s == "UNVERIFIED" for s in p["gates"].values()))
            self.assertEqual(p["checks"][0]["conclusion"], "success")

    def test_missing_head_ref_fails_closed(self):
        def missing(path, token):
            if path.endswith("/git/ref/heads/main"):
                return {}
            return self.fake(path, token)
        with self.assertRaises(RuntimeError):
            snapshot(missing)


if __name__ == "__main__":
    unittest.main()

class EvidenceIntegrityTests(unittest.TestCase):
    fake = ReleaseDirectorTests.fake
    def test_wrong_head_and_missing_check_never_pass(self):
        data = snapshot(self.fake)
        self.assertTrue(all(not p['configured_checks_passed'] for p in data['pr_dependencies']))
        self.assertTrue(all(not p['ci_policy_verified'] for p in data['pr_dependencies']))

    def test_moving_pr_head_aborts_snapshot(self):
        seen = set()
        def fetch(path, token):
            result = self.fake(path, token)
            if '/pulls/' in path and '/reviews?' not in path:
                if path in seen:
                    result['head']['sha'] = 'f' * 40
                seen.add(path)
            return result
        with self.assertRaises(RuntimeError):
            snapshot(fetch)

    def test_truncated_checks_never_pass(self):
        def fetch(path, token):
            if '/check-runs?' in path:
                return {'total_count': 101, 'check_runs': []}
            return self.fake(path, token)
        data = snapshot(fetch)
        self.assertTrue(all(not p['checks_complete'] for p in data['pr_dependencies']))
