"""Scheduler setup for the alerts service."""

import asyncio
import glob
import io
import json
import os
import pickle
import shutil
from logging import Logger

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import cartopy.crs as ccrs
import geopandas as gpd
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


async def _build_alert_cache(settings, logger: Logger) -> None:
    """Build dept/prov spatial index pickles from simplified GeoJSON files."""
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

    def _build_index(path: str) -> list:
        with open(path, encoding="utf-8") as f:
            gj = json.load(f)
        index = []
        for feat in gj["features"]:
            g = shapely_shape(feat["geometry"])
            index.append((g.bounds, g))
        return index

    def _build_and_dump(stem: str, out_name: str) -> tuple[int, float] | None:
        path = _latest_geojson(stem)
        if not path:
            return None
        logger.info(f"Building {out_name} from {os.path.basename(path)} ...")
        index = _build_index(path)
        out = os.path.join(cache_dir, out_name)
        with open(out, "wb") as f:
            pickle.dump(index, f, protocol=pickle.HIGHEST_PROTOCOL)
        size_mb = os.path.getsize(out) / 1024 / 1024
        return len(index), size_mb

    for stem, out_name in [
        ("departamentos_simple", "dept_index.pkl"),
        ("provincias_simple", "prov_index.pkl"),
    ]:
        result = await asyncio.to_thread(_build_and_dump, stem, out_name)
        if result is None:
            logger.warning(f"No {stem} geojson found — skipping {out_name}")
            continue
        count, size_mb = result
        logger.info(f"  → {count} geometries → {out_name} ({size_mb:.1f} MB)")


_DATA_DIR = "/app/data_alerts"
_BORDERS_SHP = os.path.join(_DATA_DIR, "limites.shp")
_PROVINCES_SHP = os.path.join(_DATA_DIR, "Provincias.shp")
_REFERENCES_SHP = os.path.join(_DATA_DIR, "referencias.shp")
_PLACE_LABELS_SHP = os.path.join(_DATA_DIR, "toponimos.shp")

# Simplification tolerance for IGN geometries in the cache.
# 0.005° ≈ 500 m: invisible at national/regional scale, reduces cache size ~95 %.
_IGN_SIMPLIFY_TOLERANCE = 0.005

# ign_layers.pkl format version. Geometries (except 'place_labels') are stored
# pre-projected to ccrs.Mercator() so alert_generation_worker can render them via
# add_geometries(crs=ccrs.Mercator()), skipping cartopy's expensive per-request
# trace-based reprojection (~8s/render for ~135k vertices). Bump this whenever the
# stored geometry format, projection, OR the dict key names change, to force a
# rebuild. v3: dict keys standardized to English (group_*/countries/provinces/
# place_labels) — a v2 cache has the old Spanish keys and must be regenerated.
_IGN_CACHE_FORMAT_VERSION = 3


def _read_place_labels_manual(shp_path: str, logger: Logger) -> list:
    """Read toponimos.shp without geopandas/pyogrio to avoid the latin-1 encoding error.

    Parses the DBF with latin-1 via stdlib and extracts PointZ coordinates from the SHP.
    Filters only the types relevant for the map: 'arg', 'continen' (with Arg.),
    and 'isla'.
    """
    import struct

    dbf_path = shp_path.replace(".shp", ".dbf")

    # --- Read DBF attributes (latin-1) ---------------------------------------
    attrs: list = []
    try:
        with open(dbf_path, "rb") as f:
            hdr = f.read(32)
            num_recs = struct.unpack("<I", hdr[4:8])[0]
            hdr_size = struct.unpack("<H", hdr[8:10])[0]
            rec_size = struct.unpack("<H", hdr[10:12])[0]

            fields: list = []
            while True:
                fd = f.read(32)
                if not fd or fd[0] == 0x0D:
                    break
                fname = fd[:11].rstrip(b"\x00").decode("ascii", errors="replace")
                ftype = chr(fd[11])
                flen = fd[16]
                fields.append((fname, ftype, flen))

            f.seek(hdr_size)
            for _ in range(num_recs):
                flag = f.read(1)
                rec: dict = {}
                for fname, ftype, flen in fields:
                    raw = f.read(flen)
                    if ftype == "C":
                        rec[fname] = raw.rstrip(b"\x00 ").decode(
                            "latin-1", errors="replace"
                        )
                    elif ftype in ("N", "F"):
                        try:
                            rec[fname] = float(raw.strip()) if raw.strip() else None
                        except ValueError:
                            rec[fname] = None
                    else:
                        rec[fname] = raw.rstrip(b"\x00").decode(
                            "latin-1", errors="replace"
                        )
                if flag != b"*":  # not a deleted record
                    attrs.append(rec)
    except Exception as exc:
        logger.warning("Could not read %s: %s", dbf_path, exc)
        return []

    # --- Read SHP coordinates (PointZ = type 13, Point = type 1) -------------
    coords: list = []
    try:
        with open(shp_path, "rb") as f:
            shp_hdr = f.read(100)
            file_len = struct.unpack(">I", shp_hdr[24:28])[0] * 2
            while f.tell() < file_len:
                rec_hdr = f.read(8)
                if len(rec_hdr) < 8:
                    break
                content_len = struct.unpack(">I", rec_hdr[4:8])[0] * 2
                content = f.read(content_len)
                if len(content) < 4:
                    coords.append((None, None))
                    continue
                stype = struct.unpack("<i", content[:4])[0]
                if stype in (1, 13) and len(content) >= 20:  # Point or PointZ
                    x, y = struct.unpack("<dd", content[4:20])
                    coords.append((round(x, 4), round(y, 4)))
                else:
                    coords.append((None, None))
    except Exception as exc:
        logger.warning("Could not read %s: %s", shp_path, exc)
        return []

    # --- Combine and filter ---------------------------------------------------
    TYPES = {"arg", "continen", "isla"}
    EXCLUDE = {
        "ISLAS AURORA (Arg.)",
        "ISLAS GEORGIAS DEL SUR (Arg.)",
        "ISLAS SANDWICH DEL SUR (Arg.)",
    }
    result: list = []
    for (lon, lat), attr in zip(coords, attrs):
        if lon is None:
            continue
        kind = str(attr.get("tipo", "") or "")
        name = str(attr.get("nombre", "") or "")
        if kind not in TYPES:
            continue
        if kind == "continen" and "(Arg.)" not in name:
            continue
        if name in EXCLUDE:
            continue
        # Malvinas: show only "(Arg.)" without the full name
        if name == "ISLAS MALVINAS (Arg.)":
            name = "(Arg.)"
        result.append({"lon": lon, "lat": lat, "nombre": name, "tipo": kind})

    logger.info("Place labels loaded: %d (Arg.) labels + islands", len(result))
    return result


def _build_ign_cache_sync(cache_dir: str, logger: Logger) -> None:
    """Read IGN shapefiles, simplify geometries, and persist to ign_layers.pkl.

    Groups:
      group_a – international + Rio de la Plata bed + arg-uru maritime lateral (solid thick)
      group_b – interprovincial + coastline                                    (solid thin)
      group_c – outer Rio de la Plata                                          (dashed)
      group_d – Antarctic sector (by NAM)                                      (dot-dash)
      provinces – province polygons                                            (fill white)
      countries – neighbouring countries (tipo='país')                         (fill grey)
      place_labels – point labels: (Arg.) markers + island/country names       (text)
    """
    if not os.path.exists(_BORDERS_SHP):
        logger.warning(
            "IGN shapefiles not found at %s — skipping ign_layers.pkl", _DATA_DIR
        )
        return

    out_path = os.path.join(cache_dir, "ign_layers.pkl")
    logger.info("Building ign_layers.pkl from IGN shapefiles ...")

    tol = _IGN_SIMPLIFY_TOLERANCE
    pc = ccrs.PlateCarree()
    merc = ccrs.Mercator()

    def _simplify_wkb(geoms) -> list:
        """Simplify, project to Mercator, and serialise geometries to WKB hex strings.

        Pre-projecting here (one-time, at cache-build) lets the worker render via
        add_geometries(crs=ccrs.Mercator()), avoiding per-request reprojection.
        """
        result = []
        for g in geoms:
            if g is None or g.is_empty:
                continue
            sg = g.simplify(tol, preserve_topology=True)
            if sg.is_empty:
                continue
            pg = merc.project_geometry(sg, pc)
            if not pg.is_empty:
                result.append(pg.wkb_hex)
        return result

    # --- limites.shp ---------------------------------------------------------
    lim = gpd.read_file(_BORDERS_SHP)
    obj_col = "Objeto" if "Objeto" in lim.columns else "objeto"
    nam_col = "NAM" if "NAM" in lim.columns else "nam"

    GROUP_A = {
        "Límite internacional",
        "Límite del lecho y subsuelo del Río de la Plata",
        "Límite lateral marítimo argentino-uruguayo",
    }
    GROUP_B = {"Límite Interprovincial", "Línea de costa"}
    GROUP_C = {"Límite exterior del Río de la Plata"}
    ANTARCTIC_SECTOR = "Límite del Sector Antártico Argentino"

    ga, gb, gc, gd = [], [], [], []
    for _, row in lim.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        obj = row.get(obj_col, "") or ""
        nam = row.get(nam_col, "") or ""
        if ANTARCTIC_SECTOR in nam:
            gd.append(geom)
        elif obj in GROUP_A:
            ga.append(geom)
        elif obj in GROUP_B:
            gb.append(geom)
        elif obj in GROUP_C:
            gc.append(geom)

    # --- Provincias.shp ------------------------------------------------------
    provinces_wkb: list = []
    if os.path.exists(_PROVINCES_SHP):
        prov_df = gpd.read_file(_PROVINCES_SHP)
        provinces_wkb = _simplify_wkb(list(prov_df.geometry))

    # --- referencias.shp (neighboring countries) ------------------------------
    countries_wkb: list = []
    if os.path.exists(_REFERENCES_SHP):
        ref_df = gpd.read_file(_REFERENCES_SHP)
        type_col = "tipo" if "tipo" in ref_df.columns else "TIPO"
        countries_df = ref_df[ref_df[type_col] == "país"]
        countries_wkb = _simplify_wkb(list(countries_df.geometry))

    # --- toponimos.shp (text labels: (Arg.), island names, etc.) ---------------
    # pyogrio (geopandas backend) does NOT support the encoding parameter for
    # shapefiles, so we read the file manually with stdlib.
    place_labels: list = []
    if os.path.exists(_PLACE_LABELS_SHP):
        place_labels = _read_place_labels_manual(_PLACE_LABELS_SHP, logger)

    layers = {
        "_format_version": _IGN_CACHE_FORMAT_VERSION,
        "group_a": _simplify_wkb(ga),
        "group_b": _simplify_wkb(gb),
        "group_c": _simplify_wkb(gc),
        "group_d": _simplify_wkb(gd),
        "provinces": provinces_wkb,
        "countries": countries_wkb,
        "place_labels": place_labels,
    }

    with open(out_path, "wb") as f:
        pickle.dump(layers, f, protocol=pickle.HIGHEST_PROTOCOL)

    size_mb = os.path.getsize(out_path) / 1024 / 1024
    totals = {k: len(v) for k, v in layers.items() if isinstance(v, (list, dict))}
    logger.info("ign_layers.pkl ready: %s geometries, %.1f MB", totals, size_mb)


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
    """Async wrapper: run IGN shapefile pre-processing in a thread pool."""
    cache_dir = settings.alert_cache_dir
    os.makedirs(cache_dir, exist_ok=True)
    out_path = os.path.join(cache_dir, "ign_layers.pkl")
    if _ign_cache_up_to_date(out_path):
        logger.info(
            "ign_layers.pkl already exists and is up to date — skipping rebuild."
        )
        return
    await asyncio.to_thread(_build_ign_cache_sync, cache_dir, logger)


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
        logger.info(
            "inset.png already exists and is up to date — skipping rebuild."
        )
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
