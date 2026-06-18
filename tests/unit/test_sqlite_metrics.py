"""Unit tests for the SQLite alert-metrics adapter (schema via real migrations)."""

from pathlib import Path

from adapters.sqlite_metrics import SqliteAlertMetricsRepository
from db.migrate import run_migrations

_EPOCH = "0001-01-01T00:00:00+00:00"


async def _open(tmp_path: Path) -> SqliteAlertMetricsRepository:
    db = tmp_path / "metrics.sqlite"
    run_migrations(db)
    repo = SqliteAlertMetricsRepository(str(db))
    await repo.connect()
    return repo


async def _job(repo: SqliteAlertMetricsRepository, **over) -> None:
    payload = dict(
        job_id="j1",
        phenomenon_code=1,
        finished_at="2026-06-17T10:00:00+00:00",
        duration_ms=1000,
        outcome="done",
        error_code=None,
        affected_departments=5,
        intersection_ms=40,
        filter_ms=20,
        render_ms=900,
        persist_ms=10,
        polygon_vertices=10,
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
        assert agg.avg_filter_ms == 20.0  # both done jobs default filter_ms=20
        assert agg.avg_persist_ms == 10.0

        recent = await repo.get_recent_jobs(_EPOCH, limit=1)
        assert recent[0].filter_ms == 20
        assert recent[0].persist_ms == 10
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


async def test_samples_latest_and_history(tmp_path):
    repo = await _open(tmp_path)
    try:
        await repo.record_sample(
            sampled_at="2026-06-17T10:00:00+00:00",
            queue_depth=1,
            workers=2,
            respawns=0,
            jobs_queued_total=5,
            jobs_done_total=4,
            jobs_failed_total=1,
            pending_alerts=3,
        )
        await repo.record_sample(
            sampled_at="2026-06-17T10:01:00+00:00",
            queue_depth=0,
            workers=2,
            respawns=1,
            jobs_queued_total=6,
            jobs_done_total=5,
            jobs_failed_total=1,
            pending_alerts=2,
        )

        latest = await repo.get_latest_sample()
        assert latest is not None
        assert latest.sampled_at == "2026-06-17T10:01:00+00:00"
        assert latest.respawns == 1

        history = await repo.get_processor_history(_EPOCH)
        assert [s.queue_depth for s in history] == [1, 0]
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
