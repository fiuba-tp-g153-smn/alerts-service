from unittest.mock import MagicMock

import geopandas as gpd
import pytest
from shapely.geometry import box

from domain.models import LayerType
from services.geo_intersection_service import GeoIntersectionService


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_repo():
    return MagicMock()


@pytest.fixture
def service(mock_repo, mock_logger):
    return GeoIntersectionService(mock_repo, mock_logger)


@pytest.fixture
def country_gdf():
    geom = box(-75, -55, -53, -22)
    return gpd.GeoDataFrame({"name": ["Argentina"]}, geometry=[geom], crs="EPSG:4326")


@pytest.fixture
def deptos_gdf():
    geom1 = box(-55, -27, -53, -26)
    geom2 = box(-58, -34, -56, -32)
    return gpd.GeoDataFrame(
        {"nombre": ["Dep1", "Dep2"], "in_id": [1, 2]},
        geometry=[geom1, geom2],
        crs="EPSG:4326",
    )


@pytest.fixture
def test_geometry():
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [-55.5, -26.5],
                [-54.5, -26.5],
                [-54.5, -26.0],
                [-55.5, -26.0],
                [-55.5, -26.5],
            ]
        ],
    }


def test_intersect_country_returns_feature_collection(
    service, mock_repo, country_gdf, test_geometry
):
    mock_repo.get_layer.return_value = country_gdf

    result = service.intersect_country(test_geometry, simplified=True)

    mock_repo.get_layer.assert_called_once_with(LayerType.COUNTRY, True)
    assert result["type"] == "FeatureCollection"
    assert "features" in result


def test_intersect_departments_returns_list(
    service, mock_repo, deptos_gdf, test_geometry
):
    mock_repo.get_layer.return_value = deptos_gdf

    result = service.intersect_departments(test_geometry, simplified=True)

    mock_repo.get_layer.assert_called_once_with(LayerType.DEPARTMENTS, True)
    assert isinstance(result, list)
    # Only Dep1 intersects the test polygon
    assert len(result) == 1
    assert result[0]["properties"]["nombre"] == "Dep1"
    assert "geometry" in result[0]
    assert "intersection" in result[0]


def test_intersect_country_invalid_geometry_raises(service, mock_repo):
    invalid_geometry = {"type": "Invalid", "coordinates": []}
    with pytest.raises((ValueError, Exception)):
        service.intersect_country(invalid_geometry, simplified=True)
