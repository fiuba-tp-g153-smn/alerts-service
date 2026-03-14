from unittest.mock import AsyncMock, MagicMock

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


async def test_intersect_country_simplified_returns_feature_collection(
    service, mock_repo, country_gdf, test_geometry
):
    mock_repo.get_layer.return_value = country_gdf

    result = await service.intersect_country(test_geometry, simplified=True)

    mock_repo.get_layer.assert_called_once_with(LayerType.COUNTRY, True)
    assert result["type"] == "FeatureCollection"
    assert "features" in result


async def test_intersect_country_simplified_no_intersection(service, mock_repo):
    geom = box(-80, -50, -70, -40)
    gdf = gpd.GeoDataFrame({"name": ["Far Away"]}, geometry=[geom], crs="EPSG:4326")
    mock_repo.get_layer.return_value = gdf

    test_geom = {
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

    result = await service.intersect_country(test_geom, simplified=True)

    assert result["features"] == []


async def test_intersect_departments_simplified_filters_correctly(
    service, mock_repo, deptos_gdf, test_geometry
):
    mock_repo.get_layer.return_value = deptos_gdf

    result = await service.intersect_departments(test_geometry, simplified=True)

    mock_repo.get_layer.assert_called_once_with(LayerType.DEPARTMENTS, True)
    assert len(result) == 1
    assert result[0]["properties"]["nombre"] == "Dep1"
    assert "geometry" in result[0]
    assert "intersection" in result[0]


async def test_intersect_departments_simplified_no_match(service, mock_repo):
    geom1 = box(-80, -40, -70, -30)
    geom2 = box(-60, -50, -50, -40)
    gdf = gpd.GeoDataFrame(
        {"nombre": ["Dep1", "Dep2"], "in_id": [1, 2]},
        geometry=[geom1, geom2],
        crs="EPSG:4326",
    )
    mock_repo.get_layer.return_value = gdf

    test_geom = {
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

    result = await service.intersect_departments(test_geom, simplified=True)

    assert result == []


async def test_intersect_country_invalid_geometry_raises(service, mock_repo):
    invalid_geometry = {"type": "Invalid", "coordinates": []}
    with pytest.raises(Exception):
        await service.intersect_country(invalid_geometry, simplified=True)


async def test_intersect_country_fullres_delegates_to_subprocess(
    service, mock_repo, test_geometry
):
    mock_repo.get_fullres_fgb_path.return_value = "/fake/path.fgb"
    expected = {"type": "FeatureCollection", "features": []}
    service._run_fullres_subprocess = AsyncMock(return_value=expected)

    result = await service.intersect_country(test_geometry, simplified=False)

    service._run_fullres_subprocess.assert_called_once()
    assert result == expected


async def test_intersect_departments_fullres_delegates_to_subprocess(
    service, mock_repo, test_geometry
):
    mock_repo.get_fullres_fgb_path.return_value = "/fake/path.fgb"
    subprocess_result = {"features": []}
    service._run_fullres_subprocess = AsyncMock(return_value=subprocess_result)

    result = await service.intersect_departments(test_geometry, simplified=False)

    service._run_fullres_subprocess.assert_called_once()
    assert result == []
