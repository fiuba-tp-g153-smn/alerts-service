"""Service that ensures local filesystem and S3 are consistent for all geo layer files."""

import asyncio
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
        "url_attr": "country_geojson_url",
        "raw_tmp": "pais_raw_tmp.geojson",
    },
    {
        "simplified_stem": "departamentos_simple",
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

    # Bounded exponential backoff for transient external I/O (IGN download, S3
    # upload). Overridable in tests to avoid real sleeps.
    _MAX_RETRIES = 3
    _RETRY_BACKOFF_BASE = 2.0

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
        self._needs_regen: list[tuple[dict, list[tuple[int | None, float]]]] = []

    def _levels_for(self, layer: dict) -> dict[int | None, float]:
        """Return the {level: tolerance} map to reconcile/generate for the given layer."""
        if layer["simplified_stem"] == "departamentos_simple":
            return {None: self.settings.departments_simplify_tolerance}
        return self.settings.detail_level_tolerances

    async def ensure_all(self) -> list[tuple[dict, list[tuple[int | None, float]]]]:
        """Reconcile all layers. Returns list of (layer_info, missing_levels)."""
        needs_regen = []

        for layer in LAYERS:
            missing_levels: list[tuple[int | None, float]] = []
            for level, tolerance in self._levels_for(layer).items():
                if not await self._reconcile_simplified(layer, level, tolerance):
                    missing_levels.append((level, tolerance))

            if missing_levels:
                needs_regen.append((layer, missing_levels))

        self.logger.info("All geo layers reconciled.")
        self._needs_regen = needs_regen
        return needs_regen

    async def regenerate(self) -> None:
        """Download from IGN and re-generate any files flagged missing by ensure_all().

        Each layer is regenerated independently: a transient failure (after bounded
        retries) is logged and skipped rather than propagated, so one bad IGN/S3 hop
        can never abort application startup — it only leaves that layer missing,
        degrading the endpoints that need it instead of the whole service.
        """
        data_dir = self.settings.data_dir
        for layer, missing_levels in self._needs_regen:
            try:
                await self._regenerate_layer(layer, missing_levels, data_dir)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self.logger.error(
                    "Failed to regenerate layer %s after retries: %s — continuing "
                    "startup without it",
                    layer["simplified_stem"],
                    exc,
                )

        self.logger.info("Geo layer regeneration complete.")

    async def _regenerate_layer(
        self, layer: dict, missing_levels: list, data_dir: str
    ) -> None:
        """Download the raw layer and (re)generate + upload each missing level."""
        self.logger.info(f"Re-generating layer: {layer['simplified_stem']} ...")
        url = getattr(self.settings, layer["url_attr"])
        raw_tmp = os.path.join(data_dir, layer["raw_tmp"])

        try:
            await self._retry(
                self.processor.download,
                url,
                raw_tmp,
                description=f"download {layer['simplified_stem']}",
            )

            for level, tolerance in missing_levels:
                stem = (
                    layer["simplified_stem"]
                    if level is None
                    else f"{layer['simplified_stem']}_L{level}"
                )
                versioned = IGeoLayerProcessor.tolerance_versioned_key(
                    f"{stem}.geojson", tolerance
                )
                simplified_path = os.path.join(data_dir, versioned)
                await self.processor.simplify(raw_tmp, simplified_path, tolerance)
                if self.settings.s3_bucket_name:
                    await self._retry(
                        self.storage.upload,
                        simplified_path,
                        versioned,
                        description=f"upload {versioned}",
                    )

        finally:
            if os.path.exists(raw_tmp):
                os.remove(raw_tmp)

    async def _retry(self, func, *args, description: str):
        """Await ``func(*args)`` with bounded exponential backoff; re-raise on final fail.

        A fresh awaitable is created per attempt (a coroutine can only be awaited
        once). Only transient external I/O should be routed through here.
        """
        last_exc: Exception | None = None
        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                return await func(*args)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                last_exc = exc
                if attempt < self._MAX_RETRIES:
                    delay = self._RETRY_BACKOFF_BASE ** (attempt - 1)
                    self.logger.warning(
                        "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                        description,
                        attempt,
                        self._MAX_RETRIES,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    async def _reconcile_simplified(
        self, layer: dict, level: int | None, tolerance: float
    ) -> bool:
        """Ensure the canonical simplified file for (layer, level, tolerance) exists.

        Purges any local/S3 files for this layer+level with the wrong tolerance or
        a non-canonical date. Returns False if re-generation is needed.
        """
        stem = (
            layer["simplified_stem"]
            if level is None
            else f"{layer['simplified_stem']}_L{level}"
        )
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
