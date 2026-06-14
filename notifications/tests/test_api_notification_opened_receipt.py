import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application
from devices.models import Device, DeviceApplicationLink, DeviceTokenStatus
from notifications.models import Notification, NotificationDelivery, NotificationStatus


def _url(notification_id: int) -> str:
    return f"/api/v1/notifications/{notification_id}/opened/"


def _auth(client: APIClient, user: User) -> None:
    access = RefreshToken.for_user(user).access_token
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")


def _setup(push_token="fcm_recipient", recipient_email="rcpt@example.com"):
    owner = User.objects.create_user(email="owner@example.com", password="MotDePasseTresSolide123!")
    recipient = User.objects.create_user(email=recipient_email, password="MotDePasseTresSolide123!")
    app = Application.objects.create(owner=owner, name="Acme")
    device = Device.objects.create(
        user=recipient, push_token=push_token, push_token_status=DeviceTokenStatus.ACTIVE
    )
    DeviceApplicationLink.objects.create(device=device, application=app)
    notif = Notification.objects.create(
        application=app, title="Hello", message="World", status=NotificationStatus.SENT
    )
    delivery = NotificationDelivery.objects.create(notification=notif, device=device)
    return recipient, device, notif, delivery


@pytest.mark.django_db
def test_opened_sets_opened_and_delivered_timestamps():
    client = APIClient()
    recipient, _device, notif, delivery = _setup()
    assert delivery.opened_at is None and delivery.delivered_at is None

    _auth(client, recipient)
    resp = client.post(_url(notif.id), {"push_token": "fcm_recipient"}, format="json")

    assert resp.status_code == 200
    assert resp.data["status"] == "ok"
    delivery.refresh_from_db()
    assert delivery.opened_at is not None
    # Opening implies delivery: delivered_at is backfilled to the same moment.
    assert delivery.delivered_at == delivery.opened_at


@pytest.mark.django_db
def test_opened_is_idempotent_and_keeps_first_timestamp():
    client = APIClient()
    recipient, _device, notif, delivery = _setup()
    _auth(client, recipient)

    first = client.post(_url(notif.id), {"push_token": "fcm_recipient"}, format="json")
    delivery.refresh_from_db()
    first_opened_at = delivery.opened_at

    second = client.post(_url(notif.id), {"push_token": "fcm_recipient"}, format="json")
    delivery.refresh_from_db()

    assert first.status_code == 200 and second.status_code == 200
    assert delivery.opened_at == first_opened_at, "re-opening must not move opened_at"


@pytest.mark.django_db
def test_opened_does_not_overwrite_existing_delivered_at():
    from datetime import datetime, timezone as dt_timezone

    client = APIClient()
    recipient, _device, notif, delivery = _setup()
    earlier = datetime(2026, 6, 1, 8, 0, tzinfo=dt_timezone.utc)
    delivery.delivered_at = earlier
    delivery.save(update_fields=["delivered_at"])

    _auth(client, recipient)
    resp = client.post(_url(notif.id), {"push_token": "fcm_recipient"}, format="json")

    assert resp.status_code == 200
    delivery.refresh_from_db()
    assert delivery.delivered_at == earlier, "an earlier real delivery time must be kept"
    assert delivery.opened_at is not None


@pytest.mark.django_db
def test_opened_requires_push_token():
    client = APIClient()
    recipient, _device, notif, _delivery = _setup()
    _auth(client, recipient)
    resp = client.post(_url(notif.id), {}, format="json")
    assert resp.status_code == 400


@pytest.mark.django_db
def test_opened_404_when_notification_not_delivered_to_this_device():
    client = APIClient()
    recipient, _device, _notif, _delivery = _setup()
    # A different notification (no delivery to this device).
    other_app = Application.objects.get()
    orphan = Notification.objects.create(
        application=other_app, title="Orphan", message="m", status=NotificationStatus.SENT
    )
    _auth(client, recipient)
    resp = client.post(_url(orphan.id), {"push_token": "fcm_recipient"}, format="json")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_opened_404_for_another_users_device_token():
    client = APIClient()
    _recipient, _device, notif, _delivery = _setup()
    intruder = User.objects.create_user(email="intruder@example.com", password="MotDePasseTresSolide123!")
    _auth(client, intruder)
    # Intruder presents someone else's push token → not their device → 404, no receipt.
    resp = client.post(_url(notif.id), {"push_token": "fcm_recipient"}, format="json")
    assert resp.status_code == 404
