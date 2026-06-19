"""Abstract interface for the alerts-service processor-metrics store.

Holds sampled processor telemetry (``processor_samples``) only — the durable job
history lives in the separate job store (see ``ports/job_store.py``).
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from domain.metrics import ProcessorSampleRow


class IProcessorMetricsRepository(ABC):
    """Port for persisting and querying periodic processor-health samples."""

    @abstractmethod
    async def connect(self) -> None:
        """Open the underlying store (schema owned by migrations)."""

    @abstractmethod
    async def close(self) -> None:
        """Close the underlying store."""

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
    async def get_latest_sample(self) -> Optional[ProcessorSampleRow]:
        """Return the most recent processor snapshot, if any."""

    @abstractmethod
    async def get_processor_history(self, since_iso: str) -> List[ProcessorSampleRow]:
        """Return processor snapshots since ``since_iso``, chronological."""

    @abstractmethod
    async def prune(self, before_iso: str) -> None:
        """Delete samples older than ``before_iso``."""

    @abstractmethod
    async def prune_to_max_rows(self, max_rows: int) -> int:
        """Cap the table to its newest ``max_rows`` rows; return rows deleted."""
