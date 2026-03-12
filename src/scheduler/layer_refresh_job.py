import asyncio
import os
import time
from datetime import date
from logging import Logger
from typing import TYPE_CHECKING, Any, Dict, List

import aiohttp
import geopandas as gpd

from scheduler.s3_client import S3Client

if TYPE_CHECKING:
    from settings import Settings

os.environ.setdefault("OGR_GEOJSON_MAX_OBJ_SIZE", "0")

GEOJSON_FILES = [
    "pais.geojson",
    "departamentos.geojson",
    "pais_simple.geojson",
    "departamentos_simple.geojson",
]


def _versioned_key(fname: str) -> str:
    stem, ext = os.path.splitext(fname)
    return f"{stem}_{date.today().strftime('%Y%m%d')}{ext}"


async def _download(url: str, out_path: str, logger: Logger) -> None:
    logger.info(f"Downloading {url} ...")
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=600)) as resp:
            resp.raise_for_status()
            content = await resp.read()
    with open(out_path, "wb") as f:
        f.write(content)
    size_mb = os.path.getsize(out_path) / 1_048_576
    logger.info(f"Saved {out_path} ({size_mb:.1f} MB)")


async def _simplify(
    in_path: str, out_path: str, tolerance: float, logger: Logger
) -> None:
    logger.info(f"Simplifying {in_path} (tolerance={tolerance}) ...")

    def _run():
        gdf = gpd.read_file(in_path)
        gdf["geometry"] = gdf["geometry"].simplify(tolerance, preserve_topology=True)
        gdf.to_file(out_path, driver="GeoJSON")

    await asyncio.to_thread(_run)
    logger.info(f"Simplified → {out_path}")


async def run_layer_refresh(settings: "Settings", logger: Logger) -> Dict[str, Any]:
    start = time.monotonic()
    data_dir = settings.data_dir
    os.makedirs(data_dir, exist_ok=True)
    tolerance = float(getattr(settings, "simplify_tolerance", 0.01))

    country_url = getattr(
        settings,
        "country_geojson_url",
        "https://wms.ign.gob.ar/geoserver/ows?service=WFS&version=1.0.0&request=GetFeature&typeName=ign:pais&outputFormat=application/json",
    )
    departments_url = getattr(
        settings,
        "departments_geojson_url",
        "https://wms.ign.gob.ar/geoserver/ows?service=WFS&version=1.0.0&request=GetFeature&typeName=ign:departamento&outputFormat=application/json",
    )

    country_path = os.path.join(data_dir, _versioned_key("pais.geojson"))
    country_simple_path = os.path.join(data_dir, _versioned_key("pais_simple.geojson"))
    deptos_path = os.path.join(data_dir, _versioned_key("departamentos.geojson"))
    deptos_simple_path = os.path.join(
        data_dir, _versioned_key("departamentos_simple.geojson")
    )

    try:
        logger.info("Starting layer refresh: downloading from IGN ...")
        await asyncio.gather(
            _download(country_url, country_path, logger),
            _download(departments_url, deptos_path, logger),
        )

        logger.info("Simplifying layers ...")
        await asyncio.gather(
            _simplify(country_path, country_simple_path, tolerance, logger),
            _simplify(deptos_path, deptos_simple_path, tolerance, logger),
        )

        logger.info("Uploading layers to S3 ...")
        s3 = S3Client(settings, logger)
        updated_files: List[str] = []
        for fname in GEOJSON_FILES:
            local = os.path.join(data_dir, _versioned_key(fname))
            stem, ext = os.path.splitext(fname)
            old_keys = await s3.list_objects(f"{stem}_")
            for key in old_keys:
                await s3.delete_file(key)
            new_key = _versioned_key(fname)
            await s3.upload_file(local, new_key)
            updated_files.append(new_key)

        duration = time.monotonic() - start
        logger.info(f"Layer refresh completed in {duration:.1f}s")
        return {
            "status": "success",
            "files": updated_files,
            "duration_seconds": duration,
            "error": None,
        }

    except Exception as exc:
        duration = time.monotonic() - start
        logger.error(f"Layer refresh failed after {duration:.1f}s: {exc}")
        return {
            "status": "failed",
            "files": [],
            "duration_seconds": duration,
            "error": str(exc),
        }
