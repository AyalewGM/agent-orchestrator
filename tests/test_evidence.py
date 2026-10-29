import tempfile
from pathlib import Path
import unittest

from director.core import connect
from director.evidence import observe
from director.governance import initialize, report


class EvidenceTests(unittest.TestCase):
    def test_observations_survive_restart_but_never_become_approvals(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'ledger.sqlite')
            db = connect(path)
            initialize(db)
            values = dict(event_id='native-review-1', task_id='mihur:ontario-9:audit',
                          repository='AyalewGM/ai-tutor', head='a' * 40,
                          kind='native_review', actor='/root/real-reviewer',
                          source='local-session://review-1', payload={'verdict': 'accept', 'scope': 'bounded source review'})
            observe(db, **values)
            observe(db, **values)
            with self.assertRaises(ValueError):
                observe(db, **dict(values, payload={'verdict': 'reject'}))
            db.close()
            db = connect(path)
            data = report(db)
            self.assertEqual(len(data['evidence_observations']), 1)
            self.assertEqual(data['verified_independent_approvals'], [])
            self.assertEqual(data['approval_integrity'], 'UNVERIFIED_SHARED_HOST')
            self.assertFalse(data['merge_allowed'])
            self.assertFalse(data['release_authorized'])
            self.assertEqual(db.execute('SELECT count(*) FROM accepted').fetchone()[0], 0)
            db.close()
