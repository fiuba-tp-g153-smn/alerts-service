"""Service that downloads, simplifies, and uploads geographic layer files."""

import asyncio
import os
import time
from logging import Logger
from typing import TYPE_CHECKING

from domain.models import LayerRefreshResult
from ports.object_storage import IObjectStorage
from scheduler.layer_refresh_job import _download, _simplify, _versioned_key

if TYPE_CHECKING:
    from settings import Settings

GEOJSON_FILES = [
    "pais.geojson",
    "departamentos.geojson",
    "pais_simple.geojson",
    "departamentos_simple.geojson",
]


class LayerRefreshService:  # pylint: disable=too-few-public-methods
    """Orchestrates a full layer refresh: download from IGN, simplify, and sync to S3."""

    def __init__(self, settings: "Settings", storage: IObjectStorage, logger: Logger):
        """Initialise with application settings, an object storage client, and a logger."""
        self.settings = settings
        self.storage = storage
        self.logger = logger

    async def run(self) -> LayerRefreshResult:
        """Execute the full refresh cycle and return a result with status and timing."""
        start = time.monotonic()
        data_dir = self.settings.data_dir
        os.makedirs(data_dir, exist_ok=True)
        tolerance = float(getattr(self.settings, "simplify_tolerance", 0.01))

        country_url = self.settings.country_geojson_url
        departments_url = self.settings.departments_geojson_url

        country_path = os.path.join(data_dir, _versioned_key("pais.geojson"))
        country_simple_path = os.path.join(
            data_dir, _versioned_key("pais_simple.geojson")
        )
        deptos_path = os.path.join(data_dir, _versioned_key("departamentos.geojson"))
        deptos_simple_path = os.path.join(
            data_dir, _versioned_key("departamentos_simple.geojson")
        )

        try:
            self.logger.info("Starting layer refresh: downloading from IGN ...")
            await asyncio.gather(
                _download(country_url, country_path, self.logger),
                _download(departments_url, deptos_path, self.logger),
            )

            self.logger.info("Simplifying layers ...")
            await asyncio.gather(
                _simplify(country_path, country_simple_path, tolerance, self.logger),
                _simplify(deptos_path, deptos_simple_path, tolerance, self.logger),
            )

            self.logger.info("Uploading layers to S3 ...")
            updated_files: list[str] = []
            for fname in GEOJSON_FILES:
                local = os.path.join(data_dir, _versioned_key(fname))
                stem = os.path.splitext(fname)[0]
                old_keys = await self.storage.list_keys(f"{stem}_")
                for key in old_keys:
                    await self.storage.delete(key)
                new_key = _versioned_key(fname)
                await self.storage.upload(local, new_key)
                updated_files.append(new_key)

            duration = time.monotonic() - start
            self.logger.info(f"Layer refresh completed in {duration:.1f}s")
            return LayerRefreshResult(
                status="success",
                files=updated_files,
                duration_seconds=duration,
                error=None,
            )

        except Exception as exc:  # pylint: disable=broad-exception-caught
            duration = time.monotonic() - start
            self.logger.error(f"Layer refresh failed after {duration:.1f}s: {exc}")
            return LayerRefreshResult(
                status="failed",
                files=[],
                duration_seconds=duration,
                error=str(exc),
            )
