#!/usr/bin/env python3
"""
Alert generation worker subprocess — generates GIF visualizations.

Reads JSON from stdin, generates 2 GIF maps using matplotlib/cartopy,
writes result to stdout, and exits (releasing all matplotlib memory).

Input JSON:
{
    "geometry_wkb_hex": "...",
    "phenomenon_text": "TORMENTAS...",
    "timestamp": "20260315_143052",
    "affected_departments": [{...}],
    "all_departments": [{...}],
    "output_dir": "/app/output/alerts",
    "cache_dir": "/app/cache"
}

Output JSON:
{
    "status": "success",
    "gif_area": "/app/output/alerts/20260315_143052_aviso.gif",
    "gif_gral": "/app/output/alerts/20260315_143052_gral.gif"
}
"""

import json
import os
import pickle
import sys
from typing import Any, cast

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib
import matplotlib.patches as mpatches
import matplotlib.patheffects as pe
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.patches import Polygon as MplPolygon
from PIL import Image
from shapely import wkb as shapely_wkb
from shapely.geometry import Polygon

matplotlib.use("Agg")  # must precede pyplot import

import matplotlib.pyplot as plt  # pylint: disable=wrong-import-position,import-error,ungrouped-imports


def _load_index(path: str) -> list:
    """Load spatial index from pickle file, return empty list if missing."""
    if not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        return pickle.load(f)


def _load_spatial_indices(payload: dict, cache_dir: str) -> tuple:
    """Deserialize spatial indices from payload, falling back to disk."""
    dept_index_serialized = payload.get("dept_index_serialized")
    prov_geoms_serialized = payload.get("prov_geoms_serialized")

    if dept_index_serialized is not None:
        dept_index = [
            (tuple(item["bbox"]), shapely_wkb.loads(item["wkb_hex"], hex=True))
            for item in dept_index_serialized
        ]
    else:
        dept_index = _load_index(os.path.join(cache_dir, "dept_index.pkl"))
    dept_geoms_all = [geom for _, geom in dept_index]

    if prov_geoms_serialized is not None:
        prov_geoms = [
            shapely_wkb.loads(wkb_hex, hex=True) for wkb_hex in prov_geoms_serialized
        ]
    else:
        prov_index = _load_index(os.path.join(cache_dir, "prov_index.pkl"))
        prov_geoms = [g for _, g in prov_index]

    return dept_index, dept_geoms_all, prov_geoms


def _dept_geoms_en_bbox(dept_index: list, lon_o, lon_e, lat_s, lat_n) -> list:
    """Filter department geometries that overlap the given bounding box."""
    return [
        g
        for (bx0, by0, bx1, by1), g in dept_index
        if not (bx1 < lon_o or bx0 > lon_e or by1 < lat_s or by0 > lat_n)
    ]


def _panel_aviso(fig, texto, modo="area"):
    """Add white header panel with red border containing alert text."""
    ax2 = fig.add_axes([0.0, 0.86, 1.0, 0.14])
    ax2.set_xlim(0, 1)
    ax2.set_ylim(0, 1)
    ax2.axis("off")

    # White box with red border
    ax2.add_patch(
        mpatches.FancyBboxPatch(
            (0, 0),
            1,
            1,
            facecolor="white",
            edgecolor="red",
            linewidth=2.5,
            boxstyle="square,pad=0",
            transform=ax2.transAxes,
            clip_on=False,
        )
    )

    # Horizontal divider
    ax2.axhline(y=0.52, color="red", linewidth=1.2)

    # Title (monospace font)
    ax2.text(
        0.5,
        0.76,
        "AVISO A CORTO PLAZO",
        ha="center",
        va="center",
        fontsize=22,
        fontweight="bold",
        color="#000000",
        fontfamily="monospace",
        fontstyle="normal",
        antialiased=False,
        transform=ax2.transAxes,
    )

    if modo == "gral":
        ax2.text(
            0.97,
            0.80,
            "ZONA: AREA TOTAL",
            ha="right",
            va="center",
            fontsize=11,
            fontweight="bold",
            fontstyle="italic",
            fontfamily="monospace",
            antialiased=False,
            color="#000000",
            transform=ax2.transAxes,
        )

    # Description (serif font)
    ax2.text(
        0.02,
        0.44,
        "EL AREA GRAFICADA EN EL MAPA DELIMITA LA OCURRENCIA DE:",
        ha="left",
        va="top",
        fontsize=12,
        fontstyle="italic",
        fontfamily="serif",
        fontweight="bold",
        antialiased=False,
        color="#000000",
        transform=ax2.transAxes,
    )

    # Phenomenon text (red, serif, italic)
    ax2.text(
        0.5,
        0.14,
        texto,
        ha="center",
        va="center",
        fontsize=14,
        fontweight="normal",
        fontstyle="italic",
        fontfamily="serif",
        antialiased=False,
        color="red",
        transform=ax2.transAxes,
    )


def generar_gif_area(  # pylint: disable=too-many-locals
    text,
    coords,
    departments,
    timestamp,
    output_dir,
    all_departments,
    dept_index,
    prov_geoms,
):
    """Generate zoomed-in area GIF showing affected region."""
    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    lat_s, lat_n = min(lats) - 0.8, max(lats) + 0.8
    lon_o, lon_e = min(lons) - 1.0, max(lons) + 1.0

    proj = ccrs.Mercator()
    fig = plt.figure(figsize=(13.75, 14), dpi=80)
    ax: GeoAxes = cast(GeoAxes, fig.add_axes((0, 0.01, 1, 0.86), projection=proj))
    ax.set_extent([lon_o, lon_e, lat_s, lat_n], crs=ccrs.PlateCarree())

    try:
        ax.spines["geo"].set_visible(False)
    except KeyError:
        cast(Any, ax).outline_patch.set_visible(False)

    # Base layers
    ax.add_feature(cfeature.OCEAN.with_scale("50m"), facecolor="#B0D8E8", zorder=0)
    ax.add_feature(cfeature.LAND.with_scale("50m"), facecolor="white", zorder=1)
    ax.add_feature(
        cfeature.BORDERS.with_scale("50m"), edgecolor="black", linewidth=1.8, zorder=4
    )
    ax.add_feature(
        cfeature.COASTLINE.with_scale("50m"), edgecolor="black", linewidth=0.8, zorder=4
    )

    # Department boundaries filtered to visible bbox
    dept_vis = _dept_geoms_en_bbox(dept_index, lon_o, lon_e, lat_s, lat_n)
    if dept_vis:
        ax.add_geometries(
            dept_vis,
            crs=ccrs.PlateCarree(),
            edgecolor="black",
            facecolor="none",
            linewidth=0.8,
            zorder=4,
        )

    # Province boundaries (all, thicker line)
    if prov_geoms:
        ax.add_geometries(
            prov_geoms,
            crs=ccrs.PlateCarree(),
            edgecolor="black",
            facecolor="none",
            linewidth=1.8,
            zorder=5,
        )

    # Polygon (hatch pattern + border)
    xy = list(zip(lons, lats))
    ax.add_patch(
        MplPolygon(
            xy,
            closed=True,
            facecolor="none",
            edgecolor="#CC0000",
            linewidth=0,
            hatch="//",
            transform=ccrs.PlateCarree(),
            zorder=5,
        )
    )
    ax.add_patch(
        MplPolygon(
            xy,
            closed=True,
            facecolor="none",
            edgecolor="#CC0000",
            linewidth=2.5,
            transform=ccrs.PlateCarree(),
            zorder=6,
        )
    )

    # Departments - all visible in bbox, highlighted if affected
    affected_ids = {(d["id_provincia"], d["id_localidad"]) for d in departments}
    for department in all_departments or []:
        lon, lat = float(department["longitud"]), float(department["latitud"])
        if not (lon_o <= lon <= lon_e and lat_s <= lat <= lat_n):
            continue

        is_affected = (
            department["id_provincia"],
            department["id_localidad"],
        ) in affected_ids

        color_pt = "#111111" if is_affected else "#555555"
        color_txt = "#111111"
        marker = "o" if is_affected else "."
        marker_size = 5 if is_affected else 3.5
        z_pt, z_txt = (9, 10) if is_affected else (7, 8)

        ax.plot(
            lon,
            lat,
            marker,
            color=color_pt,
            markersize=marker_size,
            transform=ccrs.PlateCarree(),
            zorder=z_pt,
        )

        ax.text(
            lon + 0.04,
            lat + 0.03,
            department["nom_departamento"],
            fontsize=7.5,
            color=color_txt,
            fontweight="bold",
            transform=ccrs.PlateCarree(),
            zorder=z_txt,
            clip_on=True,
            path_effects=[pe.withStroke(linewidth=2, foreground="white")],
        )

    _panel_aviso(fig, text, modo="area")

    out = os.path.join(output_dir, f"{timestamp}_aviso.gif")
    tmp = out.replace(".gif", "_tmp.png")
    fig.savefig(
        tmp, format="png", bbox_inches=None, pad_inches=0, facecolor="white", dpi=80
    )
    plt.close(fig)
    Image.open(tmp).convert("P", palette=Image.Palette.ADAPTIVE).save(out, format="GIF")
    os.remove(tmp)

    return out


def generar_gif_general(text, coords, timestamp, output_dir, dept_geoms, prov_geoms):
    """Generate country-wide GIF showing full Argentina with polygon."""
    lons = [c[1] for c in coords]
    lats = [c[0] for c in coords]
    xy = list(zip(lons, lats))

    proj = ccrs.Mercator()
    fig_final = plt.figure(figsize=(13.75, 14), dpi=80)
    ax_map: GeoAxes = cast(
        GeoAxes, fig_final.add_axes((0, 0.01, 1, 0.86), projection=proj)
    )
    ax_map.set_extent([-78, -51, -56, -21], crs=ccrs.PlateCarree())

    try:
        ax_map.spines["geo"].set_visible(False)
    except KeyError:
        cast(Any, ax_map).outline_patch.set_visible(False)

    # Base layers
    ax_map.add_feature(cfeature.OCEAN.with_scale("50m"), facecolor="#B0D8E8", zorder=0)
    ax_map.add_feature(cfeature.LAND.with_scale("50m"), facecolor="white", zorder=1)
    ax_map.add_feature(
        cfeature.BORDERS.with_scale("50m"), edgecolor="black", linewidth=1.8, zorder=4
    )
    ax_map.add_feature(
        cfeature.COASTLINE.with_scale("50m"), edgecolor="black", linewidth=1.4, zorder=4
    )

    # All department boundaries
    if dept_geoms:
        ax_map.add_geometries(
            dept_geoms,
            crs=ccrs.PlateCarree(),
            edgecolor="black",
            facecolor="none",
            linewidth=0.5,
            zorder=3,
        )

    # All province boundaries (thicker)
    if prov_geoms:
        ax_map.add_geometries(
            prov_geoms,
            crs=ccrs.PlateCarree(),
            edgecolor="black",
            facecolor="none",
            linewidth=1.8,
            zorder=4,
        )

    # Polygon (filled with transparency + border)
    ax_map.add_patch(
        MplPolygon(
            xy,
            closed=True,
            facecolor="#FF4444",
            alpha=0.55,
            edgecolor="#CC0000",
            linewidth=2.5,
            transform=ccrs.PlateCarree(),
            zorder=5,
        )
    )

    _panel_aviso(fig_final, text, modo="gral")

    out = os.path.join(output_dir, f"{timestamp}_gral.gif")
    tmp = out.replace(".gif", "_tmp.png")
    fig_final.savefig(
        tmp, format="png", bbox_inches=None, pad_inches=0, facecolor="white", dpi=80
    )
    plt.close(fig_final)
    Image.open(tmp).convert("P", palette=Image.Palette.ADAPTIVE).save(out, format="GIF")
    os.remove(tmp)

    return out


def main():
    """Read request from stdin, generate GIFs, write result to stdout."""
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("Invalid JSON input", file=sys.stderr)
        sys.exit(1)

    try:
        # Extract input
        geometry_wkb_hex = payload["geometry_wkb_hex"]
        phenomenon_text = payload["phenomenon_text"]
        timestamp = payload["timestamp"]
        affected_departments = payload["affected_departments"]
        all_departments = payload["all_departments"]
        output_dir = payload["output_dir"]
        cache_dir = payload.get("cache_dir", "/app/cache")

        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)

        # Load spatial indices (from payload cache or disk fallback)
        dept_index, dept_geoms_all, prov_geoms = _load_spatial_indices(
            payload, cache_dir
        )

        # Decode geometry
        geom = shapely_wkb.loads(geometry_wkb_hex, hex=True)

        # Extract coordinates (lat, lon) from polygon
        if not isinstance(geom, Polygon):
            raise TypeError(f"Expected Polygon, got {type(geom)}")
        coords_raw = list(geom.exterior.coords)
        coords = [(lon, lat) for lat, lon in coords_raw]  # swap to (lat, lon)

        # Generate GIFs
        gif_area = generar_gif_area(
            phenomenon_text,
            coords,
            affected_departments,
            timestamp,
            output_dir,
            all_departments,
            dept_index,
            prov_geoms,
        )
        gif_gral = generar_gif_general(
            phenomenon_text, coords, timestamp, output_dir, dept_geoms_all, prov_geoms
        )

        # Write result
        result = {
            "status": "success",
            "gif_area": gif_area,
            "gif_gral": gif_gral,
        }
        json.dump(result, sys.stdout)

    except Exception as e:  # pylint: disable=broad-exception-caught
        result = {
            "status": "error",
            "error": str(e),
        }
        json.dump(result, sys.stdout)
        sys.exit(1)


if __name__ == "__main__":
    main()
