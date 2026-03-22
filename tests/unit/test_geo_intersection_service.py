import json
from unittest.mock import AsyncMock, MagicMock, patch

import geopandas as gpd
import pytest
from shapely.geometry import box, shape

from domain.models import LayerType
from services.geo_intersection_service import GeoIntersectionService

_TEST_LEVELS = {1: 0.0, 2: 0.001, 5: 0.008, 6: 0.01, 10: 0.2}


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_repo():
    return MagicMock()


@pytest.fixture
def service(mock_repo, mock_logger):
    return GeoIntersectionService(mock_repo, mock_logger, _TEST_LEVELS)


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

    result = await service.intersect_country(test_geometry, simplification_level=1)

    mock_repo.get_layer.assert_called_once_with(LayerType.COUNTRY, True)
    assert result["type"] == "FeatureCollection"
    assert len(result["features"]) > 0
    assert result["features"][0]["geometry"]["type"] in (
        "Polygon",
        "MultiPolygon",
        "GeometryCollection",
    )


async def test_intersect_country_simplified_passes_correct_simplified_flag_to_repo(
    service, mock_repo, country_gdf, test_geometry
):
    mock_repo.get_layer.return_value = country_gdf

    await service.intersect_country(test_geometry, simplification_level=1)

    # Verify the flag is passed as True, not False
    call_args = mock_repo.get_layer.call_args
    assert call_args.args == (LayerType.COUNTRY, True)


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

    result = await service.intersect_country(test_geom, simplification_level=1)

    assert result["features"] == []


async def test_intersect_country_level_10_simplifies_result(
    service, mock_repo, country_gdf, test_geometry
):
    mock_repo.get_layer.return_value = country_gdf

    result_l1 = await service.intersect_country(test_geometry, simplification_level=1)
    mock_repo.get_layer.reset_mock()
    mock_repo.get_layer.return_value = country_gdf
    result_l10 = await service.intersect_country(test_geometry, simplification_level=10)

    def coord_count(feature_collection):
        total = 0
        for f in feature_collection.get("features", []):
            coords = f.get("geometry", {}).get("coordinates", [])
            for ring in coords:
                total += len(ring)
        return total

    assert coord_count(result_l10) <= coord_count(result_l1)


async def test_intersect_country_level_1_does_not_apply_extra_tolerance(
    service, mock_repo, country_gdf, test_geometry
):
    # Level 1 has tolerance 0.0 — result should be unchanged by simplification
    mock_repo.get_layer.return_value = country_gdf

    result = await service.intersect_country(test_geometry, simplification_level=1)

    # Verify result is a valid FeatureCollection (no error from simplify with tolerance=0)
    assert result["type"] == "FeatureCollection"
    assert len(result["features"]) > 0


async def test_intersect_departments_simplified_filters_correctly(
    service, mock_repo, deptos_gdf, test_geometry
):
    mock_repo.get_layer.return_value = deptos_gdf

    result = await service.intersect_departments(test_geometry, simplification_level=1)

    mock_repo.get_layer.assert_called_once_with(LayerType.DEPARTMENTS, True)
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
    mock_repo.get_layer.return_value = deptos_gdf

    result = await service.intersect_departments(wide_geometry, simplification_level=1)

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

    result = await service.intersect_departments(test_geom, simplification_level=1)

    assert result == []


async def test_intersect_departments_level_10_simplifies_geometry_and_intersection(
    service, mock_repo, deptos_gdf
):
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
    mock_repo.get_layer.return_value = deptos_gdf

    result = await service.intersect_departments(wide_geometry, simplification_level=10)

    assert len(result) == 2
    for feature in result:
        assert "geometry" in feature
        assert "intersection" in feature


async def test_intersect_country_invalid_geometry_raises(service, mock_repo):
    invalid_geometry = {"type": "Invalid", "coordinates": []}
    with pytest.raises(Exception):
        await service.intersect_country(invalid_geometry, simplification_level=1)


async def test_intersect_country_fullres_delegates_to_subprocess(
    service, mock_repo, test_geometry
):
    mock_repo.get_fullres_fgb_path.return_value = "/fake/path.fgb"
    expected = {"type": "FeatureCollection", "features": []}
    service._run_fullres_subprocess = AsyncMock(return_value=expected)

    result = await service.intersect_country(test_geometry, simplification_level=0)

    call_args = service._run_fullres_subprocess.call_args
    assert call_args.args[0] == "country"
    assert call_args.args[2] == "/fake/path.fgb"
    assert result == expected


async def test_intersect_departments_fullres_delegates_to_subprocess(
    service, mock_repo, test_geometry
):
    mock_repo.get_fullres_fgb_path.return_value = "/fake/path.fgb"
    dep_feature = {"properties": {"nombre": "Dep1"}, "geometry": {}, "intersection": {}}
    service._run_fullres_subprocess = AsyncMock(
        return_value={"features": [dep_feature]}
    )

    result = await service.intersect_departments(test_geometry, simplification_level=0)

    call_args = service._run_fullres_subprocess.call_args
    assert call_args.args[0] == "departments"
    assert call_args.args[2] == "/fake/path.fgb"
    assert result == [dep_feature]


async def test_intersect_departments_fullres_passes_bbox_to_subprocess(
    service, mock_repo, test_geometry
):
    mock_repo.get_fullres_fgb_path.return_value = "/fake/path.fgb"
    service._run_fullres_subprocess = AsyncMock(return_value={"features": []})

    await service.intersect_departments(test_geometry, simplification_level=0)

    expected_bbox = tuple(shape(test_geometry).bounds)
    call_kwargs = service._run_fullres_subprocess.call_args.kwargs
    assert call_kwargs["bbox"] == expected_bbox


async def test_intersect_country_fullres_falls_back_to_geojson_path_when_no_fgb(
    service, mock_repo, test_geometry
):
    mock_repo.get_fullres_fgb_path.side_effect = FileNotFoundError("no fgb file")
    mock_repo.get_layer_path.return_value = "/fallback/pais_20240101.geojson"
    service._run_fullres_subprocess = AsyncMock(
        return_value={"type": "FeatureCollection", "features": []}
    )

    await service.intersect_country(test_geometry, simplification_level=0)

    mock_repo.get_layer_path.assert_called_once_with(
        LayerType.COUNTRY, simplified=False
    )
    assert (
        service._run_fullres_subprocess.call_args.args[2]
        == "/fallback/pais_20240101.geojson"
    )


async def test_run_fullres_subprocess_nonzero_exit_raises_runtime_error(
    service, test_geometry
):
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"some error"))

    input_geom = shape(test_geometry)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        with pytest.raises(RuntimeError, match="fullres_worker failed"):
            await service._run_fullres_subprocess(
                "country", input_geom, "/fake/path.fgb"
            )


async def test_run_fullres_subprocess_invalid_json_raises_runtime_error(
    service, test_geometry
):
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"not valid json", b""))

    input_geom = shape(test_geometry)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        with pytest.raises(RuntimeError, match="invalid JSON"):
            await service._run_fullres_subprocess(
                "country", input_geom, "/fake/path.fgb"
            )


async def test_run_fullres_subprocess_worker_error_in_result_raises_runtime_error(
    service, test_geometry
):
    error_output = json.dumps(
        {"results": {}, "errors": {"req": "geometry is invalid"}}
    ).encode()
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(error_output, b""))

    input_geom = shape(test_geometry)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        with pytest.raises(RuntimeError, match="fullres_worker error"):
            await service._run_fullres_subprocess(
                "country", input_geom, "/fake/path.fgb"
            )
