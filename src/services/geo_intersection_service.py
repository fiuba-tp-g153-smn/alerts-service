"""Service for computing geospatial intersections against Argentine geographic layers."""

import asyncio
import json
import os
import sys
import time
from logging import Logger

from shapely import wkb as shapely_wkb
from shapely.geometry import shape

from domain.models import LayerType
from geo_utils import build_department_features
from ports.geo_repository import IGeoLayerRepository

_WORKER_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "fullres_worker.py")
)

# Caps concurrent full-resolution subprocess launches to prevent simultaneous RAM peaks.
_FULLRES_SEMAPHORE = asyncio.Semaphore(1)


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

    async def _run_fullres_subprocess(
        self, task: str, geom, layer_path: str, bbox: tuple | None = None
    ) -> dict:
        """Run the intersection in an isolated subprocess to prevent glibc arena bloat.

        Uses asyncio.create_subprocess_exec so no thread-pool thread is blocked and
        no per-thread glibc arena accumulates the large JSON payload. The module-level
        semaphore serialises launches to prevent simultaneous RAM peaks.
        """
        payload = json.dumps(
            [
                {
                    "id": "req",
                    "task": task,
                    "geometry_wkb_hex": shapely_wkb.dumps(geom, hex=True),
                    "layer_path": layer_path,
                    "bbox": list(bbox) if bbox else None,
                }
            ]
        )

        async with _FULLRES_SEMAPHORE:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                _WORKER_PATH,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await proc.communicate(payload.encode())

        if proc.returncode != 0:
            raise RuntimeError(
                f"fullres_worker failed (exit {proc.returncode}): "
                f"{stderr_bytes.decode(errors='replace')}"
            )

        try:
            output = json.loads(stdout_bytes)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"fullres_worker returned invalid JSON: {stdout_bytes[:200]!r}"
            ) from exc

        if "req" in output.get("errors", {}):
            raise RuntimeError(f"fullres_worker error: {output['errors']['req']}")
        if "req" not in output.get("results", {}):
            raise RuntimeError("fullres_worker did not return a result")

        return output["results"]["req"]

    async def intersect_country(self, geometry_dict: dict, simplified: bool) -> dict:
        """Return a GeoJSON FeatureCollection of the intersection with Argentina."""
        input_geom = shape(geometry_dict)

        if not simplified:
            t0 = time.time()
            try:
                layer_path = self.repo.get_fullres_fgb_path(LayerType.COUNTRY)
            except FileNotFoundError:
                layer_path = self.repo.get_layer_path(
                    LayerType.COUNTRY, simplified=False
                )
            result = await self._run_fullres_subprocess(
                "country", input_geom, layer_path, bbox=None
            )
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

    async def intersect_departments(
        self, geometry_dict: dict, simplified: bool
    ) -> list[dict]:
        """Return departments intersecting the input geometry with their intersection shapes."""
        input_geom = shape(geometry_dict)

        if not simplified:
            t0 = time.time()
            try:
                layer_path = self.repo.get_fullres_fgb_path(LayerType.DEPARTMENTS)
            except FileNotFoundError:
                layer_path = self.repo.get_layer_path(
                    LayerType.DEPARTMENTS, simplified=False
                )
            bbox = tuple(input_geom.bounds)
            output = await self._run_fullres_subprocess(
                "departments", input_geom, layer_path, bbox=bbox
            )
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
        features = build_department_features(intersecting)
        self.logger.info(f"intersect_departments: serialize={time.time()-t0:.3f}s")

        return features
