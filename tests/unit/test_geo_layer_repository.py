from unittest.mock import MagicMock, patch

import pytest
import geopandas as gpd

from adapters.geo_layer_repository import FileSystemGeoLayerRepository, _versioned_stem
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


def test_get_layer_raises_file_not_found_when_no_files(tmp_path):
    logger = MagicMock()
    repo = FileSystemGeoLayerRepository(str(tmp_path), logger)

    with pytest.raises(FileNotFoundError):
        repo.get_layer(LayerType.COUNTRY, simplified=True)


@patch("adapters.geo_layer_repository.gpd.read_file")
def test_get_layer_caches_simplified_layers(mock_read_file, tmp_path):
    logger = MagicMock()
    repo = FileSystemGeoLayerRepository(str(tmp_path), logger)

    # Create dummy files
    (tmp_path / "pais_simple_20240101.geojson").touch()

    # Mock the return of gpd.read_file
    mock_gdf = MagicMock(spec=gpd.GeoDataFrame)
    mock_gdf.__len__.return_value = 1
    mock_read_file.return_value = mock_gdf

    # First call: should read from file
    result1 = repo.get_layer(LayerType.COUNTRY, simplified=True)
    mock_read_file.assert_called_once()
    assert result1 is mock_gdf

    # Second call: should read from cache
    result2 = repo.get_layer(LayerType.COUNTRY, simplified=True)
    assert mock_read_file.call_count == 1  # Still 1
    assert result2 is mock_gdf

    # Third call but with NOT simplified: should NOT read from cache
    (tmp_path / "pais_20240101.geojson").touch()
    mock_read_file.reset_mock()
    result3 = repo.get_layer(LayerType.COUNTRY, simplified=False)
    mock_read_file.assert_called_once()
    assert result3 is mock_gdf

    # Fourth call with NOT simplified: still shouldn't read from cache
    result4 = repo.get_layer(LayerType.COUNTRY, simplified=False)
    assert mock_read_file.call_count == 2
    assert result4 is mock_gdf
