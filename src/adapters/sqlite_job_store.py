"""SQLite-backed durable store for terminal alert-generation jobs (job history).

Always-on (independent of the optional metrics store). Schema owned by Alembic
(``src/db/job_migrations``). Self-prunes on write (time retention + row cap) so it
stays bounded without an external pruner.
"""

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from adapters._sqlite_store import SqliteStore
from domain.metrics import JobHistoryBucket, JobRow, JobsAggregate
from ports.job_store import IJobStore

logger = logging.getLogger(__name__)

# Column list for reading an ``alert_jobs`` row into a ``JobRow`` (order matches
# ``_to_job_row``). Shared by the recent-jobs window query and the by-id lookup.
_JOB_COLUMNS = (
    "job_id, phenomenon_code, finished_at, duration_ms, outcome, error_code,"
    " error_message, alert_id, affected_departments, intersection_ms, filter_ms,"
    " render_ms, persist_ms, polygon_vertices, gif_area_filename, gif_gral_filename"
)


class SqliteJobStore(SqliteStore, IJobStore):
    """Async wrapper over one ``sqlite3.Connection`` persisting job history."""

    def __init__(self, db_path: str, retention_days: int = 0, max_rows: int = 0):
        super().__init__(db_path, "Alert job store")
        self._retention_days = retention_days
        self._max_rows = max_rows

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
        error_message: Optional[str],
        alert_id: Optional[int],
        affected_departments: Optional[int],
        intersection_ms: Optional[int],
        filter_ms: Optional[int],
        render_ms: Optional[int],
        persist_ms: Optional[int],
        polygon_vertices: Optional[int],
        gif_area_filename: Optional[str],
        gif_gral_filename: Optional[str],
    ) -> None:
        row = (
            job_id,
            phenomenon_code,
            finished_at,
            duration_ms,
            outcome,
            error_code,
            error_message,
            alert_id,
            affected_departments,
            intersection_ms,
            filter_ms,
            render_ms,
            persist_ms,
            polygon_vertices,
            gif_area_filename,
            gif_gral_filename,
        )
        async with self._access_lock:
            await asyncio.to_thread(self._record_and_maintain, row)

    def _record_and_maintain(self, row: tuple) -> None:
        conn = self._require_conn()
        conn.execute(
            """
            INSERT INTO alert_jobs
                (job_id, phenomenon_code, finished_at, duration_ms, outcome,
                 error_code, error_message, alert_id, affected_departments,
                 intersection_ms, filter_ms, render_ms, persist_ms,
                 polygon_vertices, gif_area_filename, gif_gral_filename)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        # Self-maintain so the store stays bounded without an external pruner.
        if self._retention_days > 0:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=self._retention_days)
            ).isoformat()
            conn.execute("DELETE FROM alert_jobs WHERE finished_at < ?", (cutoff,))
        if self._max_rows > 0:
            self._cap_sync(conn, self._max_rows)

    async def prune(self, before_iso: str) -> None:
        async with self._access_lock:
            await asyncio.to_thread(self._prune_sync, before_iso)

    def _prune_sync(self, before_iso: str) -> None:
        self._require_conn().execute(
            "DELETE FROM alert_jobs WHERE finished_at < ?", (before_iso,)
        )

    async def prune_to_max_rows(self, max_rows: int) -> int:
        async with self._access_lock:
            return await asyncio.to_thread(self._cap, max_rows)

    def _cap(self, max_rows: int) -> int:
        return self._cap_sync(self._require_conn(), max_rows)

    @staticmethod
    def _cap_sync(conn: sqlite3.Connection, max_rows: int) -> int:
        """Delete all but the newest ``max_rows`` rows (by autoincrement id)."""
        if max_rows <= 0:
            return 0
        row = conn.execute(
            "SELECT id FROM alert_jobs ORDER BY id DESC LIMIT 1 OFFSET ?",
            (max_rows - 1,),
        ).fetchone()
        if row is None:
            return 0
        deleted = conn.execute(
            "DELETE FROM alert_jobs WHERE id < ?", (row["id"],)
        ).rowcount
        if deleted:
            logger.info("Pruned %d job row(s); capped at %d", deleted, max_rows)
        return deleted

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
            f"SELECT {_JOB_COLUMNS} FROM alert_jobs"
            " WHERE finished_at >= ? ORDER BY finished_at DESC"
        )
        params: list = [since_iso]
        if limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._require_conn().execute(sql, params).fetchall()
        return [self._to_job_row(r) for r in rows]

    async def get_job_by_id(self, job_id: str) -> Optional[JobRow]:
        async with self._access_lock:
            return await asyncio.to_thread(self._get_job_by_id_sync, job_id)

    def _get_job_by_id_sync(self, job_id: str) -> Optional[JobRow]:
        row = (
            self._require_conn()
            .execute(
                f"SELECT {_JOB_COLUMNS} FROM alert_jobs"
                " WHERE job_id = ? ORDER BY id DESC LIMIT 1",
                (job_id,),
            )
            .fetchone()
        )
        return self._to_job_row(row) if row else None

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

    @staticmethod
    def _to_job_row(r: sqlite3.Row) -> JobRow:
        def _opt(key: str) -> Optional[int]:
            value = r[key]
            return int(value) if value is not None else None

        def _opt_str(key: str) -> Optional[str]:
            value = r[key]
            return str(value) if value is not None else None

        return JobRow(
            job_id=str(r["job_id"]),
            phenomenon_code=int(r["phenomenon_code"]),
            finished_at=str(r["finished_at"]),
            duration_ms=int(r["duration_ms"]),
            outcome=str(r["outcome"]),
            error_code=r["error_code"],
            error_message=_opt_str("error_message"),
            alert_id=_opt("alert_id"),
            affected_departments=_opt("affected_departments"),
            intersection_ms=_opt("intersection_ms"),
            filter_ms=_opt("filter_ms"),
            render_ms=_opt("render_ms"),
            persist_ms=_opt("persist_ms"),
            polygon_vertices=_opt("polygon_vertices"),
            gif_area_filename=_opt_str("gif_area_filename"),
            gif_gral_filename=_opt_str("gif_gral_filename"),
        )
