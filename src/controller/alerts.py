"""Weather alert generation endpoints."""

import time
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from container import get_alert_service, get_logger, get_mysql_repo
from controller.schemas import GeoJSONInput, Phenomenon
from ports.mysql_repository import IMySQLRepository
from services.alert_generation_service import AlertGenerationService

router = APIRouter(prefix="/alerts", tags=["Alerts"])


@router.post(
    "/generate",
    summary="Generate weather alert maps",
    response_description="Returns metadata and URLs to generated GIF maps",
)
async def generate_alert(
    geojson: GeoJSONInput,
    phenomenon_code: int = Query(
        ..., ge=1, le=92, description="Weather phenomenon code (1-92)"
    ),
    service: AlertGenerationService = Depends(get_alert_service),
    logger=Depends(get_logger),
):
    """
    Generate weather alert GIF maps for the given polygon and phenomenon.

    - **Body**: GeoJSON Geometry, Feature, or FeatureCollection
    - **phenomenon_code**: Integer code for the weather phenomenon (1-92)

    Returns URLs to two generated GIF files:
    - `gif_area_url`: Zoomed map of the affected area with labeled municipalities
    - `gif_gral_url`: Full Argentina map with the alert polygon highlighted

    Also inserts the alert record into the `taviso` table in MySQL.
    """
    start_time = time.perf_counter()
    try:
        geometry = geojson.extract_geometry()
        logger.info(
            f"generate_alert: processing (phenomenon={phenomenon_code},"
            f" type={geometry.get('type')})"
        )
        result = await service.generate_alert(geometry, phenomenon_code)
        elapsed = time.perf_counter() - start_time
        logger.info(
            f"generate_alert: done (alert_id={result['alert_id']})"
            f" in {elapsed:.3f}s"
        )
        return JSONResponse(content=result)
    except ValueError as e:
        logger.warning(f"generate_alert: bad request: {e}")
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        logger.error(f"generate_alert: layer file not found: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        logger.error(f"generate_alert: unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    "/phenomena",
    summary="Get available weather phenomena",
    response_description="Returns list of weather phenomenon codes and descriptions",
    response_model=List[Phenomenon],
)
async def get_phenomena(
    mysql_repo: IMySQLRepository = Depends(get_mysql_repo),
    logger=Depends(get_logger),
):
    """
    Get all available weather phenomenon codes and their descriptions.

    Returns a list of objects with:
    - **code**: Integer code for the weather phenomenon (1-92)
    - **description**: Human-readable description of the phenomenon (null for code 50)
    """
    try:
        phenomena = mysql_repo.get_all_phenomena()
        result = [
            Phenomenon(code=code, description=desc) for code, desc in phenomena.items()
        ]
        return result
    except Exception as e:
        logger.error(f"get_phenomena: unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
