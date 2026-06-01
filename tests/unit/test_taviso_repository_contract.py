"""Contract tests for the ITavisoReadRepository port.

These verify the interface is usable by any conforming implementation, using an
in-memory fake (no network) — the real MySQL adapter is exercised by the manual
docker-based verification, since the test suite runs with sockets disabled.
"""

from typing import List

from ports.taviso_repository import ITavisoReadRepository


class FakeTavisoReadRepository(ITavisoReadRepository):
    """In-memory fake conforming to the read-only taviso port."""

    def __init__(self, rows: List[dict]):
        self._rows = rows
        self.closed = False

    def fetch_alerts(self, limit: int = 100) -> List[dict]:
        return self._rows[:limit]

    def close(self) -> None:
        self.closed = True


def test_fetch_alerts_respects_limit():
    rows = [{"IdAlerta": i} for i in range(5)]
    repo = FakeTavisoReadRepository(rows)

    assert repo.fetch_alerts(limit=2) == [{"IdAlerta": 0}, {"IdAlerta": 1}]


def test_fetch_alerts_returns_all_within_limit():
    repo = FakeTavisoReadRepository([{"IdAlerta": 1}])

    assert repo.fetch_alerts() == [{"IdAlerta": 1}]


def test_close_is_callable():
    repo = FakeTavisoReadRepository([])

    repo.close()

    assert repo.closed is True
