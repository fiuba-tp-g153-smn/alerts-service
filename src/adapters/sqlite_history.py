"""SQLite-backed repository for recording layer refresh job history."""

import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from ports.history_repository import IHistoryRepository


class SqliteHistoryRepository(IHistoryRepository):
    """Persists layer refresh job run records in a local SQLite database."""

    def __init__(self, db_path: str):
        """Initialise with the path to the SQLite database file."""
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Create the job_runs table if it does not already exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS job_runs (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status       TEXT NOT NULL,
                    files        TEXT,
                    duration_sec REAL,
                    error        TEXT
                )
                """
            )
            conn.commit()

    def record_run(
        self,
        status: str,
        files: Optional[List[str]] = None,
        duration_sec: Optional[float] = None,
        error: Optional[str] = None,
    ) -> None:
        """Insert a new job run record into the database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO job_runs (run_at, status, files, duration_sec, error)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    datetime.utcnow().isoformat(),
                    status,
                    json.dumps(files) if files is not None else None,
                    duration_sec,
                    error,
                ),
            )
            conn.commit()

    def get_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return the most recent job run records ordered by newest first."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM job_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        result = []
        for row in rows:
            entry = dict(row)
            if entry.get("files"):
                entry["files"] = json.loads(entry["files"])
            result.append(entry)
        return result
