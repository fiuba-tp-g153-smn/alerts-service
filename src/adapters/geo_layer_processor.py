"""Adapter implementing geo layer download and processing via aiohttp and subprocess."""

import asyncio
import json
import os
import subprocess
import sys
from logging import Logger

import aiohttp

from ports.geo_layer_processor import IGeoLayerProcessor

# OGR_GEOJSON_MAX_OBJ_SIZE=0 lifts GDAL's per-feature size cap; the thread caps
# confine numpy/GEOS BLAS/OpenMP to one thread so a simplification job doesn't
# fan out to every core and starve the event loop (same reason as the alert
# render worker — see services/alert_generation_service.py:_SUBPROCESS_ENV).
_WORKER_ENV = {
    **os.environ,
    "OGR_GEOJSON_MAX_OBJ_SIZE": "0",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
}
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


# Stream downloads in 1 MiB chunks so the full-resolution national GeoJSON
# (~100+ MB) is never buffered whole in the main process — only one chunk is
# resident at a time (CLAUDE.md "stream large files").
_DOWNLOAD_CHUNK_SIZE = 1024 * 1024


async def _stream_to_file(resp: aiohttp.ClientResponse, path: str) -> None:
    """Stream an aiohttp response body to a file in chunks, writing off the loop."""
    file = await asyncio.to_thread(open, path, "wb")
    try:
        async for chunk in resp.content.iter_chunked(_DOWNLOAD_CHUNK_SIZE):
            await asyncio.to_thread(file.write, chunk)
    finally:
        await asyncio.to_thread(file.close)


class GeoLayerProcessor(IGeoLayerProcessor):
    """Downloads and processes geo layer files using aiohttp and geo_processing_worker."""

    def __init__(self, logger: Logger) -> None:
        self._logger = logger

    async def download(self, url: str, out_path: str) -> None:
        """Stream a URL to ``out_path`` in chunks (never buffering the whole body).

        The body is written to a ``.tmp`` sidecar then atomically renamed, so a
        partial download never masquerades as a complete file. The ``.tmp`` name
        also matches the scheduler's orphaned-tmp sweep as a backstop.
        """
        self._logger.info(f"Downloading {url} ...")
        tmp_path = f"{out_path}.tmp"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=600)
                ) as resp:
                    resp.raise_for_status()
                    await _stream_to_file(resp, tmp_path)
            await asyncio.to_thread(os.replace, tmp_path, out_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
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
