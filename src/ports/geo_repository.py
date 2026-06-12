"""Port definition for geographic layer data access."""

from abc import ABC, abstractmethod

import geopandas as gpd


class IGeoLayerRepository(ABC):
    """Abstract base class for geo layer repository implementations."""

    @abstractmethod
    async def get_country_layer(self, detail_level: int) -> gpd.GeoDataFrame:
        """Load and return the cached GeoDataFrame for the country layer at the given level."""

    @abstractmethod
    async def get_departments_layer(self) -> gpd.GeoDataFrame:
        """Load and return the cached GeoDataFrame for the departments layer."""
