"""Port definition for geographic layer data access."""

from abc import ABC, abstractmethod

import geopandas as gpd

from domain.models import LayerType


class IGeoLayerRepository(ABC):  # pylint: disable=too-few-public-methods
    """Abstract base class for geo layer repository implementations."""

    @abstractmethod
    async def get_layer(self, layer: LayerType, level: int) -> gpd.GeoDataFrame:
        """Load and return the cached GeoDataFrame for the given layer and simplification level."""
