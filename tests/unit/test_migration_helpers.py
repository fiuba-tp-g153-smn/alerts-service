"""Unit tests for the one-time legacy job-history copy."""

import logging
import sqlite3
import types
from pathlib import Path

from db.migrate import run_job_migrations
from migration_helpers import copy_legacy_job_history

_LOG = logging.getLogger("test")


def _settings(tmp_path: Path):
    return types.SimpleNamespace(
        jobs_db_path=str(tmp_path / "jobs.sqlite"),
        metrics_db_path=str(tmp_path / "metrics.sqlite"),
    )


def _legacy_metrics_db(path: str, rows: int) -> None:
    """Create a pre-split metrics.sqlite with an ``alert_jobs`` table + rows."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE alert_jobs (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " job_id TEXT, phenomenon_code INTEGER, finished_at TEXT,"
            " duration_ms INTEGER, outcome TEXT, error_code TEXT,"
            " error_message TEXT, alert_id INTEGER)"
        )
        conn.executemany(
            "INSERT INTO alert_jobs (job_id, phenomenon_code, finished_at,"
            " duration_ms, outcome, alert_id) VALUES (?, ?, ?, ?, ?, ?)",
            [(f"j{i}", 1, f"2026-06-17T10:0{i}:00+00:00", 100, "done", i) for i in range(rows)],
        )
        conn.commit()
    finally:
        conn.close()


def _job_count(path: str) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM alert_jobs").fetchone()[0]
    finally:
        conn.close()


def test_copies_legacy_rows_then_is_idempotent(tmp_path):
    settings = _settings(tmp_path)
    run_job_migrations(Path(settings.jobs_db_path))
    _legacy_metrics_db(settings.metrics_db_path, rows=3)

    assert copy_legacy_job_history(settings, _LOG) == 3
    assert _job_count(settings.jobs_db_path) == 3

    # Idempotent: job store now populated → no re-copy.
    assert copy_legacy_job_history(settings, _LOG) == 0
    assert _job_count(settings.jobs_db_path) == 3


def test_no_legacy_metrics_db_is_a_noop(tmp_path):
    settings = _settings(tmp_path)
    run_job_migrations(Path(settings.jobs_db_path))
    # metrics.sqlite does not exist → nothing to copy.
    assert copy_legacy_job_history(settings, _LOG) == 0
    assert _job_count(settings.jobs_db_path) == 0
