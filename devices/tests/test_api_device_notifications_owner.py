import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application
from devices.models import Device, DeviceApplicationLink, DeviceTokenStatus
from notifications.models import DeliveryStatus, Notification, NotificationDelivery, NotificationStatus


def _auth(client: APIClient, user: User) -> None:
    access = RefreshToken.for_user(user).access_token
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")


def _url(device_id: int) -> str:
    return f"/api/v1/devices/{device_id}/notifications/"


@pytest.mark.django_db
def test_owner_sees_device_notifications_paginated_with_delivery_status():
    client = APIClient()
    owner = User.objects.create_user(email="owner@example.com", password="MotDePasseTresSolide123!")
    app = Application.objects.create(owner=owner, name="Acme")
    device = Device.objects.create(
        user=owner, push_token="fcm_d1", push_token_status=DeviceTokenStatus.ACTIVE
    )
    DeviceApplicationLink.objects.create(device=device, application=app)
    notif = Notification.objects.create(
        application=app, title="Hello", message="World", status=NotificationStatus.SENT
    )
    NotificationDelivery.objects.create(
        notification=notif, device=device, status=DeliveryStatus.SENT, attempt_count=1
    )

    _auth(client, owner)
    resp = client.get(_url(device.id))

    assert resp.status_code == 200
    # Paginated envelope (not a bare array).
    assert set(resp.data.keys()) >= {"count", "results"}
    assert resp.data["count"] == 1
    row = resp.data["results"][0]
    assert row["title"] == "Hello"
    assert row["application_id"] == app.id
    assert row["delivery_status"] == DeliveryStatus.SENT
    assert row["delivery_attempt_count"] == 1


@pytest.mark.django_db
def test_device_notifications_filter_by_application_and_no_cross_owner_leak():
    client = APIClient()
    owner = User.objects.create_user(email="owner2@example.com", password="MotDePasseTresSolide123!")
    other = User.objects.create_user(email="other@example.com", password="MotDePasseTresSolide123!")
    app_a = Application.objects.create(owner=owner, name="A")
    app_b = Application.objects.create(owner=owner, name="B")
    app_foreign = Application.objects.create(owner=other, name="Foreign")
    device = Device.objects.create(
        user=owner, push_token="fcm_d2", push_token_status=DeviceTokenStatus.ACTIVE
    )
    for app in (app_a, app_b, app_foreign):
        DeviceApplicationLink.objects.create(device=device, application=app)
        notif = Notification.objects.create(
            application=app, title=f"n-{app.name}", message="m", status=NotificationStatus.SENT
        )
        NotificationDelivery.objects.create(notification=notif, device=device)

    _auth(client, owner)

    # No filter → only the owner's two apps (foreign app's notif is not leaked).
    resp = client.get(_url(device.id))
    assert resp.status_code == 200
    assert {r["title"] for r in resp.data["results"]} == {"n-A", "n-B"}

    # Filter by application_id.
    resp_a = client.get(_url(device.id), {"application_id": app_a.id})
    assert {r["title"] for r in resp_a.data["results"]} == {"n-A"}


@pytest.mark.django_db
def test_device_notifications_404_for_inaccessible_device():
    client = APIClient()
    owner = User.objects.create_user(email="o3@example.com", password="MotDePasseTresSolide123!")
    stranger = User.objects.create_user(email="s3@example.com", password="MotDePasseTresSolide123!")
    app = Application.objects.create(owner=owner, name="Acme3")
    device = Device.objects.create(
        user=owner, push_token="fcm_d3", push_token_status=DeviceTokenStatus.ACTIVE
    )
    DeviceApplicationLink.objects.create(device=device, application=app)

    # A stranger (owns nothing this device is linked to) gets 404.
    _auth(client, stranger)
    assert client.get(_url(device.id)).status_code == 404
