"""Data containers for alert-service metrics (records and query results)."""

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass(frozen=True, slots=True)
class JobRow:  # pylint: disable=too-many-instance-attributes
    """One terminal alert-generation job persisted in ``alert_jobs``."""

    job_id: str
    phenomenon_code: int
    finished_at: str
    duration_ms: int
    outcome: str  # 'done' | 'failed'
    error_code: Optional[str] = None
    affected_departments: Optional[int] = None
    intersection_ms: Optional[int] = None
    filter_ms: Optional[int] = None
    render_ms: Optional[int] = None
    persist_ms: Optional[int] = None
    polygon_vertices: Optional[int] = None
    gif_area_filename: Optional[str] = None
    gif_gral_filename: Optional[str] = None


@dataclass(frozen=True, slots=True)
class JobHistoryBucket:
    """Aggregated job activity for one time bucket."""

    bucket: str
    done: int
    failed: int
    avg_duration_ms: float


@dataclass(frozen=True, slots=True)
class ProcessorSampleRow:
    """One periodic snapshot of the job processor persisted in ``processor_samples``."""

    sampled_at: str
    queue_depth: int
    workers: int
    respawns: int
    jobs_queued_total: int
    jobs_done_total: int
    jobs_failed_total: int
    pending_alerts: int


@dataclass(frozen=True, slots=True)
class JobsAggregate:
    """Windowed aggregate over ``alert_jobs`` for the dashboard summary."""

    total: int = 0
    done: int = 0
    failed: int = 0
    failure_breakdown: Dict[str, int] = field(default_factory=dict)
    avg_duration_ms: float = 0.0
    p95_duration_ms: int = 0
    avg_intersection_ms: float = 0.0
    avg_filter_ms: float = 0.0
    avg_render_ms: float = 0.0
    avg_persist_ms: float = 0.0
