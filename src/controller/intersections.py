"""Geo intersection API endpoints."""

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from container import get_history_repo, get_intersection_service, get_logger
from controller.schemas import GeoJSONInput
from ports.history_repository import IHistoryRepository
from services.geo_intersection_service import GeoIntersectionService

router = APIRouter(prefix="/intersect", tags=["Geo Intersection"])


@router.post(
    "/country",
    summary="Intersect polygon with Argentina",
    response_description="Returns intersection with Argentina's territory",
)
async def intersect_country(
    geojson: GeoJSONInput,
    simplification_level: int = Query(
        0,
        ge=0,
        le=10,
        description="0 = full resolution, 1-10 = simplified layer with increasing result tolerance",
    ),
    service: GeoIntersectionService = Depends(get_intersection_service),
    logger=Depends(get_logger),
):
    """
    Return the intersection of the input polygon (GeoJSON) with Argentina's territory.

    Input: GeoJSON Geometry, Feature, or FeatureCollection.
    Output: GeoJSON FeatureCollection of intersection(s).
    """
    start_time = time.perf_counter()
    try:
        geometry = geojson.extract_geometry()
        logger.info(
            f"intersect_country: processing (simplification_level={simplification_level},"
            f" type={geometry.get('type')})"
        )
        result = await service.intersect_country(geometry, simplification_level)
        elapsed = time.perf_counter() - start_time
        logger.info(
            f"intersect_country: done (simplification_level={simplification_level})"
            f" in {elapsed:.3f}s"
        )
        return JSONResponse(content=result)
    except FileNotFoundError as e:
        logger.error(f"intersect_country: layer file not found: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:
        logger.warning(f"intersect_country: bad request: {e}")
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"intersect_country: unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post(
    "/departments",
    summary="Intersect polygon with departments",
    response_description="Returns list of intersecting departments with geometries",
)
async def intersect_departments(
    geojson: GeoJSONInput,
    simplification_level: int = Query(
        0,
        ge=0,
        le=10,
        description="0 = full resolution, 1–10 = simplified layer with increasing result tolerance",
    ),
    service: GeoIntersectionService = Depends(get_intersection_service),
    logger=Depends(get_logger),
):
    """
    Return a list of departments intersecting the input polygon (GeoJSON).

    Each result includes department properties, the full geometry, and the intersection geometry.

    Input: GeoJSON Geometry, Feature, or FeatureCollection.
    Output: List of departments with intersection and full geometry.
    """
    start_time = time.perf_counter()
    try:
        geometry = geojson.extract_geometry()
        logger.info(
            f"intersect_departments: processing (simplification_level={simplification_level},"
            f" type={geometry.get('type')})"
        )
        features = await service.intersect_departments(geometry, simplification_level)
        elapsed = time.perf_counter() - start_time
        logger.info(
            f"intersect_departments: done (simplification_level={simplification_level})"
            f" in {elapsed:.3f}s, {len(features)} departments"
        )
        return {"departments": features}
    except FileNotFoundError as e:
        logger.error(f"intersect_departments: layer file not found: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:
        logger.warning(f"intersect_departments: bad request: {e}")
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"intersect_departments: unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get(
    "/layer-refresh-history",
    tags=["General"],
    summary="Layer refresh job history",
    response_description="Returns recent layer refresh job runs",
)
def layer_refresh_history(
    limit: int = Query(20, ge=1, le=100),
    history_repo: IHistoryRepository = Depends(get_history_repo),
    logger=Depends(get_logger),
):
    """Return the most recent layer refresh job run records."""
    logger.info(f"Layer refresh history requested (limit={limit})")
    return {"runs": history_repo.get_recent(limit)}
