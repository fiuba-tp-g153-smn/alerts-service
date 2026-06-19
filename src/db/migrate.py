"""Apply Alembic migrations to the alerts-service local SQLite databases.

There are two independent Alembic histories, each living next to this file so they
ship with the code via ``COPY ./src /app`` (no extra Docker step):

* ``job_migrations``   → ``jobs.sqlite``   (durable job history/status; always on)
* ``metrics_migrations`` → ``metrics.sqlite`` (processor telemetry; optional)

Each config is built programmatically (rather than from an ``alembic.ini``) so the
script location resolves the same way in-container (source flattened into ``/app``)
and in tests (``pythonpath=src``), and never collides with the MySQL Alembic env
used by the entrypoint. The connection URL is injected from runtime config.
"""

import fcntl
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

from settings import Settings

# Migration packages sit next to this file; resolving relative to __file__ keeps
# them correct in-container (/app/db/...) and in tests (src/db/...).
_DB_DIR = Path(__file__).resolve().parent
_JOB_SCRIPT_LOCATION = _DB_DIR / "job_migrations"
_METRICS_SCRIPT_LOCATION = _DB_DIR / "metrics_migrations"


def _enable_wal(db_path: Path) -> None:
    """Persist WAL journal mode on the file (autocommit; never inside a txn)."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    finally:
        conn.close()


def _config(script_location: Path, db_path: Path) -> AlembicConfig:
    """Build an Alembic config for a SQLite DB with the URL injected."""
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(script_location))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.resolve()}")
    return cfg


def run_migrations(script_location: Path, db_path: Path) -> None:
    """Upgrade ``db_path`` to ``head`` using ``script_location``'s chain."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(_config(script_location, db_path), "head")
    _enable_wal(db_path)


def run_job_migrations(db_path: Path) -> None:
    """Upgrade the job-history database to head."""
    run_migrations(_JOB_SCRIPT_LOCATION, db_path)


def run_metrics_migrations(db_path: Path) -> None:
    """Upgrade the processor-metrics database to head."""
    run_migrations(_METRICS_SCRIPT_LOCATION, db_path)


def _ensure(db_path: Path, script_location: Path, lock_name: str) -> None:
    """Apply a chain at startup, serialized across processes via a POSIX flock.

    The lock guarantees only one process migrates at a time; the rest block briefly
    and then ``upgrade head`` no-ops at the stamped version. Race-free because
    SQLite already pins every process to the same host and local volume.
    """
    lock_path = db_path.parent / lock_name
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)  # released when the fd closes
        run_migrations(script_location, db_path)


def ensure_job_migrations(settings: Settings) -> None:
    """Migrate the job-history DB at process startup (always on)."""
    _ensure(
        Path(settings.jobs_db_path), _JOB_SCRIPT_LOCATION, ".jobs_migrate.lock"
    )


def ensure_metrics_migrations(settings: Settings) -> None:
    """Migrate the processor-metrics DB at process startup (when metrics enabled)."""
    _ensure(
        Path(settings.metrics_db_path),
        _METRICS_SCRIPT_LOCATION,
        ".metrics_migrate.lock",
    )
