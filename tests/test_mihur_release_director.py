import unittest

from director.mihur_release_director import GATES, snapshot


class ReleaseDirectorTests(unittest.TestCase):
    def fake(self, path, token):
        if path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": "a" * 40}}
        if path.endswith("/issues/488"):
            return {"number": 488}
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
