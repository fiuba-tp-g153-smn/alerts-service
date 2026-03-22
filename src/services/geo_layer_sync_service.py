"""Service that ensures local filesystem and S3 are consistent for all geo layer files."""

import glob as _glob
import os
from logging import Logger
from typing import TYPE_CHECKING

from ports.geo_layer_processor import IGeoLayerProcessor
from ports.object_storage import IObjectStorage

if TYPE_CHECKING:
    from settings import Settings

LAYERS = [
    {
        "simplified_stem": "pais_simple",
        "fgb_stem": "pais",
        "url_attr": "country_geojson_url",
        "raw_tmp": "pais_raw_tmp.geojson",
    },
    {
        "simplified_stem": "departamentos_simple",
        "fgb_stem": "departamentos",
        "url_attr": "departments_geojson_url",
        "raw_tmp": "departamentos_raw_tmp.geojson",
    },
]


def _extract_date(key: str) -> str | None:
    """Return the 8-digit date suffix from a versioned filename, or None."""
    stem_part = os.path.splitext(os.path.basename(key))[0]
    parts = stem_part.split("_")
    if parts and len(parts[-1]) == 8 and parts[-1].isdigit():
        return parts[-1]
    return None


class GeoLayerSyncService:  # pylint: disable=too-few-public-methods
    """Ensures local filesystem and S3 are consistent for all geo layer files.

    - Detects stale files (wrong tolerance or superseded date).
    - Purges stale files from local disk and S3.
    - Downloads files missing locally from S3.
    - Uploads files missing in S3 from local.
    - Returns which layers/levels need re-generation.
    """

    def __init__(
        self,
        settings: "Settings",
        storage: IObjectStorage,
        processor: IGeoLayerProcessor,
        logger: Logger,
    ):
        self.settings = settings
        self.storage = storage
        self.processor = processor
        self.logger = logger
        self._needs_regen: list[tuple[dict, list[tuple[int, float]], bool]] = []

    async def ensure_all(self) -> list[tuple[dict, list[tuple[int, float]], bool]]:
        """Reconcile all layers. Returns list of (layer_info, missing_levels, fgb_needed)."""
        levels: dict[int, float] = self.settings.simplification_levels
        needs_regen = []

        for layer in LAYERS:
            missing_levels: list[tuple[int, float]] = []
            for level, tolerance in levels.items():
                if not await self._reconcile_simplified(layer, level, tolerance):
                    missing_levels.append((level, tolerance))

            fgb_needed = not await self._reconcile_fgb(layer)

            if missing_levels or fgb_needed:
                needs_regen.append((layer, missing_levels, fgb_needed))

        self.logger.info("All geo layers reconciled.")
        self._needs_regen = needs_regen
        return needs_regen

    async def regenerate(self) -> None:
        """Download from IGN and re-generate any files flagged missing by ensure_all()."""
        data_dir = self.settings.data_dir
        for layer, missing_levels, fgb_needed in self._needs_regen:
            self.logger.info(f"Re-generating layer: {layer['fgb_stem']} ...")
            url = getattr(self.settings, layer["url_attr"])
            raw_tmp = os.path.join(data_dir, layer["raw_tmp"])

            await self.processor.download(url, raw_tmp)

            for level, tolerance in missing_levels:
                stem = f"{layer['simplified_stem']}_L{level}"
                versioned = IGeoLayerProcessor.tolerance_versioned_key(
                    f"{stem}.geojson", tolerance
                )
                simplified_path = os.path.join(data_dir, versioned)
                await self.processor.simplify(raw_tmp, simplified_path, tolerance)
                if self.settings.s3_bucket_name:
                    await self.storage.upload(simplified_path, versioned)

            if fgb_needed:
                fgb_key = IGeoLayerProcessor.versioned_key(f"{layer['fgb_stem']}.fgb")
                fgb_path = os.path.join(data_dir, fgb_key)
                await self.processor.convert_to_fgb(raw_tmp, fgb_path)
                if self.settings.s3_bucket_name:
                    await self.storage.upload(fgb_path, fgb_key)

            os.remove(raw_tmp)

        self.logger.info("All geo layers are ready.")

    async def _reconcile_simplified(
        self, layer: dict, level: int, tolerance: float
    ) -> bool:
        """Ensure the canonical simplified file for (layer, level, tolerance) exists.

        Purges any local/S3 files for this layer+level with the wrong tolerance or
        a non-canonical date. Returns False if re-generation is needed.
        """
        stem = f"{layer['simplified_stem']}_L{level}"
        tol_str = IGeoLayerProcessor.tolerance_str(tolerance)

        # Correct-tolerance files (to find canonical date)
        local_correct = self._local_files(stem, f"_T{tol_str}_????????.geojson")
        s3_correct = await self._s3_keys(f"{stem}_T{tol_str}_", ".geojson")
        local_date = _extract_date(local_correct[-1]) if local_correct else None
        s3_date = _extract_date(sorted(s3_correct)[-1]) if s3_correct else None

        # All files for this level (any tolerance, any date) — for stale sweep
        all_local = self._local_files(stem, "_T*_????????.geojson")
        all_s3 = await self._s3_keys(f"{stem}_", ".geojson")

        if local_date is None and s3_date is None:
            self._purge_stale_local(all_local, "")  # purge all — nothing canonical
            await self._purge_stale_s3(all_s3, "")
            return False

        canonical_date = max(d for d in [local_date, s3_date] if d is not None)
        canonical_filename = f"{stem}_T{tol_str}_{canonical_date}.geojson"
        canonical_path = os.path.join(self.settings.data_dir, canonical_filename)

        self._purge_stale_local(all_local, canonical_filename)
        await self._purge_stale_s3(all_s3, canonical_filename)

        if not await self._ensure_local(canonical_filename, canonical_path):
            return False

        await self._ensure_s3(
            canonical_filename, canonical_path, s3_date, canonical_date
        )
        self.logger.info(f"{canonical_filename}: ready.")
        return True

    async def _reconcile_fgb(self, layer: dict) -> bool:
        """Ensure the canonical FlatGeobuf file exists. Purges non-canonical versions."""
        data_dir = self.settings.data_dir
        stem = layer["fgb_stem"]

        local_files = sorted(_glob.glob(os.path.join(data_dir, f"{stem}_????????.fgb")))
        s3_keys = await self._s3_keys(f"{stem}_", ".fgb")
        local_date = _extract_date(local_files[-1]) if local_files else None
        s3_date = _extract_date(sorted(s3_keys)[-1]) if s3_keys else None

        if local_date is None and s3_date is None:
            return False

        canonical_date = max(d for d in [local_date, s3_date] if d is not None)
        canonical_filename = f"{stem}_{canonical_date}.fgb"
        canonical_path = os.path.join(data_dir, canonical_filename)

        self._purge_stale_local(local_files, canonical_filename)
        await self._purge_stale_s3(s3_keys, canonical_filename)

        if not await self._ensure_local(canonical_filename, canonical_path):
            return False

        await self._ensure_s3(
            canonical_filename, canonical_path, s3_date, canonical_date
        )
        self.logger.info(f"{canonical_filename}: ready.")
        return True

    def _purge_stale_local(self, paths: list[str], canonical_filename: str) -> None:
        """Remove local files whose basename does not match canonical_filename."""
        for path in paths:
            if os.path.basename(path) != canonical_filename:
                self.logger.info(f"Removing stale local file: {path}")
                os.remove(path)

    async def _purge_stale_s3(self, keys: list[str], canonical_filename: str) -> None:
        """Delete S3 keys whose basename does not match canonical_filename."""
        for key in keys:
            if os.path.basename(key) != canonical_filename:
                self.logger.info(f"Removing stale S3 key: {key}")
                await self.storage.delete(key)

    async def _ensure_local(self, canonical_filename: str, canonical_path: str) -> bool:
        """Return True if the file is present locally, downloading from S3 if needed."""
        if os.path.exists(canonical_path):
            return True
        self.logger.info(
            f"{canonical_filename}: missing locally, downloading from S3 ..."
        )
        if not await self.storage.download(canonical_filename, canonical_path):
            self.logger.warning(
                f"{canonical_filename}: S3 download failed, will re-generate."
            )
            return False
        return True

    async def _ensure_s3(
        self,
        canonical_filename: str,
        canonical_path: str,
        s3_date: str | None,
        canonical_date: str,
    ) -> None:
        """Upload the file to S3 if it is missing there."""
        if self.settings.s3_bucket_name and s3_date != canonical_date:
            self.logger.info(f"{canonical_filename}: missing from S3, uploading ...")
            await self.storage.upload(canonical_path, canonical_filename)

    def _local_files(self, stem: str, suffix_pattern: str) -> list[str]:
        """Return sorted list of local files matching stem + suffix_pattern."""
        return sorted(
            _glob.glob(os.path.join(self.settings.data_dir, f"{stem}{suffix_pattern}"))
        )

    async def _s3_keys(self, prefix: str, ext: str) -> list[str]:
        """Return S3 keys with the given prefix that end with ext."""
        if not self.settings.s3_bucket_name:
            return []
        return [k for k in await self.storage.list_keys(prefix) if k.endswith(ext)]
