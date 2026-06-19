"""One-time data migrations run at startup (outside the Alembic schema chains)."""

import os
import sqlite3
from logging import Logger

from settings import Settings

# Columns to carry over from a legacy ``metrics.sqlite`` ``alert_jobs`` table into
# the new job store. Only those present in the legacy table are copied (older
# metrics DBs may lack the newer columns).
_WANTED_COLUMNS = [
    "job_id",
    "phenomenon_code",
    "finished_at",
    "duration_ms",
    "outcome",
    "error_code",
    "error_message",
    "alert_id",
    "affected_departments",
    "intersection_ms",
    "filter_ms",
    "render_ms",
    "persist_ms",
    "polygon_vertices",
    "gif_area_filename",
    "gif_gral_filename",
]


def copy_legacy_job_history(settings: Settings, logger: Logger) -> int:
    """Copy ``alert_jobs`` rows from a legacy ``metrics.sqlite`` into the job store.

    Best-effort, idempotent, one-time: runs only when the job store is empty and a
    legacy metrics DB still has the ``alert_jobs`` table (i.e. on the cutover to the
    split). Must run before the metrics migration drops that table. Any error is
    logged and swallowed — a failed copy just means starting the history fresh.
    """
    jobs_path = settings.jobs_db_path
    metrics_path = settings.metrics_db_path
    if not jobs_path or not metrics_path or not os.path.exists(metrics_path):
        return 0
    try:
        jobs_conn = sqlite3.connect(jobs_path)
        try:
            if jobs_conn.execute("SELECT 1 FROM alert_jobs LIMIT 1").fetchone():
                return 0  # already populated — nothing to migrate

            metrics_conn = sqlite3.connect(metrics_path)
            try:
                legacy_cols = {
                    r[1] for r in metrics_conn.execute("PRAGMA table_info(alert_jobs)")
                }
                if not legacy_cols:
                    return 0  # no legacy table (fresh deploy)
                cols = [c for c in _WANTED_COLUMNS if c in legacy_cols]
                col_sql = ", ".join(cols)
                rows = metrics_conn.execute(
                    f"SELECT {col_sql} FROM alert_jobs ORDER BY id"
                ).fetchall()
            finally:
                metrics_conn.close()

            if not rows:
                return 0
            placeholders = ", ".join(["?"] * len(cols))
            jobs_conn.executemany(
                f"INSERT INTO alert_jobs ({col_sql}) VALUES ({placeholders})", rows
            )
            jobs_conn.commit()
            logger.info(
                "Copied %d legacy job(s) from metrics.sqlite into the job store",
                len(rows),
            )
            return len(rows)
        finally:
            jobs_conn.close()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Legacy job-history copy skipped: %s", exc)
        return 0
