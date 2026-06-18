"""Endpoint test for POST /alerts when the polygon exceeds the DB column limit."""

import pytest
from fastapi.testclient import TestClient

from container import get_alert_service, get_job_processor
from domain.alert_job import PreparedAlert
from domain.models import PolygonTooLargeError


class _RaisingAlertService:
    """Stub alert service that reports the polygon as too large on validation."""

    def __init__(self, max_vertex_count: int):
        self._max_vertex_count = max_vertex_count

    async def validate_request(self, geometry, phenomenon_code) -> PreparedAlert:
        raise PolygonTooLargeError(
            "Polygon serialization too large",
            max_vertex_count=self._max_vertex_count,
        )


class _RecordingProcessor:
    """Records submissions so the test can assert none happened on 413."""

    def __init__(self):
        self.submissions = []

    def try_submit(self, geometry, phenomenon_code, phenomenon_text):
        self.submissions.append((phenomenon_code, phenomenon_text))
        return "job-123"


@pytest.fixture
def client_with_too_large():
    from main import app

    processor = _RecordingProcessor()

    def _make(max_vertex_count):
        app.dependency_overrides[get_alert_service] = lambda: _RaisingAlertService(
            max_vertex_count
        )
        app.dependency_overrides[get_job_processor] = lambda: processor
        return TestClient(app), processor

    yield _make
    app.dependency_overrides.pop(get_alert_service, None)
    app.dependency_overrides.pop(get_job_processor, None)


def test_generate_alert_returns_413_with_max_vertex_count(client_with_too_large):
    client, processor = client_with_too_large(62)

    resp = client.post(
        "/alerts",
        json={
            "phenomenon_code": 1,
            "geojson": {
                "type": "Polygon",
                "coordinates": [[[-58.5, -34.6], [-58.4, -34.6], [-58.4, -34.5]]],
            },
        },
    )

    assert resp.status_code == 413
    assert resp.json() == {"max_vertex_count": 62}
    # Validation runs before submit, so the oversized polygon is never queued.
    assert processor.submissions == []
