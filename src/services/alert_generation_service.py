"""Service for weather alert generation and visualization."""

import asyncio
import json
import os
import pickle
import sys
import time
from datetime import datetime
from logging import Logger
from typing import List

from shapely import wkb as shapely_wkb
from shapely.geometry import Point, shape

from ports.mysql_repository import IMySQLRepository
from services.geo_intersection_service import GeoIntersectionService
from settings import Settings

_WORKER_PATH = os.path.join(
    os.path.dirname(__file__), "..", "alert_generation_worker.py"
)


class AlertGenerationService:  # pylint: disable=too-few-public-methods
    """Generates weather alert maps and persists to database."""

    def __init__(
        self,
        mysql_repo: IMySQLRepository,
        geo_service: GeoIntersectionService,
        settings: Settings,
        logger: Logger,
    ):
        """Initialize with MySQL repository, geo service, settings and logger."""
        self.mysql_repo = mysql_repo
        self.geo_service = geo_service
        self.settings = settings
        self.logger = logger

    async def generate_alert(  # pylint: disable=too-many-locals
        self, geometry: dict, fenomeno_codigo: int
    ) -> dict:
        """Generate alert from geometry and phenomenon code.

         Returns dict with:
         - taviso_id: Database ID of saved alert
         - timestamp: Timestamp string used in filenames
        - fenomeno_codigo: Input phenomenon code
         - fenomeno: Full text description
         - gif_area_url: URL path to area GIF
         - gif_gral_url: URL path to country GIF
         - affected_partidos_count: Number of affected municipalities
        """
        t0 = time.time()

        # 1. Validate code
        fenomeno_text = self.mysql_repo.get_fenomeno_text(fenomeno_codigo)
        if not fenomeno_text:
            raise ValueError(f"Invalid fenomeno code: {fenomeno_codigo}")

        # 2. Calculate intersection with departments (reuse existing service)
        self.logger.info(f"Calculating intersections for fenomeno {fenomeno_codigo}")
        departments = await self.geo_service.intersect_departments(
            geometry, simplification_level=1
        )

        # 3. Filter partidos spatially
        all_partidos = self.mysql_repo.get_partidos()
        affected_partidos = self._filter_partidos_by_departments(
            geometry, departments, all_partidos
        )

        self.logger.info(
            f"Found {len(departments)} intersecting departments, "
            f"{len(affected_partidos)} affected partidos"
        )

        # 4. Generate GIFs via subprocess worker
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        worker_result = await self._run_visualization_worker(
            geometry, fenomeno_text, timestamp, affected_partidos, all_partidos
        )

        if worker_result.get("status") != "success":
            raise RuntimeError(f"Visualization failed: {worker_result.get('error')}")

        # 5. Save to database
        area_html = self._format_area_html(affected_partidos)
        poligono_str = self._format_poligono(geometry)
        taviso_id = self.mysql_repo.insert_taviso(
            fenomeno_text, area_html, poligono_str
        )

        duration = time.time() - t0
        self.logger.info(f"Alert {taviso_id} generated in {duration:.2f}s")

        # Extract just the filename from full path for URL
        gif_area_filename = os.path.basename(worker_result["gif_area"])
        gif_gral_filename = os.path.basename(worker_result["gif_gral"])

        return {
            "taviso_id": taviso_id,
            "timestamp": timestamp,
            "fenomeno_codigo": fenomeno_codigo,
            "fenomeno": fenomeno_text,
            "gif_area_url": f"/alerts/{gif_area_filename}",
            "gif_gral_url": f"/alerts/{gif_gral_filename}",
            "affected_partidos_count": len(affected_partidos),
        }

    def _filter_partidos_by_departments(  # pylint: disable=too-many-locals
        self, geometry: dict, departments: List[dict], all_partidos: List[dict]
    ) -> List[dict]:
        """Filter partidos that fall within intersecting departments.

        Matches genero_aviso.py lines 136-166: uses FULL department geometries
        (not intersection fragments) so partidos anywhere in an affected department
        are included.
        """
        input_geom = shape(geometry)
        if not input_geom.is_valid:
            input_geom = input_geom.buffer(0)

        umbral = 0.001  # minimum department coverage fraction (same as genero_aviso.py)

        # Load full department geometries from pre-built cache
        dept_geoms = []
        dept_index_path = os.path.join(self.settings.alert_cache_dir, "dept_index.pkl")
        if os.path.exists(dept_index_path):
            with open(dept_index_path, "rb") as f:
                dept_index = pickle.load(f)
            bx0, by0, bx1, by1 = input_geom.bounds
            for (dx0, dy0, dx1, dy1), dg in dept_index:
                # Bbox pre-filter
                if dx1 < bx0 or dx0 > bx1 or dy1 < by0 or dy0 > by1:
                    continue
                inter = dg.intersection(input_geom)
                if inter.is_empty:
                    continue
                if inter.area / dg.area >= umbral:
                    dept_geoms.append(dg)  # full department geometry
        else:
            # Fallback if cache not built yet: use intersection fragments
            self.logger.warning(
                "dept_index.pkl not found — falling back to intersection fragments "
                "(some partidos may be missed)"
            )
            for d in departments:
                if "intersection" in d:
                    try:
                        dept_geoms.append(shape(d["intersection"]))
                    except Exception:  # pylint: disable=broad-exception-caught
                        pass

        resultado = []
        for p in all_partidos:
            pt = Point(float(p["longitud"]), float(p["latitud"]))
            hit = (
                any(dg.contains(pt) for dg in dept_geoms)
                if dept_geoms
                else input_geom.contains(pt)
            )
            if hit:
                resultado.append(p)

        return resultado

    async def _run_visualization_worker(
        self, geometry, fenomeno_text, timestamp, affected_partidos, all_partidos
    ) -> dict:
        """Run visualization in isolated subprocess (follows fullres_worker pattern)."""
        payload = json.dumps(
            {
                "geometry_wkb_hex": shapely_wkb.dumps(shape(geometry), hex=True),
                "fenomeno_text": fenomeno_text,
                "timestamp": timestamp,
                "affected_partidos": affected_partidos,
                "all_partidos": all_partidos,
                "output_dir": self.settings.output_dir,
                "cache_dir": self.settings.alert_cache_dir,
            }
        )

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            _WORKER_PATH,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate(payload.encode())

        if proc.returncode != 0:
            self.logger.error(f"Worker failed: {stderr_bytes.decode()}")
            raise RuntimeError(f"Worker failed (exit {proc.returncode})")

        try:
            return json.loads(stdout_bytes)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Worker returned invalid JSON: {stdout_bytes[:200]!r}"
            ) from exc

    def _format_area_html(self, partidos: List[dict]) -> str:
        """Format partidos by province for HTML display.

        Adapted from genero_aviso.py lines 398-405.
        """
        by_prov: dict[str, list[str]] = {}
        for p in partidos:
            by_prov.setdefault(p["provincia"].upper(), []).append(p["nom_partido"])
        return (
            "<br /><br />".join(
                f"<b>{prov}:</b> {' - '.join(sorted(ns))}."
                for prov, ns in sorted(by_prov.items())
            )
            or "(Sin partidos en el área)"
        )

    def _format_poligono(self, geometry: dict) -> str:
        """Format geometry coordinates for database storage.

        Adapted from genero_aviso.py line 476.
        """
        coords = geometry["coordinates"][0]
        return ",".join(f"[{c[1]:.2f},{c[0]:.2f}]" for c in coords)
