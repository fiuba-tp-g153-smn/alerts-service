"""Unit tests for MySQL adapter close() — BUG-04 (drain pool, actually close sockets)."""

import queue

from adapters.mysql_alerts import MySQLAlertsRepository
from adapters.mysql_taviso import MySQLTavisoReadRepository


class _FakeCnx:
    """Stands in for a raw MySQLConnection held in the pool's internal queue."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakePool:
    def __init__(self, cnxs):
        self._cnx_queue: queue.Queue = queue.Queue()
        for cnx in cnxs:
            self._cnx_queue.put(cnx)


def _make(repo_cls, cnxs):
    # Bypass __init__ so no real MySQL connection is attempted.
    repo = object.__new__(repo_cls)
    repo.pool = _FakePool(cnxs)
    return repo


def test_alerts_close_drains_and_closes_all():
    cnxs = [_FakeCnx() for _ in range(3)]
    repo = _make(MySQLAlertsRepository, cnxs)

    repo.close()

    assert all(c.closed for c in cnxs)
    assert repo.pool._cnx_queue.empty()


def test_taviso_close_drains_and_closes_all():
    cnxs = [_FakeCnx() for _ in range(5)]
    repo = _make(MySQLTavisoReadRepository, cnxs)

    repo.close()

    assert all(c.closed for c in cnxs)
    assert repo.pool._cnx_queue.empty()


def test_close_on_empty_pool_terminates():
    # Regression against the old busy-spin: an empty pool must return immediately.
    repo = _make(MySQLAlertsRepository, [])

    repo.close()

    assert repo.pool._cnx_queue.empty()


def test_close_survives_a_failing_connection():
    good1, bad, good2 = _FakeCnx(), _FakeCnx(), _FakeCnx()

    def boom():
        raise RuntimeError("connection already dead")

    bad.close = boom
    repo = _make(MySQLTavisoReadRepository, [good1, bad, good2])

    repo.close()  # must not raise

    assert good1.closed and good2.closed
    assert repo.pool._cnx_queue.empty()
