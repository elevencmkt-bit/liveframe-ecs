"""SQLite ledger: makes the pipeline idempotent and restart-safe.

Each camera file is keyed by the SHA-1 of its content, so a file the camera
re-sends (or a restart mid-batch) is never processed or published twice.
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    sha1        TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    output_name TEXT,
    status      TEXT NOT NULL,          -- review | ready | sent | rejected | skipped | failed | video
    error       TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0,
    next_try    REAL NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    sent_at     REAL
);
CREATE INDEX IF NOT EXISTS files_status ON files(status);
CREATE TABLE IF NOT EXISTS notices (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         REAL NOT NULL,
    count      INTEGER NOT NULL,
    message    TEXT NOT NULL
);
"""


def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Ledger:
    def __init__(self, db: Path):
        db.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db), isolation_level=None, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def get(self, sha1: str):
        return self.conn.execute(
            "SELECT sha1, source_name, output_name, status FROM files WHERE sha1=?",
            (sha1,)).fetchone()

    def add(self, sha1: str, source: str, output: str | None, status: str,
            error: str | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO files(sha1, source_name, output_name, status, error, created_at)"
            " VALUES (?,?,?,?,?,?)", (sha1, source, output, status, error, time.time()))

    def by_output(self, output_name: str):
        return self.conn.execute(
            "SELECT sha1, status FROM files WHERE output_name=?", (output_name,)).fetchone()

    def set_status(self, sha1: str, status: str) -> None:
        # created_at restarts so the upload queue follows the approval order
        self.conn.execute("UPDATE files SET status=?, created_at=? WHERE sha1=?",
                          (status, time.time(), sha1))

    def due_for_upload(self, now: float | None = None):
        now = time.time() if now is None else now
        return self.conn.execute(
            "SELECT sha1, output_name, attempts FROM files WHERE status='ready' AND next_try<=?"
            " ORDER BY created_at", (now,)).fetchall()

    def mark_sent(self, sha1: str) -> None:
        self.conn.execute(
            "UPDATE files SET status='sent', sent_at=?, error=NULL WHERE sha1=?",
            (time.time(), sha1))

    def mark_retry(self, sha1: str, attempts: int, error: str) -> None:
        delay = min(300, 5 * 2 ** attempts)  # 5s, 10s, 20s ... capped at 5 min
        self.conn.execute(
            "UPDATE files SET attempts=?, next_try=?, error=? WHERE sha1=?",
            (attempts + 1, time.time() + delay, error[:500], sha1))

    def count(self, status: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM files WHERE status=?",
                                 (status,)).fetchone()[0]

    def last_notice_at(self) -> float:
        row = self.conn.execute("SELECT MAX(at) FROM notices").fetchone()
        return row[0] or 0.0

    def sent_since(self, t: float) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM files WHERE status='sent' AND sent_at>?", (t,)).fetchone()[0]

    def record_notice(self, count: int, message: str, at: float | None = None) -> None:
        self.conn.execute("INSERT INTO notices(at, count, message) VALUES (?,?,?)",
                          (time.time() if at is None else at, count, message))
