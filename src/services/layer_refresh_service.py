"""Service that downloads, simplifies, and uploads geographic layer files."""

import asyncio
import os
import time
from logging import Logger
from typing import TYPE_CHECKING

from domain.models import LayerRefreshResult
from ports.object_storage import IObjectStorage
from scheduler.layer_refresh_job import (
    _convert_to_fgb,
    _download,
    _simplify,
    _versioned_key,
)

if TYPE_CHECKING:
    from settings import Settings

_FGB_FILES = ["pais.fgb", "departamentos.fgb"]


class LayerRefreshService:  # pylint: disable=too-few-public-methods
    """Orchestrates a full layer refresh: download from IGN, simplify, and sync to S3."""

    def __init__(self, settings: "Settings", storage: IObjectStorage, logger: Logger):
        """Initialise with application settings, an object storage client, and a logger."""
        self.settings = settings
        self.storage = storage
        self.logger = logger

    def _simplified_fnames(self) -> list[str]:
        """Return the list of simplified GeoJSON filenames for all configured levels."""
        fnames = []
        for level in self.settings.simplification_levels:
            fnames.append(f"pais_simple_L{level}.geojson")
            fnames.append(f"departamentos_simple_L{level}.geojson")
        return fnames

    async def _upload_files(self, data_dir: str) -> list[str]:
        """Delete old S3 keys and upload the current versioned files; return uploaded key names."""
        uploaded: list[str] = []
        for fname in self._simplified_fnames() + _FGB_FILES:
            local = os.path.join(data_dir, _versioned_key(fname))
            stem = os.path.splitext(fname)[0]
            for key in await self.storage.list_keys(f"{stem}_"):
                await self.storage.delete(key)
            new_key = _versioned_key(fname)
            await self.storage.upload(local, new_key)
            uploaded.append(new_key)
        return uploaded

    async def run(self) -> LayerRefreshResult:
        """Execute the full refresh cycle and return a result with status and timing."""
        start = time.monotonic()
        data_dir = self.settings.data_dir
        os.makedirs(data_dir, exist_ok=True)
        levels: dict[int, float] = self.settings.simplification_levels

        country_url = self.settings.country_geojson_url
        departments_url = self.settings.departments_geojson_url

        country_tmp = os.path.join(data_dir, "pais_raw_tmp.geojson")
        deptos_tmp = os.path.join(data_dir, "departamentos_raw_tmp.geojson")

        try:
            self.logger.info("Starting layer refresh: downloading from IGN ...")
            await asyncio.gather(
                _download(country_url, country_tmp, self.logger),
                _download(departments_url, deptos_tmp, self.logger),
            )

            self.logger.info("Simplifying layers ...")
            simplify_tasks = []
            for level, tolerance in levels.items():
                simplify_tasks.append(
                    _simplify(
                        country_tmp,
                        os.path.join(
                            data_dir, _versioned_key(f"pais_simple_L{level}.geojson")
                        ),
                        tolerance,
                        self.logger,
                    )
                )
                simplify_tasks.append(
                    _simplify(
                        deptos_tmp,
                        os.path.join(
                            data_dir,
                            _versioned_key(f"departamentos_simple_L{level}.geojson"),
                        ),
                        tolerance,
                        self.logger,
                    )
                )
            await asyncio.gather(*simplify_tasks)

            self.logger.info("Converting layers to FlatGeobuf ...")
            country_fgb = os.path.join(data_dir, _versioned_key("pais.fgb"))
            deptos_fgb = os.path.join(data_dir, _versioned_key("departamentos.fgb"))
            await asyncio.gather(
                _convert_to_fgb(country_tmp, country_fgb, self.logger),
                _convert_to_fgb(deptos_tmp, deptos_fgb, self.logger),
            )

            self.logger.info("Removing temporary raw files ...")
            os.remove(country_tmp)
            os.remove(deptos_tmp)

            self.logger.info("Uploading layers to S3 ...")
            updated_files = await self._upload_files(data_dir)

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
