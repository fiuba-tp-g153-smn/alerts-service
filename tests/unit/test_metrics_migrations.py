"""Unit tests for the metrics-database Alembic migrations."""

import sqlite3
from pathlib import Path

from db.migrate import run_migrations


def _tables(db: Path) -> set:
    conn = sqlite3.connect(db)
    try:
        return {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()


def test_migrations_create_tables_and_stamp_head(tmp_path):
    db = tmp_path / "metrics.sqlite"
    run_migrations(db)

    tables = _tables(db)
    assert {"alert_jobs", "processor_samples", "alembic_version"} <= tables

    conn = sqlite3.connect(db)
    try:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert version == "metrics_0003"
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        cols = {row[1] for row in conn.execute("PRAGMA table_info(alert_jobs)")}
        assert {"filter_ms", "persist_ms"} <= cols
        assert {"gif_area_filename", "gif_gral_filename"} <= cols
    finally:
        conn.close()


def test_migrations_are_idempotent(tmp_path):
    db = tmp_path / "metrics.sqlite"
    run_migrations(db)
    run_migrations(db)  # must not raise on the already-migrated DB

    conn = sqlite3.connect(db)
    try:
        count = conn.execute("SELECT COUNT(*) FROM alembic_version").fetchone()[0]
        assert count == 1
    finally:
        conn.close()
