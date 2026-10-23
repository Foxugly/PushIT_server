from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application
from applications.models_send_token import AppSendToken
from devices.models import Device, DeviceApplicationLink, DeviceTokenStatus
from notifications.models import Notification, NotificationDelivery, NotificationStatus

PASSWORD = "MotDePasseTresSolide123!"


def _auth_client_for(user: User) -> APIClient:
    client = APIClient()
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client


@pytest.fixture
def setup():
    owner = User.objects.create_user(email="owner@example.com", password=PASSWORD)
    recipient = User.objects.create_user(email="rcpt@example.com", password=PASSWORD)
    app = Application.objects.create(owner=owner, name="Acme")
    device = Device.objects.create(
        user=recipient,
        push_token="fcm_recipient",
        push_token_status=DeviceTokenStatus.ACTIVE,
    )
    DeviceApplicationLink.objects.create(device=device, application=app)
    notif = Notification.objects.create(
        application=app, title="Hello", message="World", status=NotificationStatus.SENT
    )
    NotificationDelivery.objects.create(notification=notif, device=device)
    return owner, recipient, app, device, notif


@pytest.mark.django_db
def test_new_notification_is_visible_by_default(setup):
    *_, notif = setup
    assert notif.is_visible is True


@pytest.mark.django_db
def test_owner_delete_hides_notification_without_removing_it(setup):
    owner, *_, notif = setup

    response = _auth_client_for(owner).delete(f"/api/v1/notifications/{notif.id}/")

    assert response.status_code == 204
    notif.refresh_from_db()
    assert notif.is_visible is False
    assert NotificationDelivery.objects.filter(notification=notif).exists()


@pytest.mark.django_db
def test_hidden_notification_disappears_from_every_listing(setup):
    owner, recipient, app, device, notif = setup
    visible = Notification.objects.create(
        application=app, title="Still here", message="x", status=NotificationStatus.SENT
    )
    NotificationDelivery.objects.create(notification=visible, device=device)
    notif.is_visible = False
    notif.save(update_fields=["is_visible"])

    owner_client = _auth_client_for(owner)
    ids = [row["id"] for row in owner_client.get("/api/v1/notifications/").data]
    assert ids == [visible.id]
    page = owner_client.get("/api/v1/notifications/", {"page": 1}).data
    assert page["count"] == 1

    # The owner's device view lists only visible notifications too.
    device.user = owner
    device.save(update_fields=["user"])
    device_rows = owner_client.get(f"/api/v1/devices/{device.id}/notifications/").data
    device_rows = device_rows["results"] if isinstance(device_rows, dict) else device_rows
    assert [row["id"] for row in device_rows] == [visible.id]
    device.user = recipient
    device.save(update_fields=["user"])

    inbox = _auth_client_for(recipient).get(
        "/api/v1/notifications/device/", {"push_token": "fcm_recipient"}
    ).data
    assert [row["id"] for row in inbox] == [visible.id]

    _, raw_token = AppSendToken.issue(app, "tests")
    app_rows = APIClient().get("/api/v1/notifications/app/", HTTP_X_APP_TOKEN=raw_token).data
    app_rows = app_rows["results"] if isinstance(app_rows, dict) else app_rows
    assert [row["id"] for row in app_rows] == [visible.id]


@pytest.mark.django_db
def test_hidden_notification_detail_is_404_for_owner_and_recipient(setup):
    owner, recipient, *_, notif = setup
    notif.is_visible = False
    notif.save(update_fields=["is_visible"])

    for user in (owner, recipient):
        response = _auth_client_for(user).get(f"/api/v1/notifications/{notif.id}/")
        assert response.status_code == 404


@pytest.mark.django_db
def test_hidden_notification_cannot_be_resent(setup):
    owner, *_, notif = setup
    notif.status = NotificationStatus.FAILED
    notif.is_visible = False
    notif.save(update_fields=["status", "is_visible"])

    response = _auth_client_for(owner).post(f"/api/v1/notifications/{notif.id}/send/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_stats_still_count_hidden_notifications(setup):
    owner, *_, notif = setup
    notif.is_visible = False
    notif.save(update_fields=["is_visible"])

    stats = _auth_client_for(owner).get("/api/v1/notifications/stats/").data

    assert stats == [{"status": "sent", "count": 1}]


@pytest.mark.django_db
def test_recipient_cannot_hide_notification(setup):
    _, recipient, *_, notif = setup

    response = _auth_client_for(recipient).delete(f"/api/v1/notifications/{notif.id}/")

    assert response.status_code == 404
    notif.refresh_from_db()
    assert notif.is_visible is True


@pytest.mark.django_db
def test_delete_already_hidden_notification_is_404(setup):
    owner, *_, notif = setup
    notif.is_visible = False
    notif.save(update_fields=["is_visible"])

    response = _auth_client_for(owner).delete(f"/api/v1/notifications/{notif.id}/")

    assert response.status_code == 404
    assert response.data["code"] == "notification_not_found"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status_value",
    [NotificationStatus.SCHEDULED, NotificationStatus.QUEUED, NotificationStatus.PROCESSING],
)
def test_pending_notification_cannot_be_hidden(setup, status_value):
    owner, *_, notif = setup
    notif.status = status_value
    notif.scheduled_for = timezone.now() + timedelta(hours=1)
    notif.save(update_fields=["status", "scheduled_for"])

    response = _auth_client_for(owner).delete(f"/api/v1/notifications/{notif.id}/")

    assert response.status_code == 409
    assert response.data["code"] == "notification_pending"
    notif.refresh_from_db()
    assert notif.is_visible is True
