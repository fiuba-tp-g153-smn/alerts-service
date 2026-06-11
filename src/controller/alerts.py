"""Weather alert generation endpoints."""

import asyncio
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse

from container import (
    get_alert_service,
    get_logger,
    get_mysql_repo,
    get_taviso_repo,
)
from controller.schemas import (
    AlertCreateRequest,
    AlertSummary,
    PendingAlertSummary,
    Phenomenon,
)
from domain.models import PolygonTooLargeError
from ports.mysql_repository import IMySQLRepository
from ports.taviso_repository import ITavisoReadRepository
from services.alert_generation_service import AlertGenerationService

router = APIRouter(prefix="/alerts", tags=["Alerts"])


@router.post(
    "",
    summary="Generate weather alert maps",
    response_description="Returns metadata and URLs to generated GIF maps",
)
async def generate_alert(
    request: AlertCreateRequest,
    service: AlertGenerationService = Depends(get_alert_service),
    logger=Depends(get_logger),
):
    """
    Generate weather alert GIF maps for the given polygon and phenomenon.

    Request body:
    - **phenomenon_code**: Integer code for the weather phenomenon (1-92)
    - **geojson**: GeoJSON Geometry, Feature, or FeatureCollection

    Returns URLs to two generated GIF files:
    - `gif_area_url`: Zoomed map of the affected area with labeled municipalities
    - `gif_gral_url`: Full Argentina map with the alert polygon highlighted

    Also inserts the alert record into the `taviso_temporal` table in MySQL.
    """
    start_time = time.perf_counter()
    try:
        phenomenon_code = request.phenomenon_code
        geometry = request.geojson.extract_geometry()
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
    except PolygonTooLargeError as e:
        logger.error(f"generate_alert: polygon too large: {e}")
        return JSONResponse(
            status_code=413,
            content={"max_vertex_count": e.max_vertex_count},
        )
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


def _normalize_etag(raw: str) -> str:
    """Strip weak-validator prefix and surrounding whitespace from an ETag value."""
    return raw.strip().removeprefix("W/").strip()


@router.get(
    "/pending",
    summary="List pending alerts",
    response_description="Returns pending alerts from the taviso_temporal table",
    response_model=List[PendingAlertSummary],
)
async def get_pending_alerts(
    response: Response,
    since_id: Optional[int] = Query(
        None,
        ge=0,
        description="Return only pending alerts with IdAviso_temporal greater than this",
    ),
    if_none_match: Optional[str] = Header(None),
    mysql_repo: IMySQLRepository = Depends(get_mysql_repo),
    logger=Depends(get_logger),
):
    """
    List pending alerts (`taviso_temporal` rows with `Procesado='N'`) — generated
    by POST /alerts but not yet mirrored into the active `taviso` table.

    - **since_id** (optional): only return alerts with `IdAviso_temporal` greater
      than it.

    Supports conditional requests: the response carries an `ETag` of the form
    `"<count>-<max_id>"` over the pending set. The count detects removals
    (alerts processed, `Procesado` 'N'->'Y') and max_id detects insertions, so
    the ETag changes on any add or removal. Send it back as `If-None-Match` to
    get `304 Not Modified` while the pending set is unchanged. The ETag is opaque
    — do not reuse it as `since_id`.
    """
    try:
        count, max_id = await asyncio.to_thread(mysql_repo.get_pending_alerts_etag)
        etag = f'"{count}-{max_id or 0}"'

        if if_none_match and _normalize_etag(if_none_match) == etag:
            return Response(status_code=304, headers={"ETag": etag})

        rows = await asyncio.to_thread(mysql_repo.get_pending_alerts, since_id)
        response.headers["ETag"] = etag
        return [
            PendingAlertSummary(
                alert_id=row["IdAviso_temporal"],
                phenomenon=row["Fenomeno"],
                area=row["Area"],
                polygon=row["Poligono"],
                gif_gral_url=f"/alerts/{row['Gif_general']}",
                gif_area_url=f"/alerts/{row['Gif_zoom']}",
            )
            for row in rows
        ]
    except Exception as e:
        logger.error(f"get_pending_alerts: unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    "",
    summary="List active alerts",
    response_description="Returns active alerts from the taviso table",
    response_model=List[AlertSummary],
)
async def get_alerts(
    response: Response,
    since_id: Optional[int] = Query(
        None, ge=0, description="Return only alerts with IdAlerta greater than this"
    ),
    if_none_match: Optional[str] = Header(None),
    taviso_repo: ITavisoReadRepository = Depends(get_taviso_repo),
    logger=Depends(get_logger),
):
    """
    List active alerts (started and not expired) from the external `taviso` table.

    - **since_id** (optional): only return alerts with `IdAlerta` greater than it.

    Supports conditional requests: the response carries an `ETag` equal to the
    highest active `IdAlerta`. Send it back as `If-None-Match` to get `304 Not
    Modified` when there are no newer alerts.
    """
    try:
        max_id = await asyncio.to_thread(taviso_repo.get_max_active_alert_id)
        etag = f'"{max_id or 0}"'

        if if_none_match and _normalize_etag(if_none_match) == etag:
            return Response(status_code=304, headers={"ETag": etag})

        rows = await asyncio.to_thread(taviso_repo.get_active_alerts, since_id)
        response.headers["ETag"] = etag
        return [
            AlertSummary(
                alert_id=row["IdAlerta"],
                phenomenon=row["Fenomeno"],
                area=row["Area"],
                polygon=row["Poligono"],
                start_datetime=row["FechaHora"],
                end_datetime=row["FechaFin"],
            )
            for row in rows
        ]
    except Exception as e:
        logger.error(f"get_alerts: unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
