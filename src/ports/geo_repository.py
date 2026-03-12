from typing import Protocol

import geopandas as gpd

from domain.models import LayerType


class IGeoLayerRepository(Protocol):
    def get_layer(self, layer: LayerType, simplified: bool) -> gpd.GeoDataFrame: ...
