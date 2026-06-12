import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ports.geo_layer_processor import IGeoLayerProcessor
from ports.object_storage import IObjectStorage
from services.layer_refresh_service import LayerRefreshService

_TEST_LEVELS = {1: 0.001}  # 1 level → 2 simplified files total (pais + departamentos)


@pytest.fixture
def mock_settings(tmp_path):
    settings = MagicMock()
    settings.data_dir = str(tmp_path)
    settings.detail_levels = _TEST_LEVELS
    settings.departments_detail_level = 0.005
    settings.country_geojson_url = "http://example.com/country.geojson"
    settings.departments_geojson_url = "http://example.com/departments.geojson"
    return settings


@pytest.fixture
def mock_storage():
    storage = AsyncMock(spec=IObjectStorage)
    storage.list_keys.return_value = []
    return storage


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_processor():
    return AsyncMock(spec=IGeoLayerProcessor)


@pytest.fixture
def service(mock_settings, mock_storage, mock_processor, mock_logger):
    return LayerRefreshService(mock_settings, mock_storage, mock_processor, mock_logger)


@pytest.fixture
def tmp_raw_files(mock_settings):
    """Pre-create the temp raw files that run() expects to os.remove."""
    data_dir = mock_settings.data_dir
    country_tmp = os.path.join(data_dir, "pais_raw_tmp.geojson")
    deptos_tmp = os.path.join(data_dir, "departamentos_raw_tmp.geojson")
    Path(country_tmp).touch()
    Path(deptos_tmp).touch()
    return country_tmp, deptos_tmp


async def test_run_success_returns_success_result(service, tmp_raw_files):
    result = await service.run()

    assert result.status == "success"
    assert isinstance(result.files, list)
    assert len(result.files) == 2
    assert any("pais_simple_L" in f and "_T" in f for f in result.files)
    assert any("departamentos_simple_T" in f for f in result.files)
    assert result.error is None


async def test_run_success_uploads_two_files(service, mock_storage, tmp_raw_files):
    await service.run()

    assert mock_storage.upload.call_count == 2


async def test_run_deletes_old_s3_keys_before_uploading(
    service, mock_storage, tmp_raw_files
):
    async def mock_list_keys(prefix):
        if prefix == "pais_simple_L1_":
            return ["pais_simple_L1_T0p0005_20240101.geojson"]
        return []

    mock_storage.list_keys.side_effect = mock_list_keys

    await service.run()

    mock_storage.delete.assert_called_once_with(
        "pais_simple_L1_T0p0005_20240101.geojson"
    )


async def test_run_failure_returns_failed_result(service, mock_processor):
    mock_processor.download.side_effect = RuntimeError("network error")

    result = await service.run()

    assert result.status == "failed"
    assert result.error == "network error"
    assert result.files == []


async def test_run_removes_temp_files_on_success(service, tmp_raw_files):
    country_tmp, deptos_tmp = tmp_raw_files

    await service.run()

    assert not os.path.exists(country_tmp)
    assert not os.path.exists(deptos_tmp)


async def test_run_success_calls_download_with_correct_urls(
    service, mock_settings, mock_processor, tmp_raw_files
):
    await service.run()

    download_urls = [call.args[0] for call in mock_processor.download.call_args_list]
    assert mock_settings.country_geojson_url in download_urls
    assert mock_settings.departments_geojson_url in download_urls


async def test_run_success_calls_simplify_with_configured_tolerances(
    service, mock_settings, mock_processor, tmp_raw_files
):
    mock_settings.detail_levels = {1: 0.001, 2: 0.05}

    await service.run()

    tolerance_values = {call.args[2] for call in mock_processor.simplify.call_args_list}
    assert tolerance_values == {0.001, 0.05, mock_settings.departments_detail_level}

    out_paths = [call.args[1] for call in mock_processor.simplify.call_args_list]
    assert any("_T0p001_" in p for p in out_paths)
    assert any("_T0p05_" in p for p in out_paths)


async def test_run_failure_when_simplify_raises_returns_failed_result(
    service, mock_processor, tmp_raw_files
):
    mock_processor.simplify.side_effect = RuntimeError("simplify failed")

    result = await service.run()

    assert result.status == "failed"
    assert "simplify failed" in result.error


async def test_run_failure_when_storage_upload_raises_returns_failed_result(
    service, mock_storage, tmp_raw_files
):
    mock_storage.upload.side_effect = RuntimeError("S3 upload failed")

    result = await service.run()

    assert result.status == "failed"
    assert "S3 upload failed" in result.error


async def test_run_removes_temp_files_on_failure(
    service, mock_processor, tmp_raw_files
):
    country_tmp, deptos_tmp = tmp_raw_files
    mock_processor.simplify.side_effect = RuntimeError("simplify failed")

    await service.run()

    assert not os.path.exists(country_tmp)
    assert not os.path.exists(deptos_tmp)


async def test_run_records_duration_in_result(service, tmp_raw_files):
    result = await service.run()

    assert isinstance(result.duration_seconds, float)
    assert result.duration_seconds >= 0
