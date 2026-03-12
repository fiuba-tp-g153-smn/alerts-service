import os
from logging import Logger

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from scheduler.history_tracker import HistoryTracker
from scheduler.layer_refresh_job import GEOJSON_FILES, run_layer_refresh
from scheduler.s3_client import S3Client


async def setup_scheduler(settings, logger: Logger) -> AsyncIOScheduler:
    data_dir = settings.data_dir
    os.makedirs(data_dir, exist_ok=True)

    # Try to restore geojson files from S3 if missing locally
    s3 = S3Client(settings, logger)
    missing = [f for f in GEOJSON_FILES if not os.path.exists(os.path.join(data_dir, f))]

    if missing:
        if settings.s3_bucket_name:
            logger.info(f"Attempting to restore {len(missing)} file(s) from S3 ...")
            restored = []
            for fname in missing:
                ok = await s3.download_file(fname, os.path.join(data_dir, fname))
                if ok:
                    restored.append(fname)
            still_missing = [f for f in GEOJSON_FILES if not os.path.exists(os.path.join(data_dir, f))]
        else:
            still_missing = missing

        if still_missing:
            logger.info("Running initial layer refresh (files not available in S3) ...")
            result = await run_layer_refresh(settings, logger)
            logger.info(f"Initial refresh: {result['status']}")
    else:
        logger.info("All geojson files present locally — skipping initial download.")

    db_path = os.path.join(data_dir, "history.db")
    history = HistoryTracker(db_path)

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
