"""Tests for GeoLayerSyncService — local ↔ S3 consistency and stale file cleanup."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

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
def service(mock_settings, mock_storage, mock_logger):
    return GeoLayerSyncService(mock_settings, mock_storage, mock_logger)


# ── ensure_all ────────────────────────────────────────────────────────────────

async def test_ensure_all_returns_empty_when_all_files_present(
    service, mock_settings, tmp_path
):
    for level, tol_str in [(1, "0p001"), (2, "0p01")]:
        (tmp_path / f"pais_simple_L{level}_T{tol_str}_20260322.geojson").touch()
        (tmp_path / f"departamentos_simple_L{level}_T{tol_str}_20260322.geojson").touch()
    (tmp_path / "pais_20260322.fgb").touch()
    (tmp_path / "departamentos_20260322.fgb").touch()

    result = await service.ensure_all()

    assert result == []


async def test_ensure_all_returns_layer_when_simplified_missing(
    service, mock_settings, tmp_path
):
    # Only fgb present, no simplified files
    (tmp_path / "pais_20260322.fgb").touch()
    (tmp_path / "departamentos_20260322.fgb").touch()

    result = await service.ensure_all()

    assert len(result) == 2
    for layer, missing_levels, fgb_needed in result:
        assert len(missing_levels) == 2  # both levels missing
        assert not fgb_needed


async def test_ensure_all_returns_layer_when_fgb_missing(
    service, mock_settings, tmp_path
):
    # Simplified present, fgb missing
    for level, tol_str in [(1, "0p001"), (2, "0p01")]:
        (tmp_path / f"pais_simple_L{level}_T{tol_str}_20260322.geojson").touch()
        (tmp_path / f"departamentos_simple_L{level}_T{tol_str}_20260322.geojson").touch()

    result = await service.ensure_all()

    assert len(result) == 2
    for layer, missing_levels, fgb_needed in result:
        assert missing_levels == []
        assert fgb_needed


# ── stale file cleanup ────────────────────────────────────────────────────────

async def test_reconcile_simplified_purges_wrong_tolerance_local_file(
    service, mock_settings, tmp_path
):
    wrong_tol_file = tmp_path / "pais_simple_L1_T0p05_20260322.geojson"
    wrong_tol_file.touch()

    await service._reconcile_simplified(
        {"simplified_stem": "pais_simple", "fgb_stem": "pais"},
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
        {"simplified_stem": "pais_simple", "fgb_stem": "pais"},
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
        ["pais_simple_L1_T0p05_20240101.geojson",
         "pais_simple_L1_T0p001_20260322.geojson"]
        if "pais_simple_L1_" in prefix else []
    )

    await service._reconcile_simplified(
        {"simplified_stem": "pais_simple", "fgb_stem": "pais"},
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
        if prefix == "pais_simple_L1_T0p001_" else []
    )
    mock_storage.download.return_value = True

    result = await service._reconcile_simplified(
        {"simplified_stem": "pais_simple", "fgb_stem": "pais"},
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
        if prefix == "pais_simple_L1_T0p001_" else []
    )
    mock_storage.download.return_value = False

    result = await service._reconcile_simplified(
        {"simplified_stem": "pais_simple", "fgb_stem": "pais"},
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
        {"simplified_stem": "pais_simple", "fgb_stem": "pais"},
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
        {"simplified_stem": "pais_simple", "fgb_stem": "pais"},
        level=1,
        tolerance=0.001,
    )

    assert result is False


# ── FGB reconciliation ────────────────────────────────────────────────────────

async def test_reconcile_fgb_returns_true_when_file_present(
    service, mock_settings, tmp_path
):
    (tmp_path / "pais_20260322.fgb").touch()

    result = await service._reconcile_fgb({"fgb_stem": "pais"})

    assert result is True


async def test_reconcile_fgb_returns_false_when_nothing_exists(
    service, mock_settings, mock_storage, tmp_path
):
    mock_storage.list_keys.return_value = []

    result = await service._reconcile_fgb({"fgb_stem": "pais"})

    assert result is False


async def test_reconcile_fgb_purges_old_date_local_file(
    service, mock_settings, tmp_path
):
    old_file = tmp_path / "pais_20240101.fgb"
    new_file = tmp_path / "pais_20260322.fgb"
    old_file.touch()
    new_file.touch()

    await service._reconcile_fgb({"fgb_stem": "pais"})

    assert not old_file.exists()
    assert new_file.exists()


# ── no S3 configured ─────────────────────────────────────────────────────────

async def test_reconcile_simplified_skips_s3_when_no_bucket(
    service, mock_settings, mock_storage, tmp_path
):
    mock_settings.s3_bucket_name = ""
    (tmp_path / "pais_simple_L1_T0p001_20260322.geojson").touch()

    await service._reconcile_simplified(
        {"simplified_stem": "pais_simple", "fgb_stem": "pais"},
        level=1,
        tolerance=0.001,
    )

    mock_storage.list_keys.assert_not_called()
    mock_storage.upload.assert_not_called()
