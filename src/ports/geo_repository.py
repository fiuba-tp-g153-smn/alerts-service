"""Port definition for geographic layer data access."""

from typing import Protocol

import geopandas as gpd

from domain.models import LayerType


class IGeoLayerRepository(Protocol):  # pylint: disable=too-few-public-methods
    """Protocol for geo layer repository implementations."""

    def get_layer(self, layer: LayerType, simplified: bool) -> gpd.GeoDataFrame:
        """Load and return the GeoDataFrame for the given layer and resolution."""
