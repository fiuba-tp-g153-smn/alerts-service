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

import cartopy.crs as ccrs
from shapely import wkb as shapely_wkb
from shapely.geometry import Point, shape

from domain.alert_job import PreparedAlert
from domain.models import AreaTooLargeError, PolygonTooLargeError
from ports.mysql_repository import IMySQLRepository
from services.geo_intersection_service import GeoIntersectionService
from settings import Settings

_WORKER_PATH = os.path.join(
    os.path.dirname(__file__), "..", "alert_generation_worker.py"
)

# Confine each render subprocess's numpy/matplotlib BLAS/OpenMP backends to a
# single thread. Without this, one render fans out to one thread per core and
# (with the semaphore allowing two at once) saturates the box, starving the
# FastAPI event loop so every endpoint — incl. the dashboard's /metrics — crawls
# for the duration of the render. One thread per worker leaves the loop responsive.
_SUBPROCESS_ENV = {
    **os.environ,
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
}

# Caps concurrent visualization subprocess launches to prevent simultaneous RAM peaks.
# Value of 2: safe now that dept/prov indices are cached in-process and passed via
# payload (workers no longer load pickle files from disk independently).
_ALERT_VIZ_SEMAPHORE = asyncio.Semaphore(2)

# Module-level cache for dept_index.pkl — immutable during app lifetime (only rebuilt
# at startup by the scheduler before the app serves requests).
_DEPT_INDEX_CACHE: list | None = None
_DEPT_INDEX_LOCK = asyncio.Lock()

_PROV_INDEX_CACHE: list | None = None
_PROV_INDEX_LOCK = asyncio.Lock()

# Mercator-projected copies of dept_index/prov_index geometries, for rendering only
# (the lon/lat versions above stay in PlateCarree for spatial filtering). Computed
# once on first use and cached — pre-projecting avoids cartopy's expensive per-request
# trace-based reprojection (~2s/render for ~53k vertices) in the worker.
_DEPT_INDEX_MERC_CACHE: list | None = None
_DEPT_INDEX_MERC_LOCK = asyncio.Lock()

_PROV_GEOMS_MERC_CACHE: list | None = None
_PROV_GEOMS_MERC_LOCK = asyncio.Lock()


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

    async def validate_request(
        self, geometry: dict, phenomenon_code: int
    ) -> PreparedAlert:
        """Validate an alert request quickly, before any expensive work.

        Runs the cheap checks that the POST handler needs synchronously so it can
        return 400/413 immediately: the phenomenon code must be known and the
        serialized polygon must fit the DB column. Returns the phenomenon text
        and serialized polygon so the heavy generation step need not recompute
        the lookup.
        """
        phenomenon_text = self.mysql_repo.get_phenomenon_text(phenomenon_code)
        if not phenomenon_text:
            raise ValueError(f"Invalid phenomenon code: {phenomenon_code}")

        polygon_str = self._format_polygon(geometry)
        await self._validate_polygon_size(polygon_str)
        return PreparedAlert(phenomenon_text=phenomenon_text, polygon_str=polygon_str)

    async def generate_alert(  # pylint: disable=too-many-locals
        self, geometry: dict, phenomenon_code: int, phenomenon_text: str | None = None
    ) -> dict:
        """Generate alert from geometry and phenomenon code.

         When `phenomenon_text` is provided (background worker path), the cheap
         validation done by `validate_request` is skipped. When it is None, the
         request is validated here so the method stays correct when called
         directly.

         Returns dict with:
         - alert_id: Database ID of saved alert
         - timestamp: Timestamp string used in filenames
        - phenomenon_code: Input phenomenon code
         - phenomenon: Full text description
         - area: HTML with affected departments grouped by province
         - polygon: Serialized polygon ("[lat,lon],[lat,lon],...")
         - gif_area_url: URL path to area GIF
         - gif_gral_url: URL path to country GIF
         - affected_departments_count: Number of affected departments
        """
        t0 = time.perf_counter()

        # 1. Validate (skipped when the caller already validated the request).
        if phenomenon_text is None:
            prepared = await self.validate_request(geometry, phenomenon_code)
            phenomenon_text = prepared.phenomenon_text
            polygon_str = prepared.polygon_str
        else:
            polygon_str = self._format_polygon(geometry)

        # 2. Calculate intersection with departments (reuse existing service)
        self.logger.info(f"Calculating intersections for phenomenon {phenomenon_code}")
        t_intersect = time.perf_counter()
        departments = await self.geo_service.intersect_departments(geometry)
        intersection_ms = int((time.perf_counter() - t_intersect) * 1000)

        # 3. Filter departments spatially (DB read off the event loop so a MySQL
        # stall can never block the loop / freeze the whole service).
        t_filter = time.perf_counter()
        all_departments = await asyncio.to_thread(self.mysql_repo.get_departments)
        affected_departments = await self._filter_departments_by_departments(
            geometry, departments, all_departments
        )
        filter_ms = int((time.perf_counter() - t_filter) * 1000)

        self.logger.info(
            f"Found {len(departments)} intersecting departments, "
            f"{len(affected_departments)} affected departments"
        )

        # 4. Generate GIFs via subprocess worker
        timestamp = datetime.now().strftime("%y%m%d%H%M%S")
        t_render = time.perf_counter()
        worker_result = await self._run_visualization_worker(
            geometry, phenomenon_text, timestamp, affected_departments, all_departments
        )
        render_ms = int((time.perf_counter() - t_render) * 1000)

        if worker_result.get("status") != "success":
            raise RuntimeError(f"Visualization failed: {worker_result.get('error')}")

        # 5. Save to database
        area_html = self._format_area_html(affected_departments)
        self.logger.info(
            "Final polygon sizes; phenomenon text length: %d, "
            "area html length: %d, polygon str length: %d",
            len(phenomenon_text),
            len(area_html),
            len(polygon_str),
        )
        self.logger.info("polygon str value: %s", polygon_str)

        # 5b. Validate area HTML against the DB column limit before insert, so an
        # oversized affected area becomes an actionable error instead of a raw
        # MySQL DataError. Only knowable now (depends on the intersection result).
        t_persist = time.perf_counter()
        await self._validate_area_size(area_html, len(affected_departments))

        # Extract just the filename from full path (persisted in DB and used for URL)
        gif_area_filename = os.path.basename(worker_result["gif_area"])
        gif_gral_filename = os.path.basename(worker_result["gif_gral"])

        # DB write off the event loop (see get_departments above).
        alert_id = await asyncio.to_thread(
            self.mysql_repo.insert_alert,
            phenomenon_text,
            area_html,
            polygon_str,
            gif_general=gif_gral_filename,
            gif_zoom=gif_area_filename,
        )
        persist_ms = int((time.perf_counter() - t_persist) * 1000)

        duration = time.perf_counter() - t0
        self.logger.info(f"Alert {alert_id} generated in {duration:.2f}s")

        return {
            "alert_id": alert_id,
            "timestamp": timestamp,
            "phenomenon_code": phenomenon_code,
            "phenomenon": phenomenon_text,
            "area": area_html,
            "polygon": polygon_str,
            "gif_area_url": f"/alerts/{gif_area_filename}",
            "gif_gral_url": f"/alerts/{gif_gral_filename}",
            "gif_area_filename": gif_area_filename,
            "gif_gral_filename": gif_gral_filename,
            "affected_departments_count": len(affected_departments),
            "intersection_ms": intersection_ms,
            "filter_ms": filter_ms,
            "render_ms": render_ms,
            "persist_ms": persist_ms,
        }

    async def _filter_departments_by_departments(  # pylint: disable=too-many-locals
        self, geometry: dict, departments: List[dict], all_departments: List[dict]
    ) -> List[dict]:
        """Filter departments that fall within intersecting departments.

        Matches genero_aviso.py lines 136-166: uses FULL department geometries
        (not intersection fragments) so departments anywhere in an affected department
        are included.
        """
        input_geom = shape(geometry)
        if not input_geom.is_valid:
            input_geom = input_geom.buffer(0)

        umbral = 0.001  # minimum department coverage fraction (same as genero_aviso.py)

        # Load full department geometries from cached index (double-checked locking)
        dept_index_path = os.path.join(self.settings.alert_cache_dir, "dept_index.pkl")
        dept_index = await self._get_dept_index(dept_index_path)

        if dept_index is not None:
            result = await asyncio.to_thread(
                self._compute_spatial_filter,
                input_geom,
                dept_index,
                all_departments,
                umbral,
            )
        else:
            # Fallback if cache not built yet: use intersection fragments
            self.logger.warning(
                "dept_index.pkl not found — falling back to intersection fragments "
                "(some departments may be missed)"
            )
            dept_geoms = []
            for d in departments:
                if "intersection" in d:
                    try:
                        dept_geoms.append(shape(d["intersection"]))
                    except (KeyError, ValueError, TypeError) as exc:
                        self.logger.warning(
                            "Skipping malformed intersection fragment: %s", exc
                        )
                        continue
            result = await asyncio.to_thread(
                self._compute_spatial_filter_from_geoms,
                input_geom,
                dept_geoms,
                all_departments,
            )

        return result

    @staticmethod
    async def _get_dept_index(dept_index_path: str) -> list | None:
        """Return cached dept_index, loading from disk on first call."""
        global _DEPT_INDEX_CACHE  # pylint: disable=global-statement
        if not os.path.exists(dept_index_path):
            return None
        if _DEPT_INDEX_CACHE is None:
            async with _DEPT_INDEX_LOCK:
                if _DEPT_INDEX_CACHE is None:
                    _DEPT_INDEX_CACHE = await asyncio.to_thread(
                        AlertGenerationService._load_dept_index, dept_index_path
                    )
        return _DEPT_INDEX_CACHE

    @staticmethod
    async def _get_prov_index(prov_index_path: str) -> list | None:
        """Return cached prov_index, loading from disk on first call."""
        global _PROV_INDEX_CACHE  # pylint: disable=global-statement
        if not os.path.exists(prov_index_path):
            return None
        if _PROV_INDEX_CACHE is None:
            async with _PROV_INDEX_LOCK:
                if _PROV_INDEX_CACHE is None:
                    _PROV_INDEX_CACHE = await asyncio.to_thread(
                        AlertGenerationService._load_dept_index, prov_index_path
                    )
        return _PROV_INDEX_CACHE

    @staticmethod
    async def _get_dept_index_merc(dept_index: list) -> list:
        """Return dept_index with geometries pre-projected to ccrs.Mercator().

        Bounding boxes (lon/lat) are kept as-is — they only prefilter by visible
        extent, which is also expressed in lon/lat. Computed once and cached for the
        app lifetime.
        """
        global _DEPT_INDEX_MERC_CACHE  # pylint: disable=global-statement
        if _DEPT_INDEX_MERC_CACHE is None:
            async with _DEPT_INDEX_MERC_LOCK:
                if _DEPT_INDEX_MERC_CACHE is None:
                    _DEPT_INDEX_MERC_CACHE = await asyncio.to_thread(
                        AlertGenerationService._project_index_to_mercator, dept_index
                    )
        return _DEPT_INDEX_MERC_CACHE

    @staticmethod
    async def _get_prov_geoms_merc(prov_index: list) -> list:
        """Return prov_index geometries pre-projected to ccrs.Mercator() (cached)."""
        global _PROV_GEOMS_MERC_CACHE  # pylint: disable=global-statement
        if _PROV_GEOMS_MERC_CACHE is None:
            async with _PROV_GEOMS_MERC_LOCK:
                if _PROV_GEOMS_MERC_CACHE is None:
                    projected = await asyncio.to_thread(
                        AlertGenerationService._project_index_to_mercator, prov_index
                    )
                    _PROV_GEOMS_MERC_CACHE = [geom for _, geom in projected]
        return _PROV_GEOMS_MERC_CACHE

    @staticmethod
    def _project_index_to_mercator(index: list) -> list:
        """Project each geometry in a (bbox, geom) index list to ccrs.Mercator()."""
        pc = ccrs.PlateCarree()
        merc = ccrs.Mercator()
        result = []
        for bbox, geom in index:
            projected = merc.project_geometry(geom, pc)
            if not projected.is_empty:
                result.append((bbox, projected))
        return result

    @staticmethod
    def _compute_spatial_filter(
        input_geom, dept_index: list, all_departments: list, umbral: float
    ) -> list:
        """CPU-bound spatial filtering (runs in thread pool)."""
        dept_geoms = []
        bx0, by0, bx1, by1 = input_geom.bounds
        for (dx0, dy0, dx1, dy1), dg in dept_index:
            if dx1 < bx0 or dx0 > bx1 or dy1 < by0 or dy0 > by1:
                continue
            inter = dg.intersection(input_geom)
            if inter.is_empty:
                continue
            if inter.area / dg.area >= umbral:
                dept_geoms.append(dg)

        result = []
        for department in all_departments:
            pt = Point(float(department["longitud"]), float(department["latitud"]))
            if dept_geoms and any(dg.contains(pt) for dg in dept_geoms):
                result.append(department)
            elif not dept_geoms and input_geom.contains(pt):
                result.append(department)
        return result

    @staticmethod
    def _compute_spatial_filter_from_geoms(
        input_geom, dept_geoms: list, all_departments: list
    ) -> list:
        """CPU-bound spatial filtering from pre-built geom list (runs in thread pool)."""
        result = []
        for department in all_departments:
            pt = Point(float(department["longitud"]), float(department["latitud"]))
            hit = (
                any(dg.contains(pt) for dg in dept_geoms)
                if dept_geoms
                else input_geom.contains(pt)
            )
            if hit:
                result.append(department)
        return result

    async def prewarm_render_geometry(self) -> None:
        """Project + cache the dept/prov index to Mercator at startup.

        Called from the app lifespan after the scheduler builds the caches, so the
        first alert doesn't pay the projection cost and no cartopy runs mid-request.
        Best-effort: never blocks startup.
        """
        try:
            cache_dir = self.settings.alert_cache_dir
            dept_index = await self._get_dept_index(
                os.path.join(cache_dir, "dept_index.pkl")
            )
            prov_index = await self._get_prov_index(
                os.path.join(cache_dir, "prov_index.pkl")
            )
            await self._get_dept_index_merc(dept_index or [])
            await self._get_prov_geoms_merc(prov_index or [])
            self.logger.info("Render geometry pre-warmed (dept/prov → Mercator)")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.warning("Render-geometry pre-warm failed: %s", exc)

    async def _run_visualization_worker(
        self,
        geometry,
        phenomenon_text,
        timestamp,
        affected_departments,
        all_departments,
    ) -> dict:
        """Run visualization in an isolated subprocess to contain GeoPandas memory."""
        dept_index_path = os.path.join(self.settings.alert_cache_dir, "dept_index.pkl")
        prov_index_path = os.path.join(self.settings.alert_cache_dir, "prov_index.pkl")
        dept_index = await self._get_dept_index(dept_index_path)
        prov_index = await self._get_prov_index(prov_index_path)

        # Send Mercator-projected geometries for rendering — avoids cartopy
        # reprojecting ~53k vertices per request in the worker.
        dept_index_merc = await self._get_dept_index_merc(dept_index or [])
        prov_geoms_merc = await self._get_prov_geoms_merc(prov_index or [])

        dept_index_serialized = [
            {"bbox": list(bbox), "wkb_hex": shapely_wkb.dumps(geom, hex=True)}
            for bbox, geom in dept_index_merc
        ]
        prov_geoms_serialized = [
            shapely_wkb.dumps(geom, hex=True) for geom in prov_geoms_merc
        ]

        payload = json.dumps(
            {
                "geometry_wkb_hex": shapely_wkb.dumps(shape(geometry), hex=True),
                "phenomenon_text": phenomenon_text,
                "timestamp": timestamp,
                "affected_departments": affected_departments,
                "all_departments": all_departments,
                "output_dir": self.settings.output_dir,
                "cache_dir": self.settings.alert_cache_dir,
                "dept_index_serialized": dept_index_serialized,
                "prov_geoms_serialized": prov_geoms_serialized,
            }
        )

        async with _ALERT_VIZ_SEMAPHORE:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                _WORKER_PATH,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_SUBPROCESS_ENV,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(payload.encode()), timeout=120
                )
            except asyncio.TimeoutError as exc:
                proc.kill()
                await proc.wait()
                raise RuntimeError("Visualization worker timed out after 120s") from exc
            except asyncio.CancelledError:
                # Outer cancel (per-job timeout or shutdown): never orphan the child.
                proc.kill()
                await proc.wait()
                raise

        if proc.returncode != 0:
            self.logger.error(f"Worker failed: {stderr_bytes.decode()}")
            raise RuntimeError(f"Worker failed (exit {proc.returncode})")

        try:
            return json.loads(stdout_bytes)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Worker returned invalid JSON: {stdout_bytes[:200]!r}"
            ) from exc

    @staticmethod
    def _load_dept_index(path: str) -> list:
        """Load pickled department spatial index from disk (blocking I/O)."""
        with open(path, "rb") as f:
            return pickle.load(f)

    def _format_area_html(self, departments: List[dict]) -> str:
        """Format departments by province for HTML display.

        Adapted from genero_aviso.py lines 398-405.
        """
        by_prov: dict[str, list[str]] = {}
        for department in departments:
            by_prov.setdefault(department["provincia"].upper(), []).append(
                department["nom_departamento"]
            )
        return (
            "<br /><br />".join(
                f"<b>{prov}:</b> {' - '.join(sorted(names))}."
                for prov, names in sorted(by_prov.items())
            )
            or "(Sin departamentos en el área)"
        )

    async def get_max_vertex_count(self) -> int:
        """Return the maximum number of polygon vertices that fit the DB column.

        N is the VARCHAR character limit of taviso_temporal.Poligono, queried from
        the DB. The maximum vertex count is derived as (N + 1) // 16.
        """
        max_chars = await asyncio.to_thread(self.mysql_repo.get_polygon_max_length)
        return self._max_vertex_count(max_chars)

    @staticmethod
    def _max_vertex_count(max_chars: int) -> int:
        """Derive the max polygon vertex count from the column char limit."""
        return (max_chars + 1) // 16

    async def _validate_polygon_size(self, polygon_str: str) -> None:
        """Raise PolygonTooLargeError if polygon_str exceeds the DB column limit."""
        max_chars = await asyncio.to_thread(self.mysql_repo.get_polygon_max_length)
        if len(polygon_str) <= max_chars:
            return
        raise PolygonTooLargeError(
            f"Polygon serialization is {len(polygon_str)} characters, exceeds DB"
            f" column limit of {max_chars}. Use more aggressive simplification to"
            " reduce the number of vertices in the input polygon.",
            max_vertex_count=self._max_vertex_count(max_chars),
        )

    async def _validate_area_size(self, area_html: str, affected_count: int) -> None:
        """Raise AreaTooLargeError if area_html exceeds the DB column limit."""
        max_chars = await asyncio.to_thread(self.mysql_repo.get_area_max_length)
        if len(area_html) <= max_chars:
            return
        raise AreaTooLargeError(
            f"Affected-area HTML is {len(area_html)} characters, exceeds DB column"
            f" limit of {max_chars}. The polygon covers too many departments;"
            " reduce its size.",
            max_chars=max_chars,
            actual_chars=len(area_html),
            affected_count=affected_count,
        )

    def _format_polygon(self, geometry: dict) -> str:
        """Format geometry coordinates for database storage.

        Adapted from genero_aviso.py line 476.
        """
        coords = geometry["coordinates"][0]
        return ",".join(f"[{c[1]:.2f},{c[0]:.2f}]" for c in coords)
