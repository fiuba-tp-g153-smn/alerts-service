"""Dependency injection container for FastAPI."""

import os
from functools import lru_cache
from logging import Logger

from fastapi import Depends, Request

from adapters.geo_layer_repository import FileSystemGeoLayerRepository
from adapters.mysql_alerts import MySQLAlertsRepository
from adapters.mysql_taviso import MySQLTavisoReadRepository
from adapters.sqlite_history import SqliteHistoryRepository
from adapters.sqlite_job_store import SqliteJobStore
from adapters.sqlite_processor_metrics import SqliteProcessorMetricsRepository
from initializers import init_logger
from ports.job_store import IJobStore
from ports.metrics_repository import IProcessorMetricsRepository
from ports.mysql_repository import IMySQLRepository
from ports.taviso_repository import ITavisoReadRepository
from services.alert_generation_service import AlertGenerationService
from services.alert_job_processor import AlertJobProcessor
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
    ttl_s = settings.layer_cache_ttl_minutes * 60.0
    return FileSystemGeoLayerRepository(settings.data_dir, logger, ttl_s=ttl_s)


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
def get_job_store() -> IJobStore:
    """Return the singleton always-on durable job store (schema owned by migrations)."""
    settings = get_settings()
    return SqliteJobStore(
        settings.jobs_db_path,
        retention_days=settings.metrics_retention_days,
        max_rows=settings.metrics_max_rows,
    )


@lru_cache
def get_metrics_repo() -> IProcessorMetricsRepository:
    """Return the singleton SQLite processor-metrics store (schema via migrations)."""
    settings = get_settings()
    return SqliteProcessorMetricsRepository(settings.metrics_db_path)


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


@lru_cache
def get_taviso_repo() -> ITavisoReadRepository:
    """Return a singleton read-only repository for the external taviso database."""
    settings = get_settings()
    return MySQLTavisoReadRepository(
        host=settings.mysql_taviso_host,
        port=settings.mysql_taviso_port,
        database=settings.mysql_taviso_database,
        user=settings.mysql_taviso_user,
        password=settings.mysql_taviso_password,
    )


def get_alert_service(
    mysql_repo: IMySQLRepository = Depends(get_mysql_repo),
    geo_service: GeoIntersectionService = Depends(get_intersection_service),
    settings: Settings = Depends(get_settings),
    logger: Logger = Depends(get_logger),
) -> AlertGenerationService:
    """Return the alert generation service."""
    return AlertGenerationService(mysql_repo, geo_service, settings, logger)


@lru_cache
def get_singleton_logger() -> Logger:
    """Return a process-wide logger for long-lived background components."""
    return init_logger(get_settings())


@lru_cache
def get_singleton_intersection_service() -> GeoIntersectionService:
    """Return a process-wide geo intersection service (shares the geo repo)."""
    return GeoIntersectionService(get_geo_repo(), get_singleton_logger())


@lru_cache
def get_singleton_alert_service() -> AlertGenerationService:
    """Return a process-wide alert service for the background worker pool.

    Shares the singleton MySQL/geo repositories, so it adds no extra connection
    pools — only a stateless logger and intersection service are duplicated.
    """
    return AlertGenerationService(
        get_mysql_repo(),
        get_singleton_intersection_service(),
        get_settings(),
        get_singleton_logger(),
    )


def get_job_processor(request: Request) -> AlertJobProcessor:
    """Return the alert job processor created and owned by the app lifespan."""
    return request.app.state.alert_job_processor
