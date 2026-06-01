"""Endpoint tests for GET /alerts using a fake taviso repository (no network)."""

from datetime import datetime
from typing import List, Optional

import pytest
from fastapi.testclient import TestClient

from container import get_taviso_repo
from ports.taviso_repository import ITavisoReadRepository


class FakeTavisoReadRepository(ITavisoReadRepository):
    """In-memory fake of the read-only taviso port."""

    def __init__(self, rows: List[dict]):
        self._rows = rows

    def get_active_alerts(self, since_id: Optional[int] = None) -> List[dict]:
        rows = sorted(self._rows, key=lambda r: r["IdAlerta"])
        if since_id is not None:
            rows = [r for r in rows if r["IdAlerta"] > since_id]
        return rows

    def get_max_active_alert_id(self) -> Optional[int]:
        if not self._rows:
            return None
        return max(r["IdAlerta"] for r in self._rows)

    def close(self) -> None:
        pass


def _row(id_alerta: int) -> dict:
    return {
        "IdAlerta": id_alerta,
        "Fenomeno": "TORMENTAS",
        "Area": "Area X",
        "Poligono": "-58.5 -34.6",
        "FechaHora": datetime(2026, 6, 1, 10, 0, 0),
        "FechaFin": datetime(2026, 6, 1, 13, 0, 0),
    }


@pytest.fixture
def client_with_alerts():
    from main import app

    def _make(rows):
        app.dependency_overrides[get_taviso_repo] = lambda: FakeTavisoReadRepository(
            rows
        )
        return TestClient(app)

    yield _make
    app.dependency_overrides.pop(get_taviso_repo, None)


def test_list_active_alerts_field_names(client_with_alerts):
    client = client_with_alerts([_row(10)])

    resp = client.get("/alerts")

    assert resp.status_code == 200
    body = resp.json()
    assert body == [
        {
            "alert_id": 10,
            "phenomenon": "TORMENTAS",
            "area": "Area X",
            "polygon": "-58.5 -34.6",
            "start_datetime": "2026-06-01T10:00:00Z",
            "end_datetime": "2026-06-01T13:00:00Z",
        }
    ]
    assert resp.headers["ETag"] == '"10"'


def test_since_id_filters(client_with_alerts):
    client = client_with_alerts([_row(1), _row(2), _row(3)])

    resp = client.get("/alerts", params={"since_id": 1})

    assert resp.status_code == 200
    assert [a["alert_id"] for a in resp.json()] == [2, 3]
    assert resp.headers["ETag"] == '"3"'


def test_if_none_match_returns_304(client_with_alerts):
    client = client_with_alerts([_row(7)])

    resp = client.get("/alerts", headers={"If-None-Match": '"7"'})

    assert resp.status_code == 304
    assert resp.headers["ETag"] == '"7"'


def test_if_none_match_stale_returns_200(client_with_alerts):
    client = client_with_alerts([_row(7)])

    resp = client.get("/alerts", headers={"If-None-Match": '"5"'})

    assert resp.status_code == 200
    assert [a["alert_id"] for a in resp.json()] == [7]


def test_empty_active_alerts_etag_zero(client_with_alerts):
    client = client_with_alerts([])

    resp = client.get("/alerts")

    assert resp.status_code == 200
    assert resp.json() == []
    assert resp.headers["ETag"] == '"0"'
