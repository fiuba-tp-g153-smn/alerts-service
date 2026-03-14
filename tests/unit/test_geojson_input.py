import pytest
from pydantic import ValidationError

from controller.schemas import GeoJSONInput


def test_extract_geometry_from_geometry_type():
    data = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
    }
    obj = GeoJSONInput(**data)
    result = obj.extract_geometry()
    assert result["type"] == "Polygon"
    assert result["coordinates"] == [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]


def test_extract_geometry_from_feature():
    data = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [0, 0]},
        "properties": {},
    }
    obj = GeoJSONInput(**data)
    result = obj.extract_geometry()
    assert result == {"type": "Point", "coordinates": [0, 0]}


def test_extract_geometry_from_feature_collection():
    data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [1, 2]},
                "properties": {},
            }
        ],
    }
    obj = GeoJSONInput(**data)
    result = obj.extract_geometry()
    assert result == {"type": "Point", "coordinates": [1, 2]}


def test_extract_geometry_empty_feature_collection_raises():
    data = {"type": "FeatureCollection", "features": []}
    obj = GeoJSONInput(**data)
    with pytest.raises(ValueError):
        obj.extract_geometry()


def test_missing_type_field_raises():
    with pytest.raises(ValidationError):
        GeoJSONInput(**{"coordinates": []})


def test_non_dict_input_raises():
    with pytest.raises(ValidationError):
        GeoJSONInput.model_validate("not a dict")


def test_extract_geometry_feature_with_null_geometry_returns_empty_dict():
    data = {"type": "Feature", "geometry": None, "properties": {}}
    obj = GeoJSONInput(**data)
    result = obj.extract_geometry()
    assert result == {}


def test_extract_geometry_feature_collection_uses_first_feature_only():
    first_geom = {"type": "Point", "coordinates": [1, 2]}
    second_geom = {"type": "Point", "coordinates": [3, 4]}
    data = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": first_geom, "properties": {}},
            {"type": "Feature", "geometry": second_geom, "properties": {}},
        ],
    }
    obj = GeoJSONInput(**data)
    result = obj.extract_geometry()
    assert result == first_geom


def test_extract_geometry_feature_collection_with_none_geometry_in_first_feature_returns_empty_dict():
    data = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": None, "properties": {}},
        ],
    }
    obj = GeoJSONInput(**data)
    result = obj.extract_geometry()
    assert result == {}


def test_extract_geometry_geometry_type_passthrough_with_extra_fields():
    # GeoJSONInput uses extra="allow", so unknown fields are preserved on passthrough
    data = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
        "custom_field": "extra_value",
    }
    obj = GeoJSONInput(**data)
    result = obj.extract_geometry()
    assert result["type"] == "Polygon"
    assert result["custom_field"] == "extra_value"
