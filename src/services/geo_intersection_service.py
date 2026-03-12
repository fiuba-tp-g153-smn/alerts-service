import json
import time
from logging import Logger

from shapely.geometry import shape

from domain.models import LayerType
from ports.geo_repository import IGeoLayerRepository


class GeoIntersectionService:
    def __init__(self, repo: IGeoLayerRepository, logger: Logger):
        self.repo = repo
        self.logger = logger

    def intersect_country(self, geometry_dict: dict, simplified: bool) -> dict:
        input_geom = shape(geometry_dict)

        t0 = time.time()
        gdf = self.repo.get_layer(LayerType.COUNTRY, simplified)
        self.logger.info(f"intersect_country: load={time.time()-t0:.3f}s")

        t0 = time.time()
        intersection = gdf[gdf.intersects(input_geom)]
        intersection = intersection.intersection(input_geom)
        self.logger.info(f"intersect_country: intersect={time.time()-t0:.3f}s")

        t0 = time.time()
        result = json.loads(intersection.to_json())
        self.logger.info(f"intersect_country: serialize={time.time()-t0:.3f}s")

        return result

    def intersect_departments(
        self, geometry_dict: dict, simplified: bool
    ) -> list[dict]:
        input_geom = shape(geometry_dict)

        t0 = time.time()
        gdf = self.repo.get_layer(LayerType.DEPARTMENTS, simplified)
        self.logger.info(f"intersect_departments: load={time.time()-t0:.3f}s")

        t0 = time.time()
        mask = gdf.intersects(input_geom)
        intersecting = gdf[mask].copy()
        intersecting["intersection"] = intersecting["geometry"].intersection(input_geom)
        self.logger.info(f"intersect_departments: intersect={time.time()-t0:.3f}s")

        t0 = time.time()
        features = []
        for _, row in intersecting.iterrows():
            features.append(
                {
                    "properties": {
                        k: row[k]
                        for k in row.index
                        if k not in ("geometry", "intersection")
                    },
                    "geometry": row["geometry"].__geo_interface__,
                    "intersection": row["intersection"].__geo_interface__,
                }
            )
        self.logger.info(f"intersect_departments: serialize={time.time()-t0:.3f}s")

        return features
