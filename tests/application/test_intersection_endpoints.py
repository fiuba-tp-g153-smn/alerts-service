from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from services.geo_intersection_service import GeoIntersectionService

_VALID_POLYGON = {
    "type": "Polygon",
    "coordinates": [
        [[-55.5, -26.5], [-54.5, -26.5], [-54.5, -26.0], [-55.5, -26.0], [-55.5, -26.5]]
    ],
}

_VALID_FEATURE = {
    "type": "Feature",
    "geometry": _VALID_POLYGON,
    "properties": {},
}

_VALID_FEATURE_COLLECTION = {
    "type": "FeatureCollection",
    "features": [_VALID_FEATURE],
}

_EMPTY_FEATURE_COLLECTION = {"type": "FeatureCollection", "features": []}


@pytest.fixture
def mock_service():
    return AsyncMock(spec=GeoIntersectionService)


@pytest.fixture
def mock_history_repo():
    repo = MagicMock()
    repo.get_recent.return_value = []
    return repo


@pytest.fixture
def app_client(mock_service, mock_history_repo, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    from main import app
    from container import get_history_repo, get_intersection_service

    app.dependency_overrides[get_intersection_service] = lambda: mock_service
    app.dependency_overrides[get_history_repo] = lambda: mock_history_repo
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_intersect_country_200(app_client, mock_service):
    mock_service.intersect_country.return_value = {
        "type": "FeatureCollection",
        "features": [],
    }

    response = app_client.post("/intersect/country", json=_VALID_POLYGON)

    assert response.status_code == 200


def test_intersect_country_accepts_feature_wrapper(app_client, mock_service):
    mock_service.intersect_country.return_value = {
        "type": "FeatureCollection",
        "features": [],
    }

    response = app_client.post("/intersect/country", json=_VALID_FEATURE)

    assert response.status_code == 200


def test_intersect_country_accepts_feature_collection(app_client, mock_service):
    mock_service.intersect_country.return_value = {
        "type": "FeatureCollection",
        "features": [],
    }

    response = app_client.post("/intersect/country", json=_VALID_FEATURE_COLLECTION)

    assert response.status_code == 200


def test_intersect_country_missing_type_422(app_client):
    response = app_client.post("/intersect/country", json={"coordinates": []})

    assert response.status_code == 422


def test_intersect_country_layer_not_found_500(app_client, mock_service):
    mock_service.intersect_country.side_effect = FileNotFoundError("layer not found")

    response = app_client.post("/intersect/country", json=_VALID_POLYGON)

    assert response.status_code == 500


def test_intersect_country_value_error_400(app_client, mock_service):
    mock_service.intersect_country.side_effect = ValueError("bad geometry")

    response = app_client.post("/intersect/country", json=_VALID_POLYGON)

    assert response.status_code == 400


def test_intersect_departments_200(app_client, mock_service):
    mock_service.intersect_departments.return_value = []

    response = app_client.post("/intersect/departments", json=_VALID_POLYGON)

    assert response.status_code == 200
    assert response.json()["departments"] == []


def test_intersect_departments_layer_not_found_500(app_client, mock_service):
    mock_service.intersect_departments.side_effect = FileNotFoundError(
        "layer not found"
    )

    response = app_client.post("/intersect/departments", json=_VALID_POLYGON)

    assert response.status_code == 500


def test_layer_refresh_history_returns_runs(app_client, mock_history_repo):
    mock_history_repo.get_recent.return_value = [{"status": "success"}]

    response = app_client.get("/intersect/layer-refresh-history")

    assert response.status_code == 200
    assert len(response.json()["runs"]) == 1
