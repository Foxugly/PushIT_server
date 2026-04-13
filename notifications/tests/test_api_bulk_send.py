import pytest
from unittest.mock import patch

from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application
from notifications.models import Notification, NotificationStatus


@pytest.mark.django_db
@patch("notifications.api_views.send_notification_task.delay")
def test_bulk_send_queues_multiple_notifications(mock_delay):
    mock_delay.return_value.id = "fake-task-id"

    client = APIClient()
    user = User.objects.create_user(
        email="bulk@example.com", username="bulk", password="StrongPass123!",
    )
    app = Application.objects.create(owner=user, name="Bulk App")
    n1 = Notification.objects.create(application=app, title="N1", message="M1", status=NotificationStatus.DRAFT)
    n2 = Notification.objects.create(application=app, title="N2", message="M2", status=NotificationStatus.DRAFT)
    n3 = Notification.objects.create(application=app, title="N3", message="M3", status=NotificationStatus.SENT)

    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.post(
        "/api/v1/notifications/bulk-send/",
        {"notification_ids": [n1.id, n2.id, n3.id, 99999]},
        format="json",
    )

    assert response.status_code == 200
    data = response.data
    assert set(data["queued"]) == {n1.id, n2.id}
    assert len(data["errors"]) == 2
    error_ids = {e["id"] for e in data["errors"]}
    assert n3.id in error_ids
    assert 99999 in error_ids


@pytest.mark.django_db
def test_bulk_send_rejects_empty_list():
    client = APIClient()
    user = User.objects.create_user(
        email="empty@example.com", username="empty", password="StrongPass123!",
    )
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.post(
        "/api/v1/notifications/bulk-send/",
        {"notification_ids": []},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "validation_error"
