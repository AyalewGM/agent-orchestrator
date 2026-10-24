"""SQLite development coordinator; not a production approval or execution engine."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

STAGES = ("ontario-9", "maryland-algebra-1", "grade-8")
PHASES = ("audit", "content", "integration", "verification")
GATES = ("engineering", "mathematics", "pedagogy", "security", "qa")
MAX_ATTEMPTS = 3


def connect(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(str(Path(path)), isolation_level=None, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY, project TEXT NOT NULL, stage TEXT NOT NULL,
        phase TEXT NOT NULL, priority INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        worker TEXT, lease_until INTEGER, completed_at INTEGER
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS approvals (
        task_id TEXT NOT NULL REFERENCES tasks(id),
        gate TEXT NOT NULL, reviewer TEXT NOT NULL, reviewed_at INTEGER NOT NULL,
        PRIMARY KEY(task_id,gate)
    )""")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def seed_mihur(db: sqlite3.Connection) -> int:
    count = 0
    with db:
        for stage_index, stage in enumerate(STAGES):
            for phase_index, phase in enumerate(PHASES):
                tid = f"mihur:{stage}:{phase}"
                before = db.total_changes
                db.execute("""INSERT OR IGNORE INTO tasks (id, project, stage, phase, priority)
                    VALUES (?, 'mihur', ?, ?, ?)""",
                    (tid, stage, phase, stage_index * 100 + phase_index))
                count += db.total_changes - before
    return count


def claim(db: sqlite3.Connection, worker: str, now: int | None = None, lease_seconds: int = 900) -> str | None:
    if not worker.strip() or lease_seconds <= 0:
        raise ValueError("worker and positive lease_seconds are required")
    current = int(time.time()) if now is None else now
    db.execute("BEGIN IMMEDIATE")
    try:
        # Enforce both sequential phases and sequential curriculum stages.
        row = db.execute("""SELECT t.id FROM tasks t
            WHERE t.status IN ('pending','running') AND t.attempts < ?
            AND (t.status='pending' OR t.lease_until <= ?)
            AND NOT EXISTS (
                SELECT 1 FROM tasks prior WHERE prior.project=t.project
                AND prior.priority<t.priority AND (
                    prior.status!='completed' OR
                    (SELECT count(*) FROM approvals a WHERE a.task_id=prior.id) < ?
                )
            )
            ORDER BY t.priority, t.id LIMIT 1""", (MAX_ATTEMPTS, current, len(GATES))).fetchone()
        if row is None:
            db.execute("COMMIT")
            return None
        db.execute("""UPDATE tasks SET status='running', attempts=attempts+1,
            worker=?, lease_until=? WHERE id=?""",
            (worker, current + lease_seconds, row["id"]))
        db.execute("COMMIT")
        return str(row["id"])
    except BaseException:
        db.execute("ROLLBACK")
        raise


def complete(db: sqlite3.Connection, task_id: str, worker: str, now: int | None = None) -> None:
    current = int(time.time()) if now is None else now
    with db:
        result = db.execute("""UPDATE tasks SET status='completed', completed_at=?,
            lease_until=NULL WHERE id=? AND status='running'
            AND worker=? AND lease_until>?""", (current, task_id, worker, current))
        if result.rowcount != 1:
            raise ValueError("task not owned by worker, lease expired, or not running")


def approve(db: sqlite3.Connection, task_id: str, gate: str, reviewer: str, now: int | None = None) -> None:
    if gate not in GATES or not reviewer.strip():
        raise ValueError("valid gate and reviewer required")
    current = int(time.time()) if now is None else now
    with db:
        task = db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if task is None or task["status"] != "completed":
            raise ValueError("cannot approve incomplete task")
        db.execute("""INSERT INTO approvals(task_id,gate,reviewer,reviewed_at)
            VALUES(?,?,?,?) ON CONFLICT(task_id,gate) DO UPDATE
            SET reviewer=excluded.reviewer,reviewed_at=excluded.reviewed_at""",
            (task_id, gate, reviewer, current))


def status(db: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in db.execute("""SELECT t.id,t.status,t.attempts,t.worker,
        (SELECT count(*) FROM approvals a WHERE a.task_id=t.id) AS gates
        FROM tasks t ORDER BY t.priority,t.id""")]
