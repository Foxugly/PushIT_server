"""Malformed bulk-send payloads must be rejected with 400, never 500."""
import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application
from applications.models_send_token import AppSendToken

MALFORMED = [
    {"notification_ids": "abc"},
    {"notification_ids": [{"id": 1}]},
    {"notification_ids": ["x"]},
    {"notification_ids": [[1]]},
    {"notification_ids": list(range(1, 502))},
    {},
]


@pytest.fixture
def jwt_client(db):
    user = User.objects.create_user(email="bulk@example.com", password="StrongPass123!")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


@pytest.fixture
def app_token_client(db):
    user = User.objects.create_user(email="bulk-app@example.com", password="StrongPass123!")
    app = Application.objects.create(owner=user, name="Bulk App")
    _, raw = AppSendToken.issue(app, "tests")
    client = APIClient()
    client.credentials(HTTP_X_APP_TOKEN=raw)
    return client


@pytest.mark.parametrize("payload", MALFORMED)
def test_jwt_bulk_send_rejects_malformed_input(jwt_client, payload):
    response = jwt_client.post("/api/v1/notifications/bulk-send/", payload, format="json")
    assert response.status_code == 400
    assert response.data["code"] == "validation_error"


@pytest.mark.parametrize("payload", MALFORMED)
def test_app_token_bulk_send_rejects_malformed_input(app_token_client, payload):
    response = app_token_client.post("/api/v1/notifications/app/bulk-send/", payload, format="json")
    assert response.status_code == 400
    assert response.data["code"] == "validation_error"


def test_bulk_send_rejects_non_object_body(jwt_client):
    response = jwt_client.post("/api/v1/notifications/bulk-send/", [1, 2], format="json")
    assert response.status_code == 400
