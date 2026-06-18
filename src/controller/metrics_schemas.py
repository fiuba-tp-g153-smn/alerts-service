"""Pydantic response models for the alerts-service metrics endpoints."""

from typing import Dict, List, Optional

from pydantic import BaseModel


class JobsAggregate(BaseModel):
    """Windowed aggregate over alert-generation jobs."""

    total: int
    done: int
    failed: int
    failure_breakdown: Dict[str, int]
    avg_duration_ms: float
    p95_duration_ms: int
    avg_intersection_ms: float
    avg_filter_ms: float
    avg_render_ms: float
    avg_persist_ms: float


class ProcessorStats(BaseModel):
    """Latest snapshot of the background job processor."""

    sampled_at: Optional[str] = None
    queue_depth: int = 0
    workers: int = 0
    respawns: int = 0
    jobs_queued_total: int = 0
    jobs_done_total: int = 0
    jobs_failed_total: int = 0
    pending_alerts: int = 0


class MetricsSummary(BaseModel):
    """Dashboard KPIs: windowed job aggregates + the latest processor snapshot."""

    window_hours: int
    jobs: JobsAggregate
    processor: ProcessorStats


class JobMetric(BaseModel):
    """One terminal alert-generation job."""

    job_id: str
    phenomenon_code: int
    finished_at: str
    duration_ms: int
    outcome: str
    error_code: Optional[str] = None
    affected_departments: Optional[int] = None
    intersection_ms: Optional[int] = None
    filter_ms: Optional[int] = None
    render_ms: Optional[int] = None
    persist_ms: Optional[int] = None
    polygon_vertices: Optional[int] = None


class JobHistoryPoint(BaseModel):
    """Job counts/durations aggregated into one time bucket."""

    bucket: str
    done: int
    failed: int
    avg_duration_ms: float


class ProcessorSamplePoint(BaseModel):
    """One processor snapshot in the time series."""

    sampled_at: str
    queue_depth: int
    workers: int
    respawns: int
    jobs_queued_total: int
    jobs_done_total: int
    jobs_failed_total: int
    pending_alerts: int


class LayerRefreshRun(BaseModel):
    """One layer-refresh job run from the scheduler history."""

    run_at: Optional[str] = None
    status: str
    files: List[str] = []
    duration_sec: Optional[float] = None
    error: Optional[str] = None
