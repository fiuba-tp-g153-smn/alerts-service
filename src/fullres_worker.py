"""Subprocess worker for full-resolution GeoDataFrame intersection.

Reads a JSON payload from stdin:
  {"task": "country" | "departments", "geometry_wkb_hex": "...", "layer_path": "..."}

Writes the JSON result to stdout and exits. The OS reclaims the entire process
address space on exit — GEOS heap and all glibc arenas — unconditionally.
"""

import json
import sys

import geopandas as gpd
import numpy as np
from shapely import wkb as shapely_wkb


class _NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def _intersect_country(gdf, geom):
    matching = gdf[gdf.intersects(geom)]
    intersection = matching.intersection(geom)
    return json.loads(intersection.to_json())


def _intersect_departments(gdf, geom):
    mask = gdf.intersects(geom)
    intersecting = gdf[mask].copy()
    intersecting["intersection"] = intersecting["geometry"].intersection(geom)
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
    return features


def main():
    payload = json.load(sys.stdin)
    task = payload["task"]
    geom = shapely_wkb.loads(payload["geometry_wkb_hex"], hex=True)
    layer_path = payload["layer_path"]

    gdf = gpd.read_file(layer_path)

    if task == "country":
        result = _intersect_country(gdf, geom)
        json.dump(result, sys.stdout, cls=_NumpyEncoder)
    elif task == "departments":
        features = _intersect_departments(gdf, geom)
        json.dump({"features": features}, sys.stdout, cls=_NumpyEncoder)
    else:
        print(f"Unknown task: {task}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
