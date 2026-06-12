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

_TWO_FEATURE_COLLECTION = {
    "type": "FeatureCollection",
    "features": [
        _VALID_FEATURE,
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0, 0]},
            "properties": {},
        },
    ],
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


# --- /intersect/country ---


def test_intersect_country_200(app_client, mock_service):
    mock_service.intersect_country.return_value = {
        "type": "FeatureCollection",
        "features": [],
    }

    response = app_client.post("/intersect/country", json=_VALID_POLYGON)

    assert response.status_code == 200
    assert response.json()["type"] == "FeatureCollection"
    mock_service.intersect_country.assert_called_once_with(_VALID_POLYGON, 1)


def test_intersect_country_accepts_feature_wrapper(app_client, mock_service):
    mock_service.intersect_country.return_value = {
        "type": "FeatureCollection",
        "features": [],
    }

    response = app_client.post("/intersect/country", json=_VALID_FEATURE)

    assert response.status_code == 200
    called_geometry = mock_service.intersect_country.call_args.args[0]
    assert called_geometry == _VALID_POLYGON


def test_intersect_country_accepts_feature_collection(app_client, mock_service):
    mock_service.intersect_country.return_value = {
        "type": "FeatureCollection",
        "features": [],
    }

    response = app_client.post("/intersect/country", json=_VALID_FEATURE_COLLECTION)

    assert response.status_code == 200
    called_geometry = mock_service.intersect_country.call_args.args[0]
    assert called_geometry == _VALID_POLYGON


def test_intersect_country_feature_collection_with_multiple_features_uses_first(
    app_client, mock_service
):
    mock_service.intersect_country.return_value = {
        "type": "FeatureCollection",
        "features": [],
    }

    app_client.post("/intersect/country", json=_TWO_FEATURE_COLLECTION)

    called_geometry = mock_service.intersect_country.call_args.args[0]
    assert called_geometry == _VALID_POLYGON


def test_intersect_country_default_level_is_one(app_client, mock_service):
    mock_service.intersect_country.return_value = {
        "type": "FeatureCollection",
        "features": [],
    }

    app_client.post("/intersect/country", json=_VALID_POLYGON)

    assert mock_service.intersect_country.call_args.args[1] == 1


def test_intersect_country_level_param_forwarded_to_service(app_client, mock_service):
    mock_service.intersect_country.return_value = {
        "type": "FeatureCollection",
        "features": [],
    }

    app_client.post("/intersect/country?simplification_level=5", json=_VALID_POLYGON)

    assert mock_service.intersect_country.call_args.args[1] == 5


def test_intersect_country_simplification_level_10_accepted(app_client, mock_service):
    mock_service.intersect_country.return_value = {
        "type": "FeatureCollection",
        "features": [],
    }

    response = app_client.post(
        "/intersect/country?simplification_level=10", json=_VALID_POLYGON
    )

    assert response.status_code == 200
    assert mock_service.intersect_country.call_args.args[1] == 10


def test_intersect_country_simplification_level_11_returns_422(app_client):
    response = app_client.post(
        "/intersect/country?simplification_level=11", json=_VALID_POLYGON
    )

    assert response.status_code == 422


def test_intersect_country_simplification_level_0_returns_422(app_client):
    response = app_client.post(
        "/intersect/country?simplification_level=0", json=_VALID_POLYGON
    )

    assert response.status_code == 422


def test_intersect_country_missing_type_422(app_client):
    response = app_client.post("/intersect/country", json={"coordinates": []})

    assert response.status_code == 422


def test_intersect_country_empty_feature_collection_returns_400(
    app_client, mock_service
):
    response = app_client.post("/intersect/country", json=_EMPTY_FEATURE_COLLECTION)

    assert response.status_code == 400


def test_intersect_country_layer_not_found_500(app_client, mock_service):
    mock_service.intersect_country.side_effect = FileNotFoundError("layer not found")

    response = app_client.post("/intersect/country", json=_VALID_POLYGON)

    assert response.status_code == 500


def test_intersect_country_value_error_400(app_client, mock_service):
    mock_service.intersect_country.side_effect = ValueError("bad geometry")

    response = app_client.post("/intersect/country", json=_VALID_POLYGON)

    assert response.status_code == 400


def test_intersect_country_unexpected_exception_returns_400(app_client, mock_service):
    mock_service.intersect_country.side_effect = RuntimeError("unexpected failure")

    response = app_client.post("/intersect/country", json=_VALID_POLYGON)

    assert response.status_code == 400


# --- /intersect/departments ---


def test_intersect_departments_200(app_client, mock_service):
    dep_feature = {
        "properties": {"nombre": "Dep1", "in_id": 1},
        "geometry": {"type": "Polygon", "coordinates": []},
        "intersection": {"type": "Polygon", "coordinates": []},
    }
    mock_service.intersect_departments.return_value = [dep_feature]

    response = app_client.post("/intersect/departments", json=_VALID_POLYGON)

    assert response.status_code == 200
    body = response.json()
    assert body["departments"][0]["properties"]["nombre"] == "Dep1"


def test_intersect_departments_default_level_is_one(app_client, mock_service):
    mock_service.intersect_departments.return_value = []

    app_client.post("/intersect/departments", json=_VALID_POLYGON)

    assert mock_service.intersect_departments.call_args.args[1] == 1


def test_intersect_departments_level_param_forwarded_to_service(
    app_client, mock_service
):
    mock_service.intersect_departments.return_value = []

    app_client.post(
        "/intersect/departments?simplification_level=3", json=_VALID_POLYGON
    )

    assert mock_service.intersect_departments.call_args.args[1] == 3


def test_intersect_departments_simplification_level_10_accepted(
    app_client, mock_service
):
    mock_service.intersect_departments.return_value = []

    response = app_client.post(
        "/intersect/departments?simplification_level=10", json=_VALID_POLYGON
    )

    assert response.status_code == 200
    assert mock_service.intersect_departments.call_args.args[1] == 10


def test_intersect_departments_simplification_level_11_returns_422(app_client):
    response = app_client.post(
        "/intersect/departments?simplification_level=11", json=_VALID_POLYGON
    )

    assert response.status_code == 422


def test_intersect_departments_simplification_level_0_returns_422(app_client):
    response = app_client.post(
        "/intersect/departments?simplification_level=0", json=_VALID_POLYGON
    )

    assert response.status_code == 422


def test_intersect_departments_layer_not_found_500(app_client, mock_service):
    mock_service.intersect_departments.side_effect = FileNotFoundError(
        "layer not found"
    )

    response = app_client.post("/intersect/departments", json=_VALID_POLYGON)

    assert response.status_code == 500


def test_intersect_departments_value_error_returns_400(app_client, mock_service):
    mock_service.intersect_departments.side_effect = ValueError("bad geometry")

    response = app_client.post("/intersect/departments", json=_VALID_POLYGON)

    assert response.status_code == 400


# --- /intersect/layer-refresh-history ---


def test_layer_refresh_history_returns_runs(app_client, mock_history_repo):
    mock_history_repo.get_recent.return_value = [{"status": "success"}]

    response = app_client.get("/intersect/layer-refresh-history")

    assert response.status_code == 200
    assert len(response.json()["runs"]) == 1
    mock_history_repo.get_recent.assert_called_once_with(20)


def test_layer_refresh_history_limit_param_forwarded_to_repo(
    app_client, mock_history_repo
):
    response = app_client.get("/intersect/layer-refresh-history?limit=5")

    assert response.status_code == 200
    mock_history_repo.get_recent.assert_called_once_with(5)


def test_layer_refresh_history_limit_below_min_returns_422(app_client):
    response = app_client.get("/intersect/layer-refresh-history?limit=0")

    assert response.status_code == 422


def test_layer_refresh_history_limit_above_max_returns_422(app_client):
    response = app_client.get("/intersect/layer-refresh-history?limit=101")

    assert response.status_code == 422
