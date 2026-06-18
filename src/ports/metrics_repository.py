"""Abstract interface for the alerts-service metrics store."""

from abc import ABC, abstractmethod
from typing import List, Optional

from domain.metrics import (
    JobHistoryBucket,
    JobRow,
    JobsAggregate,
    ProcessorSampleRow,
)


class IAlertMetricsRepository(ABC):
    """Port for persisting and querying alert-generation metrics."""

    @abstractmethod
    async def connect(self) -> None:
        """Open the underlying store (schema owned by migrations)."""

    @abstractmethod
    async def close(self) -> None:
        """Close the underlying store."""

    @abstractmethod
    async def record_job(  # pylint: disable=too-many-arguments
        self,
        *,
        job_id: str,
        phenomenon_code: int,
        finished_at: str,
        duration_ms: int,
        outcome: str,
        error_code: Optional[str],
        affected_departments: Optional[int],
        intersection_ms: Optional[int],
        filter_ms: Optional[int],
        render_ms: Optional[int],
        persist_ms: Optional[int],
        polygon_vertices: Optional[int],
        gif_area_filename: Optional[str],
        gif_gral_filename: Optional[str],
    ) -> None:
        """Append one terminal alert-generation job."""

    @abstractmethod
    async def record_sample(  # pylint: disable=too-many-arguments
        self,
        *,
        sampled_at: str,
        queue_depth: int,
        workers: int,
        respawns: int,
        jobs_queued_total: int,
        jobs_done_total: int,
        jobs_failed_total: int,
        pending_alerts: int,
    ) -> None:
        """Append one periodic processor snapshot."""

    @abstractmethod
    async def get_summary(self, since_iso: str) -> JobsAggregate:
        """Return windowed job aggregates (counts, failures, durations)."""

    @abstractmethod
    async def get_recent_jobs(self, since_iso: str, limit: int) -> List[JobRow]:
        """Return recent job rows, newest first."""

    @abstractmethod
    async def get_jobs_history(
        self, since_iso: str, bucket: str
    ) -> List[JobHistoryBucket]:
        """Return job counts/durations aggregated into time buckets."""

    @abstractmethod
    async def get_latest_sample(self) -> Optional[ProcessorSampleRow]:
        """Return the most recent processor snapshot, if any."""

    @abstractmethod
    async def get_processor_history(self, since_iso: str) -> List[ProcessorSampleRow]:
        """Return processor snapshots since ``since_iso``, chronological."""

    @abstractmethod
    async def prune(self, before_iso: str) -> None:
        """Delete rows older than ``before_iso`` across every table."""

    @abstractmethod
    async def prune_to_max_rows(self, max_rows: int) -> int:
        """Cap each table to its newest ``max_rows`` rows; return total deleted."""
