from typing import Any, Dict

from pydantic import BaseModel, model_validator


class GeoJSONInput(BaseModel):
    model_config = {"extra": "allow"}

    type: str

    @model_validator(mode="before")
    @classmethod
    def validate_geojson(cls, v: Any) -> Any:
        if not isinstance(v, dict):
            raise ValueError("Input must be a GeoJSON object")
        if not v.get("type"):
            raise ValueError("Missing 'type' field in GeoJSON")
        return v

    def extract_geometry(self) -> Dict[str, Any]:
        data = self.model_dump()
        geojson_type = data.get("type", "").lower()

        if geojson_type == "featurecollection":
            features = data.get("features", [])
            if not features:
                raise ValueError("FeatureCollection is empty")
            return features[0].get("geometry")
        elif geojson_type == "feature":
            return data.get("geometry")
        else:
            return data
