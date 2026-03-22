"""Scheduler setup for the alerts service."""

import glob
import os
from logging import Logger

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from adapters.geo_layer_processor import GeoLayerProcessor
from adapters.s3_storage import S3ObjectStorage
from adapters.sqlite_history import SqliteHistoryRepository
from services.geo_layer_sync_service import GeoLayerSyncService
from services.layer_refresh_service import LayerRefreshService

_RAW_TMP_FILES = ["pais_raw_tmp.geojson", "departamentos_raw_tmp.geojson"]


def _sweep_orphaned_tmp_files(data_dir: str, logger: Logger) -> None:
    """Remove leftover tmp files from previously crashed runs."""
    for fname in _RAW_TMP_FILES:
        path = os.path.join(data_dir, fname)
        if os.path.exists(path):
            logger.warning("Removing orphaned tmp file from previous run: %s", path)
            os.remove(path)
    for tmp_file in glob.glob(os.path.join(data_dir, "*.tmp")):
        logger.warning("Removing orphaned .tmp file from previous run: %s", tmp_file)
        os.remove(tmp_file)


async def setup_scheduler(settings, logger: Logger) -> AsyncIOScheduler:
    """Configure and return an AsyncIOScheduler with the layer refresh cron job."""
    data_dir = settings.data_dir
    os.makedirs(data_dir, exist_ok=True)

    _sweep_orphaned_tmp_files(data_dir, logger)

    storage = S3ObjectStorage(settings, logger)
    processor = GeoLayerProcessor(logger)
    sync_service = GeoLayerSyncService(settings, storage, processor, logger)
    await sync_service.ensure_all()
    await sync_service.regenerate()

    db_path = os.path.join(data_dir, "history.db")
    history = SqliteHistoryRepository(db_path)
    refresh_service = LayerRefreshService(settings, storage, processor, logger)

    scheduler = AsyncIOScheduler(timezone="UTC")

    async def _job():
        result = await refresh_service.run()
        history.record_run(
            status=result.status,
            files=result.files,
            duration_sec=result.duration_seconds,
            error=result.error,
        )

    trigger = CronTrigger.from_crontab(settings.layer_update_cron, timezone="UTC")
    scheduler.add_job(_job, trigger, id="layer_refresh", replace_existing=True)
    logger.info(f"Scheduler configured with cron: {settings.layer_update_cron}")

    return scheduler
