"""Unit tests for the metrics controller's graceful handling of disabled metrics."""

from domain.metrics import ProcessorSampleRow
from controller.metrics import get_processor_history


class _RaisingMetricsRepo:
    """Stands in for a metrics repo that was never connected (metrics disabled)."""

    async def get_processor_history(self, since_iso: str):
        raise RuntimeError("metrics repository not connected")


class _StubMetricsRepo:
    """Returns a fixed set of processor samples."""

    def __init__(self, rows):
        self._rows = rows

    async def get_processor_history(self, since_iso: str):
        return self._rows


async def test_processor_history_returns_empty_when_metrics_unavailable():
    # BUG-05: a disabled/unconnected metrics repo must not surface as a 500.
    result = await get_processor_history(hours=168, metrics=_RaisingMetricsRepo())

    assert result == []


async def test_processor_history_maps_rows_when_available():
    row = ProcessorSampleRow(
        sampled_at="2026-01-01T00:00:00+00:00",
        queue_depth=1,
        workers=2,
        respawns=0,
        jobs_queued_total=5,
        jobs_done_total=4,
        jobs_failed_total=1,
        pending_alerts=3,
    )

    result = await get_processor_history(hours=168, metrics=_StubMetricsRepo([row]))

    assert len(result) == 1
    assert result[0].queue_depth == 1
    assert result[0].pending_alerts == 3
