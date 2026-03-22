"""General API endpoints (root and health check)."""

from fastapi import APIRouter, Depends, status

from container import get_logger
from controller.responses import HEALTH_RESPONSES, ROOT_RESPONSES

router = APIRouter()


@router.get(
    "/",
    status_code=status.HTTP_200_OK,
    tags=["General"],
    summary="Root Endpoint",
    response_description="Return service status",
    responses=ROOT_RESPONSES,
)
def root(logger=Depends(get_logger)):
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
