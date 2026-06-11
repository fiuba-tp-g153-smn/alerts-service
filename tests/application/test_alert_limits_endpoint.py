"""Endpoint test for GET /alerts/limits using a fake MySQL repository."""

from typing import Dict, List, Optional, Tuple

import pytest
from fastapi.testclient import TestClient

from container import get_mysql_repo
from ports.mysql_repository import IMySQLRepository


class FakeMySQLRepository(IMySQLRepository):
    """In-memory fake exposing a configurable polygon column limit."""

    def __init__(self, polygon_max_length: int):
        self._polygon_max_length = polygon_max_length

    def get_polygon_max_length(self) -> int:
        return self._polygon_max_length

    def get_departments(self) -> List[dict]:
        return []

    def insert_alert(self, phenomenon, area, polygon, gif_general, gif_zoom) -> int:
        return 0

    def get_pending_alerts(self, since_id: Optional[int] = None) -> List[dict]:
        return []

    def get_pending_alerts_etag(self) -> Tuple[int, Optional[int]]:
        return (0, None)

    def get_phenomenon_text(self, code: int) -> Optional[str]:
        return None

    def get_all_phenomena(self) -> Dict[int, Optional[str]]:
        return {}

    def close(self) -> None:
        pass


@pytest.fixture
def client_with_limit():
    from main import app

    def _make(polygon_max_length):
        app.dependency_overrides[get_mysql_repo] = lambda: FakeMySQLRepository(
            polygon_max_length
        )
        return TestClient(app)

    yield _make
    app.dependency_overrides.pop(get_mysql_repo, None)


def test_limits_returns_max_vertex_count(client_with_limit):
    client = client_with_limit(1000)

    resp = client.get("/alerts/limits")

    assert resp.status_code == 200
    # (1000 + 1) // 16 == 62
    assert resp.json() == {"max_vertex_count": 62}


def test_limits_reflects_db_column_length(client_with_limit):
    client = client_with_limit(255)

    resp = client.get("/alerts/limits")

    assert resp.status_code == 200
    # (255 + 1) // 16 == 16
    assert resp.json() == {"max_vertex_count": 16}
