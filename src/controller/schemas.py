"""Pydantic input schemas for geo intersection endpoints."""

from typing import Any, Dict, Optional

from pydantic import BaseModel, model_validator


class GeoJSONInput(BaseModel):
    """Pydantic model for validating GeoJSON input (Geometry, Feature, or FeatureCollection)."""

    model_config = {"extra": "allow"}

    type: str

    @model_validator(mode="before")
    @classmethod
    def validate_geojson(cls, v: Any) -> Any:
        """Validate that the input is a dict with a 'type' field."""
        if not isinstance(v, dict):
            raise ValueError("Input must be a GeoJSON object")
        if not v.get("type"):
            raise ValueError("Missing 'type' field in GeoJSON")
        return v

    def extract_geometry(self) -> Dict[str, Any]:
        """Extract the geometry dict from any GeoJSON wrapper type."""
        data = self.model_dump()
        geojson_type = data.get("type", "").lower()

        if geojson_type == "featurecollection":
            features = data.get("features", [])
            if not features:
                raise ValueError("FeatureCollection is empty")
            return features[0].get("geometry") or {}
        if geojson_type == "feature":
            return data.get("geometry") or {}
        return data


class Phenomenon(BaseModel):
    """Model for a weather phenomenon."""

    code: int
    description: Optional[str]
