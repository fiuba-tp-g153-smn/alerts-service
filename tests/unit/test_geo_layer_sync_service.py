"""Tests for GeoLayerSyncService — local ↔ S3 consistency and stale file cleanup."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ports.geo_layer_processor import IGeoLayerProcessor
from ports.object_storage import IObjectStorage
from services.geo_layer_sync_service import GeoLayerSyncService


@pytest.fixture
def mock_settings(tmp_path):
    settings = MagicMock()
    settings.data_dir = str(tmp_path)
    settings.s3_bucket_name = "test-bucket"
    settings.simplification_levels = {1: 0.001, 2: 0.01}
    return settings


@pytest.fixture
def mock_storage():
    storage = AsyncMock(spec=IObjectStorage)
    storage.list_keys.return_value = []
    storage.download.return_value = False
    return storage


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_processor():
    return AsyncMock(spec=IGeoLayerProcessor)


@pytest.fixture
def service(mock_settings, mock_storage, mock_processor, mock_logger):
    return GeoLayerSyncService(mock_settings, mock_storage, mock_processor, mock_logger)


# ── ensure_all ────────────────────────────────────────────────────────────────


async def test_ensure_all_returns_empty_when_all_files_present(
    service, mock_settings, tmp_path
):
    for level, tol_str in [(1, "0p001"), (2, "0p01")]:
        (tmp_path / f"pais_simple_L{level}_T{tol_str}_20260322.geojson").touch()
        (
            tmp_path / f"departamentos_simple_L{level}_T{tol_str}_20260322.geojson"
        ).touch()

    result = await service.ensure_all()

    assert result == []


async def test_ensure_all_returns_layer_when_simplified_missing(
    service, mock_settings, tmp_path
):
    # Nothing present locally — both levels missing for both layers
    result = await service.ensure_all()

    assert len(result) == 2
    for _layer, missing_levels in result:
        assert len(missing_levels) == 2  # both levels missing


# ── stale file cleanup ────────────────────────────────────────────────────────


async def test_reconcile_simplified_purges_wrong_tolerance_local_file(
    service, mock_settings, tmp_path
):
    wrong_tol_file = tmp_path / "pais_simple_L1_T0p05_20260322.geojson"
    wrong_tol_file.touch()

    await service._reconcile_simplified(
        {"simplified_stem": "pais_simple"},
        level=1,
        tolerance=0.001,  # correct tolerance is 0p001, file has 0p05
    )

    assert not wrong_tol_file.exists()


async def test_reconcile_simplified_purges_old_date_local_file(
    service, mock_settings, tmp_path
):
    old_file = tmp_path / "pais_simple_L1_T0p001_20240101.geojson"
    new_file = tmp_path / "pais_simple_L1_T0p001_20260322.geojson"
    old_file.touch()
    new_file.touch()

    result = await service._reconcile_simplified(
        {"simplified_stem": "pais_simple"},
        level=1,
        tolerance=0.001,
    )

    assert result is True
    assert not old_file.exists()
    assert new_file.exists()


async def test_reconcile_simplified_purges_stale_s3_keys(
    service, mock_settings, mock_storage, tmp_path
):
    (tmp_path / "pais_simple_L1_T0p001_20260322.geojson").touch()

    mock_storage.list_keys.side_effect = lambda prefix: (
        [
            "pais_simple_L1_T0p05_20240101.geojson",
            "pais_simple_L1_T0p001_20260322.geojson",
        ]
        if "pais_simple_L1_" in prefix
        else []
    )

    await service._reconcile_simplified(
        {"simplified_stem": "pais_simple"},
        level=1,
        tolerance=0.001,
    )

    mock_storage.delete.assert_called_once_with("pais_simple_L1_T0p05_20240101.geojson")


# ── download / upload sync ────────────────────────────────────────────────────


async def test_reconcile_simplified_downloads_from_s3_when_local_missing(
    service, mock_settings, mock_storage, tmp_path
):
    mock_storage.list_keys.side_effect = lambda prefix: (
        ["pais_simple_L1_T0p001_20260322.geojson"]
        if prefix == "pais_simple_L1_T0p001_"
        else []
    )
    mock_storage.download.return_value = True

    result = await service._reconcile_simplified(
        {"simplified_stem": "pais_simple"},
        level=1,
        tolerance=0.001,
    )

    mock_storage.download.assert_called_once()
    assert result is True


async def test_reconcile_simplified_returns_false_when_s3_download_fails(
    service, mock_settings, mock_storage, tmp_path
):
    mock_storage.list_keys.side_effect = lambda prefix: (
        ["pais_simple_L1_T0p001_20260322.geojson"]
        if prefix == "pais_simple_L1_T0p001_"
        else []
    )
    mock_storage.download.return_value = False

    result = await service._reconcile_simplified(
        {"simplified_stem": "pais_simple"},
        level=1,
        tolerance=0.001,
    )

    assert result is False


async def test_reconcile_simplified_uploads_to_s3_when_s3_missing(
    service, mock_settings, mock_storage, tmp_path
):
    (tmp_path / "pais_simple_L1_T0p001_20260322.geojson").touch()
    # S3 has nothing
    mock_storage.list_keys.return_value = []

    await service._reconcile_simplified(
        {"simplified_stem": "pais_simple"},
        level=1,
        tolerance=0.001,
    )

    mock_storage.upload.assert_called_once()
    assert "pais_simple_L1_T0p001" in mock_storage.upload.call_args.args[1]


async def test_reconcile_simplified_returns_false_when_nothing_exists(
    service, mock_settings, mock_storage, tmp_path
):
    mock_storage.list_keys.return_value = []

    result = await service._reconcile_simplified(
        {"simplified_stem": "pais_simple"},
        level=1,
        tolerance=0.001,
    )

    assert result is False


# ── no S3 configured ─────────────────────────────────────────────────────────


async def test_reconcile_simplified_skips_s3_when_no_bucket(
    service, mock_settings, mock_storage, tmp_path
):
    mock_settings.s3_bucket_name = ""
    (tmp_path / "pais_simple_L1_T0p001_20260322.geojson").touch()

    await service._reconcile_simplified(
        {"simplified_stem": "pais_simple"},
        level=1,
        tolerance=0.001,
    )

    mock_storage.list_keys.assert_not_called()
    mock_storage.upload.assert_not_called()


# ── regenerate cleanup ────────────────────────────────────────────────────────


async def test_regenerate_removes_raw_tmp_on_success(
    service, mock_settings, mock_processor, tmp_path
):
    raw_tmp = tmp_path / "pais_raw_tmp.geojson"
    layer = {
        "simplified_stem": "pais_simple",
        "url_attr": "country_geojson_url",
        "raw_tmp": "pais_raw_tmp.geojson",
    }
    mock_settings.country_geojson_url = "http://example.com/country.geojson"
    mock_settings.s3_bucket_name = ""

    async def fake_download(url, path):
        Path(path).touch()

    mock_processor.download.side_effect = fake_download
    service._needs_regen = [(layer, [(1, 0.001)])]

    await service.regenerate()

    assert not raw_tmp.exists()


async def test_regenerate_removes_raw_tmp_on_processor_failure(
    service, mock_settings, mock_processor, tmp_path
):
    raw_tmp = tmp_path / "pais_raw_tmp.geojson"
    layer = {
        "simplified_stem": "pais_simple",
        "url_attr": "country_geojson_url",
        "raw_tmp": "pais_raw_tmp.geojson",
    }
    mock_settings.country_geojson_url = "http://example.com/country.geojson"
    mock_settings.s3_bucket_name = ""

    async def fake_download(url, path):
        Path(path).touch()

    mock_processor.download.side_effect = fake_download
    mock_processor.simplify.side_effect = RuntimeError("simplify failed")
    service._needs_regen = [(layer, [(1, 0.001)])]

    with pytest.raises(RuntimeError, match="simplify failed"):
        await service.regenerate()

    assert not raw_tmp.exists()
