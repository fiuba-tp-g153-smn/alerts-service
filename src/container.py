import os
from functools import lru_cache
from logging import Logger

from fastapi import Depends

from adapters.geo_layer_repository import FileSystemGeoLayerRepository
from adapters.sqlite_history import SqliteHistoryRepository
from initializers import init_logger
from settings import Settings


@lru_cache
def get_settings() -> Settings:
    return Settings.get_settings()


def get_logger(settings: Settings = Depends(get_settings)) -> Logger:
    return init_logger(settings)


def get_geo_repo(
    settings: Settings = Depends(get_settings),
    logger: Logger = Depends(get_logger),
) -> FileSystemGeoLayerRepository:
    return FileSystemGeoLayerRepository(settings.data_dir, logger)


def get_intersection_service(
    repo: FileSystemGeoLayerRepository = Depends(get_geo_repo),
    logger: Logger = Depends(get_logger),
):
    from services.geo_intersection_service import GeoIntersectionService

    return GeoIntersectionService(repo, logger)


def get_history_repo(
    settings: Settings = Depends(get_settings),
) -> SqliteHistoryRepository:
    return SqliteHistoryRepository(os.path.join(settings.data_dir, "history.db"))
