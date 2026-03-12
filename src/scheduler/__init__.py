import asyncio
import glob as _glob
import os
from datetime import date
from logging import Logger

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from scheduler.history_tracker import HistoryTracker
from scheduler.layer_refresh_job import _download, _simplify, run_layer_refresh
from scheduler.s3_client import S3Client

RAW_FILES = {
    "pais.geojson": "country_geojson_url",
    "departamentos.geojson": "departments_geojson_url",
}
SIMPLIFIED_FILES = {
    "pais_simple.geojson": "pais.geojson",
    "departamentos_simple.geojson": "departamentos.geojson",
}


def _versioned_key(fname: str) -> str:
    stem, ext = os.path.splitext(fname)
    return f"{stem}_{date.today().strftime('%Y%m%d')}{ext}"


def _latest_local_key(data_dir: str, stem: str) -> str | None:
    matches = sorted(_glob.glob(os.path.join(data_dir, f"{stem}_????????.geojson")))
    return os.path.basename(matches[-1]) if matches else None


async def _ensure_layers(settings, logger: Logger, s3: S3Client) -> None:
    """
    For each geojson file: if missing locally, try S3 (latest versioned key).
    If still missing: download raw files from IGN, generate simplified files locally.
    """
    data_dir = settings.data_dir
    tolerance = float(getattr(settings, "simplify_tolerance", 0.01))

    async def _latest_s3_key(prefix: str) -> str | None:
        keys = await s3.list_objects(prefix)
        return sorted(keys)[-1] if keys else None

    async def _get_raw(fname: str) -> None:
        stem, ext = os.path.splitext(fname)
        if _latest_local_key(data_dir, stem):
            logger.info(f"{fname}: already present locally, skipping.")
            return
        if settings.s3_bucket_name:
            latest = await _latest_s3_key(f"{stem}_")
            if latest:
                local = os.path.join(data_dir, os.path.basename(latest))
                if await s3.download_file(latest, local):
                    return
        logger.info(f"{fname}: not in S3, will download from IGN.")
        versioned = _versioned_key(fname)
        local = os.path.join(data_dir, versioned)
        url = getattr(settings, RAW_FILES[fname])
        await _download(url, local, logger)
        if settings.s3_bucket_name:
            await s3.upload_file(local, versioned)

    async def _get_simplified(fname: str, source: str) -> None:
        stem, ext = os.path.splitext(fname)
        if _latest_local_key(data_dir, stem):
            logger.info(f"{fname}: already present locally, skipping.")
            return
        if settings.s3_bucket_name:
            latest = await _latest_s3_key(f"{stem}_")
            if latest:
                local = os.path.join(data_dir, os.path.basename(latest))
                if await s3.download_file(latest, local):
                    return
        logger.info(f"{fname}: not in S3, will generate from {source}.")
        src_stem = os.path.splitext(source)[0]
        src = os.path.join(data_dir, _latest_local_key(data_dir, src_stem))
        versioned = _versioned_key(fname)
        local = os.path.join(data_dir, versioned)
        await _simplify(src, local, tolerance, logger)
        if settings.s3_bucket_name:
            await s3.upload_file(local, versioned)

    # Download raw files in parallel
    await asyncio.gather(*[_get_raw(f) for f in RAW_FILES])

    # Generate simplified files in parallel (raw must be ready first)
    await asyncio.gather(
        *[_get_simplified(f, src) for f, src in SIMPLIFIED_FILES.items()]
    )

    logger.info("All geojson layers are ready.")


async def setup_scheduler(settings, logger: Logger) -> AsyncIOScheduler:
    data_dir = settings.data_dir
    os.makedirs(data_dir, exist_ok=True)

    s3 = S3Client(settings, logger)
    await _ensure_layers(settings, logger, s3)

    db_path = os.path.join(data_dir, "history.db")
    history = HistoryTracker(db_path, logger)

    scheduler = AsyncIOScheduler(timezone="UTC")

    async def _job():
        result = await run_layer_refresh(settings, logger)
        history.record_run(
            status=result["status"],
            files=result.get("files"),
            duration_sec=result.get("duration_seconds"),
            error=result.get("error"),
        )

    trigger = CronTrigger.from_crontab(settings.layer_update_cron, timezone="UTC")
    scheduler.add_job(_job, trigger, id="layer_refresh", replace_existing=True)
    logger.info(f"Scheduler configured with cron: {settings.layer_update_cron}")

    return scheduler


def get_history_tracker(settings) -> HistoryTracker:
    db_path = os.path.join(settings.data_dir, "history.db")
    return HistoryTracker(db_path)
