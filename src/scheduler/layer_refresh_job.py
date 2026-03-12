"""Low-level helpers for downloading and simplifying GeoJSON layer files."""

import asyncio
import os
from datetime import date
from logging import Logger

import aiohttp
import geopandas as gpd

os.environ.setdefault("OGR_GEOJSON_MAX_OBJ_SIZE", "0")


def _versioned_key(fname: str) -> str:
    """Return a date-stamped filename, e.g. pais_20260312.geojson."""
    stem, ext = os.path.splitext(fname)
    return f"{stem}_{date.today().strftime('%Y%m%d')}{ext}"


async def _download(url: str, out_path: str, logger: Logger) -> None:
    """Download a URL to a local path, logging progress and file size."""
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
    """Simplify a GeoJSON layer with the given tolerance and save it to out_path."""
    logger.info(f"Simplifying {in_path} (tolerance={tolerance}) ...")

    def _run():
        gdf = gpd.read_file(in_path)
        gdf["geometry"] = gdf["geometry"].simplify(tolerance, preserve_topology=True)
        gdf.to_file(out_path, driver="GeoJSON")

    await asyncio.to_thread(_run)
    logger.info(f"Simplified → {out_path}")
