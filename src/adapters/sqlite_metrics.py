"""SQLite-backed store for alert-generation metrics.

Mirrors the data-service metrics store: a single ``sqlite3.Connection`` in WAL
mode, all access serialized by ``_access_lock`` and offloaded via
``asyncio.to_thread`` so the event loop never blocks. The schema is owned by
Alembic (``src/db/metrics_migrations``) and applied at startup by
``db.migrate.ensure_migrations`` — this store only opens the migrated DB.
"""

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import List, Optional

from domain.metrics import (
    JobHistoryBucket,
    JobRow,
    JobsAggregate,
    ProcessorSampleRow,
)
from ports.metrics_repository import IAlertMetricsRepository

logger = logging.getLogger(__name__)

# Capped independently by ``prune_to_max_rows``; each has an autoincrement ``id``
# PK, so the newest rows always have the highest ids.
_CAPPED_TABLES = ("alert_jobs", "processor_samples")


class SqliteAlertMetricsRepository(IAlertMetricsRepository):
    """Async wrapper over a single ``sqlite3.Connection`` persisting metrics."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._access_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Open the SQLite connection (schema is owned by Alembic migrations)."""
        if self._conn is not None:
            return
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._open)
        logger.info("Alert metrics store opened at %s", self._db_path)

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
        logger.info("Alert metrics store closed")

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("SqliteAlertMetricsRepository not connected")
        return self._conn

    # ============== Writes ==============

    async def record_job(  # pylint: disable=too-many-arguments
        self,
        *,
        job_id: str,
        phenomenon_code: int,
        finished_at: str,
        duration_ms: int,
        outcome: str,
        error_code: Optional[str],
        affected_departments: Optional[int],
        intersection_ms: Optional[int],
        filter_ms: Optional[int],
        render_ms: Optional[int],
        persist_ms: Optional[int],
        polygon_vertices: Optional[int],
    ) -> None:
        row = (
            job_id,
            phenomenon_code,
            finished_at,
            duration_ms,
            outcome,
            error_code,
            affected_departments,
            intersection_ms,
            filter_ms,
            render_ms,
            persist_ms,
            polygon_vertices,
        )
        async with self._access_lock:
            await asyncio.to_thread(self._record_job_sync, row)

    def _record_job_sync(self, row: tuple) -> None:
        self._require_conn().execute(
            """
            INSERT INTO alert_jobs
                (job_id, phenomenon_code, finished_at, duration_ms, outcome,
                 error_code, affected_departments, intersection_ms, filter_ms,
                 render_ms, persist_ms, polygon_vertices)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )

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
        conn = self._require_conn()
        conn.execute("DELETE FROM alert_jobs WHERE finished_at < ?", (before_iso,))
        conn.execute(
            "DELETE FROM processor_samples WHERE sampled_at < ?", (before_iso,)
        )

    async def prune_to_max_rows(self, max_rows: int) -> int:
        async with self._access_lock:
            return await asyncio.to_thread(self._prune_to_max_rows_sync, max_rows)

    def _prune_to_max_rows_sync(self, max_rows: int) -> int:
        if max_rows <= 0:
            return 0
        conn = self._require_conn()
        total = 0
        for table in _CAPPED_TABLES:
            row = conn.execute(
                f"SELECT id FROM {table} ORDER BY id DESC LIMIT 1 OFFSET ?",
                (max_rows - 1,),
            ).fetchone()
            if row is None:
                continue
            total += conn.execute(
                f"DELETE FROM {table} WHERE id < ?", (row["id"],)
            ).rowcount
        if total:
            logger.info(
                "Pruned %d metrics row(s); capped each table at %d", total, max_rows
            )
        return total

    # ============== Reads ==============

    async def get_summary(self, since_iso: str) -> JobsAggregate:
        async with self._access_lock:
            return await asyncio.to_thread(self._get_summary_sync, since_iso)

    def _get_summary_sync(self, since_iso: str) -> JobsAggregate:
        conn = self._require_conn()
        counts = {
            str(r["outcome"]): int(r["c"])
            for r in conn.execute(
                "SELECT outcome, COUNT(*) AS c FROM alert_jobs"
                " WHERE finished_at >= ? GROUP BY outcome",
                (since_iso,),
            ).fetchall()
        }
        done = counts.get("done", 0)
        failed = counts.get("failed", 0)
        breakdown = {
            str(r["error_code"] or "unknown"): int(r["c"])
            for r in conn.execute(
                "SELECT error_code, COUNT(*) AS c FROM alert_jobs"
                " WHERE outcome = 'failed' AND finished_at >= ? GROUP BY error_code",
                (since_iso,),
            ).fetchall()
        }
        agg = conn.execute(
            "SELECT AVG(duration_ms) AS d, AVG(intersection_ms) AS i,"
            " AVG(filter_ms) AS f, AVG(render_ms) AS r, AVG(persist_ms) AS p"
            " FROM alert_jobs WHERE outcome = 'done' AND finished_at >= ?",
            (since_iso,),
        ).fetchone()
        return JobsAggregate(
            total=done + failed,
            done=done,
            failed=failed,
            failure_breakdown=breakdown,
            avg_duration_ms=float(agg["d"] or 0.0),
            p95_duration_ms=self._p95_done_duration(conn, since_iso),
            avg_intersection_ms=float(agg["i"] or 0.0),
            avg_filter_ms=float(agg["f"] or 0.0),
            avg_render_ms=float(agg["r"] or 0.0),
            avg_persist_ms=float(agg["p"] or 0.0),
        )

    @staticmethod
    def _p95_done_duration(conn: sqlite3.Connection, since_iso: str) -> int:
        """95th-percentile done-job duration via an ORDER BY + OFFSET lookup."""
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM alert_jobs WHERE outcome = 'done'"
            " AND finished_at >= ? AND duration_ms IS NOT NULL",
            (since_iso,),
        ).fetchone()["c"]
        if not count:
            return 0
        offset = min((int(count) * 95) // 100, int(count) - 1)
        row = conn.execute(
            "SELECT duration_ms FROM alert_jobs WHERE outcome = 'done'"
            " AND finished_at >= ? AND duration_ms IS NOT NULL"
            " ORDER BY duration_ms LIMIT 1 OFFSET ?",
            (since_iso, offset),
        ).fetchone()
        return int(row["duration_ms"]) if row else 0

    async def get_recent_jobs(self, since_iso: str, limit: int) -> List[JobRow]:
        async with self._access_lock:
            return await asyncio.to_thread(self._get_recent_jobs_sync, since_iso, limit)

    def _get_recent_jobs_sync(self, since_iso: str, limit: int) -> List[JobRow]:
        sql = (
            "SELECT job_id, phenomenon_code, finished_at, duration_ms, outcome,"
            " error_code, affected_departments, intersection_ms, filter_ms,"
            " render_ms, persist_ms, polygon_vertices FROM alert_jobs"
            " WHERE finished_at >= ? ORDER BY finished_at DESC"
        )
        params: list = [since_iso]
        if limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._require_conn().execute(sql, params).fetchall()
        return [self._to_job_row(r) for r in rows]

    async def get_jobs_history(
        self, since_iso: str, bucket: str
    ) -> List[JobHistoryBucket]:
        async with self._access_lock:
            return await asyncio.to_thread(
                self._get_jobs_history_sync, since_iso, bucket
            )

    def _get_jobs_history_sync(
        self, since_iso: str, bucket: str
    ) -> List[JobHistoryBucket]:
        # ISO-8601 UTC strings bucket by prefix length: 10 = "YYYY-MM-DD" (day),
        # 13 = "YYYY-MM-DDTHH" (hour).
        prefix_len = 10 if bucket == "day" else 13
        rows = (
            self._require_conn()
            .execute(
                "SELECT substr(finished_at, 1, ?) AS bucket,"
                " SUM(CASE WHEN outcome = 'done' THEN 1 ELSE 0 END) AS done,"
                " SUM(CASE WHEN outcome = 'failed' THEN 1 ELSE 0 END) AS failed,"
                " AVG(duration_ms) AS avg_duration_ms FROM alert_jobs"
                " WHERE finished_at >= ? GROUP BY bucket ORDER BY bucket",
                (prefix_len, since_iso),
            )
            .fetchall()
        )
        return [
            JobHistoryBucket(
                bucket=str(r["bucket"]),
                done=int(r["done"] or 0),
                failed=int(r["failed"] or 0),
                avg_duration_ms=float(r["avg_duration_ms"] or 0.0),
            )
            for r in rows
        ]

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

    # ============== Row mappers ==============

    @staticmethod
    def _to_job_row(r: sqlite3.Row) -> JobRow:
        def _opt(key: str) -> Optional[int]:
            value = r[key]
            return int(value) if value is not None else None

        return JobRow(
            job_id=str(r["job_id"]),
            phenomenon_code=int(r["phenomenon_code"]),
            finished_at=str(r["finished_at"]),
            duration_ms=int(r["duration_ms"]),
            outcome=str(r["outcome"]),
            error_code=r["error_code"],
            affected_departments=_opt("affected_departments"),
            intersection_ms=_opt("intersection_ms"),
            filter_ms=_opt("filter_ms"),
            render_ms=_opt("render_ms"),
            persist_ms=_opt("persist_ms"),
            polygon_vertices=_opt("polygon_vertices"),
        )

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
