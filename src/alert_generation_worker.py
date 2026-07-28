#!/usr/bin/env python3
"""
Alert generation worker subprocess — generates GIF visualizations.

Reads JSON from stdin, generates 2 GIF maps using matplotlib/cartopy,
writes result to stdout, and exits (releasing all matplotlib memory).

Input JSON:
{
    "geometry_wkb_hex": "...",
    "phenomenon_text": "TORMENTAS...",
    "timestamp": "260315143052",
    "affected_departments": [{...}],
    "all_departments": [{...}],
    "output_dir": "/app/output/alerts",
    "cache_dir": "/app/cache"
}

Output JSON:
{
    "status": "success",
    "gif_area": "/app/output/alerts/aviso_260315143052.gif",
    "gif_gral": "/app/output/alerts/avi_gral_260315143052.gif"
}
"""

import asyncio
import io
import json
import os
import pickle
import sys
import unicodedata
from typing import Any, cast

import cartopy.crs as ccrs
import matplotlib
import matplotlib.patches as mpatches
import matplotlib.patheffects as pe
import numpy as np
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Polygon as MplPolygon
from matplotlib.font_manager import FontProperties
from PIL import Image
from shapely import wkb as shapely_wkb
from shapely.geometry import Polygon

matplotlib.use("Agg")  # must precede pyplot import

import matplotlib.pyplot as plt  # pylint: disable=wrong-import-position,import-error,ungrouped-imports

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
FONT_BLACK = FontProperties(fname="/app/data_alerts/EncodeSans-Black.ttf")
FONT_MEDIUM = FontProperties(fname="/app/data_alerts/EncodeSans-Medium.ttf")
FONT_SEMIBOLD = FontProperties(fname="/app/data_alerts/EncodeSans-SemiBold.ttf")
WATERMARK_PATH = "/app/data_alerts/logo_smn.png"
HEADER_LOGO_PATH = "/app/data_alerts/logo_smn_header.png"
INSET_SVG_PATH = "/app/data_alerts/cuarteron.svg"
# Pre-rasterised corner-inset PNG, built by scheduler._build_cuarteron_cache_sync
# (mirrors the rasterisation + pixel-masking below) and stored in cache_dir.
INSET_CACHE_NAME = "inset.png"

# ---------------------------------------------------------------------------
# Layout — page split into 8 columns × 9 rows (per SMN template).
# Header band = 1 row (module 1); phenomenon band = 2/3 of row (module 2).
# ---------------------------------------------------------------------------
HEADER_H = 1.0 / 9.0
PHENOM_H = 2.0 / 27.0
HEADER_Y = 1.0 - HEADER_H  # 8/9
PHENOM_Y = HEADER_Y - PHENOM_H  # 22/27
MAP_TOP = PHENOM_Y  # map fills [0, 22/27]

# Header palette
HEADER_BG = "#252c4f"
HEADER_ALPHA = 0.9  # 10% transparency

_IGN: dict | None = None  # loaded lazily on first call inside main()
_INSET_PNG: np.ndarray | None = None  # rasterised once per process

# ---------------------------------------------------------------------------
# AMBA seats (requested by SMN) — within the AMBA bbox, ONLY these
# districts are shown, labeled with their seat; the rest of the conurbano
# is hidden to avoid clutter. Outside the bbox, the department name is kept.
# ---------------------------------------------------------------------------
# bbox: CABA + Greater Buenos Aires (conurbano), not the whole province.
AMBA_BBOX = (-59.20, -57.80, -35.25, -34.05)  # (lon_o, lon_e, lat_s, lat_n)

# CABA is stored as 15 "Comuna N" departments, never as a single row. Both maps
# collapse them into one point here (the Obelisco) labeled "CABA".
CABA_POINT = (-58.3816, -34.6037)

# Key = normalized district name (no accents, lowercase); value = seat.
_AMBA_PAIRS = {
    "Almirante Brown": "Adrogué",
    "Avellaneda": "Avellaneda",
    "Berazategui": "Berazategui",
    "Berisso": "Berisso",
    "Brandsen": "Brandsen",
    "Campana": "Campana",
    "Cañuelas": "Cañuelas",
    "Ensenada": "Ensenada",
    "Escobar": "Belén de Escobar",
    "Exaltación de la Cruz": "Capilla del Señor",
    "Ezeiza": "Ezeiza",
    "General Las Heras": "General Las Heras",
    "General Rodríguez": "General Rodríguez",
    "La Matanza": "San Justo",
    "La Plata": "La Plata",
    "Luján": "Luján",
    "Marcos Paz": "Marcos Paz",
    "Merlo": "Merlo",
    "Quilmes": "Quilmes",
    "Pilar": "Pilar",
    "Presidente Perón": "Guernica",
    "San Fernando": "San Fernando",
    "San Isidro": "San Isidro",
    "San Vicente": "San Vicente",
    "Tigre": "Tigre",
    "Vicente López": "Olivos",
    "Zárate": "Zárate",
}


def _norm(s: str) -> str:
    """Normalise a name for matching: strip accents, lowercase, collapse spaces."""
    nfkd = unicodedata.normalize("NFKD", s)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(no_accents.lower().split())


AMBA_SEATS = {_norm(k): v for k, v in _AMBA_PAIRS.items()}

# ---------------------------------------------------------------------------
# Seats for the rest of the country (requested by SMN) — outside the AMBA bbox,
# the seat is shown instead of the department name. Key = (province, dept)
# because "Capital" repeats across several provinces with different seats.
# Departments without an entry: the department name is kept (incremental).
# ---------------------------------------------------------------------------
_NATIONAL_SEATS_PAIRS = {
    # --- Jujuy ---
    ("Jujuy", "Yavi"): "La Quiaca",
    ("Jujuy", "Dr Manuel Belgrano"): "San Salvador de Jujuy",
    # --- Formosa ---
    ("Formosa", "Formosa"): "Formosa",
    ("Formosa", "Patiño"): "Comandante Fontana",
    # --- Salta ---
    ("Salta", "Capital"): "Salta",
    ("Salta", "General José de San Martín"): "Tartagal",
    ("Salta", "San Carlos"): "San Carlos",
    # --- Misiones ---
    ("Misiones", "Capital"): "Posadas",
    ("Misiones", "Caniguás"): "Campo Grande",
    ("Misiones", "Cainguás"): "Campo Grande",  # alternative spelling in DB
    ("Misiones", "Iguazú"): "Puerto Esperanza",
    # --- Chaco ---
    ("Chaco", "San Fernando"): "Resistencia",
    ("Chaco", "Comandante Fernández"): "Presidencia Roque Saenz Peña",
    ("Chaco", "General Güemes"): "Juan José Castelli",
    # --- Santiago del Estero ---
    ("Santiago del Estero", "Capital"): "Santiago del Estero",
    ("Santiago del Estero", "Copo"): "Monte Quemado",
    ("Santiago del Estero", "Figueroa"): "La Cañada",
    ("Santiago del Estero", "General Taboada"): "Añatuya",
    ("Santiago del Estero", "Choya"): "Frías",
    # --- Corrientes ---
    ("Corrientes", "Capital"): "Corrientes",
    ("Corrientes", "Goya"): "Goya",
    ("Corrientes", "Paso de los Libres"): "Paso de los Libres",
    # --- Tucumán ---
    ("Tucumán", "Capital"): "San Miguel de Tucumán",
    ("Tucumán", "La Cocha"): "La Cocha",
    # --- Catamarca ---
    ("Catamarca", "Capital"): "San Fernando del Valle de Catamarca",
    ("Catamarca", "Tinogasta"): "Tinogasta",
    # --- La Rioja ---
    ("La Rioja", "Capital"): "La Rioja",
    ("La Rioja", "Chilecito"): "Chilecito",
    ("La Rioja", "Chamical"): "Chamical",
    # --- Santa Fe ---
    ("Santa Fe", "Rosario"): "Rosario",
    ("Santa Fe", "La Capital"): "Santa Fe de la Veracruz",
    ("Santa Fe", "Castellanos"): "Rafaela",
    ("Santa Fe", "General Obligado"): "Reconquista",
    ("Santa Fe", "General López"): "Venado Tuerto",
    ("Santa Fe", "San Cristóbal"): "Ceres",
    # --- San Juan ---
    ("San Juan", "Capital"): "Ciudad de San Juan",
    ("San Juan", "Jáchal"): "San José de Jáchal",
    # --- Córdoba ---
    ("Córdoba", "Capital"): "Córdoba",
    ("Córdoba", "Cruz del Eje"): "Cruz del Eje",
    ("Córdoba", "General San Martín"): "Villa María",
    ("Córdoba", "Río Cuarto"): "Río Cuarto",
    ("Córdoba", "Presidente Roque Saenz Peña"): "Laboulaye",
    # --- Entre Ríos ---
    ("Entre Ríos", "Concordia"): "Concordia",
    ("Entre Ríos", "Gualeguaychú"): "Gualeguaychú",
    ("Entre Ríos", "Tala"): "Rosario del Tala",
    ("Entre Ríos", "Paraná"): "Paraná",
    # --- San Luis ---
    ("San Luis", "Juan Martín de Pueyrredon"): "Ciudad de San Luis",
    ("San Luis", "General Pedernera"): "Villa Mercedes",
    ("San Luis", "Junín"): "Merlo",
    ("San Luis", "Gobernador Dupuy"): "Buena Esperanza",
    # --- Mendoza ---
    ("Mendoza", "Capital"): "Mendoza",
    ("Mendoza", "San Rafael"): "San Rafael",
    ("Mendoza", "Malargüe"): "Malargüe",
    ("Mendoza", "San Carlos"): "San Carlos",
    # --- Buenos Aires (outside the AMBA bbox, plus La Plata) ---
    # La Plata falls inside AMBA_BBOX, so on the zoom map it is resolved by
    # AMBA_SEATS; this entry is what puts it on the country-wide map.
    ("Buenos Aires", "La Plata"): "La Plata",
    ("Buenos Aires", "Pergamino"): "Pergamino",
    ("Buenos Aires", "Junín"): "Junín",
    ("Buenos Aires", "General Villegas"): "General Villegas",
    ("Buenos Aires", "Pehuajó"): "Pehuajó",
    ("Buenos Aires", "Bolívar"): "San Carlos de Bolívar",
    ("Buenos Aires", "Azul"): "Azul",
    ("Buenos Aires", "Tandil"): "Tandil",
    ("Buenos Aires", "Lobos"): "Lobos",
    ("Buenos Aires", "Las Flores"): "Las Flores",
    ("Buenos Aires", "Partido de La Costa"): "Mar del Tuyú",
    ("Buenos Aires", "Villa Gesell"): "Villa Gesell",
    ("Buenos Aires", "General Pueyrredon"): "Mar del Plata",
    ("Buenos Aires", "Necochea"): "Necochea",
    ("Buenos Aires", "Bahía Blanca"): "Bahía Blanca",
    ("Buenos Aires", "Saavedra"): "Pigüe",
    ("Buenos Aires", "Coronel Pringles"): "Coronel Pringles",
    # --- La Pampa ---
    ("La Pampa", "Capital"): "Santa Rosa",
    ("La Pampa", "Maracó"): "General Pico",
    ("La Pampa", "Loventué"): "Victorica",
    ("La Pampa", "Ultracán"): "General Acha",
    ("La Pampa", "Puelén"): "25 de Mayo",
    # --- Neuquén ---
    ("Neuquén", "Chos Malal"): "Chos Malal",
    ("Neuquén", "Confluencia"): "Ciudad de Neuquén",
    ("Neuquén", "Zapala"): "Zapala",
    ("Neuquén", "Los Lagos"): "Villa La Angostura",
    # --- Río Negro ---
    ("Río Negro", "Adolfo Alsina"): "Viedma",
    ("Río Negro", "Avellaneda"): "Choele Choel",
    ("Río Negro", "San Antonio Oeste"): "San Antonio Oeste",
    ("Río Negro", "25 de Mayo"): "Maquinchao",
    # --- Chubut ---
    ("Chubut", "Escalante"): "Comodoro Rivadavia",
    ("Chubut", "Rawson"): "Trelew",
    ("Chubut", "Biedma"): "Puerto Madryn",
    ("Chubut", "Futaleufú"): "Esquel",
    ("Chubut", "Paso de Indios"): "Paso de Indios",
}

NATIONAL_SEATS = {
    (_norm(prov), _norm(dep)): seat
    for (prov, dep), seat in _NATIONAL_SEATS_PAIRS.items()
}


def _department_label(
    dept_name: str, province: str, lon: float, lat: float
) -> str | None:
    """Text to display for a department, or None if it should be hidden.

    - CABA communes ("Comuna N"): hidden.
    - Within the AMBA bbox: only the SMN-listed districts (with their seat);
      the rest are hidden to avoid clutter.
    - Outside the AMBA bbox: use the national seat list if it exists;
      otherwise keep the department name.
    """
    if dept_name.lower().startswith("comuna ") and dept_name[7:].strip().isdigit():
        return None

    lon_o, lon_e, lat_s, lat_n = AMBA_BBOX
    in_amba = lon_o <= lon <= lon_e and lat_s <= lat <= lat_n
    if in_amba:
        return AMBA_SEATS.get(_norm(dept_name))  # None if not in the list
    return NATIONAL_SEATS.get((_norm(province), _norm(dept_name)), dept_name)


def _national_label(dept_name: str, province: str) -> str | None:
    """Text to display for a department on the country-wide map, or None to hide it.

    Unlike `_department_label`, there is no fallback to the department name and no
    AMBA special-casing: at country scale only the SMN-listed seats are shown, so
    the map stays readable instead of drawing every department in the country.
    """
    return NATIONAL_SEATS.get((_norm(province), _norm(dept_name)))


PLACE_FONTSIZE = 7.0

# Candidate label positions, tried in order, as (dx, dy, ha, va) with the offset
# in typographic points. Offsets are in points rather than degrees so a label sits
# the same distance from its dot in Jujuy as in Chubut.
_LABEL_OFFSETS = (
    (4, 3, "left", "bottom"),
    (4, -3, "left", "top"),
    (-4, 3, "right", "bottom"),
    (-4, -3, "right", "top"),
    (4, 9, "left", "bottom"),
    (-4, 9, "right", "bottom"),
    (4, -9, "left", "top"),
    (-4, -9, "right", "top"),
)

# Clear space kept around each label, in display pixels. Horizontal padding is the
# large one: two names side by side with only a few pixels between them read as a
# single word ("La CochaSantiago del Estero"), whereas stacked lines stay legible.
# It also absorbs the white halo drawn by the path effect, which the font metrics
# below do not account for.
_LABEL_PAD_X_PX = 8.0
_LABEL_PAD_Y_PX = 2.0

# Half-size of the box reserved around each dot. Dots are obstacles too: a label
# covering another city's dot hides the very thing it marks.
_DOT_HALF_PX = 3.0


def _dot_box(anchor_px):
    """(x0, y0, x1, y1) display-pixel box reserved for a city dot."""
    return (
        anchor_px[0] - _DOT_HALF_PX,
        anchor_px[1] - _DOT_HALF_PX,
        anchor_px[0] + _DOT_HALF_PX,
        anchor_px[1] + _DOT_HALF_PX,
    )


def _measure_label(ax: GeoAxes, renderer, label: str) -> tuple[float, float]:
    """Display-pixel (width, height) of a label, measured with the real font metrics.

    Estimating from character count under-measures bold text by 10-25%, which lets
    visibly touching labels pass the overlap test.
    """
    probe = ax.text(0, 0, label, fontsize=PLACE_FONTSIZE, fontweight="bold")
    box = probe.get_window_extent(renderer)
    probe.remove()
    return box.width, box.height


def _label_box(anchor_px, offset, size, dpi):
    """(x0, y0, x1, y1) display-pixel box of a label at one candidate offset."""
    dx, dy, ha, va = offset
    width, height = size
    scale = dpi / 72.0

    x = anchor_px[0] + dx * scale
    y = anchor_px[1] + dy * scale
    x0 = x if ha == "left" else x - width
    y0 = y if va == "bottom" else y - height
    return (
        x0 - _LABEL_PAD_X_PX,
        y0 - _LABEL_PAD_Y_PX,
        x0 + width + _LABEL_PAD_X_PX,
        y0 + height + _LABEL_PAD_Y_PX,
    )


def _overlaps(box, obstacles):
    """True if `box` intersects any of `obstacles`."""
    return any(
        box[0] < other[2] and other[0] < box[2] and box[1] < other[3] and other[1] < box[3]
        for other in obstacles
    )


def _choose_offset(anchor_px, size, dpi, obstacles):
    """First candidate offset clear of `obstacles`, falling back to the default one.

    Every label is drawn even when all candidates collide: the city list is
    prescribed by the SMN, so dropping a name would silently lose required
    information.
    """
    for offset in _LABEL_OFFSETS:
        if not _overlaps(_label_box(anchor_px, offset, size, dpi), obstacles):
            return offset
    return _LABEL_OFFSETS[0]


def _draw_dots(ax: GeoAxes, places) -> None:
    """Draw the dot marking each reference city."""
    for lon, lat, _ in places:
        ax.plot(
            lon,
            lat,
            ".",
            color="#555555",
            markersize=3.5,
            transform=ccrs.PlateCarree(),
            zorder=11,
        )


def _annotate_place(ax: GeoAxes, xy, label: str, offset) -> None:
    """Draw one city label at `offset` from its dot, haloed so it reads over the map."""
    dx, dy, ha, va = offset
    ax.annotate(
        label,
        xy=xy,
        xycoords="data",
        xytext=(dx, dy),
        textcoords="offset points",
        ha=ha,
        va=va,
        fontsize=PLACE_FONTSIZE,
        color="#111111",
        fontweight="bold",
        zorder=12,
        annotation_clip=True,
        path_effects=[pe.withStroke(linewidth=2, foreground="white")],
    )


def _get_renderer(fig):
    """Renderer for measuring text.

    A bare Figure has no Agg canvas until savefig creates one, but measuring text
    needs a renderer now — attach the same canvas savefig would use later.
    """
    if not hasattr(fig.canvas, "get_renderer"):
        FigureCanvasAgg(fig)
    return fig.canvas.get_renderer()


def _draw_places(ax: GeoAxes, places, dpi: int) -> None:
    """Draw reference cities: a dot each, then labels placed to avoid overlapping."""
    _draw_dots(ax, places)
    renderer = _get_renderer(ax.get_figure())

    # Longest names first: they are the hardest to fit, so they get first pick of
    # the free space. Name breaks ties to keep the output deterministic.
    ordered = sorted(places, key=lambda p: (-len(p[2]), p[2]))
    projected = [
        ax.projection.transform_point(lon, lat, ccrs.PlateCarree())
        for lon, lat, _ in ordered
    ]
    anchors = [ax.transData.transform(xy) for xy in projected]
    dot_boxes = [_dot_box(anchor) for anchor in anchors]
    placed: list[tuple[float, float, float, float]] = []

    for i, (_, _, label) in enumerate(ordered):
        size = _measure_label(ax, renderer, label)
        # Every dot except this label's own — that one sits inside the label box
        # by construction and would rule out every candidate offset.
        obstacles = placed + dot_boxes[:i] + dot_boxes[i + 1 :]
        chosen = _choose_offset(anchors[i], size, dpi, obstacles)
        placed.append(_label_box(anchors[i], chosen, size, dpi))
        _annotate_place(ax, projected[i], label, chosen)


# ---------------------------------------------------------------------------
# IGN base-map layers loader
# ---------------------------------------------------------------------------


def _load_ign_layers(cache_path: str) -> dict:
    """
    Load IGN geometries from the pre-built pickle.
    Falls back to an empty dict if the pickle does not exist yet
    (allows the worker to run even before the cache is ready).
    """
    empty: dict = {
        "group_a": [],
        "group_b": [],
        "group_c": [],
        "group_d": [],
        "provinces": [],
        "countries": [],
        "place_labels": [],
    }
    if not os.path.exists(cache_path):
        print(
            f"WARNING: {cache_path} not found. Skipping IGN base map.",
            file=sys.stderr,
        )
        return empty

    with open(cache_path, "rb") as f:
        raw: dict = pickle.load(f)

    # Deserialise WKB hex → shapely geometries (except 'place_labels' which are
    # dicts and '_format_version' which is a metadata int)
    layers: dict = {}
    for key, wkb_list in raw.items():
        if key in ("place_labels", "_format_version"):
            layers[key] = wkb_list  # place labels are already dicts {lon, lat, nombre, tipo}
        else:
            layers[key] = [shapely_wkb.loads(w, hex=True) for w in wkb_list]
    return layers


def _add_watermark(fig):
    """Add a low-opacity watermark over the map area (not header/phenom)."""
    if os.path.exists(WATERMARK_PATH):
        img = plt.imread(WATERMARK_PATH)
        # Large watermark: almost touches the bottom edge of the phenomenon
        # (MAP_TOP), never overlaps it. Logo is ~square; adjust width based on
        # figure aspect ratio.
        wm_y = 0.02
        gap = 0.015
        wm_h = MAP_TOP - wm_y - gap  # height = almost the whole map area
        fw_in, fh_in = fig.get_size_inches()
        wm_w = wm_h * (fh_in / fw_in)  # keeps 1:1 visual aspect
        wm_x = (1.0 - wm_w) / 2.0
        ax_wm = fig.add_axes([wm_x, wm_y, wm_w, wm_h], facecolor="none")
        ax_wm.set_zorder(100)
        ax_wm.axis("off")
        ax_wm.imshow(img, alpha=0.3, zorder=100)
    else:
        print(
            f"WARNING: Logo not found at path: {WATERMARK_PATH}",
            file=sys.stderr,
        )


def _load_index(path: str) -> list:
    """Load spatial index from pickle file, return empty list if missing."""
    if not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        return pickle.load(f)


def _load_spatial_indices(payload: dict, cache_dir: str) -> tuple:
    """Deserialize spatial indices from payload, falling back to disk.

    Geometries from the payload are pre-projected to ccrs.Mercator() by
    AlertGenerationService (see _get_dept_index_merc/_get_prov_geoms_merc), so the
    renderers draw them with crs=ccrs.Mercator(). The disk fallback below is only
    hit when the service didn't provide a payload (e.g. cache not built yet) and
    yields lon/lat geometries instead — boundaries would then be misaligned, an
    accepted degraded fallback rather than no map at all.
    """
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


def _dept_geoms_in_bbox(dept_index: list, lon_o, lon_e, lat_s, lat_n) -> list:
    """Filter department geometries that overlap the given bounding box."""
    return [
        g
        for (bx0, by0, bx1, by1), g in dept_index
        if not (bx1 < lon_o or bx0 > lon_e or by1 < lat_s or by0 > lat_n)
    ]


def _load_inset_png(
    cache_dir: str | None = None, target_px: int = 600
) -> np.ndarray | None:
    """Return the cuarterón inset as an RGBA array.

    Prefers a pre-rasterised PNG built by the scheduler (INSET_CACHE_NAME in
    cache_dir) — loading it is near-instant, vs. ~1.9s for on-the-fly cairosvg
    rasterisation + pixel masking on every alert generation subprocess. Falls back
    to on-the-fly rasterisation if the cache isn't present yet.

    target_px: nominal width in px for the on-the-fly fallback rasterisation.
    """
    global _INSET_PNG  # pylint: disable=global-statement
    if _INSET_PNG is not None:
        return _INSET_PNG

    if cache_dir is not None:
        prerendered_path = os.path.join(cache_dir, INSET_CACHE_NAME)
        if os.path.exists(prerendered_path):
            _INSET_PNG = np.array(Image.open(prerendered_path).convert("RGBA"))
            return _INSET_PNG

    if not os.path.exists(INSET_SVG_PATH):
        print(
            f"WARNING: corner inset not found at {INSET_SVG_PATH}",
            file=sys.stderr,
        )
        return None
    try:
        import cairosvg  # local import: heavy native dep, optional

        png_bytes = cairosvg.svg2png(url=INSET_SVG_PATH, output_width=target_px)
        arr = np.array(Image.open(io.BytesIO(png_bytes)).convert("RGBA"))
        # SMN request: translucent gray background (#bebebe) so the map's light
        # blue shows through behind it; black lines/borders remain opaque.
        r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
        # "Gray background" pixel: channel near 190 ± tolerance, no color bias
        is_bg = (
            (np.abs(r.astype(int) - 190) < 25)
            & (np.abs(g.astype(int) - 190) < 25)
            & (np.abs(b.astype(int) - 190) < 25)
        )
        arr[is_bg, 3] = (
            0  # gray background fully transparent — map's light blue shows through
        )
        _INSET_PNG = arr
        return _INSET_PNG
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"WARNING: failed to rasterise corner inset: {exc}", file=sys.stderr)
        return None


def _add_inset(
    fig,
    anchor: str = "br",
    width_frac: float = 0.13,
    margin_x: float = 0.015,
    margin_y: float = 0.015,
) -> None:
    """Place the corner inset at one of the map corners.

    anchor: one of 'br' (bottom-right), 'bl', 'tr', 'tl'.
    width_frac: width in figure-fraction coords.
    margin_x/margin_y: distance from the corresponding edge (figure coords).
    """
    img = _load_inset_png()
    if img is None:
        return

    h_px, w_px = img.shape[:2]
    fw_in, fh_in = fig.get_size_inches()
    # Match aspect in figure coords: height_frac/width_frac = (h_px/w_px) * (fw_in/fh_in)
    height_frac = width_frac * (h_px / w_px) * (fw_in / fh_in)

    if anchor in ("br", "tr"):
        x = 1.0 - width_frac - margin_x
    else:
        x = margin_x
    if anchor in ("br", "bl"):
        y = margin_y
    else:
        y = MAP_TOP - height_frac - margin_y

    ax_c = fig.add_axes([x, y, width_frac, height_frac])
    ax_c.set_facecolor("none")  # transparent background → map's light blue shows through
    ax_c.patch.set_alpha(0)
    ax_c.imshow(img, interpolation="bilinear")
    ax_c.set_xticks([])
    ax_c.set_yticks([])
    # Thin gray border #bebebe around the corner inset
    for spine in ax_c.spines.values():
        spine.set_edgecolor("#bebebe")
        spine.set_linewidth(0.8)
    ax_c.set_zorder(150)


def _pick_far_corner(coords_lonlat, lon_o, lon_e, lat_s, lat_n) -> str:
    """Pick map corner farthest from the polygon centroid (anchor key 'br'/'bl'/'tr'/'tl')."""
    lons = [c[0] for c in coords_lonlat]
    lats = [c[1] for c in coords_lonlat]
    cx = sum(lons) / len(lons)
    cy = sum(lats) / len(lats)
    # Normalise centroid to [0,1] within map bbox; pick opposite corner
    nx = (cx - lon_o) / max(lon_e - lon_o, 1e-9)
    ny = (cy - lat_s) / max(lat_n - lat_s, 1e-9)
    horiz = "l" if nx >= 0.5 else "r"
    vert = "t" if ny < 0.5 else "b"
    return f"{vert}{horiz}"


def _draw_phenomenon_border(fig, ax_ph) -> None:
    """Draw the phenomenon band's red border as 4 filled bars.

    A stroked rectangle's corners rely on sub-pixel AA join coverage, which
    can leave a 1px gap at a corner. Filled bars always meet cleanly at the
    corners and give a uniform border thickness on all four sides.
    """
    border_px = 3
    fig_w_in, fig_h_in = fig.get_size_inches()
    bx = border_px / (fig_w_in * fig.dpi)
    by = border_px / (fig_h_in * fig.dpi * PHENOM_H)

    for x0, y0, w, h in (
        (0, 1 - by, 1, by),  # top
        (0, 0, 1, by),  # bottom
        (0, 0, bx, 1),  # left
        (1 - bx, 0, bx, 1),  # right
    ):
        ax_ph.add_patch(
            mpatches.Rectangle(
                (x0, y0),
                w,
                h,
                facecolor="red",
                edgecolor="none",
                transform=ax_ph.transAxes,
                clip_on=False,
            )
        )


def _alert_panel(fig, text, mode="area"):
    """Render header band (title + logo) and phenomenon band per SMN template."""
    # ---- Header band: 1 module = 1/9 of figure height ----
    ax_hdr = fig.add_axes([0.0, HEADER_Y, 1.0, HEADER_H])
    ax_hdr.set_xlim(0, 1)
    ax_hdr.set_ylim(0, 1)
    ax_hdr.axis("off")

    ax_hdr.add_patch(
        mpatches.Rectangle(
            (0, 0),
            1,
            1,
            facecolor=HEADER_BG,
            # alpha=HEADER_ALPHA,
            edgecolor="none",
            transform=ax_hdr.transAxes,
            clip_on=False,
        )
    )

    # Logo on the left (with safety margin, vertically centred within the band)
    if os.path.exists(HEADER_LOGO_PATH):
        try:
            logo_img = plt.imread(HEADER_LOGO_PATH)
            lh_px, lw_px = logo_img.shape[:2]
            fw_in, fh_in = fig.get_size_inches()
            # Fit logo to ~65% of band height, preserve aspect
            target_h_frac = HEADER_H * 0.65
            target_w_frac = target_h_frac * (lw_px / lh_px) * (fh_in / fw_in)
            logo_y = HEADER_Y + (HEADER_H - target_h_frac) / 2.0
            ax_logo = fig.add_axes([0.018, logo_y, target_w_frac, target_h_frac])
            ax_logo.imshow(logo_img)
            ax_logo.axis("off")
            ax_logo.set_zorder(110)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"WARNING: failed to load header logo: {exc}", file=sys.stderr)
    else:
        print(
            f"WARNING: header logo not found at {HEADER_LOGO_PATH}",
            file=sys.stderr,
        )

    # Title — centred, white
    ax_hdr.text(
        0.5,
        0.5,
        "AVISO A CORTO PLAZO",
        ha="center",
        va="center",
        fontsize=34,
        color="white",
        fontproperties=FONT_BLACK,
        antialiased=True,
        transform=ax_hdr.transAxes,
    )

    # ---- Phenomenon band: 2/3 of module 2 ----
    ax_ph = fig.add_axes([0.0, PHENOM_Y, 1.0, PHENOM_H])
    ax_ph.set_xlim(0, 1)
    ax_ph.set_ylim(0, 1)
    ax_ph.axis("off")

    # White background fill (no border — the red border is drawn separately
    # below as 4 solid bars).
    ax_ph.add_patch(
        mpatches.Rectangle(
            (0, 0),
            1,
            1,
            facecolor="white",
            edgecolor="none",
            transform=ax_ph.transAxes,
            clip_on=False,
        )
    )

    _draw_phenomenon_border(fig, ax_ph)

    ax_ph.text(
        0.02,
        0.78,
        "EL AREA GRAFICADA EN EL MAPA DELIMITA LA OCURRENCIA DE:",
        ha="left",
        va="center",
        fontsize=16,
        color="#000000",
        fontproperties=FONT_MEDIUM,
        antialiased=True,
        transform=ax_ph.transAxes,
    )

    ax_ph.text(
        0.5,
        0.30,
        text,
        ha="center",
        va="center",
        fontsize=19,
        color="red",
        fontproperties=FONT_SEMIBOLD,
        antialiased=True,
        transform=ax_ph.transAxes,
    )


def _add_ign_layers(ax: GeoAxes, mode: str = "area") -> None:
    """
    Draw the IGN base map on the given cartopy axes.

    Layer order (zorder):
      0  - water background (axes background color)
      1  - neighboring countries (filled polygon)
      2  - Argentine provinces (white filled polygon)
      3  - Group B borders: interprovincial + coastline (thin line)
      4  - Group A borders: international + maritime lateral + Rio de la Plata bed
      5  - Group C borders: outer Rio de la Plata (dotted line)
      6  - Group D borders: Antarctic sector (special line)

    The water background is achieved by setting the axes facecolor before
    calling this function.
    """
    # group_a/b/c/d, provinces and countries come pre-projected to Mercator from
    # the cache (ign_layers.pkl) — using crs=merc avoids cartopy's per-request
    # reprojection (~8s per render for ~135k vertices). Place labels stay in
    # lon/lat (transform=PlateCarree) because they are points, not cache geometries.
    merc = ccrs.Mercator()

    # Neighboring countries (filled polygon + border to show borders between
    # neighboring countries, which are not in limites.shp since that only
    # contains Argentina's borders with its neighbors, not the borders
    # between neighbors)
    if _IGN["countries"]:
        lw_countries = 0.8 if mode == "area" else 1.0
        ax.add_geometries(
            _IGN["countries"],
            crs=merc,
            facecolor="#bebebe",
            edgecolor="#888888",
            linewidth=lw_countries,
            zorder=1,
        )

    # Argentine provinces — white background
    if _IGN["provinces"]:
        ax.add_geometries(
            _IGN["provinces"],
            crs=merc,
            facecolor="white",
            edgecolor="none",
            zorder=2,
        )

    # Group B: Interprovincial border + coastline
    # SMN guideline (requested by Sebas): #656565, 1.5 px solid — thicker than
    # the interdepartmental one to distinguish it in small provinces (Jujuy, Tucumán)
    if _IGN["group_b"]:
        ax.add_geometries(
            _IGN["group_b"],
            crs=merc,
            facecolor="none",
            edgecolor="#656565",
            linewidth=1.5,
            zorder=3,
        )

    # Group A: International border + Rio de la Plata bed + arg-uru maritime lateral
    # SMN guideline: #000000, 2 px, solid simple line
    if _IGN["group_a"]:
        ax.add_geometries(
            _IGN["group_a"],
            crs=merc,
            facecolor="none",
            edgecolor="#000000",
            linewidth=2.0,
            zorder=4,
        )

    # Group C: Outer Rio de la Plata border — dotted line
    if _IGN["group_c"]:
        ax.add_geometries(
            _IGN["group_c"],
            crs=merc,
            facecolor="none",
            edgecolor="#444444",
            linewidth=1.0,
            linestyle="dashed",
            zorder=5,
        )

    # Group D: Antarctic Sector — distinctive dotted line
    if _IGN["group_d"]:
        ax.add_geometries(
            _IGN["group_d"],
            crs=merc,
            facecolor="none",
            edgecolor="#444444",
            linewidth=1.0,
            linestyle=(0, (5, 3, 1, 3)),  # dot-dash
            zorder=6,
        )

    # Place labels: "(Arg.)" ownership tags and islands
    # Only in 'gral' mode (full country map), since 'area' mode zooms into
    # the affected region and does not cover these island areas.
    if mode == "gral" and _IGN.get("place_labels"):
        for top in _IGN["place_labels"]:
            lon, lat, name, kind = top["lon"], top["lat"], top["nombre"], top["tipo"]
            # Style depends on type:
            #   'arg'      -> just the text "(Arg.)", small bold
            #   'continen' -> "ISLAS MALVINAS (Arg.)" etc., small italic
            #   'isla'     -> South Atlantic island names, very small italic
            if kind == "arg":
                ax.text(
                    lon,
                    lat,
                    name,
                    transform=ccrs.PlateCarree(),
                    fontsize=6,
                    fontweight="bold",
                    color="#111111",
                    ha="center",
                    va="center",
                    clip_on=True,
                    zorder=10,
                )
            elif kind == "continen":
                ax.text(
                    lon,
                    lat,
                    name,
                    transform=ccrs.PlateCarree(),
                    fontsize=5.5,
                    fontstyle="italic",
                    color="#111111",
                    ha="center",
                    va="center",
                    clip_on=True,
                    zorder=10,
                )
            elif kind == "isla":
                ax.text(
                    lon,
                    lat,
                    name,
                    transform=ccrs.PlateCarree(),
                    fontsize=5,
                    fontstyle="italic",
                    color="#333333",
                    ha="center",
                    va="center",
                    clip_on=True,
                    zorder=10,
                )


# ---------------------------------------------------------------------------
# GIF generation functions
# ---------------------------------------------------------------------------


def generate_area_gif(  # pylint: disable=too-many-locals
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
    # Wider padding so the polygon doesn't touch the edges, leaving
    # free space (especially bottom-right for the corner inset).
    lat_s, lat_n = min(lats) - 1.8, max(lats) + 1.3
    lon_o, lon_e = min(lons) - 1.5, max(lons) + 2.5

    proj = ccrs.Mercator()
    # Use matplotlib.figure.Figure directly (not pyplot.figure) so this can run
    # concurrently with generate_general_gif in a separate thread without touching
    # pyplot's global figure-manager state.
    fig = Figure(figsize=(13.75, 14), dpi=80)
    # White lateral margin (15.5% each side) to fit the corner inset.
    # No vertical margin: map flush with the bottom edge and the phenomenon on top.
    ax: GeoAxes = cast(
        GeoAxes,
        fig.add_axes((0.155, 0.0, 0.69, MAP_TOP), projection=proj),
    )
    ax.set_extent([lon_o, lon_e, lat_s, lat_n], crs=ccrs.PlateCarree())
    ax.set_facecolor("#e1f1f4")  # background water color

    try:
        ax.spines["geo"].set_visible(False)
    except KeyError:
        cast(Any, ax).outline_patch.set_visible(False)

    # --- IGN base map ---
    _add_ign_layers(ax, mode="area")

    # Department boundaries filtered to visible bbox
    dept_vis = _dept_geoms_in_bbox(dept_index, lon_o, lon_e, lat_s, lat_n)
    if dept_vis:
        # SMN interdepartmental guideline: #C4C4C4, 0.5 px, solid simple line
        ax.add_geometries(
            dept_vis,
            crs=ccrs.Mercator(),
            edgecolor="#C4C4C4",
            facecolor="none",
            linewidth=0.5,
            zorder=7,
        )

    # Province boundaries from alert cache (all, thicker line) — zorder 8
    # These are the prov_geoms from the dept/prov spatial index (GeoJSONs),
    # kept here as a second source in case IGN data is unavailable.
    if prov_geoms and not _IGN["provinces"]:
        ax.add_geometries(
            prov_geoms,
            crs=ccrs.Mercator(),
            edgecolor="black",
            facecolor="none",
            linewidth=1.8,
            zorder=8,
        )

    # Polygon (solid shading + border) — same style as gral, low alpha
    # so the names of affected cities can be read on top.
    xy = list(zip(lons, lats))
    ax.add_patch(
        MplPolygon(
            xy,
            closed=True,
            facecolor="#FF4444",
            alpha=0.40,
            edgecolor="#CC0000",
            linewidth=2.5,
            transform=ccrs.PlateCarree(),
            zorder=9,
        )
    )

    # Departments - all visible in bbox, highlighted if affected
    affected_ids = {(d["id_provincia"], d["id_localidad"]) for d in departments}
    caba_visible = False
    caba_affected = False
    for department in all_departments or []:
        lon, lat = float(department["longitud"]), float(department["latitud"])
        if not (lon_o <= lon <= lon_e and lat_s <= lat <= lat_n):
            continue

        dept_name = department["nom_departamento"]
        is_affected = (
            department["id_provincia"],
            department["id_localidad"],
        ) in affected_ids

        # CABA communes: skip individual dots, consolidate into single point after loop
        if dept_name.lower().startswith("comuna ") and dept_name[7:].strip().isdigit():
            caba_visible = True
            if is_affected:
                caba_affected = True
            continue

        color_pt = "#111111" if is_affected else "#555555"
        color_txt = "#111111"
        marker = "o" if is_affected else "."
        marker_size = 5 if is_affected else 3.5
        z_pt, z_txt = (13, 14) if is_affected else (11, 12)

        ax.plot(
            lon,
            lat,
            marker,
            color=color_pt,
            markersize=marker_size,
            transform=ccrs.PlateCarree(),
            zorder=z_pt,
        )

        label = _department_label(
            dept_name, department.get("provincia", ""), lon, lat
        )
        if label is not None:
            label = label.replace("General ", "Gral. ")
            ax.text(
                lon + 0.04,
                lat + 0.03,
                label,
                fontsize=7.5,
                color=color_txt,
                fontweight="bold",
                transform=ccrs.PlateCarree(),
                zorder=z_txt,
                clip_on=True,
                path_effects=[pe.withStroke(linewidth=2, foreground="white")],
            )

    if caba_visible:
        caba_lon, caba_lat = -58.3816, -34.6037  # Obelisco
        c_color = "#111111" if caba_affected else "#555555"
        c_marker = "o" if caba_affected else "."
        c_size = 5 if caba_affected else 3.5
        c_z_pt, c_z_txt = (13, 14) if caba_affected else (11, 12)
        ax.plot(
            caba_lon,
            caba_lat,
            c_marker,
            color=c_color,
            markersize=c_size,
            transform=ccrs.PlateCarree(),
            zorder=c_z_pt,
        )
        ax.text(
            caba_lon + 0.04,
            caba_lat + 0.03,
            "CABA",
            fontsize=7.5,
            color="#111111",
            fontweight="bold",
            transform=ccrs.PlateCarree(),
            zorder=c_z_txt,
            clip_on=True,
            path_effects=[pe.withStroke(linewidth=2, foreground="white")],
        )

    _add_watermark(fig)

    # Corner inset — fixed bottom-right; the extent padding guarantees
    # free space so it doesn't overlap the polygon.
    _add_inset(fig, anchor="br", margin_x=0.02, margin_y=0.02)

    _alert_panel(fig, text, mode="area")

    out = os.path.join(output_dir, f"aviso_{timestamp}.gif")
    tmp = out.replace(".gif", "_tmp.png")
    fig.savefig(
        tmp, format="png", bbox_inches=None, pad_inches=0, facecolor="white", dpi=80
    )
    Image.open(tmp).convert("RGB").convert("P", palette=Image.Palette.ADAPTIVE).save(
        out, format="GIF"
    )
    os.remove(tmp)

    return out


def generate_general_gif(  # pylint: disable=too-many-locals
    text, coords, timestamp, output_dir, dept_geoms, prov_geoms, all_departments
):
    """Generate country-wide GIF showing full Argentina with polygon."""
    lons = [c[1] for c in coords]
    lats = [c[0] for c in coords]
    xy = list(zip(lons, lats))

    proj = ccrs.Mercator()
    fig_final = Figure(figsize=(13.75, 14), dpi=80)
    ax_map: GeoAxes = cast(
        GeoAxes, fig_final.add_axes((0, 0.0, 1, MAP_TOP), projection=proj)
    )

    # Top 2/3 of the country: we cut off the southern third (Tierra del Fuego/southern Santa Cruz).
    # Original lat range [-56, -21] (35°) → keep top 2/3 ⇒ lat_s = -56 + 35/3 ≈ -44.33
    extent = [-78, -51, -46.33, -21]

    ax_map.set_extent(extent, crs=ccrs.PlateCarree())
    ax_map.set_facecolor("#e1f1f4")  # background water color

    try:
        ax_map.spines["geo"].set_visible(False)
    except KeyError:
        cast(Any, ax_map).outline_patch.set_visible(False)

    # --- IGN base map ---
    _add_ign_layers(ax_map, mode="gral")

    # All department boundaries
    # SMN interdepartmental guideline: #C4C4C4, 0.5 px, solid simple line
    if dept_geoms:
        ax_map.add_geometries(
            dept_geoms,
            crs=ccrs.Mercator(),
            edgecolor="#C4C4C4",
            facecolor="none",
            linewidth=0.5,
            zorder=7,
        )

    # All province boundaries from alert cache — fallback only if IGN data unavailable
    if prov_geoms and not _IGN["provinces"]:
        ax_map.add_geometries(
            prov_geoms,
            crs=ccrs.Mercator(),
            edgecolor="black",
            facecolor="none",
            linewidth=1.8,
            zorder=8,
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
            zorder=9,
        )
    )

    # Reference cities (SMN-provided seat list) — only those, no per-department
    # fallback, so the country-wide map stays readable.
    lon_min, lon_max, lat_min, lat_max = extent
    places = []
    caba_visible = False
    for department in all_departments or []:
        lon, lat = float(department["longitud"]), float(department["latitud"])
        if not (lon_min <= lon <= lon_max and lat_min <= lat <= lat_max):
            continue

        dept_name = department["nom_departamento"]

        # CABA communes: skip individual dots, consolidate into one point below.
        if dept_name.lower().startswith("comuna ") and dept_name[7:].strip().isdigit():
            caba_visible = True
            continue

        label = _national_label(dept_name, department.get("provincia", ""))
        if label is None:
            continue

        places.append((lon, lat, label.replace("General ", "Gral. ")))

    if caba_visible:
        places.append((CABA_POINT[0], CABA_POINT[1], "CABA"))

    _draw_places(ax_map, places, dpi=80)

    _add_watermark(fig_final)

    # Corner inset — bottom-right over water per template
    # Over the ocean (light blue part) on the south-east edge, not flush with the right margin.
    _add_inset(fig_final, anchor="br", margin_x=0.15, margin_y=0.015)

    _alert_panel(fig_final, text, mode="gral")

    out = os.path.join(output_dir, f"avi_gral_{timestamp}.gif")
    tmp = out.replace(".gif", "_tmp.png")
    fig_final.savefig(
        tmp, format="png", bbox_inches=None, pad_inches=0, facecolor="white", dpi=80
    )
    Image.open(tmp).convert("RGB").convert("P", palette=Image.Palette.ADAPTIVE).save(
        out, format="GIF"
    )
    os.remove(tmp)

    return out


async def main():
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

        # Load IGN base-map cache (pre-built by scheduler at startup)
        global _IGN  # pylint: disable=global-statement
        if _IGN is None:
            ign_cache = os.path.join(cache_dir, "ign_layers.pkl")
            _IGN = _load_ign_layers(ign_cache)

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

        # Pre-warm the corner-inset rasterisation before going parallel: it's cached
        # in a module-level global and is not safe to race from both threads.
        _load_inset_png(cache_dir)

        # Generate both GIFs concurrently — independent figures, no shared mutable
        # state. Most of the cost is in cartopy/shapely/Agg C extensions, which
        # release the GIL, so this overlaps real work instead of just I/O.
        gif_area, gif_gral = await asyncio.gather(
            asyncio.to_thread(
                generate_area_gif,
                phenomenon_text,
                coords,
                affected_departments,
                timestamp,
                output_dir,
                all_departments,
                dept_index,
                prov_geoms,
            ),
            asyncio.to_thread(
                generate_general_gif,
                phenomenon_text,
                coords,
                timestamp,
                output_dir,
                dept_geoms_all,
                prov_geoms,
                all_departments,
            ),
        )

        # Write result
        result = {
            "status": "success",
            "gif_area": gif_area,
            "gif_gral": gif_gral,
        }
        json.dump(result, sys.stdout)

    except Exception as e:  # pylint: disable=broad-exception-caught
        import traceback

        # Surface the full traceback on stderr so the parent process logs it
        # (the result dict below only carries the message).
        traceback.print_exc(file=sys.stderr)
        result = {
            "status": "error",
            "error": str(e),
        }
        json.dump(result, sys.stdout)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
