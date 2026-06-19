"""Unit tests for the SQLite processor-metrics store (schema via real migrations)."""

from pathlib import Path

from adapters.sqlite_processor_metrics import SqliteProcessorMetricsRepository
from db.migrate import run_metrics_migrations

_EPOCH = "0001-01-01T00:00:00+00:00"


async def _open(tmp_path: Path) -> SqliteProcessorMetricsRepository:
    db = tmp_path / "metrics.sqlite"
    run_metrics_migrations(db)
    repo = SqliteProcessorMetricsRepository(str(db))
    await repo.connect()
    return repo


async def _sample(repo, sampled_at, **over) -> None:
    payload = dict(
        sampled_at=sampled_at,
        queue_depth=1,
        workers=2,
        respawns=0,
        jobs_queued_total=5,
        jobs_done_total=4,
        jobs_failed_total=1,
        pending_alerts=3,
    )
    payload.update(over)
    await repo.record_sample(**payload)


async def test_samples_latest_and_history(tmp_path):
    repo = await _open(tmp_path)
    try:
        await _sample(repo, "2026-06-17T10:00:00+00:00", queue_depth=1, respawns=0)
        await _sample(repo, "2026-06-17T10:01:00+00:00", queue_depth=0, respawns=1)

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
        await _sample(repo, "2026-01-01T00:00:00+00:00")
        await _sample(repo, "2026-06-17T10:00:00+00:00")

        await repo.prune("2026-06-01T00:00:00+00:00")
        history = await repo.get_processor_history(_EPOCH)
        assert [s.sampled_at for s in history] == ["2026-06-17T10:00:00+00:00"]

        for i in range(4):
            await _sample(repo, f"2026-06-18T0{i}:00:00+00:00")
        deleted = await repo.prune_to_max_rows(2)
        assert deleted == 3  # 5 rows remained -> capped to 2
        assert len(await repo.get_processor_history(_EPOCH)) == 2
    finally:
        await repo.close()
