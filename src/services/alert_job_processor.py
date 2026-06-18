"""Bounded in-process queue and worker pool for background alert generation."""

import asyncio
from collections import OrderedDict
from logging import Logger
from typing import Optional
from uuid import uuid4

from domain.alert_job import AlertJob, AlertJobRecord, JobStatus
from domain.models import AreaTooLargeError
from services.alert_generation_service import AlertGenerationService

# Max number of job status records kept in memory. Bounds growth in a
# long-running process; oldest terminal records are evicted first.
_REGISTRY_CAP = 256


class AlertJobProcessor:
    """Runs alert generation off the request path via a bounded queue.

    POST /alerts enqueues a job and returns immediately; a small pool of worker
    tasks (started in the app lifespan) consumes the queue and records each
    job's outcome in an in-memory registry exposed via GET /alerts/jobs/{id}.
    Generation errors are captured as FAILED records and never crash a worker.
    """

    def __init__(
        self,
        alert_service: AlertGenerationService,
        logger: Logger,
        maxsize: int,
        workers: int,
    ):
        """Initialize with the alert service, logger and queue/pool sizing."""
        self._alert_service = alert_service
        self._logger = logger
        self._num_workers = workers
        self._queue: asyncio.Queue[AlertJob] = asyncio.Queue(maxsize=maxsize)
        self._registry: "OrderedDict[str, AlertJobRecord]" = OrderedDict()
        self._workers: list[asyncio.Task] = []
        self._closing = False

    def start(self) -> None:
        """Spawn the worker tasks (idempotent)."""
        if self._workers:
            return
        self._workers = [
            asyncio.create_task(self._worker_loop(), name=f"alert-worker-{i}")
            for i in range(self._num_workers)
        ]
        self._logger.info(
            "AlertJobProcessor started with %d workers", self._num_workers
        )

    def try_submit(
        self, geometry: dict, phenomenon_code: int, phenomenon_text: str
    ) -> Optional[str]:
        """Enqueue a job and return its id, or None if closing/queue is full."""
        if self._closing:
            return None
        job_id = uuid4().hex
        job = AlertJob(job_id, geometry, phenomenon_code, phenomenon_text)
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            self._logger.warning("Alert job queue full — rejecting submission")
            return None
        self._set_status(job_id, AlertJobRecord(JobStatus.QUEUED))
        return job_id

    def get_status(self, job_id: str) -> Optional[AlertJobRecord]:
        """Return the status record for a job, or None if unknown."""
        return self._registry.get(job_id)

    async def shutdown(self, drain: bool = True, timeout: float = 130.0) -> None:
        """Stop accepting work, optionally drain in-flight jobs, then stop workers."""
        self._closing = True
        if drain and self._workers:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=timeout)
            except asyncio.TimeoutError:
                self._logger.warning(
                    "Alert job drain timed out after %.0fs — cancelling workers",
                    timeout,
                )
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        self._logger.info("AlertJobProcessor stopped")

    async def _worker_loop(self) -> None:
        """Consume jobs until cancelled."""
        while True:
            job = await self._queue.get()
            try:
                await self._process(job)
            finally:
                self._queue.task_done()

    async def _process(self, job: AlertJob) -> None:
        """Generate one alert and record its outcome (never raises)."""
        self._set_status(job.job_id, AlertJobRecord(JobStatus.PROCESSING))
        try:
            result = await self._alert_service.generate_alert(
                job.geometry, job.phenomenon_code, phenomenon_text=job.phenomenon_text
            )
        except AreaTooLargeError as exc:
            self._fail(job.job_id, "area_too_large", str(exc))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._logger.error(
                "Alert job %s failed: %s", job.job_id, exc, exc_info=True
            )
            self._fail(job.job_id, "generation_failed", str(exc))
        else:
            self._set_status(
                job.job_id,
                AlertJobRecord(JobStatus.DONE, alert_id=result["alert_id"]),
            )

    def _fail(self, job_id: str, error_code: str, error: str) -> None:
        """Record a job as failed."""
        self._set_status(
            job_id, AlertJobRecord(JobStatus.FAILED, error_code=error_code, error=error)
        )

    def _set_status(self, job_id: str, record: AlertJobRecord) -> None:
        """Store/replace a job's status record, keeping the registry bounded."""
        self._registry[job_id] = record
        self._registry.move_to_end(job_id)
        while len(self._registry) > _REGISTRY_CAP and not self._evict_oldest_terminal():
            self._registry.popitem(last=False)

    def _evict_oldest_terminal(self) -> bool:
        """Drop the oldest DONE/FAILED record; return False if none are terminal."""
        for jid, rec in self._registry.items():
            if rec.status in (JobStatus.DONE, JobStatus.FAILED):
                del self._registry[jid]
                return True
        return False
