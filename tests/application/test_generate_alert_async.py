"""Endpoint tests for the asynchronous POST /alerts + GET /alerts/jobs flow."""

import pytest
from fastapi.testclient import TestClient

from container import get_alert_service, get_job_processor
from domain.alert_job import AlertJobRecord, JobStatus, PreparedAlert

_GEOJSON = {
    "type": "Polygon",
    "coordinates": [[[-58.5, -34.6], [-58.4, -34.6], [-58.4, -34.5]]],
}


class _FakeAlertService:
    """Stub with only the synchronous validation the controller calls."""

    def __init__(self, prepared=None, error=None):
        self._prepared = prepared
        self._error = error

    async def validate_request(self, geometry, phenomenon_code) -> PreparedAlert:
        if self._error is not None:
            raise self._error
        return self._prepared


class _FakeProcessor:
    """Records submissions and serves canned job statuses."""

    def __init__(self, job_id="job-1", statuses=None):
        self._job_id = job_id
        self._statuses = statuses or {}
        self.submissions = []

    def try_submit(self, geometry, phenomenon_code, phenomenon_text):
        self.submissions.append((phenomenon_code, phenomenon_text))
        return self._job_id

    def get_status(self, job_id):
        return self._statuses.get(job_id)


@pytest.fixture
def make_client():
    from main import app

    def _make(service, processor):
        app.dependency_overrides[get_alert_service] = lambda: service
        app.dependency_overrides[get_job_processor] = lambda: processor
        return TestClient(app)

    yield _make
    app.dependency_overrides.pop(get_alert_service, None)
    app.dependency_overrides.pop(get_job_processor, None)


def _post(client):
    return client.post("/alerts", json={"phenomenon_code": 1, "geojson": _GEOJSON})


def test_post_returns_202_and_submits(make_client):
    service = _FakeAlertService(prepared=PreparedAlert("TORMENTAS", "[-34.60,-58.50]"))
    processor = _FakeProcessor(job_id="abc")
    client = make_client(service, processor)

    resp = _post(client)

    assert resp.status_code == 202
    assert resp.json() == {
        "job_id": "abc",
        "phenomenon_code": 1,
        "phenomenon": "TORMENTAS",
        "polygon": "[-34.60,-58.50]",
    }
    assert processor.submissions == [(1, "TORMENTAS")]


def test_post_invalid_phenomenon_returns_400(make_client):
    service = _FakeAlertService(error=ValueError("Invalid phenomenon code: 1"))
    processor = _FakeProcessor()
    client = make_client(service, processor)

    resp = _post(client)

    assert resp.status_code == 400
    assert processor.submissions == []


def test_post_queue_full_returns_503(make_client):
    service = _FakeAlertService(prepared=PreparedAlert("TORMENTAS", "[-34.60,-58.50]"))
    processor = _FakeProcessor()
    processor.try_submit = lambda *a, **k: None
    client = make_client(service, processor)

    resp = _post(client)

    assert resp.status_code == 503


def test_get_job_done_returns_alert_id(make_client):
    statuses = {"abc": AlertJobRecord(JobStatus.DONE, alert_id=42)}
    client = make_client(_FakeAlertService(), _FakeProcessor(statuses=statuses))

    resp = client.get("/alerts/jobs/abc")

    assert resp.status_code == 200
    assert resp.json() == {
        "job_id": "abc",
        "status": "done",
        "alert_id": 42,
        "error_code": None,
        "error": None,
    }


def test_get_job_failed_area_too_large(make_client):
    statuses = {
        "abc": AlertJobRecord(
            JobStatus.FAILED, error_code="area_too_large", error="too big"
        )
    }
    client = make_client(_FakeAlertService(), _FakeProcessor(statuses=statuses))

    resp = client.get("/alerts/jobs/abc")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "area_too_large"


def test_get_job_unknown_returns_404(make_client):
    client = make_client(_FakeAlertService(), _FakeProcessor(statuses={}))

    resp = client.get("/alerts/jobs/nope")

    assert resp.status_code == 404
