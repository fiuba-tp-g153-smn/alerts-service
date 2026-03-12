"""Port definition for geographic layer data access."""

from abc import ABC, abstractmethod

import geopandas as gpd

from domain.models import LayerType


class IGeoLayerRepository(ABC):  # pylint: disable=too-few-public-methods
    """Abstract base class for geo layer repository implementations."""

    @abstractmethod
    def get_layer(self, layer: LayerType, simplified: bool) -> gpd.GeoDataFrame:
        """Load and return the GeoDataFrame for the given layer and resolution."""
