"""Idempotency-Key and template-rendered title/message are bounded before they
reach the database (PostgreSQL would raise on an over-long varchar → 500)."""
import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application
from applications.models_send_token import AppSendToken
from devices.models import Device, DeviceApplicationLink
from notifications.models import Notification, NotificationTemplate


@pytest.fixture
def app_ctx(db):
    user = User.objects.create_user(email="bounds@example.com", password="StrongPass123!")
    app = Application.objects.create(owner=user, name="Bounds App")
    _, raw = AppSendToken.issue(app, "tests")
    app_client = APIClient()
    app_client.credentials(HTTP_X_APP_TOKEN=raw)
    jwt_client = APIClient()
    jwt_client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    tpl = NotificationTemplate.objects.create(
        application=app, name="t", title_template="Hi {{name}}", message_template="Body {{body}}"
    )
    return app, app_client, jwt_client, tpl


@pytest.mark.parametrize("url", ["/api/v1/notifications/app/create/", "/api/v1/notifications/app/send/"])
def test_overlong_idempotency_key_is_rejected(app_ctx, url):
    _, app_client, _, _ = app_ctx
    response = app_client.post(
        url, {"title": "T", "message": "M"}, format="json", HTTP_IDEMPOTENCY_KEY="k" * 256
    )
    assert response.status_code == 400
    assert response.data["code"] == "idempotency_key_invalid"
    assert Notification.objects.count() == 0


def test_idempotency_key_of_255_chars_is_accepted(app_ctx):
    _, app_client, _, _ = app_ctx
    response = app_client.post(
        "/api/v1/notifications/app/create/",
        {"title": "T", "message": "M"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="k" * 255,
    )
    assert response.status_code == 201


@pytest.mark.parametrize(
    "variables, field",
    [({"name": "x" * 300, "body": "ok"}, "title"), ({"name": "ok", "body": "x" * 5001}, "message")],
)
def test_app_token_template_render_overflow_is_rejected(app_ctx, variables, field):
    _, app_client, _, tpl = app_ctx
    response = app_client.post(
        "/api/v1/notifications/app/create/",
        {"template_id": tpl.id, "variables": variables},
        format="json",
        HTTP_IDEMPOTENCY_KEY="idem-render",
    )
    assert response.status_code == 400
    assert field in response.data["errors"]
    assert Notification.objects.count() == 0


def test_jwt_template_render_overflow_is_rejected(app_ctx):
    app, _, jwt_client, tpl = app_ctx
    device = Device.objects.create(push_token="token_" + "x" * 30, device_name="D", platform="android")
    DeviceApplicationLink.objects.create(device=device, application=app, is_active=True)
    response = jwt_client.post(
        "/api/v1/notifications/",
        {"application_id": app.id, "device_ids": [device.id], "template_id": tpl.id, "variables": {"name": "x" * 300, "body": "ok"}},
        format="json",
    )
    assert response.status_code == 400
    assert "title" in response.data["errors"]
