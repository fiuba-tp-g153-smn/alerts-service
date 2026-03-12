import pytest

from adapters.sqlite_history import SqliteHistoryRepository


def test_record_run_and_get_recent(tmp_path):
    repo = SqliteHistoryRepository(str(tmp_path / "test.db"))

    repo.record_run(
        status="success", files=["pais.geojson"], duration_sec=1.5, error=None
    )
    repo.record_run(
        status="failed", files=[], duration_sec=0.5, error="Connection error"
    )

    results = repo.get_recent(limit=10)

    assert len(results) == 2
    # Most recent first (ORDER BY id DESC)
    assert results[0]["status"] == "failed"
    assert results[0]["error"] == "Connection error"
    assert results[1]["status"] == "success"
    assert results[1]["files"] == ["pais.geojson"]


def test_get_recent_respects_limit(tmp_path):
    repo = SqliteHistoryRepository(str(tmp_path / "test.db"))

    for i in range(5):
        repo.record_run(status="success", files=None, duration_sec=float(i), error=None)

    results = repo.get_recent(limit=3)
    assert len(results) == 3


def test_empty_history(tmp_path):
    repo = SqliteHistoryRepository(str(tmp_path / "test.db"))

    results = repo.get_recent(limit=10)
    assert results == []
