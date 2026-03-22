"""Service that downloads, simplifies, and uploads geographic layer files."""

import asyncio
import os
import time
from logging import Logger
from typing import TYPE_CHECKING

from domain.models import LayerRefreshResult
from ports.geo_layer_processor import IGeoLayerProcessor
from ports.object_storage import IObjectStorage

if TYPE_CHECKING:
    from settings import Settings

_FGB_FILES = ["pais.fgb", "departamentos.fgb"]


class LayerRefreshService:  # pylint: disable=too-few-public-methods
    """Orchestrates a full layer refresh: download from IGN, simplify, and sync to S3."""

    def __init__(
        self,
        settings: "Settings",
        storage: IObjectStorage,
        processor: IGeoLayerProcessor,
        logger: Logger,
    ):
        """Initialise with application settings, an object storage client, and a logger."""
        self.settings = settings
        self.storage = storage
        self.processor = processor
        self.logger = logger

    def _simplified_fnames(self) -> list[str]:
        """Return simplified GeoJSON filenames (with tolerance) for all configured levels."""
        fnames = []
        for level, tolerance in self.settings.simplification_levels.items():
            tol = IGeoLayerProcessor.tolerance_str(tolerance)
            fnames.append(f"pais_simple_L{level}_T{tol}.geojson")
            fnames.append(f"departamentos_simple_L{level}_T{tol}.geojson")
        return fnames

    async def _upload_files(self, data_dir: str) -> list[str]:
        """Delete old S3 keys for each level and upload the current versioned files."""
        uploaded: list[str] = []
        for fname in self._simplified_fnames() + _FGB_FILES:
            new_key = IGeoLayerProcessor.versioned_key(fname)
            local = os.path.join(data_dir, new_key)
            ext = os.path.splitext(fname)[1]
            # Use level-only prefix (strip _T{tol} suffix) to sweep old-tolerance S3 keys
            level_stem = os.path.splitext(fname)[0].split("_T")[0]
            for key in await self.storage.list_keys(f"{level_stem}_"):
                if key.endswith(ext):
                    await self.storage.delete(key)
            await self.storage.upload(local, new_key)
            uploaded.append(new_key)
        return uploaded

    async def _download_layers(self, country_tmp: str, deptos_tmp: str) -> None:
        self.logger.info("Starting layer refresh: downloading from IGN ...")
        await asyncio.gather(
            self.processor.download(self.settings.country_geojson_url, country_tmp),
            self.processor.download(self.settings.departments_geojson_url, deptos_tmp),
        )

    async def _simplify_layers(self, country_tmp: str, deptos_tmp: str) -> None:
        self.logger.info("Simplifying layers ...")
        data_dir = self.settings.data_dir
        for level, tolerance in self.settings.simplification_levels.items():
            for stem, tmp in [
                ("pais_simple", country_tmp),
                ("departamentos_simple", deptos_tmp),
            ]:
                out = os.path.join(
                    data_dir,
                    IGeoLayerProcessor.tolerance_versioned_key(
                        f"{stem}_L{level}.geojson", tolerance
                    ),
                )
                await self.processor.simplify(tmp, out, tolerance)

    async def _convert_to_fgb_layers(self, country_tmp: str, deptos_tmp: str) -> None:
        self.logger.info("Converting layers to FlatGeobuf ...")
        data_dir = self.settings.data_dir
        await asyncio.gather(
            self.processor.convert_to_fgb(
                country_tmp,
                os.path.join(data_dir, IGeoLayerProcessor.versioned_key("pais.fgb")),
            ),
            self.processor.convert_to_fgb(
                deptos_tmp,
                os.path.join(
                    data_dir, IGeoLayerProcessor.versioned_key("departamentos.fgb")
                ),
            ),
        )

    def _cleanup_tmp(self, country_tmp: str, deptos_tmp: str) -> None:
        self.logger.info("Removing temporary raw files ...")
        for path in (country_tmp, deptos_tmp):
            if os.path.exists(path):
                os.remove(path)

    async def run(self) -> LayerRefreshResult:
        """Execute the full refresh cycle and return a result with status and timing."""
        start = time.monotonic()
        data_dir = self.settings.data_dir
        os.makedirs(data_dir, exist_ok=True)
        country_tmp = os.path.join(data_dir, "pais_raw_tmp.geojson")
        deptos_tmp = os.path.join(data_dir, "departamentos_raw_tmp.geojson")

        try:
            await self._download_layers(country_tmp, deptos_tmp)
            await self._simplify_layers(country_tmp, deptos_tmp)
            await self._convert_to_fgb_layers(country_tmp, deptos_tmp)
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

        finally:
            self._cleanup_tmp(country_tmp, deptos_tmp)
