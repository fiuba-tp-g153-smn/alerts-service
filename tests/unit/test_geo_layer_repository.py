from unittest.mock import MagicMock

import pytest

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
