"""Observability endpoints for the alerts service (dashboard metrics)."""

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, Query

from container import (
    get_history_repo,
    get_job_store,
    get_metrics_repo,
    get_mysql_repo,
)
from controller.metrics_schemas import (
    JobHistoryPoint,
    JobMetric,
    JobsAggregate,
    LayerRefreshRun,
    MetricsSummary,
    ProcessorSamplePoint,
    ProcessorStats,
)
from ports.history_repository import IHistoryRepository
from ports.job_store import IJobStore
from ports.metrics_repository import IProcessorMetricsRepository
from ports.mysql_repository import IMySQLRepository

router = APIRouter(prefix="/metrics", tags=["Metrics"])

# Sentinel lower bound for "all time" windows (hours=0).
_EPOCH = "0001-01-01T00:00:00+00:00"


def _since_iso(hours: int) -> str:
    """Return the ISO-8601 UTC lower bound for a window of ``hours`` (0 = all)."""
    if hours <= 0:
        return _EPOCH
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


@router.get(
    "/summary",
    response_model=MetricsSummary,
    summary="Alert metrics summary (dashboard KPIs)",
)
async def get_summary(
    hours: int = Query(24, ge=0, description="Window in hours (0 = all time)"),
    jobs: IJobStore = Depends(get_job_store),
    metrics: IProcessorMetricsRepository = Depends(get_metrics_repo),
    mysql_repo: IMySQLRepository = Depends(get_mysql_repo),
) -> MetricsSummary:
    """Windowed job aggregates (totals, failure breakdown, per-stage durations)
    plus the latest processor snapshot. ``pending_alerts`` is read live (not from
    the periodic sample) so it is always current."""
    agg = await jobs.get_summary(_since_iso(hours))
    # Processor telemetry is best-effort: if metrics is disabled/unavailable the
    # job KPIs still render (just without the processor snapshot).
    try:
        latest = await metrics.get_latest_sample()
    except Exception:  # pylint: disable=broad-exception-caught
        latest = None
    processor = ProcessorStats(**asdict(latest)) if latest else ProcessorStats()
    # Live pending count (the sampled value can be up to one interval stale).
    pending, _ = await asyncio.to_thread(mysql_repo.get_pending_alerts_etag)
    processor = processor.model_copy(update={"pending_alerts": int(pending)})
    return MetricsSummary(
        window_hours=hours, jobs=JobsAggregate(**asdict(agg)), processor=processor
    )


@router.get(
    "/jobs",
    response_model=List[JobMetric],
    summary="Recent alert-generation jobs",
)
async def get_jobs(
    hours: int = Query(24, ge=0, description="Window in hours (0 = all time)"),
    limit: int = Query(200, ge=0, le=5000, description="Max rows (0 = no limit)"),
    jobs: IJobStore = Depends(get_job_store),
) -> List[JobMetric]:
    """Recent terminal jobs (done/failed), newest first, within the window."""
    rows = await jobs.get_recent_jobs(_since_iso(hours), limit)
    return [JobMetric(**asdict(r)) for r in rows]


@router.get(
    "/jobs/history",
    response_model=List[JobHistoryPoint],
    summary="Job outcomes/durations over time",
)
async def get_jobs_history(
    hours: int = Query(168, ge=0, description="Window in hours (0 = all time)"),
    bucket: str = Query("hour", pattern="^(hour|day)$"),
    jobs: IJobStore = Depends(get_job_store),
) -> List[JobHistoryPoint]:
    """Per-bucket done/failed counts and average duration for trend charts."""
    rows = await jobs.get_jobs_history(_since_iso(hours), bucket)
    return [JobHistoryPoint(**asdict(r)) for r in rows]


@router.get(
    "/processor/history",
    response_model=List[ProcessorSamplePoint],
    summary="Processor queue/worker health over time",
)
async def get_processor_history(
    hours: int = Query(168, ge=0, description="Window in hours (0 = all time)"),
    metrics: IProcessorMetricsRepository = Depends(get_metrics_repo),
) -> List[ProcessorSamplePoint]:
    """Processor snapshots (queue depth, pending, counters) for trend charts."""
    rows = await metrics.get_processor_history(_since_iso(hours))
    return [ProcessorSamplePoint(**asdict(r)) for r in rows]


@router.get(
    "/layers",
    response_model=List[LayerRefreshRun],
    summary="Recent layer-refresh job runs",
)
async def get_layers(
    limit: int = Query(20, ge=1, le=200),
    history: IHistoryRepository = Depends(get_history_repo),
) -> List[LayerRefreshRun]:
    """Recent geo-layer refresh runs from the scheduler history (newest first)."""
    rows = await asyncio.to_thread(history.get_recent, limit)
    return [
        LayerRefreshRun(
            run_at=str(r["run_at"]) if r.get("run_at") is not None else None,
            status=str(r["status"]),
            files=list(r.get("files") or []),
            duration_sec=r.get("duration_sec"),
            error=r.get("error"),
        )
        for r in rows
    ]
