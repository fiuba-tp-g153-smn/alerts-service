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
from matplotlib.font_manager import FontProperties
from PIL import Image
from shapely import wkb as shapely_wkb
from shapely.geometry import Polygon

matplotlib.use("Agg")  # must precede pyplot import

import matplotlib.pyplot as plt  # pylint: disable=wrong-import-position,import-error,ungrouped-imports

FONT_BLACK = FontProperties(fname="/app/data_mapas/EncodeSans-Black.ttf")
FONT_MEDIUM = FontProperties(fname="/app/data_mapas/EncodeSans-Medium.ttf")
FONT_SEMIBOLD = FontProperties(fname="/app/data_mapas/EncodeSans-SemiBold.ttf")
WATERMARK_PATH = "/app/data_mapas/logo_smn.png"

def _agregar_marca_de_agua(fig):
    """Add a low-opacity watermark over the entire map."""
    if os.path.exists(WATERMARK_PATH):
        img = plt.imread(WATERMARK_PATH)
        # Añadir el eje con 'facecolor="none"' para que sea 100% transparente
        ax_wm = fig.add_axes([0, 0.01, 1, 0.86], facecolor="none")
        ax_wm.set_zorder(100)
        ax_wm.axis("off")
        ax_wm.imshow(img, aspect="auto", alpha=0.3, zorder=100)
    else:
        print(f"ATENCION: No se encontró el logo en la ruta: {WATERMARK_PATH}", file=sys.stderr)

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
    """Add header panel with alert text."""
    ax2 = fig.add_axes([0.0, 0.86, 1.0, 0.14])
    ax2.set_xlim(0, 1)
    ax2.set_ylim(0, 1)
    ax2.axis("off")

    # 1. Recuadro inferio (Fenómeno)
    ax2.add_patch(
        mpatches.Rectangle(
            (0, 0),
            1,
            0.52,
            facecolor="white",
            edgecolor="red",
            linewidth=2.5,
            transform=ax2.transAxes,
            clip_on=False,
        )
    )

    # 2. Recuadro superior (Título)
    ax2.add_patch(
        mpatches.Rectangle(
            (0, 0.52),
            1,
            0.48,
            facecolor="#ffb71b",
            edgecolor="none",
            transform=ax2.transAxes,
            clip_on=False,
        )
    )

    # Título
    ax2.text(
        0.5,
        0.76,
        "AVISO A CORTO PLAZO",
        ha="center",
        va="center",
        fontsize=28,
        color="#000000",
        fontproperties=FONT_BLACK,
        antialiased=True,
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
            color="#000000",
            fontproperties=FONT_MEDIUM,
            antialiased=True,
            transform=ax2.transAxes,
        )

    # Descripción estática
    ax2.text(
        0.02,
        0.44,
        "EL AREA GRAFICADA EN EL MAPA DELIMITA LA OCURRENCIA DE:",
        ha="left",
        va="top",
        fontsize=15,
        color="#000000",
        fontproperties=FONT_MEDIUM,
        antialiased=True,
        transform=ax2.transAxes,
    )

    # Texto del fenómeno
    ax2.text(
        0.5,
        0.14,
        texto,
        ha="center",
        va="center",
        fontsize=19,
        color="red",
        fontproperties=FONT_SEMIBOLD,
        antialiased=True,
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
    ax.add_feature(cfeature.OCEAN.with_scale("50m"), facecolor="#e1f1f4", zorder=0)
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

    _agregar_marca_de_agua(fig)

    _panel_aviso(fig, text, modo="area")

    out = os.path.join(output_dir, f"{timestamp}_aviso.gif")
    tmp = out.replace(".gif", "_tmp.png")
    fig.savefig(
        tmp, format="png", bbox_inches=None, pad_inches=0, facecolor="white", dpi=80
    )
    plt.close(fig)
    Image.open(tmp).convert("RGB").convert("P", palette=Image.Palette.ADAPTIVE).save(out, format="GIF")
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
    ax_map.add_feature(cfeature.OCEAN.with_scale("50m"), facecolor="#e1f1f4", zorder=0)
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

    _agregar_marca_de_agua(fig_final)

    _panel_aviso(fig_final, text, modo="gral")

    out = os.path.join(output_dir, f"{timestamp}_gral.gif")
    tmp = out.replace(".gif", "_tmp.png")
    fig_final.savefig(
        tmp, format="png", bbox_inches=None, pad_inches=0, facecolor="white", dpi=80
    )
    plt.close(fig_final)
    Image.open(tmp).convert("RGB").convert("P", palette=Image.Palette.ADAPTIVE).save(out, format="GIF")
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
