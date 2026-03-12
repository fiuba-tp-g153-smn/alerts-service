"""Subprocess worker for full-resolution GeoDataFrame intersection.

Reads a JSON array of requests from stdin:
  [{"id": "...", "task": "country" | "departments", "geometry_wkb_hex": "...", "layer_path": "..."}]

Writes a JSON result dictionary to stdout and exits. The OS reclaims the entire process
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
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("Invalid JSON input", file=sys.stderr)
        sys.exit(1)

    if not isinstance(payload, list):
        print("Expected a JSON array of requests", file=sys.stderr)
        sys.exit(1)

    loaded_gdfs = {}
    output = {"results": {}, "errors": {}}

    for req in payload:
        req_id = req.get("id")
        if not req_id:
            continue

        try:
            task = req["task"]
            geom = shapely_wkb.loads(req["geometry_wkb_hex"], hex=True)
            layer_path = req["layer_path"]

            if layer_path not in loaded_gdfs:
                loaded_gdfs[layer_path] = gpd.read_file(layer_path)

            gdf = loaded_gdfs[layer_path]

            if task == "country":
                result = _intersect_country(gdf, geom)
                output["results"][req_id] = result
            elif task == "departments":
                features = _intersect_departments(gdf, geom)
                output["results"][req_id] = {"features": features}
            else:
                output["errors"][req_id] = f"Unknown task: {task}"
        except Exception as e:
            output["errors"][req_id] = str(e)

    json.dump(output, sys.stdout, cls=_NumpyEncoder)


if __name__ == "__main__":
    main()
