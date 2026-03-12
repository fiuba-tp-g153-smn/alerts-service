"""Service for computing geospatial intersections against Argentine geographic layers."""

import json
import os
import subprocess
import sys
import time
from logging import Logger

from shapely import wkb as shapely_wkb
from shapely.geometry import shape

from domain.models import LayerType
from ports.geo_repository import IGeoLayerRepository

_WORKER_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "fullres_worker.py")
)


class GeoIntersectionService:
    """Computes polygon intersections with country and department layers."""

    def __init__(self, repo: IGeoLayerRepository, logger: Logger):
        """Initialise with a geo layer repository and a logger."""
        self.repo = repo
        self.logger = logger

    def _run_fullres_subprocess(self, task: str, geom, layer_path: str) -> dict:
        """Run the intersection in an isolated subprocess to prevent glibc arena bloat."""
        payload = {
            "task": task,
            "geometry_wkb_hex": shapely_wkb.dumps(geom, hex=True),
            "layer_path": layer_path,
        }
        result = subprocess.run(
            [sys.executable, _WORKER_PATH],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"fullres_worker failed (exit {result.returncode}): {result.stderr}"
            )
        return json.loads(result.stdout)

    def intersect_country(self, geometry_dict: dict, simplified: bool) -> dict:
        """Return a GeoJSON FeatureCollection of the intersection with Argentina."""
        input_geom = shape(geometry_dict)

        if not simplified:
            t0 = time.time()
            layer_path = self.repo.get_layer_path(LayerType.COUNTRY, simplified=False)
            result = self._run_fullres_subprocess("country", input_geom, layer_path)
            self.logger.info(
                f"intersect_country (fullres subprocess): {time.time()-t0:.3f}s"
            )
            return result

        t0 = time.time()
        gdf = self.repo.get_layer(LayerType.COUNTRY, simplified)
        self.logger.info(f"intersect_country: load={time.time()-t0:.3f}s")

        t0 = time.time()
        matching = gdf[gdf.intersects(input_geom)]
        intersection = matching.intersection(input_geom)
        self.logger.info(f"intersect_country: intersect={time.time()-t0:.3f}s")

        t0 = time.time()
        result = json.loads(intersection.to_json())
        self.logger.info(f"intersect_country: serialize={time.time()-t0:.3f}s")

        return result

    def intersect_departments(
        self, geometry_dict: dict, simplified: bool
    ) -> list[dict]:
        """Return departments intersecting the input geometry with their intersection shapes."""
        input_geom = shape(geometry_dict)

        if not simplified:
            t0 = time.time()
            layer_path = self.repo.get_layer_path(
                LayerType.DEPARTMENTS, simplified=False
            )
            output = self._run_fullres_subprocess("departments", input_geom, layer_path)
            self.logger.info(
                f"intersect_departments (fullres subprocess): {time.time()-t0:.3f}s"
            )
            return output["features"]

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
