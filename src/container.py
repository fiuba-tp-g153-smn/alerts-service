"""Dependency injection container for FastAPI."""

import os
from functools import lru_cache
from logging import Logger

from fastapi import Depends

from adapters.geo_layer_repository import FileSystemGeoLayerRepository
from adapters.sqlite_history import SqliteHistoryRepository
from initializers import init_logger
from services.fullres_batch_manager import FullresBatchManager
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
def get_batch_manager() -> FullresBatchManager:
    """Return the singleton batch manager for full resolution requests.

    Using lru_cache without arguments creates a true singleton instance
    that lives for the duration of the application.
    """
    settings = get_settings()
    logger = init_logger(settings)
    return FullresBatchManager(logger)


def get_geo_repo(
    settings: Settings = Depends(get_settings),
    logger: Logger = Depends(get_logger),
) -> FileSystemGeoLayerRepository:
    """Return a filesystem-backed geo layer repository."""
    return FileSystemGeoLayerRepository(settings.data_dir, logger)


def get_intersection_service(
    repo: FileSystemGeoLayerRepository = Depends(get_geo_repo),
    logger: Logger = Depends(get_logger),
    batch_manager: FullresBatchManager = Depends(get_batch_manager),
) -> GeoIntersectionService:
    """Return the geo intersection service."""
    return GeoIntersectionService(repo, logger, batch_manager)


def get_history_repo(
    settings: Settings = Depends(get_settings),
) -> SqliteHistoryRepository:
    """Return the SQLite job history repository."""
    return SqliteHistoryRepository(os.path.join(settings.data_dir, "history.db"))
