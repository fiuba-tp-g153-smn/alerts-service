"""Filesystem-backed repository for loading geographic GeoJSON layers."""

import asyncio
import glob
import os
import time
from dataclasses import dataclass
from logging import Logger
from pathlib import Path

import geopandas as gpd

from domain.models import LayerType
from ports.geo_repository import IGeoLayerRepository

_SIMPLIFIED_STEMS = {
    LayerType.COUNTRY: "pais_simple",
    LayerType.DEPARTMENTS: "departamentos_simple",
}

_FULLRES_STEMS = {
    LayerType.COUNTRY: "pais",
    LayerType.DEPARTMENTS: "departamentos",
}

_EVICTION_SWEEP_INTERVAL_S = 60


def _versioned_stem(data_dir: str, stem: str) -> str:
    """Return the path to the latest versioned full-res GeoJSON matching the given stem."""
    matches = sorted(glob.glob(os.path.join(data_dir, f"{stem}_????????.geojson")))
    if not matches:
        raise FileNotFoundError(f"No data file found for {stem}")
    return matches[-1]


def _simplified_stem(data_dir: str, stem: str, level: int) -> str:
    """Return the path to the latest versioned simplified GeoJSON for the given level."""
    matches = sorted(
        glob.glob(os.path.join(data_dir, f"{stem}_L{level}_T*_????????.geojson"))
    )
    if not matches:
        raise FileNotFoundError(
            f"No simplified data file found for {stem} level {level}"
        )
    return matches[-1]


@dataclass(slots=True)
class _CacheEntry:
    path: str
    gdf: gpd.GeoDataFrame
    last_used: float  # time.monotonic()


class FileSystemGeoLayerRepository(IGeoLayerRepository):
    """Loads GeoDataFrames from versioned per-level GeoJSON files on disk."""

    def __init__(self, data_dir: str, logger: Logger, ttl_s: float = 1800.0):
        """Initialise with the directory containing GeoJSON files."""
        self.data_dir = data_dir
        self.logger = logger
        self._ttl_s = ttl_s
        self._cache: dict[tuple[LayerType, int], _CacheEntry] = {}
        self._locks: dict[tuple[LayerType, int], asyncio.Lock] = {}
        self._eviction_task: asyncio.Task | None = None

    def _get_lock(self, key: tuple[LayerType, int]) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def get_layer(self, layer: LayerType, level: int) -> gpd.GeoDataFrame:
        """Return the GeoDataFrame for the given layer and simplification level."""
        key = (layer, level)
        path = _simplified_stem(self.data_dir, _SIMPLIFIED_STEMS[layer], level)

        # Fast path — no lock needed; dict reads are atomic in CPython event loop
        entry = self._cache.get(key)
        if entry is not None and entry.path == path:
            entry.last_used = time.monotonic()
            return entry.gdf

        # Slow path — serialize loads for the same key
        async with self._get_lock(key):
            # Double-check after acquiring lock
            entry = self._cache.get(key)
            if entry is not None and entry.path == path:
                entry.last_used = time.monotonic()
                return entry.gdf

            t0 = time.time()
            self.logger.info(f"Loading GeoDataFrame: {path}")
            gdf = await asyncio.to_thread(gpd.read_file, path)
            self.logger.info(
                f"Loaded {path} in {time.time()-t0:.3f}s ({len(gdf)} features)"
            )

            self._cache[key] = _CacheEntry(
                path=path, gdf=gdf, last_used=time.monotonic()
            )
            return gdf

    def get_fullres_geojson_path(self, layer: LayerType) -> str:
        """Return the filesystem path for the latest full-res GeoJSON file."""
        return _versioned_stem(self.data_dir, _FULLRES_STEMS[layer])

    def get_fullres_fgb_path(self, layer: LayerType) -> str:
        """Return the filesystem path for the latest full-res FlatGeobuf file."""
        stem = _FULLRES_STEMS[layer]
        paths = sorted(Path(self.data_dir).glob(f"{stem}_????????.fgb"))
        if not paths:
            raise FileNotFoundError(f"No .fgb file found for {layer}")
        return str(paths[-1])

    def start_eviction_loop(self) -> None:
        """Start the background TTL eviction task."""
        self._eviction_task = asyncio.create_task(self._eviction_loop())

    async def stop_eviction_loop(self) -> None:
        """Cancel and await the background TTL eviction task."""
        if self._eviction_task is not None:
            self._eviction_task.cancel()
            try:
                await self._eviction_task
            except asyncio.CancelledError:
                pass
            self._eviction_task = None

    async def _eviction_loop(self) -> None:
        while True:
            await asyncio.sleep(_EVICTION_SWEEP_INTERVAL_S)
            await self._evict_expired()

    async def _evict_expired(self) -> None:
        now = time.monotonic()
        expired_keys = [
            key
            for key, entry in self._cache.items()
            if now - entry.last_used > self._ttl_s
        ]
        for key in expired_keys:
            async with self._get_lock(key):
                entry = self._cache.get(key)
                if entry is not None and now - entry.last_used > self._ttl_s:
                    del self._cache[key]
                    self.logger.info(
                        f"Evicted cached layer {key} (idle > {self._ttl_s:.0f}s)"
                    )
