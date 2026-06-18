"""Unit tests for MetricsSampler._sample_once (single-tick behaviour)."""

import logging
from unittest.mock import AsyncMock, MagicMock

from services.metrics_sampler import MetricsSampler


def _settings():
    s = MagicMock()
    s.metrics_sample_interval_seconds = 60
    s.metrics_retention_days = 30
    s.metrics_max_rows = 100000
    return s


def _metrics():
    m = MagicMock()
    m.record_sample = AsyncMock()
    m.prune = AsyncMock()
    m.prune_to_max_rows = AsyncMock()
    return m


async def test_sample_once_records_snapshot_and_prunes():
    processor = MagicMock()
    processor.stats.return_value = {
        "queue_depth": 2,
        "workers": 2,
        "respawns": 1,
        "jobs_queued_total": 10,
        "jobs_done_total": 7,
        "jobs_failed_total": 3,
    }
    mysql = MagicMock()
    mysql.get_pending_alerts_etag.return_value = (4, 99)
    metrics = _metrics()

    sampler = MetricsSampler(
        processor, mysql, metrics, _settings(), logging.getLogger("test")
    )
    await sampler._sample_once()

    metrics.record_sample.assert_awaited_once()
    kwargs = metrics.record_sample.await_args.kwargs
    assert kwargs["queue_depth"] == 2
    assert kwargs["jobs_done_total"] == 7
    assert kwargs["pending_alerts"] == 4
    metrics.prune.assert_awaited_once()
    metrics.prune_to_max_rows.assert_awaited_once_with(100000)
