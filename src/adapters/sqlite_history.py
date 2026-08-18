"""SQLite-backed repository for recording layer refresh job history."""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ports.history_repository import IHistoryRepository


class SqliteHistoryRepository(IHistoryRepository):
    """Persists layer refresh job run records in a local SQLite database.

    A single connection is shared across threads (the APScheduler cron writer and
    the API reader), so WAL mode plus a ``threading.Lock`` serialise every access:
    WAL lets a reader and the writer proceed without ``database is locked``, and
    the lock prevents concurrent use of the one ``sqlite3.Connection`` object.
    """

    def __init__(self, db_path: str):
        """Initialise with the path to the SQLite database file."""
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_db()

    def _init_db(self) -> None:
        """Create the job_runs table if it does not already exist."""
        with self._lock:
            self._conn.execute(
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
            self._conn.commit()

    def record_run(
        self,
        status: str,
        files: Optional[List[str]] = None,
        duration_sec: Optional[float] = None,
        error: Optional[str] = None,
    ) -> None:
        """Insert a new job run record into the database."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO job_runs (run_at, status, files, duration_sec, error)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    status,
                    json.dumps(files) if files is not None else None,
                    duration_sec,
                    error,
                ),
            )
            self._conn.commit()

    def get_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return the most recent job run records ordered by newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM job_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        result = []
        for row in rows:
            entry = dict(row)
            if entry.get("files"):
                entry["files"] = json.loads(entry["files"])
            result.append(entry)
        return result

    def close(self) -> None:
        """Close the underlying database connection."""
        with self._lock:
            self._conn.close()
