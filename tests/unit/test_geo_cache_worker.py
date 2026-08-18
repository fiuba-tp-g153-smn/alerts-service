"""Unit tests for the geo_cache_worker subprocess and the scheduler's import weight.

MEM-01: the dept/prov index and IGN cache builds moved out of the main process
(where they caused resident glibc arena bloat) into this subprocess worker.
"""

import json
import os
import pickle
import subprocess
import sys

import geo_cache_worker

WORKER = geo_cache_worker.__file__
SRC_DIR = os.path.dirname(WORKER)


def _run_worker(tasks: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, WORKER],
        input=json.dumps(tasks).encode(),
        capture_output=True,
        check=False,
    )


def test_build_index_writes_pickle(tmp_path):
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
                "properties": {},
            }
        ],
    }
    in_path = tmp_path / "in.geojson"
    in_path.write_text(json.dumps(geojson), encoding="utf-8")
    out_path = tmp_path / "idx.pkl"

    result = _run_worker(
        [{"op": "build_index", "in_path": str(in_path), "out_path": str(out_path)}]
    )

    assert result.returncode == 0, result.stderr.decode()
    with open(out_path, "rb") as f:
        index = pickle.load(f)
    assert len(index) == 1
    bounds, geom = index[0]
    assert bounds == (1.0, 2.0, 1.0, 2.0)
    assert geom.geom_type == "Point"


def test_build_ign_missing_shapefiles_is_noop(tmp_path):
    # No limites.shp under shp_dir → worker logs a skip and exits 0 (not an error).
    out_path = tmp_path / "ign.pkl"
    result = _run_worker(
        [
            {
                "op": "build_ign",
                "shp_dir": str(tmp_path),
                "out_path": str(out_path),
                "tolerance": 0.005,
                "format_version": 3,
            }
        ]
    )

    assert result.returncode == 0, result.stderr.decode()
    assert not out_path.exists()


def test_unknown_op_exits_nonzero():
    result = _run_worker([{"op": "does_not_exist"}])

    assert result.returncode == 1
    assert b"Unknown op" in result.stderr


def test_scheduler_module_does_not_import_cartopy():
    # MEM-01 regression guard: cartopy was previously imported at the top of
    # scheduler (main process). It now lives only in geo_cache_worker (subprocess).
    code = (
        "import sys, scheduler;"
        "assert 'cartopy' not in sys.modules, 'scheduler pulled cartopy into main'"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        env={**os.environ, "PYTHONPATH": SRC_DIR},
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode()
