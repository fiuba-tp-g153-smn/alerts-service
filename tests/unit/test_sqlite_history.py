from datetime import datetime

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
    assert results[0]["duration_sec"] == 0.5
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


def test_record_run_stores_null_files_as_none(tmp_path):
    repo = SqliteHistoryRepository(str(tmp_path / "test.db"))

    repo.record_run(status="success", files=None, duration_sec=1.0, error=None)

    results = repo.get_recent(limit=1)
    assert results[0]["files"] is None


def test_record_run_run_at_is_valid_iso_datetime_string(tmp_path):
    repo = SqliteHistoryRepository(str(tmp_path / "test.db"))

    repo.record_run(status="success", files=None, duration_sec=1.0, error=None)

    results = repo.get_recent(limit=1)
    datetime.fromisoformat(results[0]["run_at"])  # raises if not valid ISO format


def test_record_run_files_list_roundtrips_correctly(tmp_path):
    repo = SqliteHistoryRepository(str(tmp_path / "test.db"))
    files = ["pais_simple_20240101.geojson", "departamentos_simple_20240101.geojson"]

    repo.record_run(status="success", files=files, duration_sec=1.0, error=None)

    results = repo.get_recent(limit=1)
    assert results[0]["files"] == files


def test_get_recent_with_limit_1_returns_only_most_recent(tmp_path):
    repo = SqliteHistoryRepository(str(tmp_path / "test.db"))

    repo.record_run(status="success", files=None, duration_sec=1.0, error=None)
    repo.record_run(status="failed", files=None, duration_sec=0.5, error="err")

    results = repo.get_recent(limit=1)

    assert len(results) == 1
    assert results[0]["status"] == "failed"
