"""Endpoint test for POST /alerts when the polygon exceeds the DB column limit."""

import pytest
from fastapi.testclient import TestClient

from container import get_alert_service
from domain.models import PolygonTooLargeError


class _RaisingAlertService:
    """Stub alert service that always reports the polygon as too large."""

    def __init__(self, max_vertex_count: int):
        self._max_vertex_count = max_vertex_count

    async def generate_alert(self, geometry, phenomenon_code):
        raise PolygonTooLargeError(
            "Polygon serialization too large",
            max_vertex_count=self._max_vertex_count,
        )


@pytest.fixture
def client_with_too_large():
    from main import app

    def _make(max_vertex_count):
        app.dependency_overrides[get_alert_service] = lambda: _RaisingAlertService(
            max_vertex_count
        )
        return TestClient(app)

    yield _make
    app.dependency_overrides.pop(get_alert_service, None)


def test_generate_alert_returns_413_with_max_vertex_count(client_with_too_large):
    client = client_with_too_large(62)

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
