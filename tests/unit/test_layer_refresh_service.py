import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# scheduler/__init__.py imports LayerRefreshService; pre-import it to break the
# circular dependency that arises when the service module is loaded in isolation.
import scheduler  # noqa: F401
from ports.object_storage import IObjectStorage
from services.layer_refresh_service import LayerRefreshService

_TEST_LEVELS = {1: 0.001}  # 1 level → 2 simplified + 2 fgb = 4 files total


@pytest.fixture
def mock_settings(tmp_path):
    settings = MagicMock()
    settings.data_dir = str(tmp_path)
    settings.simplification_levels = _TEST_LEVELS
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
def service(mock_settings, mock_storage, mock_logger):
    return LayerRefreshService(mock_settings, mock_storage, mock_logger)


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
    with (
        patch("services.layer_refresh_service._download", new_callable=AsyncMock),
        patch("services.layer_refresh_service._simplify", new_callable=AsyncMock),
        patch("services.layer_refresh_service._convert_to_fgb", new_callable=AsyncMock),
    ):
        result = await service.run()

    assert result.status == "success"
    assert isinstance(result.files, list)
    assert len(result.files) == 4
    assert any("pais_simple_L" in f for f in result.files)
    assert any("departamentos_simple_L" in f for f in result.files)
    assert any(f.endswith(".fgb") for f in result.files)
    assert result.error is None


async def test_run_success_uploads_four_files(service, mock_storage, tmp_raw_files):
    with (
        patch("services.layer_refresh_service._download", new_callable=AsyncMock),
        patch("services.layer_refresh_service._simplify", new_callable=AsyncMock),
        patch("services.layer_refresh_service._convert_to_fgb", new_callable=AsyncMock),
    ):
        await service.run()

    assert mock_storage.upload.call_count == 4


async def test_run_deletes_old_s3_keys_before_uploading(
    service, mock_storage, tmp_raw_files
):
    async def mock_list_keys(prefix):
        if prefix == "pais_simple_L1_":
            return ["pais_simple_L1_20240101.geojson"]
        return []

    mock_storage.list_keys.side_effect = mock_list_keys

    with (
        patch("services.layer_refresh_service._download", new_callable=AsyncMock),
        patch("services.layer_refresh_service._simplify", new_callable=AsyncMock),
        patch("services.layer_refresh_service._convert_to_fgb", new_callable=AsyncMock),
    ):
        await service.run()

    mock_storage.delete.assert_called_once_with("pais_simple_L1_20240101.geojson")


async def test_run_failure_returns_failed_result(service):
    with patch(
        "services.layer_refresh_service._download",
        new_callable=AsyncMock,
        side_effect=RuntimeError("network error"),
    ):
        result = await service.run()

    assert result.status == "failed"
    assert result.error == "network error"
    assert result.files == []


async def test_run_removes_temp_files_on_success(service, tmp_raw_files):
    country_tmp, deptos_tmp = tmp_raw_files

    with (
        patch("services.layer_refresh_service._download", new_callable=AsyncMock),
        patch("services.layer_refresh_service._simplify", new_callable=AsyncMock),
        patch("services.layer_refresh_service._convert_to_fgb", new_callable=AsyncMock),
    ):
        await service.run()

    assert not os.path.exists(country_tmp)
    assert not os.path.exists(deptos_tmp)


async def test_run_success_calls_download_with_correct_urls(
    service, mock_settings, tmp_raw_files
):
    with (
        patch(
            "services.layer_refresh_service._download", new_callable=AsyncMock
        ) as mock_download,
        patch("services.layer_refresh_service._simplify", new_callable=AsyncMock),
        patch("services.layer_refresh_service._convert_to_fgb", new_callable=AsyncMock),
    ):
        await service.run()

    download_urls = [call.args[0] for call in mock_download.call_args_list]
    assert mock_settings.country_geojson_url in download_urls
    assert mock_settings.departments_geojson_url in download_urls


async def test_run_success_calls_simplify_with_configured_tolerances(
    service, mock_settings, tmp_raw_files
):
    mock_settings.simplification_levels = {1: 0.001, 2: 0.05}

    with (
        patch("services.layer_refresh_service._download", new_callable=AsyncMock),
        patch(
            "services.layer_refresh_service._simplify", new_callable=AsyncMock
        ) as mock_simplify,
        patch("services.layer_refresh_service._convert_to_fgb", new_callable=AsyncMock),
    ):
        await service.run()

    tolerance_values = {call.args[2] for call in mock_simplify.call_args_list}
    assert tolerance_values == {0.001, 0.05}


async def test_run_failure_when_simplify_raises_returns_failed_result(
    service, tmp_raw_files
):
    with (
        patch("services.layer_refresh_service._download", new_callable=AsyncMock),
        patch(
            "services.layer_refresh_service._simplify",
            new_callable=AsyncMock,
            side_effect=RuntimeError("simplify failed"),
        ),
    ):
        result = await service.run()

    assert result.status == "failed"
    assert "simplify failed" in result.error


async def test_run_failure_when_convert_to_fgb_raises_returns_failed_result(
    service, tmp_raw_files
):
    with (
        patch("services.layer_refresh_service._download", new_callable=AsyncMock),
        patch("services.layer_refresh_service._simplify", new_callable=AsyncMock),
        patch(
            "services.layer_refresh_service._convert_to_fgb",
            new_callable=AsyncMock,
            side_effect=RuntimeError("fgb conversion failed"),
        ),
    ):
        result = await service.run()

    assert result.status == "failed"
    assert "fgb conversion failed" in result.error


async def test_run_failure_when_storage_upload_raises_returns_failed_result(
    service, mock_storage, tmp_raw_files
):
    mock_storage.upload.side_effect = RuntimeError("S3 upload failed")

    with (
        patch("services.layer_refresh_service._download", new_callable=AsyncMock),
        patch("services.layer_refresh_service._simplify", new_callable=AsyncMock),
        patch("services.layer_refresh_service._convert_to_fgb", new_callable=AsyncMock),
    ):
        result = await service.run()

    assert result.status == "failed"
    assert "S3 upload failed" in result.error


async def test_run_does_not_remove_temp_files_on_failure(service, tmp_raw_files):
    country_tmp, deptos_tmp = tmp_raw_files

    with (
        patch("services.layer_refresh_service._download", new_callable=AsyncMock),
        patch(
            "services.layer_refresh_service._simplify",
            new_callable=AsyncMock,
            side_effect=RuntimeError("simplify failed"),
        ),
    ):
        await service.run()

    assert os.path.exists(country_tmp)
    assert os.path.exists(deptos_tmp)


async def test_run_records_duration_in_result(service, tmp_raw_files):
    with (
        patch("services.layer_refresh_service._download", new_callable=AsyncMock),
        patch("services.layer_refresh_service._simplify", new_callable=AsyncMock),
        patch("services.layer_refresh_service._convert_to_fgb", new_callable=AsyncMock),
    ):
        result = await service.run()

    assert isinstance(result.duration_seconds, float)
    assert result.duration_seconds >= 0
