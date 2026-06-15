from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application
from devices.models import Device, DeviceApplicationLink, DeviceTokenStatus
from notifications.models import Notification, NotificationStatus

PWD = "MotDePasseTresSolide123!"


def _auth(client, user):
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")


def _owner_with_linked_device(email="o@example.com"):
    user = User.objects.create_user(email=email, password=PWD)
    app = Application.objects.create(owner=user, name="Acme")
    device = Device.objects.create(
        user=user, push_token=f"fcm_{email}", push_token_status=DeviceTokenStatus.ACTIVE
    )
    DeviceApplicationLink.objects.create(device=device, application=app, is_active=True)
    return user, app, device


# --- Owner: POST /notifications/send/ ---

@pytest.mark.django_db
@patch("notifications.api_views.send_notification_task.delay")
def test_owner_send_now_creates_and_dispatches_in_one_call(mock_delay):
    mock_delay.return_value.id = "task-1"
    client = APIClient()
    user, app, device = _owner_with_linked_device()
    _auth(client, user)

    resp = client.post(
        "/api/v1/notifications/send/",
        {"application_id": app.id, "device_ids": [device.id], "title": "Hi", "message": "yo"},
        format="json",
    )

    assert resp.status_code == 202, resp.data
    assert resp.data["status"] == NotificationStatus.QUEUED
    mock_delay.assert_called_once()
    # One notification, dispatched (not left as a draft).
    notif = Notification.objects.get()
    assert notif.status == NotificationStatus.QUEUED


@pytest.mark.django_db
@patch("notifications.api_views.send_notification_task.delay")
def test_owner_send_now_rejects_scheduled_for(mock_delay):
    client = APIClient()
    user, app, device = _owner_with_linked_device()
    _auth(client, user)
    future = (datetime.now(dt_timezone.utc) + timedelta(days=1)).isoformat()

    resp = client.post(
        "/api/v1/notifications/send/",
        {
            "application_id": app.id,
            "device_ids": [device.id],
            "title": "Hi",
            "message": "yo",
            "scheduled_for": future,
        },
        format="json",
    )

    assert resp.status_code == 400
    mock_delay.assert_not_called()
    assert Notification.objects.count() == 0


# --- App token: POST /notifications/app/send/ ---

@pytest.mark.django_db
@patch("notifications.api_views.send_notification_task.delay")
def test_app_token_send_now_creates_and_dispatches(mock_delay):
    mock_delay.return_value.id = "task-1"
    client = APIClient()
    user = User.objects.create_user(email="appt@example.com", password=PWD)
    app = Application.objects.create(owner=user, name="Acme")
    raw_token = app.set_new_app_token()
    app.save()

    resp = client.post(
        "/api/v1/notifications/app/send/",
        {"title": "Hi", "message": "yo"},
        format="json",
        HTTP_X_APP_TOKEN=raw_token,
        HTTP_IDEMPOTENCY_KEY="send-1",
    )

    assert resp.status_code == 201, resp.data
    mock_delay.assert_called_once()


@pytest.mark.django_db
@patch("notifications.api_views.send_notification_task.delay")
def test_app_token_send_now_is_idempotent_and_does_not_resend(mock_delay):
    mock_delay.return_value.id = "task-1"
    client = APIClient()
    user = User.objects.create_user(email="appt2@example.com", password=PWD)
    app = Application.objects.create(owner=user, name="Acme")
    raw_token = app.set_new_app_token()
    app.save()
    body = {"title": "Hi", "message": "yo"}

    first = client.post(
        "/api/v1/notifications/app/send/", body, format="json",
        HTTP_X_APP_TOKEN=raw_token, HTTP_IDEMPOTENCY_KEY="dup",
    )
    second = client.post(
        "/api/v1/notifications/app/send/", body, format="json",
        HTTP_X_APP_TOKEN=raw_token, HTTP_IDEMPOTENCY_KEY="dup",
    )

    assert first.status_code == 201
    assert second.status_code == 200, "idempotency replay returns the existing notification"
    assert Notification.objects.count() == 1
    mock_delay.assert_called_once(), "replay must not re-dispatch"


@pytest.mark.django_db
@patch("notifications.api_views.send_notification_task.delay")
def test_app_token_send_now_requires_idempotency_key(mock_delay):
    client = APIClient()
    user = User.objects.create_user(email="appt3@example.com", password=PWD)
    app = Application.objects.create(owner=user, name="Acme")
    raw_token = app.set_new_app_token()
    app.save()

    resp = client.post(
        "/api/v1/notifications/app/send/",
        {"title": "Hi", "message": "yo"},
        format="json",
        HTTP_X_APP_TOKEN=raw_token,
    )

    assert resp.status_code == 400
    mock_delay.assert_not_called()
