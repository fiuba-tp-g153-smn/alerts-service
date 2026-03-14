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
    assert "coordinates" in result


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
