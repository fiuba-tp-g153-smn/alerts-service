"""Port definition for geographic layer data access."""

from abc import ABC, abstractmethod

import geopandas as gpd

from domain.models import LayerType


class IGeoLayerRepository(ABC):
    """Abstract base class for geo layer repository implementations."""

    @abstractmethod
    def get_layer(self, layer: LayerType, simplified: bool) -> gpd.GeoDataFrame:
        """Load and return the GeoDataFrame for the given layer and resolution."""

    @abstractmethod
    def get_layer_path(self, layer: LayerType, simplified: bool) -> str:
        """Return the filesystem path for the given layer and resolution without loading it."""

    @abstractmethod
    def get_fullres_fgb_path(self, layer: LayerType) -> str:
        """Return the filesystem path for the latest full-res FlatGeobuf file."""

    @abstractmethod
    def preload(self) -> None:
        """Preload layers into memory."""
