"""Scheduler setup for the alerts service."""

import asyncio
import glob
import io
import json
import os
import pickle
import shutil
import subprocess
import sys
from logging import Logger

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from adapters.geo_layer_processor import GeoLayerProcessor, _WORKER_ENV
from adapters.s3_storage import S3ObjectStorage
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
    detail_level = settings.alerts_detail_level
    tolerance = settings.detail_level_tolerances.get(detail_level, 0.005)

    def _local_date(stem: str) -> str | None:
        matches = sorted(
            glob.glob(
                os.path.join(data_dir, f"{stem}_L{detail_level}_T*_????????.geojson")
            )
        )
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
            IGeoLayerProcessor.tolerance_versioned_key(
                f"{layer['simplified_stem']}_L{detail_level}.geojson", tolerance
            ),
        )

        await processor.download(url, raw_tmp)
        await processor.simplify(raw_tmp, simplified_path, tolerance)
        os.remove(raw_tmp)
        logger.info(f"{layer['simplified_stem']}: ready.")


_CACHE_WORKER_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "geo_cache_worker.py")
)


async def _run_geo_cache_worker(tasks: list[dict], logger: Logger) -> None:
    """Spawn geo_cache_worker and stream the tasks via stdin.

    The heavy GeoPandas/Shapely/Cartopy work runs in the child so its memory is
    returned to the OS on exit and never loads into the main process. Uses
    create_subprocess_exec so CancelledError terminates the child immediately.
    """
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        _CACHE_WORKER_PATH,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_WORKER_ENV,
    )
    try:
        _, stderr = await proc.communicate(json.dumps(tasks).encode())
    except asyncio.CancelledError:
        proc.terminate()
        await proc.wait()
        raise
    if proc.returncode is not None and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, sys.executable, stderr)
    for line in stderr.decode(errors="replace").splitlines():
        if line.strip():
            logger.info("geo_cache_worker: %s", line)


async def _build_alert_cache(settings, logger: Logger) -> None:
    """Build dept/prov spatial index pickles from simplified GeoJSON files.

    The GeoJSON parsing runs in geo_cache_worker (subprocess); only the light
    latest-file glob happens here in the main process.
    """
    data_dir = settings.data_dir
    cache_dir = settings.alert_cache_dir
    os.makedirs(cache_dir, exist_ok=True)

    logger.info(
        "Building alert cache using detail level %d",
        settings.alerts_detail_level,
    )

    def _latest_geojson(stem: str) -> str | None:
        matches = sorted(
            glob.glob(
                os.path.join(
                    data_dir, f"{stem}_L{settings.alerts_detail_level}_*.geojson"
                )
            )
        )
        if not matches:
            logger.warning(
                "No L%d file found for %s — falling back to latest available",
                settings.alerts_detail_level,
                stem,
            )
            matches = sorted(glob.glob(os.path.join(data_dir, f"{stem}_*.geojson")))
        return matches[-1] if matches else None

    tasks: list[dict] = []
    outputs: list[str] = []
    for stem, out_name in [
        ("departamentos_simple", "dept_index.pkl"),
        ("provincias_simple", "prov_index.pkl"),
    ]:
        path = _latest_geojson(stem)
        if not path:
            logger.warning(f"No {stem} geojson found — skipping {out_name}")
            continue
        out = os.path.join(cache_dir, out_name)
        tasks.append({"op": "build_index", "in_path": path, "out_path": out})
        outputs.append(out)

    if not tasks:
        return

    await _run_geo_cache_worker(tasks, logger)
    for out in outputs:
        if os.path.exists(out):
            size_mb = os.path.getsize(out) / 1024 / 1024
            logger.info(f"  → {os.path.basename(out)} ({size_mb:.1f} MB)")


_IGN_SHP_DIR = "/app/data_alerts"
_BORDERS_SHP = os.path.join(_IGN_SHP_DIR, "limites.shp")

# ign_layers.pkl format version. Geometries (except 'place_labels') are stored
# pre-projected to ccrs.Mercator() so alert_generation_worker can render them via
# add_geometries(crs=ccrs.Mercator()), skipping cartopy's expensive per-request
# trace-based reprojection (~8s/render for ~135k vertices). Bump this whenever the
# stored geometry format, projection, OR the dict key names change, to force a
# rebuild. v3: dict keys standardized to English (group_*/countries/provinces/
# place_labels) — a v2 cache has the old Spanish keys and must be regenerated.
# The build itself lives in geo_cache_worker._build_ign; this version is passed to
# it via the task payload, so keep the two in sync when changing the stored format.
_IGN_CACHE_FORMAT_VERSION = 3


def _ign_cache_up_to_date(out_path: str) -> bool:
    """Check whether ign_layers.pkl exists and matches the current cache format."""
    if not os.path.exists(out_path):
        return False
    try:
        with open(out_path, "rb") as f:
            layers = pickle.load(f)
    except (pickle.UnpicklingError, EOFError, OSError):
        return False
    return layers.get("_format_version") == _IGN_CACHE_FORMAT_VERSION


async def _build_ign_cache(settings, logger: Logger) -> None:
    """Build ign_layers.pkl from IGN shapefiles via the geo_cache_worker subprocess."""
    cache_dir = settings.alert_cache_dir
    os.makedirs(cache_dir, exist_ok=True)
    out_path = os.path.join(cache_dir, "ign_layers.pkl")
    if _ign_cache_up_to_date(out_path):
        logger.info(
            "ign_layers.pkl already exists and is up to date — skipping rebuild."
        )
        return
    if not os.path.exists(_BORDERS_SHP):
        logger.warning(
            "IGN shapefiles not found at %s — skipping ign_layers.pkl", _IGN_SHP_DIR
        )
        return
    await _run_geo_cache_worker(
        [
            {
                "op": "build_ign",
                "shp_dir": _IGN_SHP_DIR,
                "out_path": out_path,
                "tolerance": settings.ign_simplify_tolerance,
                "format_version": _IGN_CACHE_FORMAT_VERSION,
            }
        ],
        logger,
    )


# Pre-rasterised inset PNG — name must match
# alert_generation_worker.INSET_CACHE_NAME.
_INSET_CACHE_NAME = "inset.png"
_INSET_SVG_PATH = "/app/data_alerts/cuarteron.svg"


def _build_inset_cache_sync(cache_dir: str, logger: Logger) -> None:
    """Rasterise cuarteron.svg to a transparent-background PNG, cached for the worker.

    Mirrors alert_generation_worker._load_inset_png's on-the-fly rasterisation +
    pixel masking, but runs once at cache-build time instead of on every alert
    generation subprocess (~1.9s saved per request).
    """
    if not os.path.exists(_INSET_SVG_PATH):
        logger.warning(
            "cuarteron.svg not found at %s — skipping inset.png",
            _INSET_SVG_PATH,
        )
        return

    import cairosvg  # local import: heavy native dep, optional
    import numpy as np
    from PIL import Image

    out_path = os.path.join(cache_dir, _INSET_CACHE_NAME)
    png_bytes = cairosvg.svg2png(url=_INSET_SVG_PATH, output_width=600)
    arr = np.array(Image.open(io.BytesIO(png_bytes)).convert("RGBA"))

    # SMN request: translucent grey (#bebebe) background so the map's light blue
    # shows through behind it; black lines/border stay opaque.
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    is_bg = (
        (np.abs(r.astype(int) - 190) < 25)
        & (np.abs(g.astype(int) - 190) < 25)
        & (np.abs(b.astype(int) - 190) < 25)
    )
    arr[is_bg, 3] = 0

    Image.fromarray(arr, "RGBA").save(out_path)
    logger.info("inset.png ready: %s", out_path)


def _inset_up_to_date(out_path: str, svg_path: str) -> bool:
    """Whether the cached inset.png is present and not older than its source SVG.

    Rebuild when the PNG is missing or stale; if the SVG is gone there is nothing to
    rebuild from, so keep whatever PNG exists.
    """
    if not os.path.exists(out_path):
        return False
    if not os.path.exists(svg_path):
        return True
    return os.path.getmtime(out_path) >= os.path.getmtime(svg_path)


async def _build_inset_cache(settings, logger: Logger) -> None:
    """Async wrapper: rasterise cuarteron.svg in a thread pool if missing/stale."""
    cache_dir = settings.alert_cache_dir
    os.makedirs(cache_dir, exist_ok=True)
    out_path = os.path.join(cache_dir, _INSET_CACHE_NAME)
    if _inset_up_to_date(out_path, _INSET_SVG_PATH):
        logger.info("inset.png already exists and is up to date — skipping rebuild.")
        return
    await asyncio.to_thread(_build_inset_cache_sync, cache_dir, logger)


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
    await _build_ign_cache(settings, logger)
    await _build_inset_cache(settings, logger)

    # Reuse the DI singleton (closed in the lifespan shutdown) instead of opening
    # a second connection to the same history.db — two unsynchronised connections
    # risk `database is locked`, and the scheduler-owned one would leak on shutdown.
    from container import get_history_repo

    history = get_history_repo()
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
