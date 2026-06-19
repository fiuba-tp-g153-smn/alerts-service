"""Shared async SQLite plumbing for the local job-history / metrics stores.

A single ``sqlite3.Connection`` in WAL mode, all access serialized by
``_access_lock`` and offloaded via ``asyncio.to_thread`` so the event loop never
blocks. The schema is owned by Alembic (``src/db/*_migrations``) and applied at
startup; subclasses only open the migrated DB and run queries.
"""

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class SqliteStore:
    """Base async wrapper over one ``sqlite3.Connection`` (schema via migrations)."""

    def __init__(self, db_path: str, name: str):
        self._db_path = db_path
        self._name = name
        self._conn: Optional[sqlite3.Connection] = None
        self._access_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Open the SQLite connection (schema is owned by Alembic migrations)."""
        if self._conn is not None:
            return
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._open)
        logger.info("%s opened at %s", self._name, self._db_path)

    def _open(self) -> None:
        conn = sqlite3.connect(
            self._db_path, check_same_thread=False, isolation_level=None
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn = conn

    async def close(self) -> None:
        """Close the SQLite connection."""
        if self._conn is None:
            return
        await asyncio.to_thread(self._conn.close)
        self._conn = None
        logger.info("%s closed", self._name)

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError(f"{self._name} not connected")
        return self._conn
