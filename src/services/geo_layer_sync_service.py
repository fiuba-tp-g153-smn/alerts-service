"""Service that ensures local filesystem and S3 are consistent for all geo layer files."""

import glob as _glob
import os
from logging import Logger
from typing import TYPE_CHECKING

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


def _tolerance_str(tolerance: float) -> str:
    """Encode a tolerance as a filename-safe string. 0.0001 → '0p0001'."""
    return str(tolerance).replace(".", "p")


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

    def __init__(self, settings: "Settings", storage: IObjectStorage, logger: Logger):
        self.settings = settings
        self.storage = storage
        self.logger = logger

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
        return needs_regen

    async def _reconcile_simplified(
        self, layer: dict, level: int, tolerance: float
    ) -> bool:
        """Ensure the canonical simplified file for (layer, level, tolerance) exists.

        Purges any local/S3 files for this layer+level with the wrong tolerance or
        a non-canonical date. Returns False if re-generation is needed.
        """
        data_dir = self.settings.data_dir
        stem = f"{layer['simplified_stem']}_L{level}"
        tol_str = _tolerance_str(tolerance)

        # Correct-tolerance files (to find canonical date)
        local_correct = self._local_files(stem, f"_T{tol_str}_????????.geojson")
        s3_correct = await self._s3_keys(f"{stem}_T{tol_str}_", ".geojson")

        local_date = _extract_date(local_correct[-1]) if local_correct else None
        s3_date = _extract_date(sorted(s3_correct)[-1]) if s3_correct else None

        # All files for this level (any tolerance, any date) — for stale sweep
        all_local = self._local_files(stem, "_T*_????????.geojson")
        all_s3 = await self._s3_keys(f"{stem}_", ".geojson")

        if local_date is None and s3_date is None:
            for path in all_local:
                self.logger.info(f"Removing stale local file: {path}")
                os.remove(path)
            for key in all_s3:
                self.logger.info(f"Removing stale S3 key: {key}")
                await self.storage.delete(key)
            return False

        canonical_date = max(d for d in [local_date, s3_date] if d is not None)
        canonical_filename = f"{stem}_T{tol_str}_{canonical_date}.geojson"
        canonical_path = os.path.join(data_dir, canonical_filename)

        for path in all_local:
            if os.path.basename(path) != canonical_filename:
                self.logger.info(f"Removing stale local file: {path}")
                os.remove(path)

        for key in all_s3:
            if os.path.basename(key) != canonical_filename:
                self.logger.info(f"Removing stale S3 key: {key}")
                await self.storage.delete(key)

        if not os.path.exists(canonical_path):
            self.logger.info(f"{canonical_filename}: missing locally, downloading from S3 ...")
            if not await self.storage.download(canonical_filename, canonical_path):
                self.logger.warning(f"{canonical_filename}: S3 download failed, will re-generate.")
                return False

        if self.settings.s3_bucket_name and s3_date != canonical_date:
            self.logger.info(f"{canonical_filename}: missing from S3, uploading ...")
            await self.storage.upload(canonical_path, canonical_filename)

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

        for path in local_files:
            if os.path.basename(path) != canonical_filename:
                self.logger.info(f"Removing stale local file: {path}")
                os.remove(path)

        for key in s3_keys:
            if os.path.basename(key) != canonical_filename:
                self.logger.info(f"Removing stale S3 key: {key}")
                await self.storage.delete(key)

        if not os.path.exists(canonical_path):
            self.logger.info(f"{canonical_filename}: missing locally, downloading from S3 ...")
            if not await self.storage.download(canonical_filename, canonical_path):
                self.logger.warning(f"{canonical_filename}: S3 download failed, will re-generate.")
                return False

        if self.settings.s3_bucket_name and s3_date != canonical_date:
            self.logger.info(f"{canonical_filename}: missing from S3, uploading ...")
            await self.storage.upload(canonical_path, canonical_filename)

        self.logger.info(f"{canonical_filename}: ready.")
        return True

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
