"""Bounded in-process queue and worker pool for background alert generation."""

import asyncio
import time
from collections import OrderedDict
from datetime import datetime, timezone
from logging import Logger
from typing import Optional
from uuid import uuid4

from domain.alert_job import AlertJob, AlertJobRecord, JobStatus
from domain.models import AreaTooLargeError
from ports.metrics_repository import IAlertMetricsRepository
from services.alert_generation_service import AlertGenerationService

# Max number of job status records kept in memory. Bounds growth in a
# long-running process; oldest terminal records are evicted first.
_REGISTRY_CAP = 256

# Safety valve for the worker supervisor: stop respawning a slot that keeps
# dying so a hopelessly-broken worker can't spin forever.
_MAX_RESPAWNS = 10


class AlertJobProcessor:  # pylint: disable=too-many-instance-attributes
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
        job_timeout: float = 150.0,
        supervisor_interval: float = 30.0,
        metrics: Optional[IAlertMetricsRepository] = None,
    ):
        """Initialize with the alert service, logger and queue/pool sizing.

        ``metrics`` (optional) records each terminal job; when None, recording is
        a no-op (keeps the processor usable without the metrics store).
        """
        self._alert_service = alert_service
        self._logger = logger
        self._num_workers = workers
        self._job_timeout = job_timeout
        self._supervisor_interval = supervisor_interval
        self._metrics = metrics
        self._queue: asyncio.Queue[AlertJob] = asyncio.Queue(maxsize=maxsize)
        self._registry: "OrderedDict[str, AlertJobRecord]" = OrderedDict()
        self._workers: list[asyncio.Task] = []
        self._supervisor: Optional[asyncio.Task] = None
        self._respawns = 0
        self._queued_total = 0
        self._done_total = 0
        self._failed_total = 0
        self._closing = False

    def start(self) -> None:
        """Spawn the worker tasks and the health supervisor (idempotent)."""
        if self._workers:
            return
        self._workers = [
            asyncio.create_task(self._worker_loop(), name=f"alert-worker-{i}")
            for i in range(self._num_workers)
        ]
        self._supervisor = asyncio.create_task(
            self._supervisor_loop(), name="alert-worker-supervisor"
        )
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
        self._queued_total += 1
        self._set_status(job_id, AlertJobRecord(JobStatus.QUEUED))
        return job_id

    def get_status(self, job_id: str) -> Optional[AlertJobRecord]:
        """Return the status record for a job, or None if unknown."""
        return self._registry.get(job_id)

    def stats(self) -> dict:
        """Return a live snapshot of queue/worker health and lifetime counters."""
        return {
            "queue_depth": self._queue.qsize(),
            "workers": len(self._workers),
            "respawns": self._respawns,
            "jobs_queued_total": self._queued_total,
            "jobs_done_total": self._done_total,
            "jobs_failed_total": self._failed_total,
        }

    async def shutdown(self, drain: bool = True, timeout: float = 130.0) -> None:
        """Stop accepting work, optionally drain in-flight jobs, then stop workers."""
        self._closing = True
        # Stop the supervisor first, so it can't respawn a worker we then cancel.
        if self._supervisor is not None:
            self._supervisor.cancel()
            await asyncio.gather(self._supervisor, return_exceptions=True)
            self._supervisor = None
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

    async def _supervisor_loop(self) -> None:
        """Periodically respawn workers that died unexpectedly (self-healing)."""
        while not self._closing:
            await asyncio.sleep(self._supervisor_interval)
            if self._closing:
                break
            self._respawn_dead_workers()

    def _respawn_dead_workers(self) -> None:
        """Replace any finished worker task so the pool stays at full strength."""
        for i, task in enumerate(self._workers):
            if not task.done() or self._closing:
                continue
            self._log_dead_worker(task, i)
            if self._respawns >= _MAX_RESPAWNS:
                self._logger.critical(
                    "Alert worker respawn cap (%d) reached — pool degraded",
                    _MAX_RESPAWNS,
                )
                continue
            self._respawns += 1
            self._workers[i] = asyncio.create_task(
                self._worker_loop(), name=f"alert-worker-{i}"
            )
            self._logger.warning("Respawned dead alert worker %d", i)

    def _log_dead_worker(self, task: asyncio.Task, index: int) -> None:
        """Retrieve and log a dead worker's exception (avoids 'never retrieved')."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._logger.error("Alert worker %d died: %s", index, exc, exc_info=exc)

    async def _process(self, job: AlertJob) -> None:
        """Generate one alert and record its outcome (never raises)."""
        self._set_status(job.job_id, AlertJobRecord(JobStatus.PROCESSING))
        started = time.perf_counter()
        result: Optional[dict] = None
        outcome, error_code = "failed", None
        try:
            result = await asyncio.wait_for(
                self._alert_service.generate_alert(
                    job.geometry,
                    job.phenomenon_code,
                    phenomenon_text=job.phenomenon_text,
                ),
                timeout=self._job_timeout,
            )
        except asyncio.TimeoutError:
            self._logger.error(
                "Alert job %s exceeded %.0fs and was cancelled",
                job.job_id,
                self._job_timeout,
            )
            error_code = "timeout"
            self._fail(
                job.job_id, error_code, "Alert generation exceeded the time limit"
            )
        except AreaTooLargeError as exc:
            error_code = "area_too_large"
            self._fail(job.job_id, error_code, str(exc))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._logger.error(
                "Alert job %s failed: %s", job.job_id, exc, exc_info=True
            )
            error_code = "generation_failed"
            self._fail(job.job_id, error_code, str(exc))
        else:
            outcome = "done"
            self._set_status(
                job.job_id,
                AlertJobRecord(JobStatus.DONE, alert_id=result["alert_id"]),
            )

        if outcome == "done":
            self._done_total += 1
        else:
            self._failed_total += 1
        await self._record_job(job, started, outcome, error_code, result)

    async def _record_job(  # pylint: disable=too-many-arguments
        self,
        job: AlertJob,
        started: float,
        outcome: str,
        error_code: Optional[str],
        result: Optional[dict],
    ) -> None:
        """Persist one terminal job to the metrics store (best-effort)."""
        if self._metrics is None:
            return
        result = result or {}
        try:
            await self._metrics.record_job(
                job_id=job.job_id,
                phenomenon_code=job.phenomenon_code,
                finished_at=datetime.now(timezone.utc).isoformat(),
                duration_ms=int((time.perf_counter() - started) * 1000),
                outcome=outcome,
                error_code=error_code,
                affected_departments=result.get("affected_departments_count"),
                intersection_ms=result.get("intersection_ms"),
                render_ms=result.get("render_ms"),
                polygon_vertices=self._polygon_vertices(job.geometry),
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # Metrics must never break job processing.
            self._logger.warning("Failed to record job metrics: %s", exc)

    @staticmethod
    def _polygon_vertices(geometry: dict) -> Optional[int]:
        """Best-effort vertex count of the outer ring (None if shape is unusual)."""
        try:
            return len(geometry["coordinates"][0])
        except (KeyError, IndexError, TypeError):
            return None

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
