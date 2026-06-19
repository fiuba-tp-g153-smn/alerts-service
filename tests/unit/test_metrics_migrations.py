"""Unit tests for the two local Alembic chains (job store + processor metrics)."""

import sqlite3
from pathlib import Path

from db.migrate import run_job_migrations, run_metrics_migrations


def _tables(db: Path) -> set:
    conn = sqlite3.connect(db)
    try:
        return {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()


def _version(db: Path) -> str:
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        conn.close()


# ── Job-history chain ─────────────────────────────────────────────────────────


def test_job_migrations_create_alert_jobs_and_stamp_head(tmp_path):
    db = tmp_path / "jobs.sqlite"
    run_job_migrations(db)

    assert {"alert_jobs", "alembic_version"} <= _tables(db)
    assert _version(db) == "jobs_0001"

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        cols = {row[1] for row in conn.execute("PRAGMA table_info(alert_jobs)")}
        assert {
            "filter_ms",
            "persist_ms",
            "gif_area_filename",
            "gif_gral_filename",
            "error_message",
            "alert_id",
        } <= cols
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(alert_jobs)")}
        assert "ix_alert_jobs_job_id" in indexes
    finally:
        conn.close()


def test_job_migrations_are_idempotent(tmp_path):
    db = tmp_path / "jobs.sqlite"
    run_job_migrations(db)
    run_job_migrations(db)  # must not raise on the already-migrated DB
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM alembic_version").fetchone()[0] == 1
    finally:
        conn.close()


# ── Processor-metrics chain ───────────────────────────────────────────────────


def test_metrics_migrations_head_drops_alert_jobs(tmp_path):
    db = tmp_path / "metrics.sqlite"
    run_metrics_migrations(db)

    tables = _tables(db)
    assert {"processor_samples", "alembic_version"} <= tables
    assert "alert_jobs" not in tables  # dropped by metrics_0006 (moved to job store)
    assert _version(db) == "metrics_0006"


def test_metrics_migrations_are_idempotent(tmp_path):
    db = tmp_path / "metrics.sqlite"
    run_metrics_migrations(db)
    run_metrics_migrations(db)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM alembic_version").fetchone()[0] == 1
    finally:
        conn.close()
