"""Unit tests for the SQLite job store (schema via real job migrations)."""

from pathlib import Path

from adapters.sqlite_job_store import SqliteJobStore
from db.migrate import run_job_migrations

_EPOCH = "0001-01-01T00:00:00+00:00"


async def _open(tmp_path: Path, **kw) -> SqliteJobStore:
    db = tmp_path / "jobs.sqlite"
    run_job_migrations(db)
    # Default retention/cap = 0 (no auto-prune) so query tests are deterministic.
    repo = SqliteJobStore(str(db), **kw)
    await repo.connect()
    return repo


async def _job(repo: SqliteJobStore, **over) -> None:
    payload = dict(
        job_id="j1",
        phenomenon_code=1,
        finished_at="2026-06-17T10:00:00+00:00",
        duration_ms=1000,
        outcome="done",
        error_code=None,
        error_message=None,
        alert_id=42,
        affected_departments=5,
        intersection_ms=40,
        filter_ms=20,
        render_ms=900,
        persist_ms=10,
        polygon_vertices=10,
        gif_area_filename="aviso_260617100000.gif",
        gif_gral_filename="avi_gral_260617100000.gif",
    )
    payload.update(over)
    await repo.record_job(**payload)


async def test_summary_counts_outcomes_and_failures(tmp_path):
    repo = await _open(tmp_path)
    try:
        await _job(repo, job_id="a", outcome="done", duration_ms=1000)
        await _job(repo, job_id="b", outcome="done", duration_ms=3000)
        await _job(
            repo,
            job_id="c",
            outcome="failed",
            error_code="area_too_large",
            error_message="Affected-area HTML is 2443 characters, exceeds 2000.",
            duration_ms=500,
            intersection_ms=None,
            render_ms=None,
        )

        agg = await repo.get_summary(_EPOCH)
        assert agg.total == 3
        assert agg.done == 2
        assert agg.failed == 1
        assert agg.failure_breakdown == {"area_too_large": 1}
        assert agg.avg_duration_ms == 2000.0  # mean of done durations (1000, 3000)
        assert agg.p95_duration_ms == 3000
        assert agg.avg_filter_ms == 20.0
        assert agg.avg_persist_ms == 10.0

        recent = await repo.get_recent_jobs(_EPOCH, limit=1)
        assert recent[0].filter_ms == 20
        assert recent[0].alert_id == 42
        assert recent[0].gif_area_filename == "aviso_260617100000.gif"

        recent_all = await repo.get_recent_jobs(_EPOCH, limit=10)
        failed = next(r for r in recent_all if r.outcome == "failed")
        assert failed.error_message == "Affected-area HTML is 2443 characters, exceeds 2000."
    finally:
        await repo.close()


async def test_get_job_by_id_returns_terminal_row_or_none(tmp_path):
    repo = await _open(tmp_path)
    try:
        await _job(
            repo,
            job_id="known",
            outcome="failed",
            error_code="area_too_large",
            error_message="too big",
            alert_id=None,
        )
        await _job(repo, job_id="ok", outcome="done", alert_id=99)

        row = await repo.get_job_by_id("known")
        assert row is not None and row.outcome == "failed"
        assert row.error_message == "too big"

        done = await repo.get_job_by_id("ok")
        assert done is not None and done.alert_id == 99

        assert await repo.get_job_by_id("does-not-exist") is None
    finally:
        await repo.close()


async def test_recent_jobs_newest_first_and_limit(tmp_path):
    repo = await _open(tmp_path)
    try:
        await _job(repo, job_id="old", finished_at="2026-06-17T09:00:00+00:00")
        await _job(repo, job_id="new", finished_at="2026-06-17T12:00:00+00:00")

        rows = await repo.get_recent_jobs(_EPOCH, limit=10)
        assert [r.job_id for r in rows] == ["new", "old"]

        limited = await repo.get_recent_jobs(_EPOCH, limit=1)
        assert [r.job_id for r in limited] == ["new"]
    finally:
        await repo.close()


async def test_jobs_history_buckets_by_hour(tmp_path):
    repo = await _open(tmp_path)
    try:
        await _job(
            repo, job_id="a", outcome="done", finished_at="2026-06-17T10:05:00+00:00"
        )
        await _job(
            repo,
            job_id="b",
            outcome="failed",
            error_code="timeout",
            finished_at="2026-06-17T10:45:00+00:00",
        )

        buckets = await repo.get_jobs_history(_EPOCH, bucket="hour")
        assert len(buckets) == 1
        assert buckets[0].bucket == "2026-06-17T10"
        assert buckets[0].done == 1
        assert buckets[0].failed == 1
    finally:
        await repo.close()


async def test_prune_by_time_and_max_rows(tmp_path):
    repo = await _open(tmp_path)
    try:
        await _job(repo, job_id="old", finished_at="2026-01-01T00:00:00+00:00")
        await _job(repo, job_id="new", finished_at="2026-06-17T10:00:00+00:00")

        await repo.prune("2026-06-01T00:00:00+00:00")
        rows = await repo.get_recent_jobs(_EPOCH, limit=10)
        assert [r.job_id for r in rows] == ["new"]

        for i in range(4):
            await _job(repo, job_id=f"x{i}", finished_at=f"2026-06-18T0{i}:00:00+00:00")
        deleted = await repo.prune_to_max_rows(2)
        assert deleted == 3  # 5 rows remained -> capped to 2
        assert len(await repo.get_recent_jobs(_EPOCH, limit=100)) == 2
    finally:
        await repo.close()


async def test_record_job_self_prunes_to_max_rows(tmp_path):
    # With a row cap configured, the store self-prunes on write (always-on bound).
    repo = await _open(tmp_path, max_rows=2)
    try:
        for i in range(5):
            await _job(repo, job_id=f"j{i}", finished_at=f"2026-06-18T0{i}:00:00+00:00")
        rows = await repo.get_recent_jobs(_EPOCH, limit=100)
        assert len(rows) == 2
        assert [r.job_id for r in rows] == ["j4", "j3"]
    finally:
        await repo.close()
