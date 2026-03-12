"""Filesystem-backed repository for loading geographic GeoJSON layers."""

import glob
import os
import time
from logging import Logger

import geopandas as gpd

from domain.models import LayerType
from ports.geo_repository import IGeoLayerRepository

_LAYER_STEMS = {
    (LayerType.COUNTRY, True): "pais_simple",
    (LayerType.COUNTRY, False): "pais",
    (LayerType.DEPARTMENTS, True): "departamentos_simple",
    (LayerType.DEPARTMENTS, False): "departamentos",
}


def _versioned_stem(data_dir: str, stem: str) -> str:
    """Return the path to the latest versioned file matching the given stem."""
    matches = sorted(glob.glob(os.path.join(data_dir, f"{stem}_????????.geojson")))
    if not matches:
        raise FileNotFoundError(f"No data file found for {stem}")
    return matches[-1]


class FileSystemGeoLayerRepository(IGeoLayerRepository):
    """Loads GeoDataFrames from versioned GeoJSON files on disk."""

    def __init__(self, data_dir: str, logger: Logger):
        """Initialise with the directory containing GeoJSON files."""
        self.data_dir = data_dir
        self.logger = logger
        self._cache: dict[LayerType, tuple[str, gpd.GeoDataFrame]] = {}

    def get_layer_path(self, layer: LayerType, simplified: bool) -> str:
        """Return the filesystem path for the given layer without loading it."""
        stem = _LAYER_STEMS[(layer, simplified)]
        return _versioned_stem(self.data_dir, stem)

    def get_layer(self, layer: LayerType, simplified: bool) -> gpd.GeoDataFrame:
        """Load and return the GeoDataFrame for the requested layer and resolution."""
        path = self.get_layer_path(layer, simplified)

        if simplified:
            cached_data = self._cache.get(layer)
            if cached_data is not None:
                cached_path, cached_gdf = cached_data
                if cached_path == path:
                    return cached_gdf

        t0 = time.time()
        self.logger.info(f"Loading GeoDataFrame: {path}")
        gdf = gpd.read_file(path)
        self.logger.info(
            f"Loaded {path} in {time.time()-t0:.3f}s ({len(gdf)} features)"
        )

        if simplified:
            self._cache[layer] = (path, gdf)

        return gdf

    def preload(self) -> None:
        """Preload simplified layers into memory."""
        self.logger.info("Preloading simplified geo layers into memory...")
        self.get_layer(LayerType.COUNTRY, simplified=True)
        self.get_layer(LayerType.DEPARTMENTS, simplified=True)
        self.logger.info("Simplified geo layers preloaded successfully.")
