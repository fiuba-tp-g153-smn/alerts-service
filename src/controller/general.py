# Standard library imports
import json
import os
import time
from typing import Any, Dict

# Third-party imports
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse
import geopandas as gpd
from shapely.geometry import shape

# Local imports
from controller.responses import HEALTH_RESPONSES, ROOT_RESPONSES
from dependencies import logger

router = APIRouter()

# Constants for geo data paths
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
COUNTRY_FULL_PATH = os.path.join(DATA_DIR, "pais.geojson")
COUNTRY_SIMPLE_PATH = os.path.join(DATA_DIR, "pais_simple.geojson")
DEPTOS_FULL_PATH = os.path.join(DATA_DIR, "departamentos.geojson")
DEPTOS_SIMPLE_PATH = os.path.join(DATA_DIR, "departamentos_simple.geojson")


# Helper functions
def extract_geometry(geojson: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract geometry from various GeoJSON formats.
    Supports: Geometry, Feature, and FeatureCollection.
    """
    geojson_type = geojson.get("type", "").lower()

    if geojson_type == "featurecollection":
        # Extract first feature's geometry
        features = geojson.get("features", [])
        if not features:
            raise ValueError("FeatureCollection is empty")
        return features[0].get("geometry")
    elif geojson_type == "feature":
        # Extract geometry from feature
        return geojson.get("geometry")
    else:
        # Assume it's already a geometry
        return geojson


def load_gdf(path: str) -> gpd.GeoDataFrame:
    """Load a GeoDataFrame from a file, raising HTTP 500 if missing."""
    if not os.path.exists(path):
        raise HTTPException(status_code=500, detail=f"Missing data file: {path}")
    return gpd.read_file(path)


# General endpoints
@router.get(
    "/",
    status_code=status.HTTP_200_OK,
    tags=["General"],
    summary="Root Endpoint",
    response_description="Return service status",
    responses=ROOT_RESPONSES,
)
def root():
    """Check if the API service is up and running."""
    logger.info("Root endpoint was accessed")
    return {"status": "ok", "service": "alerts-service"}


@router.get(
    "/health",
    status_code=status.HTTP_200_OK,
    tags=["General"],
    summary="Health Check",
    response_description="Returns 200 if service is healthy",
    responses=HEALTH_RESPONSES,
)
def health_check():
    """Perform a health check of the service."""
    return {"status": "running"}


# Geo intersection endpoints
@router.post(
    "/intersect-country",
    tags=["Geo Intersection"],
    summary="Intersect polygon with Argentina",
    response_description="Returns intersection with Argentina's territory",
)
def intersect_country(
    geojson: Dict[str, Any],
    use_simplified: bool = Query(
        True, description="Use simplified geometries (faster, lower detail)"
    ),
):
    """
    Return the intersection of the input polygon (GeoJSON) with Argentina's territory.

    Input: GeoJSON Geometry, Feature, or FeatureCollection.
    Output: GeoJSON FeatureCollection of intersection(s).
    """
    start_time = time.time()
    try:
        geometry = extract_geometry(geojson)
        input_geom = shape(geometry)
        country_path = COUNTRY_SIMPLE_PATH if use_simplified else COUNTRY_FULL_PATH
        country_gdf = load_gdf(country_path)
        intersection = country_gdf[country_gdf.intersects(input_geom)]
        intersection = intersection.intersection(input_geom)
        result = json.loads(intersection.to_json())
        elapsed = time.time() - start_time
        logger.info(f"intersect_country (simplified={use_simplified}): {elapsed:.3f}s")
        return JSONResponse(content=result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/intersect-departments",
    tags=["Geo Intersection"],
    summary="Intersect polygon with departments",
    response_description="Returns list of intersecting departments with geometries",
)
def intersect_departments(
    geojson: Dict[str, Any],
    use_simplified: bool = Query(
        True, description="Use simplified geometries (faster, lower detail)"
    ),
):
    """
    Return a list of departments intersecting the input polygon (GeoJSON).

    Each result includes department properties, the full geometry, and the intersection geometry.

    Input: GeoJSON Geometry, Feature, or FeatureCollection.
    Output: List of departments with intersection and full geometry.
    """
    start_time = time.time()
    try:
        geometry = extract_geometry(geojson)
        input_geom = shape(geometry)
        deptos_path = DEPTOS_SIMPLE_PATH if use_simplified else DEPTOS_FULL_PATH
        deptos_gdf = load_gdf(deptos_path)
        mask = deptos_gdf.intersects(input_geom)
        intersecting = deptos_gdf[mask].copy()
        intersecting["intersection"] = intersecting["geometry"].intersection(input_geom)

        features = []
        for _, row in intersecting.iterrows():
            features.append(
                {
                    "properties": {
                        k: row[k]
                        for k in row.index
                        if k not in ("geometry", "intersection")
                    },
                    "geometry": row["geometry"].__geo_interface__,
                    "intersection": row["intersection"].__geo_interface__,
                }
            )
        elapsed = time.time() - start_time
        logger.info(
            f"intersect_departments (simplified={use_simplified}): {elapsed:.3f}s"
        )
        return {"departments": features}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
