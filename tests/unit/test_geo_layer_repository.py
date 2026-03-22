from unittest.mock import MagicMock, patch

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


def test_get_layer_raises_file_not_found_when_no_files(tmp_path):
    logger = MagicMock()
    repo = FileSystemGeoLayerRepository(str(tmp_path), logger)

    with pytest.raises(FileNotFoundError):
        repo.get_layer(LayerType.COUNTRY, 1)


@patch("adapters.geo_layer_repository.gpd.read_file")
def test_get_layer_caches_result(mock_read_file, tmp_path):
    logger = MagicMock()
    repo = FileSystemGeoLayerRepository(str(tmp_path), logger)

    (tmp_path / "pais_simple_L1_T0p0001_20240101.geojson").touch()

    mock_gdf = MagicMock(spec=gpd.GeoDataFrame)
    mock_gdf.__len__.return_value = 1
    mock_read_file.return_value = mock_gdf

    result1 = repo.get_layer(LayerType.COUNTRY, 1)
    mock_read_file.assert_called_once()
    assert result1 is mock_gdf

    result2 = repo.get_layer(LayerType.COUNTRY, 1)
    assert mock_read_file.call_count == 1  # cache hit
    assert result2 is mock_gdf


@patch("adapters.geo_layer_repository.gpd.read_file")
def test_get_layer_cache_invalidates_when_new_versioned_file_appears(
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

    result1 = repo.get_layer(LayerType.COUNTRY, 1)
    assert result1 is mock_gdf1
    assert mock_read_file.call_count == 1

    # New versioned file appears — cache should miss on next call
    (tmp_path / "pais_simple_L1_T0p0001_20240201.geojson").touch()

    result2 = repo.get_layer(LayerType.COUNTRY, 1)
    assert result2 is mock_gdf2
    assert mock_read_file.call_count == 2


@patch("adapters.geo_layer_repository.gpd.read_file")
def test_get_layer_different_levels_cached_independently(mock_read_file, tmp_path):
    logger = MagicMock()
    repo = FileSystemGeoLayerRepository(str(tmp_path), logger)

    (tmp_path / "pais_simple_L1_T0p0001_20240101.geojson").touch()
    (tmp_path / "pais_simple_L2_T0p001_20240101.geojson").touch()

    mock_gdf = MagicMock(spec=gpd.GeoDataFrame)
    mock_gdf.__len__.return_value = 1
    mock_read_file.return_value = mock_gdf

    repo.get_layer(LayerType.COUNTRY, 1)
    repo.get_layer(LayerType.COUNTRY, 2)

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


@patch("adapters.geo_layer_repository.gpd.read_file")
def test_preload_loads_all_level_layer_combinations(mock_read_file, tmp_path):
    logger = MagicMock()
    repo = FileSystemGeoLayerRepository(str(tmp_path), logger)

    (tmp_path / "pais_simple_L1_T0p0001_20240101.geojson").touch()
    (tmp_path / "departamentos_simple_L1_T0p0001_20240101.geojson").touch()

    mock_gdf = MagicMock(spec=gpd.GeoDataFrame)
    mock_gdf.__len__.return_value = 1
    mock_read_file.return_value = mock_gdf

    repo.preload([1])

    assert mock_read_file.call_count == 2
    call_paths = [str(call.args[0]) for call in mock_read_file.call_args_list]
    assert any("pais_simple" in p for p in call_paths)
    assert any("departamentos_simple" in p for p in call_paths)
