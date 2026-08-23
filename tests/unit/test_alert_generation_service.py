"""Unit tests for AlertGenerationService.generate_alert response shape."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from shapely.geometry import Polygon

import services.alert_generation_service as svc_mod
from domain.alert_job import PreparedAlert
from domain.models import AreaTooLargeError, PolygonTooLargeError
from services.alert_generation_service import AlertGenerationService

_POLY = Polygon([(-58.5, -34.6), (-58.4, -34.6), (-58.4, -34.5), (-58.5, -34.6)])


@pytest.fixture(autouse=True)
def _reset_merc_caches():
    """Projection/serialization caches are module globals — reset around each test."""
    for _name in (
        "_DEPT_INDEX_MERC_CACHE",
        "_PROV_GEOMS_MERC_CACHE",
        "_DEPT_INDEX_SERIALIZED_CACHE",
        "_PROV_GEOMS_SERIALIZED_CACHE",
    ):
        setattr(svc_mod, _name, None)
    yield
    for _name in (
        "_DEPT_INDEX_MERC_CACHE",
        "_PROV_GEOMS_MERC_CACHE",
        "_DEPT_INDEX_SERIALIZED_CACHE",
        "_PROV_GEOMS_SERIALIZED_CACHE",
    ):
        setattr(svc_mod, _name, None)


GEOMETRY = {
    "type": "Polygon",
    "coordinates": [
        [[-58.50, -34.60], [-58.40, -34.60], [-58.40, -34.50], [-58.50, -34.60]]
    ],
}

AFFECTED_DEPARTMENTS = [
    {"provincia": "Buenos Aires", "nom_departamento": "La Plata"},
    {"provincia": "Buenos Aires", "nom_departamento": "Ensenada"},
]


@pytest.fixture
def service():
    mysql_repo = MagicMock()
    mysql_repo.get_phenomenon_text.return_value = "TORMENTAS FUERTES"
    mysql_repo.insert_alert.return_value = 42
    mysql_repo.get_departments.return_value = []
    mysql_repo.get_polygon_max_length.return_value = 4000
    mysql_repo.get_area_max_length.return_value = 2000

    geo_service = MagicMock()
    geo_service.intersect_departments = AsyncMock(return_value=[])

    settings = MagicMock()
    settings.alert_simplification_level = 5

    svc = AlertGenerationService(mysql_repo, geo_service, settings, MagicMock())
    svc._filter_departments_by_departments = AsyncMock(
        return_value=AFFECTED_DEPARTMENTS
    )
    svc._run_visualization_worker = AsyncMock(
        return_value={
            "status": "success",
            "gif_area": "/tmp/alerts/zoom_alerta.gif",
            "gif_gral": "/tmp/alerts/gral_alerta.gif",
        }
    )
    return svc


async def test_generate_alert_includes_pending_shape_fields(service):
    result = await service.generate_alert(GEOMETRY, phenomenon_code=10)

    assert result["alert_id"] == 42
    assert result["phenomenon"] == "TORMENTAS FUERTES"
    assert result["gif_area_url"] == "/alerts/zoom_alerta.gif"
    assert result["gif_gral_url"] == "/alerts/gral_alerta.gif"
    assert result["gif_area_filename"] == "zoom_alerta.gif"
    assert result["gif_gral_filename"] == "gral_alerta.gif"
    assert result["affected_departments_count"] == 2
    # Per-stage timings are present for the dashboard breakdown.
    assert {"intersection_ms", "filter_ms", "render_ms", "persist_ms"} <= result.keys()
    # area/polygon match what is persisted and what GET /alerts/pending returns
    assert result["area"] == "<b>BUENOS AIRES:</b> Ensenada - La Plata."
    assert (
        result["polygon"]
        == "[-34.60,-58.50],[-34.60,-58.40],[-34.50,-58.40],[-34.60,-58.50]"
    )


async def test_generate_alert_persists_same_area_and_polygon(service):
    result = await service.generate_alert(GEOMETRY, phenomenon_code=10)

    args = service.mysql_repo.insert_alert.call_args
    assert args.args[1] == result["area"]
    assert args.args[2] == result["polygon"]


async def test_generate_alert_skips_revalidation_when_text_supplied(service):
    """Worker path: passing phenomenon_text avoids the phenomenon re-lookup."""
    await service.generate_alert(GEOMETRY, 10, phenomenon_text="TORMENTAS FUERTES")

    service.mysql_repo.get_phenomenon_text.assert_not_called()


async def test_validate_request_returns_prepared_alert(service):
    prepared = await service.validate_request(GEOMETRY, phenomenon_code=10)

    assert isinstance(prepared, PreparedAlert)
    assert prepared.phenomenon_text == "TORMENTAS FUERTES"
    assert prepared.polygon_str == (
        "[-34.60,-58.50],[-34.60,-58.40],[-34.50,-58.40],[-34.60,-58.50]"
    )


async def test_validate_request_rejects_unknown_phenomenon(service):
    service.mysql_repo.get_phenomenon_text.return_value = None

    with pytest.raises(ValueError):
        await service.validate_request(GEOMETRY, phenomenon_code=999)


async def test_validate_request_rejects_oversized_polygon(service):
    service.mysql_repo.get_polygon_max_length.return_value = 10

    with pytest.raises(PolygonTooLargeError):
        await service.validate_request(GEOMETRY, phenomenon_code=10)


async def test_generate_alert_raises_when_area_too_large(service):
    """The affected-area HTML over the column limit fails before inserting."""
    service.mysql_repo.get_area_max_length.return_value = 5  # smaller than the HTML

    with pytest.raises(AreaTooLargeError) as exc_info:
        await service.generate_alert(GEOMETRY, phenomenon_code=10)

    assert exc_info.value.affected_count == 2
    service.mysql_repo.insert_alert.assert_not_called()


def _stub_worker_with_real_gifs(service, tmp_path):
    """Point the render worker at two real files so cleanup can be asserted."""
    gif_area = tmp_path / "zoom_alerta.gif"
    gif_gral = tmp_path / "gral_alerta.gif"
    gif_area.write_bytes(b"gif")
    gif_gral.write_bytes(b"gif")
    service._run_visualization_worker = AsyncMock(
        return_value={
            "status": "success",
            "gif_area": str(gif_area),
            "gif_gral": str(gif_gral),
        }
    )
    return gif_area, gif_gral


async def test_generate_alert_removes_gifs_when_area_too_large(service, tmp_path):
    # BUG-02: GIFs rendered before validation must be cleaned up on AreaTooLargeError.
    gif_area, gif_gral = _stub_worker_with_real_gifs(service, tmp_path)
    service.mysql_repo.get_area_max_length.return_value = 5

    with pytest.raises(AreaTooLargeError):
        await service.generate_alert(GEOMETRY, phenomenon_code=10)

    assert not gif_area.exists()
    assert not gif_gral.exists()


async def test_generate_alert_removes_gifs_when_insert_fails(service, tmp_path):
    # BUG-02: an insert failure after render must not orphan the GIFs either.
    gif_area, gif_gral = _stub_worker_with_real_gifs(service, tmp_path)
    service.mysql_repo.insert_alert.side_effect = RuntimeError("db down")

    with pytest.raises(RuntimeError, match="db down"):
        await service.generate_alert(GEOMETRY, phenomenon_code=10)

    assert not gif_area.exists()
    assert not gif_gral.exists()


async def test_generate_alert_keeps_gifs_on_success(service, tmp_path):
    # Regression: the cleanup must NOT delete GIFs on the success path.
    gif_area, gif_gral = _stub_worker_with_real_gifs(service, tmp_path)

    await service.generate_alert(GEOMETRY, phenomenon_code=10)

    assert gif_area.exists()
    assert gif_gral.exists()


def test_project_index_to_mercator_keeps_bbox_and_uses_metres():
    bbox = _POLY.bounds
    out = AlertGenerationService._project_index_to_mercator([(bbox, _POLY)])
    assert len(out) == 1
    out_bbox, out_geom = out[0]
    assert out_bbox == bbox  # bbox stays lon/lat (used for the extent prefilter)
    # Mercator coords are in metres → magnitudes far beyond the lon/lat range.
    minx, _miny, _maxx, _maxy = out_geom.bounds
    assert abs(minx) > 1_000_000


async def test_get_dept_index_merc_projects_once_and_caches():
    index = [(_POLY.bounds, _POLY)]
    first = await AlertGenerationService._get_dept_index_merc(index)
    # Second call with different input returns the cached result (not re-projected).
    second = await AlertGenerationService._get_dept_index_merc([])
    assert first is second
    assert len(first) == 1


async def test_get_dept_index_serialized_computes_once_and_caches():
    # PERF-01: WKB-hex serialization is computed once, then reused across requests.
    merc = [(_POLY.bounds, _POLY)]
    first = await AlertGenerationService._get_dept_index_serialized(merc)
    second = await AlertGenerationService._get_dept_index_serialized([])
    assert first is second
    assert first[0]["bbox"] == list(_POLY.bounds)
    assert isinstance(first[0]["wkb_hex"], str) and first[0]["wkb_hex"]


async def test_get_prov_geoms_serialized_computes_once_and_caches():
    first = await AlertGenerationService._get_prov_geoms_serialized([_POLY])
    second = await AlertGenerationService._get_prov_geoms_serialized([])
    assert first is second
    assert isinstance(first[0], str) and first[0]


def test_build_worker_payload_contains_all_worker_fields(service):
    service.settings.output_dir = "/out"
    service.settings.alert_cache_dir = "/cache"

    payload = service._build_worker_payload(
        GEOMETRY,
        "TEXT",
        "250101000000",
        [],
        [],
        [{"bbox": [0, 0, 1, 1], "wkb_hex": "00"}],
        ["00"],
    )

    import json as _json

    data = _json.loads(payload)
    assert set(data) == {
        "geometry_wkb_hex",
        "phenomenon_text",
        "timestamp",
        "affected_departments",
        "all_departments",
        "output_dir",
        "cache_dir",
        "dept_index_serialized",
        "prov_geoms_serialized",
    }
    assert data["dept_index_serialized"] == [{"bbox": [0, 0, 1, 1], "wkb_hex": "00"}]


async def test_prewarm_render_geometry_is_best_effort(service, tmp_path):
    # No cache pickles on disk → indexes load as None → projects [] → never raises.
    service.settings.alert_cache_dir = str(tmp_path)
    await service.prewarm_render_geometry()
    assert svc_mod._DEPT_INDEX_MERC_CACHE == []
