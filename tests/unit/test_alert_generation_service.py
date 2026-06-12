"""Unit tests for AlertGenerationService.generate_alert response shape."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.alert_generation_service import AlertGenerationService

GEOMETRY = {
    "type": "Polygon",
    "coordinates": [[[-58.50, -34.60], [-58.40, -34.60], [-58.40, -34.50], [-58.50, -34.60]]],
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

    geo_service = MagicMock()
    geo_service.intersect_departments = AsyncMock(return_value=[])

    settings = MagicMock()
    settings.alert_simplification_level = 5

    svc = AlertGenerationService(mysql_repo, geo_service, settings, MagicMock())
    svc._filter_departments_by_departments = AsyncMock(return_value=AFFECTED_DEPARTMENTS)
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
    assert result["affected_departments_count"] == 2
    # area/polygon match what is persisted and what GET /alerts/pending returns
    assert result["area"] == "<b>BUENOS AIRES:</b> Ensenada - La Plata."
    assert result["polygon"] == "[-34.60,-58.50],[-34.60,-58.40],[-34.50,-58.40],[-34.60,-58.50]"


async def test_generate_alert_persists_same_area_and_polygon(service):
    result = await service.generate_alert(GEOMETRY, phenomenon_code=10)

    args = service.mysql_repo.insert_alert.call_args
    assert args.args[1] == result["area"]
    assert args.args[2] == result["polygon"]
