"""Append-only evidence observations in the existing ledger, never approval authority."""
import json
import time

from .governance import sha, transaction


def observe(db, *, event_id, task_id, repository, head, kind, actor, source, payload):
    sha(head)
    if not all(isinstance(v, str) and v.strip() for v in (event_id, task_id, repository, kind, actor, source)):
        raise ValueError('complete observation identity and source required')
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
    values = (event_id, task_id, repository, head, kind, actor, source, encoded)
    with transaction(db):
        old = db.execute('SELECT * FROM evidence_events WHERE event_id=?', (event_id,)).fetchone()
        if old:
            if tuple(old)[:8] != values:
                raise ValueError('conflicting evidence replay')
            return
        db.execute('INSERT INTO evidence_events VALUES (?,?,?,?,?,?,?,?,?)', values + (int(time.time()),))
