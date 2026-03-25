"""Scheduler setup for the alerts service."""

import glob
import json
import os
import pickle
import shutil
from logging import Logger

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from shapely.geometry import shape as shapely_shape

from adapters.geo_layer_processor import GeoLayerProcessor
from adapters.s3_storage import S3ObjectStorage
from adapters.sqlite_history import SqliteHistoryRepository
from ports.geo_layer_processor import IGeoLayerProcessor
from services.geo_layer_sync_service import GeoLayerSyncService
from services.geo_layer_sync_service import _extract_date
from services.layer_refresh_service import LayerRefreshService

_RAW_TMP_FILES = ["pais_raw_tmp.geojson", "departamentos_raw_tmp.geojson"]

# Alert-specific layers: simplified GeoJSON only (no FGB, no S3 sync needed)
_ALERT_LAYERS = [
    {
        "simplified_stem": "provincias_simple",
        "url_attr": "provinces_geojson_url",
        "raw_tmp": "provincias_raw_tmp.geojson",
    },
]


def _sweep_orphaned_tmp_files(data_dir: str, logger: Logger) -> None:
    """Remove leftover tmp files from previously crashed runs."""
    for fname in _RAW_TMP_FILES:
        path = os.path.join(data_dir, fname)
        if os.path.exists(path):
            logger.warning("Removing orphaned tmp file from previous run: %s", path)
            os.remove(path)
    # *.tmp  — legacy style (out_path + ".tmp")
    for tmp_file in glob.glob(os.path.join(data_dir, "*.tmp")):
        logger.warning("Removing orphaned .tmp file from previous run: %s", tmp_file)
        os.remove(tmp_file)
    # *.tmp.*  — current style (stem + ".tmp" + ext, e.g. pais.tmp.fgb)
    for tmp_file in glob.glob(os.path.join(data_dir, "*.tmp.*")):
        logger.warning("Removing orphaned .tmp file from previous run: %s", tmp_file)
        os.remove(tmp_file)
    # .fgb directories — left by the buggy out_path+".tmp" naming where GDAL created
    # a directory datasource that os.replace then renamed to the canonical .fgb path
    for fgb_path in glob.glob(os.path.join(data_dir, "*.fgb")):
        if os.path.isdir(fgb_path):
            logger.warning("Removing corrupt .fgb directory: %s", fgb_path)
            shutil.rmtree(fgb_path)


async def _ensure_alert_layers(
    settings, logger: Logger, processor: GeoLayerProcessor
) -> None:
    """Ensure alert-specific layers are present locally (simplified GeoJSON only)."""
    data_dir = settings.data_dir
    tolerance = float(getattr(settings, "simplify_tolerance", 0.01))

    def _local_date(stem: str) -> str | None:
        matches = sorted(glob.glob(os.path.join(data_dir, f"{stem}_????????.geojson")))
        return _extract_date(matches[-1]) if matches else None

    for layer in _ALERT_LAYERS:
        if _local_date(layer["simplified_stem"]) is not None:
            logger.info(f"{layer['simplified_stem']}: ready.")
            continue

        logger.info(f"Downloading alert layer: {layer['simplified_stem']} ...")
        url = getattr(settings, layer["url_attr"])
        raw_tmp = os.path.join(data_dir, layer["raw_tmp"])
        simplified_path = os.path.join(
            data_dir,
            IGeoLayerProcessor.versioned_key(f"{layer['simplified_stem']}.geojson"),
        )

        await processor.download(url, raw_tmp)
        await processor.simplify(raw_tmp, simplified_path, tolerance)
        os.remove(raw_tmp)
        logger.info(f"{layer['simplified_stem']}: ready.")


async def _build_alert_cache(settings, logger: Logger) -> None:
    """Build dept/prov spatial index pickles from simplified GeoJSON files."""
    data_dir = settings.data_dir
    cache_dir = settings.alert_cache_dir
    os.makedirs(cache_dir, exist_ok=True)

    def _latest_geojson(stem: str) -> str | None:
        matches = sorted(glob.glob(os.path.join(data_dir, f"{stem}_*.geojson")))
        return matches[-1] if matches else None

    def _build_index(path: str) -> list:
        with open(path, encoding="utf-8") as f:
            gj = json.load(f)
        index = []
        for feat in gj["features"]:
            g = shapely_shape(feat["geometry"])
            index.append((g.bounds, g))
        return index

    for stem, out_name in [
        ("departamentos_simple", "dept_index.pkl"),
        ("provincias_simple", "prov_index.pkl"),
    ]:
        path = _latest_geojson(stem)
        if not path:
            logger.warning(f"No {stem} geojson found — skipping {out_name}")
            continue
        logger.info(f"Building {out_name} from {os.path.basename(path)} ...")
        index = _build_index(path)
        out = os.path.join(cache_dir, out_name)
        with open(out, "wb") as f:
            pickle.dump(index, f, protocol=pickle.HIGHEST_PROTOCOL)
        size_mb = os.path.getsize(out) / 1024 / 1024
        logger.info(f"  → {len(index)} geometries → {out_name} ({size_mb:.1f} MB)")


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
    await _ensure_alert_layers(settings, logger, processor)
    await _build_alert_cache(settings, logger)

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
