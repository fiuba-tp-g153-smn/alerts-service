import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# scheduler/__init__.py imports LayerRefreshService; pre-import it to break the
# circular dependency that arises when the service module is loaded in isolation.
import scheduler  # noqa: F401
from ports.object_storage import IObjectStorage
from services.layer_refresh_service import LayerRefreshService


@pytest.fixture
def mock_settings(tmp_path):
    settings = MagicMock()
    settings.data_dir = str(tmp_path)
    settings.simplify_tolerance = "0.01"
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
    assert len(result.files) > 0
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
    mock_storage.list_keys.return_value = ["pais_20240101.geojson"]

    with (
        patch("services.layer_refresh_service._download", new_callable=AsyncMock),
        patch("services.layer_refresh_service._simplify", new_callable=AsyncMock),
        patch("services.layer_refresh_service._convert_to_fgb", new_callable=AsyncMock),
    ):
        await service.run()

    assert mock_storage.delete.call_count >= 1


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
