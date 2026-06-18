"""Unit tests for polygon-size validation in AlertGenerationService."""

import logging
from typing import Dict, List, Optional, Tuple

import pytest

from domain.models import PolygonTooLargeError
from ports.mysql_repository import IMySQLRepository
from services.alert_generation_service import AlertGenerationService


class FakeMySQLRepository(IMySQLRepository):
    """Minimal port fake exposing only the polygon-limit query."""

    def __init__(self, polygon_max_length: int):
        self._polygon_max_length = polygon_max_length

    def get_polygon_max_length(self) -> int:
        return self._polygon_max_length

    def get_area_max_length(self) -> int:
        return 2000

    def get_departments(self) -> List[dict]:
        return []

    def insert_alert(self, phenomenon, area, polygon, gif_general, gif_zoom) -> int:
        return 0

    def get_pending_alerts(self, since_id: Optional[int] = None) -> List[dict]:
        return []

    def get_pending_alerts_etag(self) -> Tuple[int, Optional[int]]:
        return (0, None)

    def get_phenomenon_text(self, code: int) -> Optional[str]:
        return None

    def get_all_phenomena(self) -> Dict[int, Optional[str]]:
        return {}

    def close(self) -> None:
        pass


def _service(polygon_max_length: int) -> AlertGenerationService:
    return AlertGenerationService(
        mysql_repo=FakeMySQLRepository(polygon_max_length),
        geo_service=None,
        settings=None,
        logger=logging.getLogger("test"),
    )


async def test_polygon_within_limit_does_not_raise():
    service = _service(polygon_max_length=1000)

    await service._validate_polygon_size("x" * 1000)  # exactly at the limit


async def test_polygon_over_limit_raises_with_max_vertex_count():
    service = _service(polygon_max_length=1000)

    with pytest.raises(PolygonTooLargeError) as exc_info:
        await service._validate_polygon_size("x" * 1001)

    # (N + 1) // 16 == (1000 + 1) // 16 == 62
    assert exc_info.value.max_vertex_count == 62


async def test_max_vertex_count_uses_db_column_length():
    service = _service(polygon_max_length=255)

    with pytest.raises(PolygonTooLargeError) as exc_info:
        await service._validate_polygon_size("x" * 256)

    # (255 + 1) // 16 == 16
    assert exc_info.value.max_vertex_count == 16
