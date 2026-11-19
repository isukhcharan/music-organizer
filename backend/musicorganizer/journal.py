"""SQLite operation journal: every file operation is recorded so it can be audited and undone."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    op_type TEXT NOT NULL,          -- move | sidecar
    source TEXT NOT NULL,
    dest TEXT NOT NULL,
    status TEXT NOT NULL,           -- pending | done | failed | undone | undo_skipped
    error TEXT DEFAULT '',
    fields_written TEXT DEFAULT '', -- comma-separated tags written to the moved copy
    tags_before TEXT DEFAULT '',    -- JSON {field: [old values]} for exactly those fields, so undo can restore them
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ops_job ON operations(job_id);
"""


class Journal:
    def __init__(self, db_path: Path | str):
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        # Databases created before tags_before existed: add the column in place.
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(operations)")}
        if "tags_before" not in cols:
            self.db.execute("ALTER TABLE operations ADD COLUMN tags_before TEXT DEFAULT ''")
            self.db.commit()

    @staticmethod
    def new_job_id() -> str:
        return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]

    def begin(self, job_id: str, op_type: str, source: Path, dest: Path) -> int:
        now = time.time()
        cur = self.db.execute(
            "INSERT INTO operations(job_id, op_type, source, dest, status, created_at, updated_at) "
            "VALUES (?,?,?,?, 'pending', ?, ?)", (job_id, op_type, str(source), str(dest), now, now))
        self.db.commit()
        return cur.lastrowid

    def finish(self, op_id: int, status: str, error: str = "", fields_written: list[str] | None = None,
               tags_before: dict | None = None) -> None:
        self.db.execute(
            "UPDATE operations SET status=?, error=?, fields_written=?, tags_before=?, updated_at=? WHERE id=?",
            (status, error, ",".join(fields_written or []), json.dumps(tags_before or {}, ensure_ascii=False),
             time.time(), op_id))
        self.db.commit()

    def operations(self, job_id: str) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM operations WHERE job_id=? ORDER BY id", (job_id,)).fetchall()

    def close(self) -> None:
        self.db.close()
