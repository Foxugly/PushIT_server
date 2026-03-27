import pytest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import requests
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application
from notifications.models import Notification, NotificationStatus


@pytest.mark.django_db
@patch("notifications.api_views.send_notification_task.delay")
def test_send_notification_endpoint_accepts_draft(mock_delay):
    mock_delay.return_value.id = "fake-task-id"

    client = APIClient()

    user = User.objects.create_user(
        email="u1@example.com",
        username="u1",
        password="1234Test!!",
    )
    app = Application.objects.create(owner=user, name="App")
    notification = Notification.objects.create(
        application=app,
        title="Hello",
        message="World",
        status=NotificationStatus.DRAFT,
    )

    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.post(
        f"/api/v1/notifications/{notification.id}/send/",
        {},
        format="json",
    )

    assert response.status_code == 202
    assert response.data["status"] == NotificationStatus.QUEUED
    assert response.data["notification_id"] == notification.id
    assert response.data["task_id"] == "fake-task-id"

    notification.refresh_from_db()
    assert notification.status == NotificationStatus.QUEUED


@pytest.mark.django_db
@patch("notifications.api_views.send_notification_task.delay")
def test_send_notification_endpoint_rejects_already_queued(mock_delay):
    client = APIClient()

    user = User.objects.create_user(
        email="u1@example.com",
        username="u1",
        password="1234Test!!",
    )
    app = Application.objects.create(owner=user, name="App")
    notification = Notification.objects.create(
        application=app,
        title="Hello",
        message="World",
        status=NotificationStatus.QUEUED,
    )

    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.post(
        f"/api/v1/notifications/{notification.id}/send/",
        {},
        format="json",
    )

    assert response.status_code == 409
    assert response.data["code"] == "notification_not_sendable"
    assert "ne peut pas être mise en file" in response.data["detail"]
    mock_delay.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_concurrent_send_notification_only_queues_once(live_server):
    user = User.objects.create_user(
        email="u1@example.com",
        username="u1",
        password="1234Test!!",
    )
    app = Application.objects.create(owner=user, name="App")
    notification = Notification.objects.create(
        application=app,
        title="Hello",
        message="World",
        status=NotificationStatus.DRAFT,
    )

    access = str(RefreshToken.for_user(user).access_token)

    url = f"{live_server.url}/api/v1/notifications/{notification.id}/send/"
    headers = {
        "Authorization": f"Bearer {access}",
    }

    def do_request():
        return requests.post(url, json={}, headers=headers, timeout=10)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: do_request(), range(2)))

    status_codes = sorted(response.status_code for response in responses)

    assert status_codes == [202, 409]
    assert "notification_not_sendable" in {response.json().get("code") for response in responses}
    notification.refresh_from_db()
    assert notification.status in {
        NotificationStatus.QUEUED,
        NotificationStatus.NO_TARGET,
        NotificationStatus.FAILED,
        NotificationStatus.PARTIAL,
        NotificationStatus.SENT,
    }


@pytest.mark.django_db
@patch("notifications.api_views.send_notification_task.delay")
def test_send_notification_endpoint_rejects_already_sent(mock_delay):
    client = APIClient()

    user = User.objects.create_user(
        email="u1@example.com",
        username="u1",
        password="1234Test!!",
    )
    app = Application.objects.create(owner=user, name="App")
    notification = Notification.objects.create(
        application=app,
        title="Hello",
        message="World",
        status=NotificationStatus.SENT,
    )

    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.post(
        f"/api/v1/notifications/{notification.id}/send/",
        {},
        format="json",
    )

    assert response.status_code == 409
    assert response.data["code"] == "notification_not_sendable"
    assert "ne peut pas être mise en file" in response.data["detail"]
    mock_delay.assert_not_called()


@pytest.mark.django_db
@patch("notifications.api_views.send_notification_task.delay")
def test_send_notification_endpoint_restores_status_when_queue_publish_fails(mock_delay):
    mock_delay.side_effect = RuntimeError("redis unavailable")

    client = APIClient()

    user = User.objects.create_user(
        email="u1@example.com",
        username="u1",
        password="1234Test!!",
    )
    app = Application.objects.create(owner=user, name="App")
    notification = Notification.objects.create(
        application=app,
        title="Hello",
        message="World",
        status=NotificationStatus.DRAFT,
    )

    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.post(
        f"/api/v1/notifications/{notification.id}/send/",
        {},
        format="json",
    )

    assert response.status_code == 503
    assert response.data["code"] == "notification_queue_unavailable"

    notification.refresh_from_db()
    assert notification.status == NotificationStatus.DRAFT


@pytest.mark.django_db
def test_notification_detail_not_found_returns_notification_not_found_code():
    client = APIClient()

    user = User.objects.create_user(
        email="u1@example.com",
        username="u1",
        password="1234Test!!",
    )
    Application.objects.create(owner=user, name="App")

    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.get("/api/v1/notifications/999999/")

    assert response.status_code == 404
    assert response.data["code"] == "notification_not_found"
    assert response.data["detail"] == "Notification introuvable."
