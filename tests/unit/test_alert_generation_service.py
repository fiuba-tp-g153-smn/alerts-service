"""Unit tests for AlertGenerationService.generate_alert response shape."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.alert_job import PreparedAlert
from domain.models import AreaTooLargeError, PolygonTooLargeError
from services.alert_generation_service import AlertGenerationService

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
