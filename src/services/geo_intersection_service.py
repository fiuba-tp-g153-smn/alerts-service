"""Service for computing geospatial intersections against Argentine geographic layers."""

import json
import time
from logging import Logger

from shapely.geometry import shape

from geo_utils import build_department_features
from ports.geo_repository import IGeoLayerRepository


class GeoIntersectionService:
    """Computes polygon intersections with country and department layers."""

    def __init__(
        self,
        repo: IGeoLayerRepository,
        logger: Logger,
    ):
        """Initialise with a geo layer repository and a logger."""
        self.repo = repo
        self.logger = logger

    async def intersect_country(self, geometry_dict: dict, detail_level: int) -> dict:
        """Return a GeoJSON FeatureCollection of the intersection with Argentina."""
        input_geom = shape(geometry_dict)

        t0 = time.perf_counter()
        gdf = await self.repo.get_country_layer(detail_level)
        self.logger.info(f"intersect_country: load={time.perf_counter()-t0:.3f}s")

        t0 = time.perf_counter()
        intersection = gdf[gdf.intersects(input_geom)].intersection(input_geom)
        self.logger.info(f"intersect_country: intersect={time.perf_counter()-t0:.3f}s")

        t0 = time.perf_counter()
        result = json.loads(intersection.to_json())
        self.logger.info(
            f"intersect_country: serialize={time.perf_counter()-t0:.3f}s"
            f" (detail_level={detail_level})"
        )
        return result

    async def intersect_departments(self, geometry_dict: dict) -> list[dict]:
        """Return departments intersecting the input geometry with their intersection shapes."""
        input_geom = shape(geometry_dict)

        t0 = time.perf_counter()
        gdf = await self.repo.get_departments_layer()
        self.logger.info(f"intersect_departments: load={time.perf_counter()-t0:.3f}s")

        t0 = time.perf_counter()
        intersecting = gdf[gdf.intersects(input_geom)].copy()
        intersecting["intersection"] = intersecting["geometry"].intersection(input_geom)
        self.logger.info(
            f"intersect_departments: intersect={time.perf_counter()-t0:.3f}s"
        )

        t0 = time.perf_counter()
        features = build_department_features(intersecting)
        self.logger.info(f"intersect_departments: serialize={time.perf_counter()-t0:.3f}s")
        return features
