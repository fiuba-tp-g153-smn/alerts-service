"""Apply Alembic migrations to the alerts-service metrics SQLite database.

The metrics DB is an independent Alembic history living under ``src/db/
metrics_migrations`` (so it ships with the code via ``COPY ./src /app`` and needs
no extra Docker step). The Alembic config is built programmatically here — rather
than from an ``alembic.ini`` section — so the script location resolves the same
way both in the container (source flattened into ``/app``) and in tests
(``pythonpath=src``), and so it never collides with the MySQL Alembic env used by
the entrypoint. The connection URL is injected because the path comes from
runtime config (``settings.metrics_db_path``).
"""

import fcntl
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

from settings import Settings

# metrics_migrations sits next to this file; resolving relative to __file__ keeps
# it correct in-container (/app/db/...) and in tests (src/db/...).
_SCRIPT_LOCATION = Path(__file__).resolve().parent / "metrics_migrations"


def _enable_wal(db_path: Path) -> None:
    """Persist WAL journal mode on the file (autocommit; never inside a txn)."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    finally:
        conn.close()


def _config(db_path: Path) -> AlembicConfig:
    """Build an Alembic config for the metrics DB with the URL injected."""
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(_SCRIPT_LOCATION))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.resolve()}")
    return cfg


def run_migrations(metrics_db_path: Path) -> None:
    """Upgrade the metrics database to ``head``, creating parent dirs as needed."""
    metrics_db_path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(_config(metrics_db_path), "head")
    _enable_wal(metrics_db_path)


def ensure_migrations(settings: Settings) -> None:
    """Apply migrations at process startup, serialized across processes.

    A POSIX ``flock`` on a lockfile next to the DB guarantees only one process
    migrates at a time; the rest block briefly and then ``upgrade head`` no-ops at
    the stamped version. Race-free because SQLite already pins every process to the
    same host and local volume.
    """
    metrics_db_path = Path(settings.metrics_db_path)
    lock_path = metrics_db_path.parent / ".metrics_migrate.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)  # released when the fd closes
        run_migrations(metrics_db_path)
