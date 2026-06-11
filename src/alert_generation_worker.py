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
CUARTERON_SVG_PATH = "/app/data_alerts/cuarteron.svg"

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
_CUARTERON_PNG: np.ndarray | None = None  # rasterised once per process

# ---------------------------------------------------------------------------
# Cabeceras AMBA (pedido SMN) — dentro del bbox AMBA se muestran SOLO estos
# partidos, etiquetados con su cabecera; el resto del conurbano se oculta para
# no saturar. Fuera del bbox se mantiene el nombre del departamento.
# ---------------------------------------------------------------------------
# bbox: CABA + Gran Buenos Aires (conurbano), no toda la provincia.
AMBA_BBOX = (-59.20, -57.80, -35.25, -34.05)  # (lon_o, lon_e, lat_s, lat_n)

# Clave = nombre de partido normalizado (sin acentos, minúsculas); valor = cabecera.
_AMBA_PARES = {
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
    sin_acentos = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(sin_acentos.lower().split())


AMBA_CABECERAS = {_norm(k): v for k, v in _AMBA_PARES.items()}

# ---------------------------------------------------------------------------
# Cabeceras del resto del país (pedido SMN) — fuera del bbox AMBA se muestra la
# cabecera en lugar del nombre del departamento. Clave = (provincia, depto)
# porque "Capital" se repite en varias provincias con cabeceras distintas.
# Departamentos sin entrada: se mantiene el nombre del departamento (incremental).
# ---------------------------------------------------------------------------
_CABECERAS_NACIONAL_PARES = {
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
    ("Misiones", "Cainguás"): "Campo Grande",  # grafía alternativa en DB
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
    # --- Buenos Aires (fuera del bbox AMBA) ---
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

CABECERAS_NACIONAL = {
    (_norm(prov), _norm(dep)): cab
    for (prov, dep), cab in _CABECERAS_NACIONAL_PARES.items()
}


def _etiqueta_departamento(
    nom_depto: str, provincia: str, lon: float, lat: float
) -> str | None:
    """Texto a mostrar para un departamento, o None si debe ocultarse.

    - Comunas CABA ("Comuna N"): ocultas.
    - Dentro del bbox AMBA: solo los partidos del listado SMN (con su cabecera);
      el resto se oculta para no saturar.
    - Fuera del bbox AMBA: se usa la cabecera del listado nacional si existe;
      si no, se mantiene el nombre del departamento.
    """
    if nom_depto.lower().startswith("comuna ") and nom_depto[7:].strip().isdigit():
        return None

    lon_o, lon_e, lat_s, lat_n = AMBA_BBOX
    en_amba = lon_o <= lon <= lon_e and lat_s <= lat <= lat_n
    if en_amba:
        return AMBA_CABECERAS.get(_norm(nom_depto))  # None si no está en el listado
    return CABECERAS_NACIONAL.get((_norm(provincia), _norm(nom_depto)), nom_depto)


# ---------------------------------------------------------------------------
# IGN base-map layers loader
# ---------------------------------------------------------------------------


def _cargar_capas_ign(cache_path: str) -> dict:
    """
    Load IGN geometries from the pre-built pickle.
    Falls back to an empty dict if the pickle does not exist yet
    (allows the worker to run even before the cache is ready).
    """
    empty: dict = {
        "grupo_a": [],
        "grupo_b": [],
        "grupo_c": [],
        "grupo_d": [],
        "provincias": [],
        "paises": [],
        "toponimos": [],
    }
    if not os.path.exists(cache_path):
        print(
            f"ATENCION: {cache_path} no encontrado. Mapa base IGN omitido.",
            file=sys.stderr,
        )
        return empty

    with open(cache_path, "rb") as f:
        raw: dict = pickle.load(f)

    # Deserialise WKB hex → shapely geometries (excepto 'toponimos' que son dicts)
    capas: dict = {}
    for key, wkb_list in raw.items():
        if key == "toponimos":
            capas[key] = wkb_list  # ya son dicts {lon, lat, nombre, tipo}
        else:
            capas[key] = [shapely_wkb.loads(w, hex=True) for w in wkb_list]
    return capas


def _agregar_marca_de_agua(fig):
    """Add a low-opacity watermark over the map area (not header/phenom)."""
    if os.path.exists(WATERMARK_PATH):
        img = plt.imread(WATERMARK_PATH)
        # Marca de agua grande: casi toca borde inferior del fenómeno (MAP_TOP),
        # nunca lo invade. Logo es ~cuadrado; ajustamos ancho según aspect de figura.
        wm_y = 0.02
        gap = 0.015
        wm_h = MAP_TOP - wm_y - gap  # alto = casi toda el área del mapa
        fw_in, fh_in = fig.get_size_inches()
        wm_w = wm_h * (fh_in / fw_in)  # mantiene aspect 1:1 visual
        wm_x = (1.0 - wm_w) / 2.0
        ax_wm = fig.add_axes([wm_x, wm_y, wm_w, wm_h], facecolor="none")
        ax_wm.set_zorder(100)
        ax_wm.axis("off")
        ax_wm.imshow(img, alpha=0.3, zorder=100)
    else:
        print(
            f"ATENCION: No se encontró el logo en la ruta: {WATERMARK_PATH}",
            file=sys.stderr,
        )


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


def _load_cuarteron_png(target_px: int = 600) -> np.ndarray | None:
    """Rasterise the cuarterón SVG once per process and return as RGBA array.

    target_px: nominal width in px for the rasterisation; height scales by SVG aspect.
    """
    global _CUARTERON_PNG  # pylint: disable=global-statement
    if _CUARTERON_PNG is not None:
        return _CUARTERON_PNG
    if not os.path.exists(CUARTERON_SVG_PATH):
        print(
            f"ATENCION: cuarterón no encontrado en {CUARTERON_SVG_PATH}",
            file=sys.stderr,
        )
        return None
    try:
        import cairosvg  # local import: heavy native dep, optional

        png_bytes = cairosvg.svg2png(url=CUARTERON_SVG_PATH, output_width=target_px)
        arr = np.array(Image.open(io.BytesIO(png_bytes)).convert("RGBA"))
        # Pedido SMN: fondo gris (#bebebe) translúcido para que se vea celeste del
        # mapa por detrás; líneas/borde negros permanecen opacos.
        r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
        # Pixel "fondo gris": canal cerca de 190 ± tolerancia, sin sesgo de color
        is_bg = (
            (np.abs(r.astype(int) - 190) < 25)
            & (np.abs(g.astype(int) - 190) < 25)
            & (np.abs(b.astype(int) - 190) < 25)
        )
        arr[is_bg, 3] = (
            0  # fondo gris totalmente transparente — celeste del mapa pasa limpio
        )
        _CUARTERON_PNG = arr
        return _CUARTERON_PNG
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"ATENCION: fallo rasterizando cuarterón: {exc}", file=sys.stderr)
        return None


def _agregar_cuarteron(
    fig,
    anchor: str = "br",
    width_frac: float = 0.13,
    margin_x: float = 0.015,
    margin_y: float = 0.015,
) -> None:
    """Place cuarterón inset at one of the map corners.

    anchor: one of 'br' (bottom-right), 'bl', 'tr', 'tl'.
    width_frac: width in figure-fraction coords.
    margin_x/margin_y: distancia desde el borde correspondiente (figure coords).
    """
    img = _load_cuarteron_png()
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
    ax_c.set_facecolor("none")  # fondo transparente → celeste del mapa pasa
    ax_c.patch.set_alpha(0)
    ax_c.imshow(img, interpolation="bilinear")
    ax_c.set_xticks([])
    ax_c.set_yticks([])
    # Borde fino gris #bebebe alrededor del cuarterón
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


def _panel_aviso(fig, texto, modo="area"):
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
            print(f"ATENCION: fallo cargando logo header: {exc}", file=sys.stderr)
    else:
        print(
            f"ATENCION: logo header no encontrado en {HEADER_LOGO_PATH}",
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

    ax_ph.add_patch(
        mpatches.Rectangle(
            (0, 0),
            1,
            1,
            facecolor="white",
            edgecolor="red",
            linewidth=2.5,
            transform=ax_ph.transAxes,
            clip_on=False,
        )
    )

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
        texto,
        ha="center",
        va="center",
        fontsize=19,
        color="red",
        fontproperties=FONT_SEMIBOLD,
        antialiased=True,
        transform=ax_ph.transAxes,
    )


def _agregar_capas_ign(ax: GeoAxes, modo: str = "area") -> None:
    """
    Dibuja el mapa base IGN sobre el eje cartopy dado.

    Orden de capas (zorder):
      0  - fondo de agua (color de fondo del eje)
      1  - países limítrofes (polígono relleno)
      2  - provincias argentinas (polígono relleno blanco)
      3  - límites Grupo B: interprovincial + costa (línea delgada)
      4  - límites Grupo A: internacionales + lateral marítimo + lecho RdlP
      5  - límites Grupo C: exterior Río de la Plata (línea punteada)
      6  - límites Grupo D: sector antártico (línea especial)

    El fondo de agua se logra seteando el facecolor del eje antes de llamar
    a esta función.
    """
    pc = ccrs.PlateCarree()

    # Países limítrofes (polígono relleno + borde para mostrar fronteras entre
    # países vecinos, que no están en limites.shp ya que ese solo contiene
    # los límites de Argentina con sus vecinos, no los límites entre vecinos)
    if _IGN["paises"]:
        lw_paises = 0.8 if modo == "area" else 1.0
        ax.add_geometries(
            _IGN["paises"],
            crs=pc,
            facecolor="#bebebe",
            edgecolor="#888888",
            linewidth=lw_paises,
            zorder=1,
        )

    # Provincias argentinas — fondo blanco
    if _IGN["provincias"]:
        ax.add_geometries(
            _IGN["provincias"],
            crs=pc,
            facecolor="white",
            edgecolor="none",
            zorder=2,
        )

    # Grupo B: Límite Interprovincial + Línea de costa
    # Guía SMN (pedido Sebas): #656565, 1.5 px continua — más grueso que el
    # interdepartamental para distinguirlo en provincias chicas (Jujuy, Tucumán)
    if _IGN["grupo_b"]:
        ax.add_geometries(
            _IGN["grupo_b"],
            crs=pc,
            facecolor="none",
            edgecolor="#656565",
            linewidth=1.5,
            zorder=3,
        )

    # Grupo A: Límite internacional + lecho RdlP + lateral marítimo arg-uru
    # Guía SMN: #000000, 2 px, línea simple continua
    if _IGN["grupo_a"]:
        ax.add_geometries(
            _IGN["grupo_a"],
            crs=pc,
            facecolor="none",
            edgecolor="#000000",
            linewidth=2.0,
            zorder=4,
        )

    # Grupo C: Límite exterior del Río de la Plata — línea punteada
    if _IGN["grupo_c"]:
        ax.add_geometries(
            _IGN["grupo_c"],
            crs=pc,
            facecolor="none",
            edgecolor="#444444",
            linewidth=1.0,
            linestyle="dashed",
            zorder=5,
        )

    # Grupo D: Sector Antártico — línea punteada distintiva
    if _IGN["grupo_d"]:
        ax.add_geometries(
            _IGN["grupo_d"],
            crs=pc,
            facecolor="none",
            edgecolor="#444444",
            linewidth=1.0,
            linestyle=(0, (5, 3, 1, 3)),  # punto-guión
            zorder=6,
        )

    # Topónimos: etiquetas de pertenencia "(Arg.)" e islas
    # Solo en modo 'gral' (mapa del país completo), ya que el modo 'area'
    # hace zoom a la región afectada y no cubre las zonas de estas islas.
    if modo == "gral" and _IGN.get("toponimos"):
        for top in _IGN["toponimos"]:
            lon, lat, nombre, tipo = top["lon"], top["lat"], top["nombre"], top["tipo"]
            # Estilo según tipo:
            #   'arg'      -> solo el texto "(Arg.)", negrita pequeña
            #   'continen' -> "ISLAS MALVINAS (Arg.)" etc., itálica pequeña
            #   'isla'     -> nombres de islas del Atlántico Sur, itálica muy pequeña
            if tipo == "arg":
                ax.text(
                    lon,
                    lat,
                    nombre,
                    transform=ccrs.PlateCarree(),
                    fontsize=6,
                    fontweight="bold",
                    color="#111111",
                    ha="center",
                    va="center",
                    clip_on=True,
                    zorder=10,
                )
            elif tipo == "continen":
                ax.text(
                    lon,
                    lat,
                    nombre,
                    transform=ccrs.PlateCarree(),
                    fontsize=5.5,
                    fontstyle="italic",
                    color="#111111",
                    ha="center",
                    va="center",
                    clip_on=True,
                    zorder=10,
                )
            elif tipo == "isla":
                ax.text(
                    lon,
                    lat,
                    nombre,
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
    # Padding más amplio para que el polígono no toque los bordes y quede
    # espacio libre (especialmente abajo-derecha para el cuarterón).
    lat_s, lat_n = min(lats) - 1.8, max(lats) + 1.3
    lon_o, lon_e = min(lons) - 1.5, max(lons) + 2.5

    proj = ccrs.Mercator()
    fig = plt.figure(figsize=(13.75, 14), dpi=80)
    # Margen blanco lateral (15.5% cada lado) para alojar el cuarterón.
    # Sin margen vertical: mapa pegado al borde inferior y al fenómeno arriba.
    ax: GeoAxes = cast(
        GeoAxes,
        fig.add_axes((0.155, 0.0, 0.69, MAP_TOP), projection=proj),
    )
    ax.set_extent([lon_o, lon_e, lat_s, lat_n], crs=ccrs.PlateCarree())
    ax.set_facecolor("#e1f1f4")  # color de agua de fondo

    try:
        ax.spines["geo"].set_visible(False)
    except KeyError:
        cast(Any, ax).outline_patch.set_visible(False)

    # --- Mapa base IGN ---
    _agregar_capas_ign(ax, modo="area")

    # Department boundaries filtered to visible bbox
    dept_vis = _dept_geoms_en_bbox(dept_index, lon_o, lon_e, lat_s, lat_n)
    if dept_vis:
        # Guía SMN interdepartamental: #C4C4C4, 0.5 px, línea simple continua
        ax.add_geometries(
            dept_vis,
            crs=ccrs.PlateCarree(),
            edgecolor="#C4C4C4",
            facecolor="none",
            linewidth=0.5,
            zorder=7,
        )

    # Province boundaries from alert cache (all, thicker line) — zorder 8
    # These are the prov_geoms from the dept/prov spatial index (GeoJSONs),
    # kept here as a second source in case IGN data is unavailable.
    if prov_geoms and not _IGN["provincias"]:
        ax.add_geometries(
            prov_geoms,
            crs=ccrs.PlateCarree(),
            edgecolor="black",
            facecolor="none",
            linewidth=1.8,
            zorder=8,
        )

    # Polygon (sombreado sólido + borde) — estilo igual al gral, alpha bajo
    # para que los nombres de ciudades afectadas se lean por encima.
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

        nom_depto = department["nom_departamento"]
        is_affected = (
            department["id_provincia"],
            department["id_localidad"],
        ) in affected_ids

        # CABA communes: skip individual dots, consolidate into single point after loop
        if nom_depto.lower().startswith("comuna ") and nom_depto[7:].strip().isdigit():
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

        etiqueta = _etiqueta_departamento(
            nom_depto, department.get("provincia", ""), lon, lat
        )
        if etiqueta is not None:
            etiqueta = etiqueta.replace("General ", "Gral. ")
            ax.text(
                lon + 0.04,
                lat + 0.03,
                etiqueta,
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

    _agregar_marca_de_agua(fig)

    # Cuarterón inset — fijo bottom-right; el padding del extent garantiza
    # espacio libre para que no pise el polígono.
    _agregar_cuarteron(fig, anchor="br", margin_x=0.02, margin_y=0.02)

    _panel_aviso(fig, text, modo="area")

    out = os.path.join(output_dir, f"aviso_{timestamp}.gif")
    tmp = out.replace(".gif", "_tmp.png")
    fig.savefig(
        tmp, format="png", bbox_inches=None, pad_inches=0, facecolor="white", dpi=80
    )
    plt.close(fig)
    Image.open(tmp).convert("RGB").convert("P", palette=Image.Palette.ADAPTIVE).save(
        out, format="GIF"
    )
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
        GeoAxes, fig_final.add_axes((0, 0.0, 1, MAP_TOP), projection=proj)
    )

    # 2/3 superiores del país: cortamos el tercio sur (Tierra del Fuego/sur Santa Cruz).
    # Rango lat original [-56, -21] (35°) → mantener 2/3 superiores ⇒ lat_s = -56 + 35/3 ≈ -44.33
    extent = [-78, -51, -46.33, -21]

    ax_map.set_extent(extent, crs=ccrs.PlateCarree())
    ax_map.set_facecolor("#e1f1f4")  # color de agua de fondo

    try:
        ax_map.spines["geo"].set_visible(False)
    except KeyError:
        cast(Any, ax_map).outline_patch.set_visible(False)

    # --- Mapa base IGN ---
    _agregar_capas_ign(ax_map, modo="gral")

    # All department boundaries
    # Guía SMN interdepartamental: #C4C4C4, 0.5 px, línea simple continua
    if dept_geoms:
        ax_map.add_geometries(
            dept_geoms,
            crs=ccrs.PlateCarree(),
            edgecolor="#C4C4C4",
            facecolor="none",
            linewidth=0.5,
            zorder=7,
        )

    # All province boundaries from alert cache — fallback only if IGN data unavailable
    if prov_geoms and not _IGN["provincias"]:
        ax_map.add_geometries(
            prov_geoms,
            crs=ccrs.PlateCarree(),
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

    _agregar_marca_de_agua(fig_final)

    # Cuarterón inset — bottom-right over water per template
    # Sobre el océano (parte celeste) en el borde sur-este, no pegado al margen derecho.
    _agregar_cuarteron(fig_final, anchor="br", margin_x=0.15, margin_y=0.015)

    _panel_aviso(fig_final, text, modo="gral")

    out = os.path.join(output_dir, f"avi_gral_{timestamp}.gif")
    tmp = out.replace(".gif", "_tmp.png")
    fig_final.savefig(
        tmp, format="png", bbox_inches=None, pad_inches=0, facecolor="white", dpi=80
    )
    plt.close(fig_final)
    Image.open(tmp).convert("RGB").convert("P", palette=Image.Palette.ADAPTIVE).save(
        out, format="GIF"
    )
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

        # Load IGN base-map cache (pre-built by scheduler at startup)
        global _IGN  # pylint: disable=global-statement
        if _IGN is None:
            ign_cache = os.path.join(cache_dir, "ign_capas.pkl")
            _IGN = _cargar_capas_ign(ign_cache)

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
