import geopandas as gpd
import pytest
from shapely.geometry import box

from geo_utils import build_department_features


@pytest.fixture
def intersecting_gdf():
    geom1 = box(-55, -27, -53, -26)
    geom2 = box(-58, -34, -56, -32)
    inter1 = box(-54.5, -26.5, -54.0, -26.1)
    inter2 = box(-57.5, -33.5, -57.0, -32.5)
    gdf = gpd.GeoDataFrame(
        {"nombre": ["Dep1", "Dep2"], "in_id": [1, 2]},
        geometry=[geom1, geom2],
        crs="EPSG:4326",
    )
    gdf["intersection"] = [inter1, inter2]
    return gdf


def test_returns_one_feature_per_row(intersecting_gdf):
    features = build_department_features(intersecting_gdf)
    assert len(features) == 2


def test_properties_exclude_geometry_and_intersection(intersecting_gdf):
    features = build_department_features(intersecting_gdf)
    for feature in features:
        assert "nombre" in feature["properties"]
        assert "in_id" in feature["properties"]
        assert "geometry" not in feature["properties"]
        assert "intersection" not in feature["properties"]


def test_geometry_and_intersection_are_geo_interface_dicts(intersecting_gdf):
    features = build_department_features(intersecting_gdf)
    for feature in features:
        assert isinstance(feature["geometry"], dict)
        assert "type" in feature["geometry"]
        assert isinstance(feature["intersection"], dict)
        assert "type" in feature["intersection"]


def test_empty_dataframe_returns_empty_list():
    empty_gdf = gpd.GeoDataFrame(
        {"nombre": [], "in_id": []},
        geometry=[],
    )
    empty_gdf["intersection"] = []

    result = build_department_features(empty_gdf)

    assert result == []
