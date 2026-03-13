"""Subprocess worker for heavy geo processing (simplify, fgb conversion).

Input (stdin): [{"op": "simplify"|"convert_fgb", "in_path": "...", "out_path": "...", "tolerance": 0.01}]
Output: exits 0 on success, 1 on error (errors printed to stderr).
"""

import json
import os
import sys

os.environ.setdefault("OGR_GEOJSON_MAX_OBJ_SIZE", "0")

import geopandas as gpd


def main():
    tasks = json.load(sys.stdin)
    for task in tasks:
        op = task["op"]
        in_path, out_path = task["in_path"], task["out_path"]
        gdf = gpd.read_file(in_path)
        if op == "simplify":
            tolerance = task["tolerance"]
            gdf["geometry"] = gdf["geometry"].simplify(
                tolerance, preserve_topology=True
            )
            gdf.to_file(out_path, driver="GeoJSON")
        elif op == "convert_fgb":
            gdf.to_file(out_path, driver="FlatGeobuf")
        else:
            print(f"Unknown op: {op}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
