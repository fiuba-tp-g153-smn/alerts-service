"""Filesystem-backed repository for loading geographic GeoJSON layers."""

import glob
import os
import time
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


def _versioned_stem(data_dir: str, stem: str) -> str:
    """Return the path to the latest versioned file matching the given stem."""
    matches = sorted(glob.glob(os.path.join(data_dir, f"{stem}_????????.geojson")))
    if not matches:
        raise FileNotFoundError(f"No data file found for {stem}")
    return matches[-1]


class FileSystemGeoLayerRepository(IGeoLayerRepository):
    """Loads GeoDataFrames from versioned per-level GeoJSON files on disk."""

    def __init__(self, data_dir: str, logger: Logger):
        """Initialise with the directory containing GeoJSON files."""
        self.data_dir = data_dir
        self.logger = logger
        self._cache: dict[tuple[LayerType, int], tuple[str, gpd.GeoDataFrame]] = {}

    def get_layer(self, layer: LayerType, level: int) -> gpd.GeoDataFrame:
        """Load and return the GeoDataFrame for the given layer and simplification level."""
        stem = f"{_SIMPLIFIED_STEMS[layer]}_L{level}"
        path = _versioned_stem(self.data_dir, stem)

        cached = self._cache.get((layer, level))
        if cached is not None:
            cached_path, cached_gdf = cached
            if cached_path == path:
                return cached_gdf

        t0 = time.time()
        self.logger.info(f"Loading GeoDataFrame: {path}")
        gdf = gpd.read_file(path)
        self.logger.info(
            f"Loaded {path} in {time.time()-t0:.3f}s ({len(gdf)} features)"
        )

        self._cache[(layer, level)] = (path, gdf)
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

    def preload(self, levels: list[int]) -> None:
        """Preload all level-specific simplified layers into memory."""
        self.logger.info("Preloading simplified geo layers into memory...")
        for layer in LayerType:
            for level in levels:
                self.get_layer(layer, level)
        self.logger.info("Simplified geo layers preloaded successfully.")
