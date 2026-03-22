"""Scheduler setup for the alerts service."""

import os
from logging import Logger

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from adapters.s3_storage import S3ObjectStorage
from adapters.sqlite_history import SqliteHistoryRepository
from scheduler.layer_refresh_job import (
    _convert_to_fgb,
    _download,
    _simplify,
    _tolerance_versioned_key,
    _versioned_key,
)
from services.geo_layer_sync_service import GeoLayerSyncService
from services.layer_refresh_service import LayerRefreshService


async def setup_scheduler(
    settings, logger: Logger
) -> AsyncIOScheduler:  # pylint: disable=too-many-locals
    """Configure and return an AsyncIOScheduler with the layer refresh cron job."""
    data_dir = settings.data_dir
    os.makedirs(data_dir, exist_ok=True)

    storage = S3ObjectStorage(settings, logger)
    sync_service = GeoLayerSyncService(settings, storage, logger)
    needs_regen = await sync_service.ensure_all()

    for layer, missing_levels, fgb_needed in needs_regen:
        logger.info(f"Re-generating layer: {layer['fgb_stem']} ...")
        url = getattr(settings, layer["url_attr"])
        raw_tmp = os.path.join(data_dir, layer["raw_tmp"])

        await _download(url, raw_tmp, logger)

        for level, tolerance in missing_levels:
            stem = f"{layer['simplified_stem']}_L{level}"
            simplified_path = os.path.join(
                data_dir, _tolerance_versioned_key(f"{stem}.geojson", tolerance)
            )
            await _simplify(raw_tmp, simplified_path, tolerance, logger)
            if settings.s3_bucket_name:
                await storage.upload(
                    simplified_path,
                    _tolerance_versioned_key(f"{stem}.geojson", tolerance),
                )

        if fgb_needed:
            fgb_path = os.path.join(
                data_dir, _versioned_key(f"{layer['fgb_stem']}.fgb")
            )
            await _convert_to_fgb(raw_tmp, fgb_path, logger)
            if settings.s3_bucket_name:
                await storage.upload(
                    fgb_path, _versioned_key(f"{layer['fgb_stem']}.fgb")
                )

        os.remove(raw_tmp)

    logger.info("All geo layers are ready.")

    db_path = os.path.join(data_dir, "history.db")
    history = SqliteHistoryRepository(db_path)
    refresh_service = LayerRefreshService(settings, storage, logger)

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
