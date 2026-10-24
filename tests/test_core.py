"""Ledger invariants, no external services or student data."""
import tempfile
import unittest
from pathlib import Path

from director.core import GATES, approve, claim, complete, connect, seed_mihur, status


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = connect(str(Path(self.temp.name) / "tasks.sqlite"))
        seed_mihur(self.db)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_idempotent_seed_and_priority(self):
        self.assertEqual(seed_mihur(self.db), 0)
        self.assertEqual(len(status(self.db)), 12)
        self.assertEqual(claim(self.db, "w1", now=10), "mihur:ontario-9:audit")
        self.assertIsNone(claim(self.db, "w2", now=11))

    def test_expiry_recovery_and_owner(self):
        tid = claim(self.db, "w1", now=10, lease_seconds=10)
        with self.assertRaises(ValueError):
            complete(self.db, tid, "w2", now=11)
        self.assertEqual(claim(self.db, "w2", now=20), tid)
        with self.assertRaises(ValueError):
            complete(self.db, tid, "w1", now=21)
        complete(self.db, tid, "w2", now=22)

    def test_block_until_all_review_gates(self):
        tid = claim(self.db, "w1", now=10)
        complete(self.db, tid, "w1", now=11)
        self.assertIsNone(claim(self.db, "w2", now=12))
        for gate in GATES[:-1]:
            approve(self.db, tid, gate, "reviewer", now=12)
        self.assertIsNone(claim(self.db, "w2", now=13))
        approve(self.db, tid, GATES[-1], "reviewer", now=13)
        self.assertEqual(claim(self.db, "w2", now=14), "mihur:ontario-9:content")

    def test_no_approval_for_unfinished_task(self):
        with self.assertRaises(ValueError):
            approve(self.db, "mihur:ontario-9:audit", "qa", "reviewer")


if __name__ == "__main__":
    unittest.main()
