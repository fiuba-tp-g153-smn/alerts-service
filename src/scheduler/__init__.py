"""Scheduler setup and layer-ensuring logic for the alerts service."""

import glob as _glob
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
    _versioned_key,
)
from services.layer_refresh_service import LayerRefreshService

_LAYERS = [
    {
        "simplified_stem": "pais_simple",
        "fgb_stem": "pais",
        "url_attr": "country_geojson_url",
        "raw_tmp": "pais_raw_tmp.geojson",
    },
    {
        "simplified_stem": "departamentos_simple",
        "fgb_stem": "departamentos",
        "url_attr": "departments_geojson_url",
        "raw_tmp": "departamentos_raw_tmp.geojson",
    },
]


def _extract_date(key: str) -> str | None:
    """Return the 8-digit date suffix from a versioned filename, or None."""
    stem_part = os.path.splitext(os.path.basename(key))[0]
    parts = stem_part.split("_")
    if parts and len(parts[-1]) == 8 and parts[-1].isdigit():
        return parts[-1]
    return None


async def _ensure_layers(settings, logger: Logger, storage: S3ObjectStorage) -> None:
    """Ensure all geo layers are present locally and in S3 via date-stamp reconciliation."""
    data_dir = settings.data_dir
    levels: dict[int, float] = settings.simplification_levels

    def _local_date(stem: str, ext: str) -> str | None:
        matches = sorted(_glob.glob(os.path.join(data_dir, f"{stem}_????????{ext}")))
        return _extract_date(matches[-1]) if matches else None

    async def _s3_date(stem: str, ext: str) -> str | None:
        if not settings.s3_bucket_name:
            return None
        keys = [k for k in await storage.list_keys(f"{stem}_") if k.endswith(ext)]
        return _extract_date(sorted(keys)[-1]) if keys else None

    async def _reconcile_file(stem: str, ext: str) -> bool:
        """Ensure the canonical version exists locally and in S3. Returns False if re-gen needed."""
        local_date = _local_date(stem, ext)
        s3_date = await _s3_date(stem, ext)

        if local_date is None and s3_date is None:
            return False

        canonical = max(d for d in [local_date, s3_date] if d is not None)
        canonical_filename = f"{stem}_{canonical}{ext}"
        local_path = os.path.join(data_dir, canonical_filename)

        if not os.path.exists(local_path):
            logger.info(
                f"{canonical_filename}: missing locally, downloading from S3 ..."
            )
            if not await storage.download(canonical_filename, local_path):
                logger.warning(
                    f"{canonical_filename}: S3 download failed, will re-generate."
                )
                return False

        if settings.s3_bucket_name and s3_date != canonical:
            logger.info(f"{canonical_filename}: missing from S3, uploading ...")
            await storage.upload(local_path, canonical_filename)

        logger.info(f"{canonical_filename}: ready.")
        return True

    for layer in _LAYERS:
        missing_levels: list[tuple[int, float]] = []
        for level, tolerance in levels.items():
            stem = f"{layer['simplified_stem']}_L{level}"
            if not await _reconcile_file(stem, ".geojson"):
                missing_levels.append((level, tolerance))

        fgb_ok = await _reconcile_file(layer["fgb_stem"], ".fgb")

        if not missing_levels and fgb_ok:
            continue

        logger.info(f"Re-generating layer: {layer['fgb_stem']} ...")
        url = getattr(settings, layer["url_attr"])
        raw_tmp = os.path.join(data_dir, layer["raw_tmp"])

        await _download(url, raw_tmp, logger)

        for level, tolerance in missing_levels:
            stem = f"{layer['simplified_stem']}_L{level}"
            simplified_path = os.path.join(data_dir, _versioned_key(f"{stem}.geojson"))
            await _simplify(raw_tmp, simplified_path, tolerance, logger)
            if settings.s3_bucket_name:
                await storage.upload(simplified_path, _versioned_key(f"{stem}.geojson"))

        if not fgb_ok:
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


async def setup_scheduler(settings, logger: Logger) -> AsyncIOScheduler:
    """Configure and return an AsyncIOScheduler with the layer refresh cron job."""
    data_dir = settings.data_dir
    os.makedirs(data_dir, exist_ok=True)

    storage = S3ObjectStorage(settings, logger)
    await _ensure_layers(settings, logger, storage)

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
