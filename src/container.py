"""Dependency injection container for FastAPI."""

import os
from functools import lru_cache
from logging import Logger

from fastapi import Depends

from adapters.geo_layer_repository import FileSystemGeoLayerRepository
from adapters.mysql_alerts import MySQLAlertsRepository
from adapters.sqlite_history import SqliteHistoryRepository
from initializers import init_logger
from ports.mysql_repository import IMySQLRepository
from services.alert_generation_service import AlertGenerationService
from services.geo_intersection_service import GeoIntersectionService
from settings import Settings


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings.get_settings()


def get_logger(settings: Settings = Depends(get_settings)) -> Logger:
    """Return the application logger."""
    return init_logger(settings)


@lru_cache
def get_geo_repo() -> FileSystemGeoLayerRepository:
    """Return a singleton filesystem-backed geo layer repository."""
    settings = get_settings()
    logger = init_logger(settings)
    return FileSystemGeoLayerRepository(settings.data_dir, logger)


def get_intersection_service(
    repo: FileSystemGeoLayerRepository = Depends(get_geo_repo),
    logger: Logger = Depends(get_logger),
) -> GeoIntersectionService:
    """Return the geo intersection service."""
    return GeoIntersectionService(repo, logger)


@lru_cache
def get_history_repo() -> SqliteHistoryRepository:
    """Return a singleton SQLite job history repository."""
    settings = get_settings()
    return SqliteHistoryRepository(os.path.join(settings.data_dir, "history.db"))


@lru_cache
def get_mysql_repo() -> IMySQLRepository:
    """Return a singleton MySQL repository for alerts."""
    settings = get_settings()
    return MySQLAlertsRepository(
        host=settings.mysql_host,
        port=settings.mysql_port,
        database=settings.mysql_database,
        user=settings.mysql_user,
        password=settings.mysql_password,
    )


def get_alert_service(
    mysql_repo: IMySQLRepository = Depends(get_mysql_repo),
    geo_service: GeoIntersectionService = Depends(get_intersection_service),
    settings: Settings = Depends(get_settings),
    logger: Logger = Depends(get_logger),
) -> AlertGenerationService:
    """Return the alert generation service."""
    return AlertGenerationService(mysql_repo, geo_service, settings, logger)
