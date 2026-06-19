"""SQLite-backed store for periodic processor-health samples (telemetry).

Optional (gated by ``metrics_enabled``). Schema owned by Alembic
(``src/db/metrics_migrations``). Holds only ``processor_samples`` — the durable
job history lives in the separate job store.
"""

import asyncio
import logging
import sqlite3
from typing import List, Optional

from adapters._sqlite_store import SqliteStore
from domain.metrics import ProcessorSampleRow
from ports.metrics_repository import IProcessorMetricsRepository

logger = logging.getLogger(__name__)


class SqliteProcessorMetricsRepository(SqliteStore, IProcessorMetricsRepository):
    """Async wrapper over one ``sqlite3.Connection`` persisting processor samples."""

    def __init__(self, db_path: str):
        super().__init__(db_path, "Processor metrics store")

    # ============== Writes ==============

    async def record_sample(  # pylint: disable=too-many-arguments
        self,
        *,
        sampled_at: str,
        queue_depth: int,
        workers: int,
        respawns: int,
        jobs_queued_total: int,
        jobs_done_total: int,
        jobs_failed_total: int,
        pending_alerts: int,
    ) -> None:
        row = (
            sampled_at,
            queue_depth,
            workers,
            respawns,
            jobs_queued_total,
            jobs_done_total,
            jobs_failed_total,
            pending_alerts,
        )
        async with self._access_lock:
            await asyncio.to_thread(self._record_sample_sync, row)

    def _record_sample_sync(self, row: tuple) -> None:
        self._require_conn().execute(
            """
            INSERT INTO processor_samples
                (sampled_at, queue_depth, workers, respawns, jobs_queued_total,
                 jobs_done_total, jobs_failed_total, pending_alerts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )

    async def prune(self, before_iso: str) -> None:
        async with self._access_lock:
            await asyncio.to_thread(self._prune_sync, before_iso)

    def _prune_sync(self, before_iso: str) -> None:
        self._require_conn().execute(
            "DELETE FROM processor_samples WHERE sampled_at < ?", (before_iso,)
        )

    async def prune_to_max_rows(self, max_rows: int) -> int:
        async with self._access_lock:
            return await asyncio.to_thread(self._cap_sync, max_rows)

    def _cap_sync(self, max_rows: int) -> int:
        if max_rows <= 0:
            return 0
        conn = self._require_conn()
        row = conn.execute(
            "SELECT id FROM processor_samples ORDER BY id DESC LIMIT 1 OFFSET ?",
            (max_rows - 1,),
        ).fetchone()
        if row is None:
            return 0
        deleted = conn.execute(
            "DELETE FROM processor_samples WHERE id < ?", (row["id"],)
        ).rowcount
        if deleted:
            logger.info("Pruned %d sample row(s); capped at %d", deleted, max_rows)
        return deleted

    # ============== Reads ==============

    async def get_latest_sample(self) -> Optional[ProcessorSampleRow]:
        async with self._access_lock:
            return await asyncio.to_thread(self._get_latest_sample_sync)

    def _get_latest_sample_sync(self) -> Optional[ProcessorSampleRow]:
        row = (
            self._require_conn()
            .execute("SELECT * FROM processor_samples ORDER BY id DESC LIMIT 1")
            .fetchone()
        )
        return self._to_sample_row(row) if row else None

    async def get_processor_history(self, since_iso: str) -> List[ProcessorSampleRow]:
        async with self._access_lock:
            return await asyncio.to_thread(self._get_processor_history_sync, since_iso)

    def _get_processor_history_sync(self, since_iso: str) -> List[ProcessorSampleRow]:
        rows = (
            self._require_conn()
            .execute(
                "SELECT * FROM processor_samples WHERE sampled_at >= ?"
                " ORDER BY sampled_at",
                (since_iso,),
            )
            .fetchall()
        )
        return [self._to_sample_row(r) for r in rows]

    @staticmethod
    def _to_sample_row(r: sqlite3.Row) -> ProcessorSampleRow:
        return ProcessorSampleRow(
            sampled_at=str(r["sampled_at"]),
            queue_depth=int(r["queue_depth"]),
            workers=int(r["workers"]),
            respawns=int(r["respawns"]),
            jobs_queued_total=int(r["jobs_queued_total"]),
            jobs_done_total=int(r["jobs_done_total"]),
            jobs_failed_total=int(r["jobs_failed_total"]),
            pending_alerts=int(r["pending_alerts"]),
        )
