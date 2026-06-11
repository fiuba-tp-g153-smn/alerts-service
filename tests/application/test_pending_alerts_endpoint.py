"""Endpoint tests for GET /alerts/pending using a fake MySQL repository."""

from typing import Dict, List, Optional, Tuple

import pytest
from fastapi.testclient import TestClient

from container import get_mysql_repo
from ports.mysql_repository import IMySQLRepository


class FakeMySQLRepository(IMySQLRepository):
    """In-memory fake of the MySQL alerts port (pending-alert methods only)."""

    def __init__(self, rows: List[dict]):
        self._rows = rows

    def get_pending_alerts(self, since_id: Optional[int] = None) -> List[dict]:
        rows = sorted(self._rows, key=lambda r: r["IdAviso_temporal"])
        if since_id is not None:
            rows = [r for r in rows if r["IdAviso_temporal"] > since_id]
        return rows

    def get_pending_alerts_etag(self) -> Tuple[int, Optional[int]]:
        max_id = max((r["IdAviso_temporal"] for r in self._rows), default=None)
        return (len(self._rows), max_id)

    def get_departments(self) -> List[dict]:
        return []

    def insert_alert(self, phenomenon, area, polygon, gif_general, gif_zoom) -> int:
        return 0

    def get_polygon_max_length(self) -> int:
        return 1000

    def get_phenomenon_text(self, code: int) -> Optional[str]:
        return None

    def get_all_phenomena(self) -> Dict[int, Optional[str]]:
        return {}

    def close(self) -> None:
        pass


def _row(id_aviso: int, gif: str = "alerta.gif") -> dict:
    return {
        "IdAviso_temporal": id_aviso,
        "Fenomeno": "TORMENTAS",
        "Area": "Area X",
        "Poligono": "[-34.60,-58.50]",
        "Gif_general": f"gral_{gif}",
        "Gif_zoom": f"zoom_{gif}",
    }


@pytest.fixture
def client_with_pending():
    from main import app

    def _make(rows):
        app.dependency_overrides[get_mysql_repo] = lambda: FakeMySQLRepository(rows)
        return TestClient(app)

    yield _make
    app.dependency_overrides.pop(get_mysql_repo, None)


def test_list_pending_alerts_field_names_and_urls(client_with_pending):
    client = client_with_pending([_row(10)])

    resp = client.get("/alerts/pending")

    assert resp.status_code == 200
    assert resp.json() == [
        {
            "alert_id": 10,
            "phenomenon": "TORMENTAS",
            "area": "Area X",
            "polygon": "[-34.60,-58.50]",
            "gif_gral_url": "/alerts/gral_alerta.gif",
            "gif_area_url": "/alerts/zoom_alerta.gif",
        }
    ]
    assert resp.headers["ETag"] == '"1-10"'


def test_placeholder_gif_is_included(client_with_pending):
    client = client_with_pending([_row(1, gif="invalido.gif")])

    resp = client.get("/alerts/pending")

    body = resp.json()
    assert body[0]["gif_gral_url"] == "/alerts/gral_invalido.gif"
    assert body[0]["gif_area_url"] == "/alerts/zoom_invalido.gif"


def test_since_id_filters(client_with_pending):
    client = client_with_pending([_row(1), _row(2), _row(3)])

    resp = client.get("/alerts/pending", params={"since_id": 1})

    assert resp.status_code == 200
    assert [a["alert_id"] for a in resp.json()] == [2, 3]
    # ETag reflects the full pending set, not the since_id-filtered view.
    assert resp.headers["ETag"] == '"3-3"'


def test_if_none_match_returns_304(client_with_pending):
    client = client_with_pending([_row(7)])

    resp = client.get("/alerts/pending", headers={"If-None-Match": '"1-7"'})

    assert resp.status_code == 304
    assert resp.headers["ETag"] == '"1-7"'


def test_if_none_match_stale_returns_200(client_with_pending):
    client = client_with_pending([_row(7)])

    resp = client.get("/alerts/pending", headers={"If-None-Match": '"1-5"'})

    assert resp.status_code == 200
    assert [a["alert_id"] for a in resp.json()] == [7]


def test_empty_pending_alerts_etag_zero(client_with_pending):
    client = client_with_pending([])

    resp = client.get("/alerts/pending")

    assert resp.status_code == 200
    assert resp.json() == []
    assert resp.headers["ETag"] == '"0-0"'


def test_etag_changes_when_pending_is_processed(client_with_pending):
    """A removal (Procesado 'N'->'Y') lowers the count, so the ETag changes
    even though MAX(id) stays the same — this is what plain MAX(id) missed."""
    before = client_with_pending([_row(5), _row(7), _row(12)]).get("/alerts/pending")
    # Alert 7 gets processed; 5 and 12 remain pending (max id unchanged at 12).
    after = client_with_pending([_row(5), _row(12)]).get("/alerts/pending")

    assert before.headers["ETag"] == '"3-12"'
    assert after.headers["ETag"] == '"2-12"'
    assert before.headers["ETag"] != after.headers["ETag"]
