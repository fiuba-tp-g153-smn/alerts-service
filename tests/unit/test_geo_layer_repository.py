import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import geopandas as gpd
import pytest

from adapters.geo_layer_repository import (
    FileSystemGeoLayerRepository,
    _simplified_stem,
    _versioned_stem,
)
from domain.models import LayerType


def test_versioned_stem_returns_latest(tmp_path):
    (tmp_path / "pais_20240101.geojson").touch()
    (tmp_path / "pais_20240201.geojson").touch()
    (tmp_path / "pais_20240301.geojson").touch()

    result = _versioned_stem(str(tmp_path), "pais")

    assert result.endswith("pais_20240301.geojson")


def test_versioned_stem_raises_when_no_files(tmp_path):
    with pytest.raises(FileNotFoundError):
        _versioned_stem(str(tmp_path), "pais")


def test_versioned_stem_ignores_non_date_suffixed_files(tmp_path):
    (tmp_path / "pais_simple.geojson").touch()

    with pytest.raises(FileNotFoundError):
        _versioned_stem(str(tmp_path), "pais_simple")


def test_simplified_stem_returns_latest(tmp_path):
    (tmp_path / "pais_simple_L1_T0p0001_20240101.geojson").touch()
    (tmp_path / "pais_simple_L1_T0p0001_20240301.geojson").touch()

    result = _simplified_stem(str(tmp_path), "pais_simple", 1)

    assert result.endswith("pais_simple_L1_T0p0001_20240301.geojson")


def test_simplified_stem_raises_when_no_files(tmp_path):
    with pytest.raises(FileNotFoundError):
        _simplified_stem(str(tmp_path), "pais_simple", 1)


def test_simplified_stem_matches_any_tolerance(tmp_path):
    (tmp_path / "pais_simple_L1_T0p001_20240101.geojson").touch()

    result = _simplified_stem(str(tmp_path), "pais_simple", 1)

    assert result.endswith("pais_simple_L1_T0p001_20240101.geojson")


@pytest.mark.asyncio
async def test_get_layer_raises_file_not_found_when_no_files(tmp_path):
    logger = MagicMock()
    repo = FileSystemGeoLayerRepository(str(tmp_path), logger)

    with pytest.raises(FileNotFoundError):
        await repo.get_layer(LayerType.COUNTRY, 1)


@pytest.mark.asyncio
@patch("adapters.geo_layer_repository.gpd.read_file")
async def test_get_layer_caches_result(mock_read_file, tmp_path):
    logger = MagicMock()
    repo = FileSystemGeoLayerRepository(str(tmp_path), logger)

    (tmp_path / "pais_simple_L1_T0p0001_20240101.geojson").touch()

    mock_gdf = MagicMock(spec=gpd.GeoDataFrame)
    mock_gdf.__len__.return_value = 1
    mock_read_file.return_value = mock_gdf

    result1 = await repo.get_layer(LayerType.COUNTRY, 1)
    mock_read_file.assert_called_once()
    assert result1 is mock_gdf

    result2 = await repo.get_layer(LayerType.COUNTRY, 1)
    assert mock_read_file.call_count == 1  # cache hit
    assert result2 is mock_gdf


@pytest.mark.asyncio
@patch("adapters.geo_layer_repository.gpd.read_file")
async def test_get_layer_cache_invalidates_when_new_versioned_file_appears(
    mock_read_file, tmp_path
):
    logger = MagicMock()
    repo = FileSystemGeoLayerRepository(str(tmp_path), logger)

    (tmp_path / "pais_simple_L1_T0p0001_20240101.geojson").touch()

    mock_gdf1 = MagicMock(spec=gpd.GeoDataFrame)
    mock_gdf1.__len__.return_value = 1
    mock_gdf2 = MagicMock(spec=gpd.GeoDataFrame)
    mock_gdf2.__len__.return_value = 2
    mock_read_file.side_effect = [mock_gdf1, mock_gdf2]

    result1 = await repo.get_layer(LayerType.COUNTRY, 1)
    assert result1 is mock_gdf1
    assert mock_read_file.call_count == 1

    # New versioned file appears — cache should miss on next call
    (tmp_path / "pais_simple_L1_T0p0001_20240201.geojson").touch()

    result2 = await repo.get_layer(LayerType.COUNTRY, 1)
    assert result2 is mock_gdf2
    assert mock_read_file.call_count == 2


@pytest.mark.asyncio
@patch("adapters.geo_layer_repository.gpd.read_file")
async def test_get_layer_different_levels_cached_independently(
    mock_read_file, tmp_path
):
    logger = MagicMock()
    repo = FileSystemGeoLayerRepository(str(tmp_path), logger)

    (tmp_path / "pais_simple_L1_T0p0001_20240101.geojson").touch()
    (tmp_path / "pais_simple_L2_T0p001_20240101.geojson").touch()

    mock_gdf = MagicMock(spec=gpd.GeoDataFrame)
    mock_gdf.__len__.return_value = 1
    mock_read_file.return_value = mock_gdf

    await repo.get_layer(LayerType.COUNTRY, 1)
    await repo.get_layer(LayerType.COUNTRY, 2)

    assert mock_read_file.call_count == 2


def test_get_fullres_geojson_path_returns_latest_for_departments(tmp_path):
    logger = MagicMock()
    repo = FileSystemGeoLayerRepository(str(tmp_path), logger)

    (tmp_path / "departamentos_20240101.geojson").touch()
    (tmp_path / "departamentos_20240301.geojson").touch()

    result = repo.get_fullres_geojson_path(LayerType.DEPARTMENTS)

    assert "departamentos_" in result
    assert "_simple_" not in result
    assert result.endswith("departamentos_20240301.geojson")


def test_get_fullres_fgb_path_returns_latest(tmp_path):
    logger = MagicMock()
    repo = FileSystemGeoLayerRepository(str(tmp_path), logger)

    (tmp_path / "pais_20240101.fgb").touch()
    (tmp_path / "pais_20240301.fgb").touch()

    result = repo.get_fullres_fgb_path(LayerType.COUNTRY)

    assert result.endswith("pais_20240301.fgb")


def test_get_fullres_fgb_path_returns_departments_latest(tmp_path):
    logger = MagicMock()
    repo = FileSystemGeoLayerRepository(str(tmp_path), logger)

    (tmp_path / "departamentos_20240101.fgb").touch()
    (tmp_path / "departamentos_20240301.fgb").touch()

    result = repo.get_fullres_fgb_path(LayerType.DEPARTMENTS)

    assert result.endswith("departamentos_20240301.fgb")


def test_get_fullres_fgb_path_raises_when_no_fgb(tmp_path):
    logger = MagicMock()
    repo = FileSystemGeoLayerRepository(str(tmp_path), logger)

    with pytest.raises(FileNotFoundError):
        repo.get_fullres_fgb_path(LayerType.COUNTRY)


@pytest.mark.asyncio
@patch("adapters.geo_layer_repository.gpd.read_file")
async def test_ttl_eviction_removes_idle_entry(mock_read_file, tmp_path):
    logger = MagicMock()
    repo = FileSystemGeoLayerRepository(str(tmp_path), logger, ttl_s=10.0)

    (tmp_path / "pais_simple_L1_T0p0001_20240101.geojson").touch()

    mock_gdf = MagicMock(spec=gpd.GeoDataFrame)
    mock_gdf.__len__.return_value = 1
    mock_read_file.return_value = mock_gdf

    await repo.get_layer(LayerType.COUNTRY, 1)
    assert (LayerType.COUNTRY, 1) in repo._cache

    # Backdate last_used so the entry appears idle
    repo._cache[(LayerType.COUNTRY, 1)].last_used -= 20.0

    await repo._evict_expired()

    assert (LayerType.COUNTRY, 1) not in repo._cache


@pytest.mark.asyncio
@patch("adapters.geo_layer_repository.gpd.read_file")
async def test_ttl_eviction_keeps_recently_used_entry(mock_read_file, tmp_path):
    logger = MagicMock()
    repo = FileSystemGeoLayerRepository(str(tmp_path), logger, ttl_s=300.0)

    (tmp_path / "pais_simple_L1_T0p0001_20240101.geojson").touch()

    mock_gdf = MagicMock(spec=gpd.GeoDataFrame)
    mock_gdf.__len__.return_value = 1
    mock_read_file.return_value = mock_gdf

    await repo.get_layer(LayerType.COUNTRY, 1)
    await repo._evict_expired()

    assert (LayerType.COUNTRY, 1) in repo._cache


@pytest.mark.asyncio
@patch("adapters.geo_layer_repository.gpd.read_file")
async def test_stampede_protection_reads_file_once_for_concurrent_requests(
    mock_read_file, tmp_path
):
    """Concurrent get_layer calls for the same key must only load the file once."""
    logger = MagicMock()
    repo = FileSystemGeoLayerRepository(str(tmp_path), logger)

    (tmp_path / "pais_simple_L1_T0p0001_20240101.geojson").touch()

    mock_gdf = MagicMock(spec=gpd.GeoDataFrame)
    mock_gdf.__len__.return_value = 1

    def slow_read(path):
        time.sleep(0.01)
        return mock_gdf

    mock_read_file.side_effect = slow_read

    results = await asyncio.gather(
        repo.get_layer(LayerType.COUNTRY, 1),
        repo.get_layer(LayerType.COUNTRY, 1),
        repo.get_layer(LayerType.COUNTRY, 1),
    )

    assert mock_read_file.call_count == 1
    assert all(r is mock_gdf for r in results)
