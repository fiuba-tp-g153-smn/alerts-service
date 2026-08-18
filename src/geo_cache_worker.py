"""Subprocess worker for heavy geo cache building (dept/prov index + IGN layers).

Runs the GeoPandas/Shapely/Cartopy-heavy cache builds in a child process so the
long-lived main process never imports the geo stack. GeoPandas causes glibc arena
memory bloat that stays resident for the process lifetime; a subprocess exits and
returns that memory to the OS (same rationale as geo_processing_worker.py and the
alert render worker).

Input (stdin): a JSON list of tasks. Supported ops:
  {"op": "build_index", "in_path": "...", "out_path": "..."}
  {"op": "build_ign", "shp_dir": "...", "out_path": "...",
   "tolerance": 0.005, "format_version": 3}
Output: exits 0 on success, 1 on error (errors printed to stderr).

GeoPandas/Cartopy are imported lazily inside _build_ign so a build_index-only
invocation pays only the (lighter) Shapely import.
"""

import json
import os
import pickle
import struct
import sys

from shapely.geometry import shape as shapely_shape


def _write_atomic_pickle(obj, out_path: str) -> None:
    """Pickle obj to a .tmp sidecar then atomically rename to out_path."""
    stem, ext = os.path.splitext(out_path)
    tmp = f"{stem}.tmp{ext}"
    try:
        with open(tmp, "wb") as f:
            pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, out_path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _build_index(in_path: str, out_path: str) -> None:
    """Parse a GeoJSON into a [(bounds, geometry), ...] spatial index pickle."""
    with open(in_path, encoding="utf-8") as f:
        gj = json.load(f)
    index = []
    for feat in gj["features"]:
        g = shapely_shape(feat["geometry"])
        index.append((g.bounds, g))
    _write_atomic_pickle(index, out_path)
    print(f"build_index: {len(index)} geometries -> {out_path}", file=sys.stderr)


def _read_place_labels_manual(shp_path: str) -> list:
    """Read toponimos.shp without geopandas/pyogrio to avoid the latin-1 encoding error.

    Parses the DBF with latin-1 via stdlib and extracts PointZ coordinates from the SHP.
    Filters only the types relevant for the map: 'arg', 'continen' (with Arg.),
    and 'isla'.
    """
    dbf_path = shp_path.replace(".shp", ".dbf")

    # --- Read DBF attributes (latin-1) ---------------------------------------
    attrs: list = []
    try:
        with open(dbf_path, "rb") as f:
            hdr = f.read(32)
            num_recs = struct.unpack("<I", hdr[4:8])[0]
            hdr_size = struct.unpack("<H", hdr[8:10])[0]

            fields: list = []
            while True:
                fd = f.read(32)
                if not fd or fd[0] == 0x0D:
                    break
                fname = fd[:11].rstrip(b"\x00").decode("ascii", errors="replace")
                ftype = chr(fd[11])
                flen = fd[16]
                fields.append((fname, ftype, flen))

            f.seek(hdr_size)
            for _ in range(num_recs):
                flag = f.read(1)
                rec: dict = {}
                for fname, ftype, flen in fields:
                    raw = f.read(flen)
                    if ftype == "C":
                        rec[fname] = raw.rstrip(b"\x00 ").decode(
                            "latin-1", errors="replace"
                        )
                    elif ftype in ("N", "F"):
                        try:
                            rec[fname] = float(raw.strip()) if raw.strip() else None
                        except ValueError:
                            rec[fname] = None
                    else:
                        rec[fname] = raw.rstrip(b"\x00").decode(
                            "latin-1", errors="replace"
                        )
                if flag != b"*":  # not a deleted record
                    attrs.append(rec)
    except Exception as exc:
        print(f"Could not read {dbf_path}: {exc}", file=sys.stderr)
        return []

    # --- Read SHP coordinates (PointZ = type 13, Point = type 1) -------------
    coords: list = []
    try:
        with open(shp_path, "rb") as f:
            shp_hdr = f.read(100)
            file_len = struct.unpack(">I", shp_hdr[24:28])[0] * 2
            while f.tell() < file_len:
                rec_hdr = f.read(8)
                if len(rec_hdr) < 8:
                    break
                content_len = struct.unpack(">I", rec_hdr[4:8])[0] * 2
                content = f.read(content_len)
                if len(content) < 4:
                    coords.append((None, None))
                    continue
                stype = struct.unpack("<i", content[:4])[0]
                if stype in (1, 13) and len(content) >= 20:  # Point or PointZ
                    x, y = struct.unpack("<dd", content[4:20])
                    coords.append((round(x, 4), round(y, 4)))
                else:
                    coords.append((None, None))
    except Exception as exc:
        print(f"Could not read {shp_path}: {exc}", file=sys.stderr)
        return []

    # --- Combine and filter ---------------------------------------------------
    TYPES = {"arg", "continen", "isla"}
    EXCLUDE = {
        "ISLAS AURORA (Arg.)",
        "ISLAS GEORGIAS DEL SUR (Arg.)",
        "ISLAS SANDWICH DEL SUR (Arg.)",
    }
    result: list = []
    for (lon, lat), attr in zip(coords, attrs):
        if lon is None:
            continue
        kind = str(attr.get("tipo", "") or "")
        name = str(attr.get("nombre", "") or "")
        if kind not in TYPES:
            continue
        if kind == "continen" and "(Arg.)" not in name:
            continue
        if name in EXCLUDE:
            continue
        # Malvinas: show only "(Arg.)" without the full name
        if name == "ISLAS MALVINAS (Arg.)":
            name = "(Arg.)"
        result.append({"lon": lon, "lat": lat, "nombre": name, "tipo": kind})

    print(
        f"Place labels loaded: {len(result)} (Arg.) labels + islands", file=sys.stderr
    )
    return result


def _build_ign(
    shp_dir: str, out_path: str, tolerance: float, format_version: int
) -> None:
    """Read IGN shapefiles, simplify + project to Mercator, pickle ign_layers.pkl.

    Geometries (except 'place_labels') are stored pre-projected to ccrs.Mercator()
    so the alert render worker can draw them via add_geometries(crs=ccrs.Mercator()),
    skipping cartopy's expensive per-request trace-based reprojection.
    """
    import cartopy.crs as ccrs
    import geopandas as gpd

    borders_shp = os.path.join(shp_dir, "limites.shp")
    provinces_shp = os.path.join(shp_dir, "Provincias.shp")
    references_shp = os.path.join(shp_dir, "referencias.shp")
    place_labels_shp = os.path.join(shp_dir, "toponimos.shp")

    if not os.path.exists(borders_shp):
        print(f"IGN shapefiles not found at {shp_dir} — skipping", file=sys.stderr)
        return

    tol = tolerance
    pc = ccrs.PlateCarree()
    merc = ccrs.Mercator()

    def _simplify_wkb(geoms) -> list:
        """Simplify, project to Mercator, and serialise geometries to WKB hex strings."""
        result = []
        for g in geoms:
            if g is None or g.is_empty:
                continue
            sg = g.simplify(tol, preserve_topology=True)
            if sg.is_empty:
                continue
            pg = merc.project_geometry(sg, pc)
            if not pg.is_empty:
                result.append(pg.wkb_hex)
        return result

    # --- limites.shp ---------------------------------------------------------
    lim = gpd.read_file(borders_shp)
    obj_col = "Objeto" if "Objeto" in lim.columns else "objeto"
    nam_col = "NAM" if "NAM" in lim.columns else "nam"

    GROUP_A = {
        "Límite internacional",
        "Límite del lecho y subsuelo del Río de la Plata",
        "Límite lateral marítimo argentino-uruguayo",
    }
    GROUP_B = {"Límite Interprovincial", "Línea de costa"}
    GROUP_C = {"Límite exterior del Río de la Plata"}
    ANTARCTIC_SECTOR = "Límite del Sector Antártico Argentino"

    ga, gb, gc, gd = [], [], [], []
    for _, row in lim.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        obj = row.get(obj_col, "") or ""
        nam = row.get(nam_col, "") or ""
        if ANTARCTIC_SECTOR in nam:
            gd.append(geom)
        elif obj in GROUP_A:
            ga.append(geom)
        elif obj in GROUP_B:
            gb.append(geom)
        elif obj in GROUP_C:
            gc.append(geom)

    # --- Provincias.shp ------------------------------------------------------
    provinces_wkb: list = []
    if os.path.exists(provinces_shp):
        prov_df = gpd.read_file(provinces_shp)
        provinces_wkb = _simplify_wkb(list(prov_df.geometry))

    # --- referencias.shp (neighboring countries) ------------------------------
    countries_wkb: list = []
    if os.path.exists(references_shp):
        ref_df = gpd.read_file(references_shp)
        type_col = "tipo" if "tipo" in ref_df.columns else "TIPO"
        countries_df = ref_df[ref_df[type_col] == "país"]
        countries_wkb = _simplify_wkb(list(countries_df.geometry))

    # --- toponimos.shp (text labels: (Arg.), island names, etc.) ---------------
    place_labels: list = []
    if os.path.exists(place_labels_shp):
        place_labels = _read_place_labels_manual(place_labels_shp)

    layers = {
        "_format_version": format_version,
        "group_a": _simplify_wkb(ga),
        "group_b": _simplify_wkb(gb),
        "group_c": _simplify_wkb(gc),
        "group_d": _simplify_wkb(gd),
        "provinces": provinces_wkb,
        "countries": countries_wkb,
        "place_labels": place_labels,
    }

    _write_atomic_pickle(layers, out_path)
    totals = {k: len(v) for k, v in layers.items() if isinstance(v, (list, dict))}
    print(f"ign_layers.pkl ready: {totals} -> {out_path}", file=sys.stderr)


def main() -> None:
    """Read cache-build tasks from stdin and execute each op."""
    tasks = json.load(sys.stdin)
    for task in tasks:
        op = task["op"]
        if op == "build_index":
            _build_index(task["in_path"], task["out_path"])
        elif op == "build_ign":
            _build_ign(
                task["shp_dir"],
                task["out_path"],
                task["tolerance"],
                task["format_version"],
            )
        else:
            print(f"Unknown op: {op}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
