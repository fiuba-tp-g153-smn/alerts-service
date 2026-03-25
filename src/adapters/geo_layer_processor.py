"""Adapter implementing geo layer download and processing via aiohttp and subprocess."""

import asyncio
import json
import os
import subprocess
import sys
from logging import Logger

import aiohttp

from ports.geo_layer_processor import IGeoLayerProcessor

_WORKER_ENV = {**os.environ, "OGR_GEOJSON_MAX_OBJ_SIZE": "0"}
_WORKER_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "geo_processing_worker.py")
)


async def _run_worker(task: list[dict]) -> None:
    """Spawn geo_processing_worker and stream the task via stdin.

    Uses create_subprocess_exec so that CancelledError terminates the child
    process immediately instead of abandoning a background thread.
    """
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        _WORKER_PATH,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_WORKER_ENV,
    )
    try:
        _, stderr = await proc.communicate(json.dumps(task).encode())
    except asyncio.CancelledError:
        proc.terminate()
        await proc.wait()
        raise
    if proc.returncode is not None and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, sys.executable, stderr)


class GeoLayerProcessor(IGeoLayerProcessor):
    """Downloads and processes geo layer files using aiohttp and geo_processing_worker."""

    def __init__(self, logger: Logger) -> None:
        self._logger = logger

    async def download(self, url: str, out_path: str) -> None:
        self._logger.info(f"Downloading {url} ...")
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=600)
            ) as resp:
                resp.raise_for_status()
                content = await resp.read()
        with open(out_path, "wb") as f:
            f.write(content)
        size_mb = os.path.getsize(out_path) / 1_048_576
        self._logger.info(f"Saved {out_path} ({size_mb:.1f} MB)")

    async def simplify(self, in_path: str, out_path: str, tolerance: float) -> None:
        self._logger.info(f"Simplifying {in_path} (tolerance={tolerance}) ...")
        await _run_worker(
            [
                {
                    "op": "simplify",
                    "in_path": in_path,
                    "out_path": out_path,
                    "tolerance": tolerance,
                }
            ]
        )
        self._logger.info(f"Simplified → {out_path}")

    async def convert_to_fgb(self, in_path: str, out_path: str) -> None:
        self._logger.info(f"Converting {in_path} to FlatGeobuf ...")
        await _run_worker(
            [{"op": "convert_fgb", "in_path": in_path, "out_path": out_path}]
        )
        self._logger.info(f"Converted → {out_path}")
