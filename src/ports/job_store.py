"""Abstract interface for the alerts-service durable job store (job history)."""

from abc import ABC, abstractmethod
from typing import List, Optional

from domain.metrics import JobHistoryBucket, JobRow, JobsAggregate


class IJobStore(ABC):
    """Port for persisting and querying terminal alert-generation jobs.

    Durable, always-on record of every finished job — the source of truth for
    ``GET /alerts/jobs/{id}`` and the dashboard's job aggregates.
    """

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
        error_message: Optional[str],
        alert_id: Optional[int],
        affected_departments: Optional[int],
        intersection_ms: Optional[int],
        filter_ms: Optional[int],
        render_ms: Optional[int],
        persist_ms: Optional[int],
        polygon_vertices: Optional[int],
        gif_area_filename: Optional[str],
        gif_gral_filename: Optional[str],
    ) -> None:
        """Append one terminal alert-generation job (and self-prune)."""

    @abstractmethod
    async def get_summary(self, since_iso: str) -> JobsAggregate:
        """Return windowed job aggregates (counts, failures, durations)."""

    @abstractmethod
    async def get_recent_jobs(self, since_iso: str, limit: int) -> List[JobRow]:
        """Return recent job rows, newest first."""

    @abstractmethod
    async def get_job_by_id(self, job_id: str) -> Optional[JobRow]:
        """Return the most recent terminal job row for ``job_id``, or None."""

    @abstractmethod
    async def get_jobs_history(
        self, since_iso: str, bucket: str
    ) -> List[JobHistoryBucket]:
        """Return job counts/durations aggregated into time buckets."""

    @abstractmethod
    async def prune(self, before_iso: str) -> None:
        """Delete jobs older than ``before_iso``."""

    @abstractmethod
    async def prune_to_max_rows(self, max_rows: int) -> int:
        """Cap the table to its newest ``max_rows`` rows; return rows deleted."""
