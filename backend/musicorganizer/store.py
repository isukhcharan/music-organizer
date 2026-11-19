"""SQLite persistence for jobs, per-track results and manual decisions.

Lets a scan survive restarts: matching results and decisions are saved as they are produced, so work
can resume without re-querying the provider. Uses the same database file as the operation journal.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

REVIEW_STATUSES = ("REVIEW", "REVIEW_RELEASE_CONFLICT", "REVIEW_DUPLICATE", "NO_RESULT", "ERROR", "APPLY_ERROR")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    input_dir TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    state TEXT NOT NULL,            -- scanning | scanned | applied
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tracks (
    job_id TEXT NOT NULL REFERENCES jobs(id),
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    row_json TEXT NOT NULL,
    PRIMARY KEY (job_id, source)
);
CREATE INDEX IF NOT EXISTS idx_tracks_status ON tracks(job_id, status);
CREATE TABLE IF NOT EXISTS decisions (
    job_id TEXT NOT NULL,
    source TEXT NOT NULL,
    candidate_json TEXT NOT NULL,
    choices_json TEXT NOT NULL,
    decided_at REAL NOT NULL,
    applied INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (job_id, source)
);
"""


class Store:
    def __init__(self, db_path: Path | str):
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # jobs
    def create_job(self, job_id: str, input_dir: Path, output_dir: Path) -> None:
        now = time.time()
        self.db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?)",
                        (job_id, str(input_dir), str(output_dir), "scanning", now, now))
        self.db.commit()

    def set_state(self, job_id: str, state: str) -> None:
        self.db.execute("UPDATE jobs SET state=?, updated_at=? WHERE id=?", (state, time.time(), job_id))
        self.db.commit()

    def get_job(self, job_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(r) if r else None

    def list_jobs(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM jobs ORDER BY created_at DESC")]

    # tracks
    def save_rows(self, job_id: str, rows: list[dict]) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO tracks(job_id, source, status, row_json) VALUES (?,?,?,?)",
            [(job_id, r["source"], r.get("status", ""), json.dumps(r, ensure_ascii=False)) for r in rows])
        self.db.commit()

    def load_rows(self, job_id: str, statuses: tuple[str, ...] | None = None) -> list[dict]:
        if statuses:
            q = f"SELECT row_json FROM tracks WHERE job_id=? AND status IN ({','.join('?' * len(statuses))}) ORDER BY source"
            cur = self.db.execute(q, (job_id, *statuses))
        else:
            cur = self.db.execute("SELECT row_json FROM tracks WHERE job_id=? ORDER BY source", (job_id,))
        return [json.loads(r["row_json"]) for r in cur]

    def counts(self, job_id: str) -> dict[str, int]:
        cur = self.db.execute("SELECT status, COUNT(*) n FROM tracks WHERE job_id=? GROUP BY status", (job_id,))
        return {r["status"]: r["n"] for r in cur}

    def pending_review(self, job_id: str) -> list[dict]:
        """Review rows with no saved decision yet."""
        decided = {r["source"] for r in self.db.execute("SELECT source FROM decisions WHERE job_id=?", (job_id,))}
        return [r for r in self.load_rows(job_id, REVIEW_STATUSES) if r["source"] not in decided]

    # decisions
    def save_decision(self, job_id: str, source: str, candidate: dict, choices: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?,0)",
                        (job_id, source, json.dumps(candidate, ensure_ascii=False),
                         json.dumps(choices, ensure_ascii=False), time.time()))
        self.db.commit()

    def get_decision(self, job_id: str, source: str) -> dict | None:
        r = self.db.execute("SELECT * FROM decisions WHERE job_id=? AND source=?", (job_id, source)).fetchone()
        if not r:
            return None
        return {"candidate": json.loads(r["candidate_json"]), "choices": json.loads(r["choices_json"]),
                "applied": bool(r["applied"])}

    def mark_applied(self, job_id: str, source: str) -> None:
        self.db.execute("UPDATE decisions SET applied=1 WHERE job_id=? AND source=?", (job_id, source))
        self.db.commit()

    def close(self) -> None:
        self.db.close()
