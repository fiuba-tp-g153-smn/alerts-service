"""Endpoint tests for /metrics/* using fake repositories (no DB, no lifespan)."""

import pytest
from fastapi.testclient import TestClient

from container import get_history_repo, get_metrics_repo
from domain.metrics import (
    JobHistoryBucket,
    JobRow,
    JobsAggregate,
    ProcessorSampleRow,
)


class FakeMetricsRepo:
    """Duck-typed metrics repo serving canned query results."""

    async def get_summary(self, since_iso):
        return JobsAggregate(
            total=3,
            done=2,
            failed=1,
            failure_breakdown={"timeout": 1},
            avg_duration_ms=1500.0,
            p95_duration_ms=3000,
            avg_intersection_ms=40.0,
            avg_render_ms=900.0,
        )

    async def get_latest_sample(self):
        return ProcessorSampleRow(
            sampled_at="2026-06-17T10:00:00+00:00",
            queue_depth=1,
            workers=2,
            respawns=0,
            jobs_queued_total=3,
            jobs_done_total=2,
            jobs_failed_total=1,
            pending_alerts=4,
        )

    async def get_recent_jobs(self, since_iso, limit):
        return [
            JobRow(
                job_id="j1",
                phenomenon_code=1,
                finished_at="2026-06-17T10:00:00+00:00",
                duration_ms=1000,
                outcome="done",
                affected_departments=5,
                intersection_ms=40,
                render_ms=900,
                polygon_vertices=10,
            )
        ]

    async def get_jobs_history(self, since_iso, bucket):
        return [
            JobHistoryBucket(
                bucket="2026-06-17T10", done=2, failed=1, avg_duration_ms=1500.0
            )
        ]

    async def get_processor_history(self, since_iso):
        return [await self.get_latest_sample()]


class FakeHistoryRepo:
    def get_recent(self, limit):
        return [
            {
                "id": 1,
                "run_at": "2026-06-17T03:00:00",
                "status": "success",
                "files": ["pais.geojson"],
                "duration_sec": 1.5,
                "error": None,
            }
        ]


@pytest.fixture
def client():
    from main import app

    app.dependency_overrides[get_metrics_repo] = FakeMetricsRepo
    app.dependency_overrides[get_history_repo] = FakeHistoryRepo
    yield TestClient(app)
    app.dependency_overrides.pop(get_metrics_repo, None)
    app.dependency_overrides.pop(get_history_repo, None)


def test_summary(client):
    resp = client.get("/metrics/summary", params={"hours": 24})
    assert resp.status_code == 200
    body = resp.json()
    assert body["window_hours"] == 24
    assert body["jobs"]["total"] == 3
    assert body["jobs"]["failure_breakdown"] == {"timeout": 1}
    assert body["processor"]["pending_alerts"] == 4


def test_jobs(client):
    resp = client.get("/metrics/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["outcome"] == "done"
    assert body[0]["polygon_vertices"] == 10


def test_jobs_history(client):
    resp = client.get("/metrics/jobs/history", params={"bucket": "hour"})
    assert resp.status_code == 200
    assert resp.json()[0]["bucket"] == "2026-06-17T10"


def test_processor_history(client):
    resp = client.get("/metrics/processor/history")
    assert resp.status_code == 200
    assert resp.json()[0]["queue_depth"] == 1


def test_layers(client):
    resp = client.get("/metrics/layers")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["status"] == "success"
    assert body[0]["files"] == ["pais.geojson"]


def test_summary_rejects_negative_hours(client):
    assert client.get("/metrics/summary", params={"hours": -1}).status_code == 422


def test_jobs_history_rejects_bad_bucket(client):
    assert (
        client.get("/metrics/jobs/history", params={"bucket": "week"}).status_code
        == 422
    )
