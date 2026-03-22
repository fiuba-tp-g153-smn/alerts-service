"""Subprocess worker for heavy geo processing (simplify, fgb conversion).

Input (stdin): [{"op": "simplify"|"convert_fgb", "in_path": "...", "out_path": "...",
                 "tolerance": 0.01}]
Output: exits 0 on success, 1 on error (errors printed to stderr).
"""

import json
import os
import sys

import geopandas as gpd


def _write_atomic(gdf, out_path: str, driver: str) -> None:
    """Write gdf to a .tmp sidecar then atomically rename to out_path."""
    tmp = out_path + ".tmp"
    try:
        gdf.to_file(tmp, driver=driver)
        os.replace(tmp, out_path)
    except:  # pylint: disable=bare-except
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def main():
    """Read geo processing tasks from stdin and execute each op (simplify or convert_fgb)."""
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
            _write_atomic(gdf, out_path, "GeoJSON")
        elif op == "convert_fgb":
            _write_atomic(gdf, out_path, "FlatGeobuf")
        else:
            print(f"Unknown op: {op}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
