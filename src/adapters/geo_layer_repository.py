"""Filesystem-backed repository for loading geographic GeoJSON layers."""

import glob
import os
import time
from logging import Logger
from pathlib import Path

import geopandas as gpd

from domain.models import LayerType
from ports.geo_repository import IGeoLayerRepository
from scheduler.layer_refresh_job import _convert_to_fgb

_LAYER_STEMS = {
    (LayerType.COUNTRY, True): "pais_simple",
    (LayerType.COUNTRY, False): "pais",
    (LayerType.DEPARTMENTS, True): "departamentos_simple",
    (LayerType.DEPARTMENTS, False): "departamentos",
}

_FGB_STEMS = {
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

    def get_fullres_fgb_path(self, layer: LayerType) -> str:
        """Return the filesystem path for the latest full-res FlatGeobuf file."""
        stem = _FGB_STEMS[layer]
        paths = sorted(Path(self.data_dir).glob(f"{stem}_????????.fgb"))
        if not paths:
            raise FileNotFoundError(f"No .fgb file found for {layer}")
        return str(paths[-1])

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

    async def ensure_fgb_files(self) -> None:
        """Convert full-res GeoJSON layers to FlatGeobuf if not already present."""
        for layer in (LayerType.COUNTRY, LayerType.DEPARTMENTS):
            stem = _FGB_STEMS[layer]
            existing_fgb = sorted(Path(self.data_dir).glob(f"{stem}_????????.fgb"))
            if existing_fgb:
                self.logger.info(
                    f"FlatGeobuf already exists for {layer}: {existing_fgb[-1].name}"
                )
                continue

            try:
                geojson_path = self.get_layer_path(layer, simplified=False)
            except FileNotFoundError:
                self.logger.warning(
                    f"No full-res GeoJSON found for {layer}, skipping FlatGeobuf conversion"
                )
                continue

            fgb_path = str(Path(geojson_path).with_suffix(".fgb"))
            await _convert_to_fgb(geojson_path, fgb_path, self.logger)
            self.logger.info(f"FlatGeobuf ready: {fgb_path}")
