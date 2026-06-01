"""Contract tests for the ITavisoReadRepository port.

These verify the interface is usable by any conforming implementation, using an
in-memory fake (no network) — the real MySQL adapter is exercised by the manual
docker-based verification, since the test suite runs with sockets disabled.
"""

from typing import List, Optional

from ports.taviso_repository import ITavisoReadRepository


class FakeTavisoReadRepository(ITavisoReadRepository):
    """In-memory fake conforming to the read-only taviso port."""

    def __init__(self, rows: List[dict]):
        self._rows = rows
        self.closed = False

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
        self.closed = True


def _row(id_alerta: int) -> dict:
    return {
        "IdAlerta": id_alerta,
        "Fenomeno": "x",
        "Area": "x",
        "Poligono": "x",
        "FechaHora": "2026-06-01 10:00:00",
        "FechaFin": "2026-06-01 13:00:00",
    }


def test_get_active_alerts_returns_all_sorted():
    repo = FakeTavisoReadRepository([_row(3), _row(1), _row(2)])

    ids = [r["IdAlerta"] for r in repo.get_active_alerts()]

    assert ids == [1, 2, 3]


def test_get_active_alerts_respects_since_id():
    repo = FakeTavisoReadRepository([_row(1), _row(2), _row(3)])

    ids = [r["IdAlerta"] for r in repo.get_active_alerts(since_id=1)]

    assert ids == [2, 3]


def test_get_max_active_alert_id():
    repo = FakeTavisoReadRepository([_row(5), _row(9), _row(2)])

    assert repo.get_max_active_alert_id() == 9


def test_get_max_active_alert_id_empty_is_none():
    repo = FakeTavisoReadRepository([])

    assert repo.get_max_active_alert_id() is None


def test_close_is_callable():
    repo = FakeTavisoReadRepository([])

    repo.close()

    assert repo.closed is True
