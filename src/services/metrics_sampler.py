"""Periodic collector that snapshots job-processor health into the metrics store.

Mirrors the worker-supervisor task pattern: a single long-lived asyncio task
started in the app lifespan. Each tick records a ``processor_samples`` row
(queue/worker health + lifetime counters + pending-alert count) and prunes old
rows. All blocking MySQL reads go through ``asyncio.to_thread`` so the event loop
never blocks.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from logging import Logger
from typing import Optional

from ports.metrics_repository import IAlertMetricsRepository
from ports.mysql_repository import IMySQLRepository
from services.alert_job_processor import AlertJobProcessor
from settings import Settings


class MetricsSampler:
    """Records periodic processor snapshots and prunes the metrics store."""

    def __init__(
        self,
        processor: AlertJobProcessor,
        mysql_repo: IMySQLRepository,
        metrics: IAlertMetricsRepository,
        settings: Settings,
        logger: Logger,
    ):
        """Initialize with the processor, repos, settings and logger."""
        self._processor = processor
        self._mysql_repo = mysql_repo
        self._metrics = metrics
        self._interval = settings.metrics_sample_interval_seconds
        self._retention_days = settings.metrics_retention_days
        self._max_rows = settings.metrics_max_rows
        self._logger = logger
        self._task: Optional[asyncio.Task] = None
        self._closing = False

    def start(self) -> None:
        """Spawn the sampling loop (idempotent)."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="metrics-sampler")
        self._logger.info("MetricsSampler started (interval=%.0fs)", self._interval)

    async def stop(self) -> None:
        """Cancel the sampling loop and wait for it to finish."""
        self._closing = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._logger.info("MetricsSampler stopped")

    async def _loop(self) -> None:
        while not self._closing:
            try:
                await self._sample_once()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self._logger.warning("Metrics sample failed: %s", exc)
            await asyncio.sleep(self._interval)

    async def _sample_once(self) -> None:
        """Record one processor snapshot and prune old rows."""
        stats = self._processor.stats()
        pending, _ = await asyncio.to_thread(self._mysql_repo.get_pending_alerts_etag)
        await self._metrics.record_sample(
            sampled_at=datetime.now(timezone.utc).isoformat(),
            queue_depth=stats["queue_depth"],
            workers=stats["workers"],
            respawns=stats["respawns"],
            jobs_queued_total=stats["jobs_queued_total"],
            jobs_done_total=stats["jobs_done_total"],
            jobs_failed_total=stats["jobs_failed_total"],
            pending_alerts=int(pending),
        )
        await self._prune()

    async def _prune(self) -> None:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        ).isoformat()
        await self._metrics.prune(cutoff)
        await self._metrics.prune_to_max_rows(self._max_rows)
