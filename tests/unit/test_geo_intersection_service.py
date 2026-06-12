from unittest.mock import AsyncMock, MagicMock

import geopandas as gpd
import pytest
from shapely.geometry import box

from services.geo_intersection_service import GeoIntersectionService


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.get_country_layer = AsyncMock()
    repo.get_departments_layer = AsyncMock()
    return repo


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
    mock_repo.get_country_layer.return_value = country_gdf

    result = await service.intersect_country(test_geometry, detail_level=1)

    mock_repo.get_country_layer.assert_called_once_with(1)
    assert result["type"] == "FeatureCollection"
    assert len(result["features"]) > 0
    assert result["features"][0]["geometry"]["type"] in (
        "Polygon",
        "MultiPolygon",
        "GeometryCollection",
    )


async def test_intersect_country_simplified_passes_correct_level_to_repo(
    service, mock_repo, country_gdf, test_geometry
):
    mock_repo.get_country_layer.return_value = country_gdf

    await service.intersect_country(test_geometry, detail_level=3)

    call_args = mock_repo.get_country_layer.call_args
    assert call_args.args == (3,)


async def test_intersect_country_simplified_no_intersection(service, mock_repo):
    geom = box(-80, -50, -70, -40)
    gdf = gpd.GeoDataFrame({"name": ["Far Away"]}, geometry=[geom], crs="EPSG:4326")
    mock_repo.get_country_layer.return_value = gdf

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

    result = await service.intersect_country(test_geom, detail_level=1)

    assert result["features"] == []


async def test_intersect_departments_simplified_filters_correctly(
    service, mock_repo, deptos_gdf, test_geometry
):
    mock_repo.get_departments_layer.return_value = deptos_gdf

    result = await service.intersect_departments(test_geometry)

    mock_repo.get_departments_layer.assert_called_once_with()
    assert len(result) == 1
    assert result[0]["properties"]["nombre"] == "Dep1"
    assert "geometry" in result[0]
    assert "intersection" in result[0]


async def test_intersect_departments_simplified_returns_correct_structure_for_each_feature(
    service, mock_repo, deptos_gdf, test_geometry
):
    # Use a geometry that intersects both departments
    wide_geometry = {
        "type": "Polygon",
        "coordinates": [
            [
                [-59.0, -35.0],
                [-52.0, -35.0],
                [-52.0, -25.0],
                [-59.0, -25.0],
                [-59.0, -35.0],
            ]
        ],
    }
    mock_repo.get_departments_layer.return_value = deptos_gdf

    result = await service.intersect_departments(wide_geometry)

    assert len(result) == 2
    for feature in result:
        assert "properties" in feature
        assert "nombre" in feature["properties"]
        assert "geometry" in feature
        assert "intersection" in feature
        assert feature["geometry"]["type"] in ("Polygon", "MultiPolygon")


async def test_intersect_departments_simplified_no_match(service, mock_repo):
    geom1 = box(-80, -40, -70, -30)
    geom2 = box(-60, -50, -50, -40)
    gdf = gpd.GeoDataFrame(
        {"nombre": ["Dep1", "Dep2"], "in_id": [1, 2]},
        geometry=[geom1, geom2],
        crs="EPSG:4326",
    )
    mock_repo.get_departments_layer.return_value = gdf

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

    result = await service.intersect_departments(test_geom)

    assert result == []


async def test_intersect_country_invalid_geometry_raises(service, mock_repo):
    invalid_geometry = {"type": "Invalid", "coordinates": []}
    with pytest.raises(Exception):
        await service.intersect_country(invalid_geometry, detail_level=1)
