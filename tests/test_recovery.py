"""Crash recovery and concurrency invariants for the local coordinator."""
import tempfile
import unittest
from pathlib import Path

from director.core import MAX_ATTEMPTS, claim, complete, connect, seed_mihur, status


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "ledger.sqlite")
        self.first = connect(self.path)
        self.second = connect(self.path)
        seed_mihur(self.first)

    def tearDown(self):
        self.first.close()
        self.second.close()
        self.tmp.cleanup()

    def test_two_connections_cannot_claim_same_live_lease(self):
        tid = claim(self.first, "one", now=100, lease_seconds=30)
        self.assertEqual(tid, "mihur:ontario-9:audit")
        self.assertIsNone(claim(self.second, "two", now=101))
        self.assertEqual(claim(self.second, "two", now=130), tid)

    def test_expired_lease_requires_new_owner(self):
        tid = claim(self.first, "one", now=100, lease_seconds=5)
        self.assertEqual(claim(self.second, "two", now=105), tid)
        with self.assertRaises(ValueError):
            complete(self.first, tid, "one", now=106)
        complete(self.second, tid, "two", now=106)

    def test_bounded_retries_never_progress_silently(self):
        for i in range(MAX_ATTEMPTS):
            self.assertEqual(
                claim(self.first, "worker", now=100 + i * 10, lease_seconds=5),
                "mihur:ontario-9:audit",
            )
        self.assertIsNone(claim(self.second, "worker", now=200))
        row = status(self.first)[0]
        self.assertEqual(row["attempts"], MAX_ATTEMPTS)
        self.assertNotEqual(row["status"], "completed")

    def test_invalid_worker_and_lease(self):
        with self.assertRaises(ValueError):
            claim(self.first, "", now=100)
        with self.assertRaises(ValueError):
            claim(self.first, "worker", now=100, lease_seconds=0)


if __name__ == "__main__":
    unittest.main()
