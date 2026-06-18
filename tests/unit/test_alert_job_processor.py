"""Unit tests for AlertJobProcessor (bounded queue + worker pool + registry)."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

from domain.alert_job import AlertJobRecord, JobStatus
from domain.models import AreaTooLargeError
from services import alert_job_processor as ajp
from services.alert_job_processor import AlertJobProcessor

GEOMETRY = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}


def _make_service(**kwargs):
    service = MagicMock()
    service.generate_alert = AsyncMock(**kwargs)
    return service


async def _wait_terminal(proc: AlertJobProcessor, job_id: str, timeout: float = 2.0):
    """Poll until the job reaches a terminal state or the timeout elapses."""
    async with asyncio.timeout(timeout):
        while True:
            record = proc.get_status(job_id)
            if record and record.status in (JobStatus.DONE, JobStatus.FAILED):
                return record
            await asyncio.sleep(0.01)


async def test_submit_runs_job_to_done():
    service = _make_service(return_value={"alert_id": 99})
    proc = AlertJobProcessor(service, logging.getLogger("test"), 16, 1)
    proc.start()
    try:
        job_id = proc.try_submit(GEOMETRY, 1, "TORMENTAS")
        assert job_id is not None

        record = await _wait_terminal(proc, job_id)
        assert record.status is JobStatus.DONE
        assert record.alert_id == 99
        service.generate_alert.assert_awaited_once()
    finally:
        await proc.shutdown(drain=False, timeout=1)


async def test_area_too_large_marks_failed_and_worker_survives():
    service = _make_service(
        side_effect=[
            AreaTooLargeError(
                "too big", max_chars=2000, actual_chars=3000, affected_count=80
            ),
            {"alert_id": 7},
        ]
    )
    proc = AlertJobProcessor(service, logging.getLogger("test"), 16, 1)
    proc.start()
    try:
        first = proc.try_submit(GEOMETRY, 1, "TORMENTAS")
        record = await _wait_terminal(proc, first)
        assert record.status is JobStatus.FAILED
        assert record.error_code == "area_too_large"

        # The worker must keep running and process the next job.
        second = proc.try_submit(GEOMETRY, 1, "TORMENTAS")
        record2 = await _wait_terminal(proc, second)
        assert record2.status is JobStatus.DONE
        assert record2.alert_id == 7
    finally:
        await proc.shutdown(drain=False, timeout=1)


async def test_generic_error_marks_failed_generation():
    service = _make_service(side_effect=RuntimeError("worker exploded"))
    proc = AlertJobProcessor(service, logging.getLogger("test"), 16, 1)
    proc.start()
    try:
        job_id = proc.try_submit(GEOMETRY, 1, "TORMENTAS")
        record = await _wait_terminal(proc, job_id)
        assert record.status is JobStatus.FAILED
        assert record.error_code == "generation_failed"
    finally:
        await proc.shutdown(drain=False, timeout=1)


async def test_job_timeout_marks_failed_and_worker_survives():
    calls = {"n": 0}

    async def generate(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.Event().wait()  # first job hangs forever
        return {"alert_id": 5}

    service = MagicMock()
    service.generate_alert = generate
    proc = AlertJobProcessor(service, logging.getLogger("test"), 16, 1, job_timeout=0.1)
    proc.start()
    try:
        first = proc.try_submit(GEOMETRY, 1, "TORMENTAS")
        record = await _wait_terminal(proc, first)
        assert record.status is JobStatus.FAILED
        assert record.error_code == "timeout"

        # The hung job was cancelled and the worker is free for the next one.
        second = proc.try_submit(GEOMETRY, 1, "TORMENTAS")
        record2 = await _wait_terminal(proc, second)
        assert record2.status is JobStatus.DONE
        assert record2.alert_id == 5
    finally:
        await proc.shutdown(drain=False, timeout=1)


async def test_supervisor_respawns_dead_worker():
    service = _make_service(return_value={"alert_id": 8})
    proc = AlertJobProcessor(
        service, logging.getLogger("test"), 16, 1, supervisor_interval=0.05
    )
    proc.start()
    try:
        # Simulate an unexpected worker death.
        proc._workers[0].cancel()

        async with asyncio.timeout(2):
            while proc._respawns < 1:
                await asyncio.sleep(0.02)

        # The respawned worker drains a freshly submitted job.
        job_id = proc.try_submit(GEOMETRY, 1, "TORMENTAS")
        record = await _wait_terminal(proc, job_id)
        assert record.status is JobStatus.DONE
        assert record.alert_id == 8
    finally:
        await proc.shutdown(drain=False, timeout=1)


async def test_shutdown_stops_supervisor():
    proc = AlertJobProcessor(
        _make_service(return_value={"alert_id": 1}),
        logging.getLogger("test"),
        16,
        1,
        supervisor_interval=0.05,
    )
    proc.start()
    supervisor = proc._supervisor
    assert supervisor is not None

    await proc.shutdown(drain=False, timeout=1)

    assert proc._supervisor is None
    assert supervisor.done()


async def test_try_submit_returns_none_when_queue_full():
    service = _make_service(return_value={"alert_id": 1})
    # No workers, maxsize 1: first submission fills the queue, second is rejected.
    proc = AlertJobProcessor(service, logging.getLogger("test"), 1, 0)
    assert proc.try_submit(GEOMETRY, 1, "TORMENTAS") is not None
    assert proc.try_submit(GEOMETRY, 1, "TORMENTAS") is None


async def test_try_submit_returns_none_when_closing():
    service = _make_service(return_value={"alert_id": 1})
    proc = AlertJobProcessor(service, logging.getLogger("test"), 16, 1)
    proc.start()
    await proc.shutdown(drain=False, timeout=1)
    assert proc.try_submit(GEOMETRY, 1, "TORMENTAS") is None


async def test_get_status_unknown_job_is_none():
    proc = AlertJobProcessor(_make_service(), logging.getLogger("test"), 16, 0)
    assert proc.get_status("does-not-exist") is None


async def test_shutdown_drains_queued_jobs():
    service = _make_service(return_value={"alert_id": 3})
    proc = AlertJobProcessor(service, logging.getLogger("test"), 16, 1)
    proc.start()
    job_id = proc.try_submit(GEOMETRY, 1, "TORMENTAS")

    await proc.shutdown(drain=True, timeout=2)

    record = proc.get_status(job_id)
    assert record.status is JobStatus.DONE


async def _wait_recorded(recorder, timeout: float = 2.0):
    async with asyncio.timeout(timeout):
        while not recorder.record_job.await_count:
            await asyncio.sleep(0.01)


async def test_records_done_job_to_metrics():
    service = _make_service(
        return_value={
            "alert_id": 9,
            "affected_departments_count": 7,
            "intersection_ms": 40,
            "filter_ms": 20,
            "render_ms": 800,
            "persist_ms": 15,
            "gif_area_filename": "aviso_260618100000.gif",
            "gif_gral_filename": "avi_gral_260618100000.gif",
        }
    )
    recorder = MagicMock()
    recorder.record_job = AsyncMock()
    proc = AlertJobProcessor(
        service, logging.getLogger("test"), 16, 1, metrics=recorder
    )
    proc.start()
    try:
        job_id = proc.try_submit(GEOMETRY, 3, "TORMENTAS")
        await _wait_recorded(recorder)
        kwargs = recorder.record_job.await_args.kwargs
        assert kwargs["outcome"] == "done"
        assert kwargs["job_id"] == job_id
        assert kwargs["phenomenon_code"] == 3
        assert kwargs["affected_departments"] == 7
        assert kwargs["intersection_ms"] == 40
        assert kwargs["filter_ms"] == 20
        assert kwargs["persist_ms"] == 15
        assert kwargs["polygon_vertices"] == 4  # GEOMETRY outer ring has 4 points
        assert kwargs["gif_area_filename"] == "aviso_260618100000.gif"
        assert kwargs["gif_gral_filename"] == "avi_gral_260618100000.gif"
        assert kwargs["error_message"] is None  # done jobs carry no error
    finally:
        await proc.shutdown(drain=False, timeout=1)


async def test_records_failed_job_to_metrics():
    service = _make_service(side_effect=RuntimeError("boom"))
    recorder = MagicMock()
    recorder.record_job = AsyncMock()
    proc = AlertJobProcessor(
        service, logging.getLogger("test"), 16, 1, metrics=recorder
    )
    proc.start()
    try:
        proc.try_submit(GEOMETRY, 1, "TORMENTAS")
        await _wait_recorded(recorder)
        kwargs = recorder.record_job.await_args.kwargs
        assert kwargs["outcome"] == "failed"
        assert kwargs["error_code"] == "generation_failed"
        assert kwargs["error_message"] == "boom"  # str(exc) persisted for the dashboard
        assert kwargs["intersection_ms"] is None
    finally:
        await proc.shutdown(drain=False, timeout=1)


async def test_stats_reports_counters():
    service = _make_service(return_value={"alert_id": 1})
    proc = AlertJobProcessor(service, logging.getLogger("test"), 16, 1)
    proc.start()
    try:
        job_id = proc.try_submit(GEOMETRY, 1, "TORMENTAS")
        await _wait_terminal(proc, job_id)
        await asyncio.sleep(0.02)
        stats = proc.stats()
        assert stats["jobs_queued_total"] == 1
        assert stats["jobs_done_total"] == 1
        assert stats["workers"] == 1
    finally:
        await proc.shutdown(drain=False, timeout=1)


async def test_registry_is_bounded(monkeypatch):
    monkeypatch.setattr(ajp, "_REGISTRY_CAP", 2)
    proc = AlertJobProcessor(_make_service(), logging.getLogger("test"), 16, 0)

    # Three terminal records with a cap of 2 → the oldest is evicted.
    proc._set_status("a", AlertJobRecord(JobStatus.DONE, alert_id=1))
    proc._set_status("b", AlertJobRecord(JobStatus.DONE, alert_id=2))
    proc._set_status("c", AlertJobRecord(JobStatus.DONE, alert_id=3))

    assert proc.get_status("a") is None
    assert proc.get_status("b") is not None
    assert proc.get_status("c") is not None
