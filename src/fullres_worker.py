"""Subprocess worker for full-resolution GeoDataFrame intersection.

Reads a JSON array of requests from stdin:
  [{"id": "...", "task": "country" | "departments", "geometry_wkb_hex": "...", "layer_path": "...", "bbox": [...] | null}]

Writes a JSON result dictionary to stdout and exits. The OS reclaims the entire process
address space on exit — GEOS heap and all glibc arenas — unconditionally.
"""

import json
import os
import sys

os.environ.setdefault("OGR_GEOJSON_MAX_OBJ_SIZE", "0")

import geopandas as gpd
import numpy as np
from shapely import wkb as shapely_wkb

from geo_utils import build_department_features


class _NumpyEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        return super().default(o)


def _intersect_country(gdf, geom):
    matching = gdf[gdf.intersects(geom)]
    intersection = matching.intersection(geom)
    return json.loads(intersection.to_json())


def _intersect_departments(gdf, geom):
    mask = gdf.intersects(geom)
    intersecting = gdf[mask].copy()
    intersecting["intersection"] = intersecting["geometry"].intersection(geom)
    return build_department_features(intersecting)


def main():
    """Read batched intersection requests from stdin, process them, and write results to stdout."""
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("Invalid JSON input", file=sys.stderr)
        sys.exit(1)

    if not isinstance(payload, list):
        print("Expected a JSON array of requests", file=sys.stderr)
        sys.exit(1)

    loaded_gdfs = {}
    output: dict[str, dict] = {"results": {}, "errors": {}}

    for req in payload:
        req_id = req.get("id")
        if not req_id:
            continue

        try:
            task = req["task"]
            geom = shapely_wkb.loads(req["geometry_wkb_hex"], hex=True)
            layer_path = req["layer_path"]
            bbox = req.get("bbox")

            cache_key = (layer_path, tuple(bbox) if bbox else ())
            if cache_key not in loaded_gdfs:
                kwargs = {"engine": "pyogrio"}
                if bbox:
                    kwargs["bbox"] = tuple(bbox)
                loaded_gdfs[cache_key] = gpd.read_file(layer_path, **kwargs)

            gdf = loaded_gdfs[cache_key]

            if task == "country":
                result = _intersect_country(gdf, geom)
                output["results"][req_id] = result
            elif task == "departments":
                features = _intersect_departments(gdf, geom)
                output["results"][req_id] = {"features": features}
            else:
                output["errors"][req_id] = f"Unknown task: {task}"
        except Exception as e:  # pylint: disable=broad-exception-caught
            output["errors"][req_id] = str(e)

    json.dump(output, sys.stdout, cls=_NumpyEncoder)


if __name__ == "__main__":
    main()
