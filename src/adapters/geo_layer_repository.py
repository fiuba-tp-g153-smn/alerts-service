import glob
import os
import time
from logging import Logger

import geopandas as gpd

from domain.models import LayerType

_LAYER_STEMS = {
    (LayerType.COUNTRY, True): "pais_simple",
    (LayerType.COUNTRY, False): "pais",
    (LayerType.DEPARTMENTS, True): "departamentos_simple",
    (LayerType.DEPARTMENTS, False): "departamentos",
}


def _versioned_stem(data_dir: str, stem: str) -> str:
    matches = sorted(glob.glob(os.path.join(data_dir, f"{stem}_????????.geojson")))
    if not matches:
        raise FileNotFoundError(f"No data file found for {stem}")
    return matches[-1]


class FileSystemGeoLayerRepository:
    def __init__(self, data_dir: str, logger: Logger):
        self.data_dir = data_dir
        self.logger = logger

    def get_layer(self, layer: LayerType, simplified: bool) -> gpd.GeoDataFrame:
        stem = _LAYER_STEMS[(layer, simplified)]
        path = _versioned_stem(self.data_dir, stem)

        t0 = time.time()
        self.logger.info(f"Loading GeoDataFrame: {path}")
        gdf = gpd.read_file(path)
        self.logger.info(
            f"Loaded {path} in {time.time()-t0:.3f}s ({len(gdf)} features)"
        )
        return gdf
