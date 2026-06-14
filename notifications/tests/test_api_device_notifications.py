import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application
from devices.models import Device, DeviceApplicationLink, DeviceTokenStatus
from notifications.models import Notification, NotificationDelivery, NotificationStatus

URL = "/api/v1/notifications/device/"


def _auth(client: APIClient, user: User) -> None:
    access = RefreshToken.for_user(user).access_token
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")


@pytest.mark.django_db
def test_device_inbox_returns_notifications_delivered_to_the_device():
    client = APIClient()
    owner = User.objects.create_user(email="owner@example.com", password="MotDePasseTresSolide123!")
    recipient = User.objects.create_user(email="rcpt@example.com", password="MotDePasseTresSolide123!")
    # The app belongs to the owner; the recipient's device is merely linked to it.
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

    _auth(client, recipient)
    resp = client.get(URL, {"push_token": "fcm_recipient"})

    assert resp.status_code == 200
    assert isinstance(resp.data, list)
    assert len(resp.data) == 1
    assert resp.data[0]["title"] == "Hello"
    assert resp.data[0]["application_name"] == "Acme"


@pytest.mark.django_db
def test_device_inbox_is_empty_for_unknown_or_missing_push_token():
    client = APIClient()
    user = User.objects.create_user(email="u@example.com", password="MotDePasseTresSolide123!")
    _auth(client, user)

    assert client.get(URL, {"push_token": "does-not-exist"}).data == []
    assert client.get(URL).data == []


@pytest.mark.django_db
def test_device_inbox_does_not_leak_another_users_device():
    client = APIClient()
    owner = User.objects.create_user(email="owner2@example.com", password="MotDePasseTresSolide123!")
    other = User.objects.create_user(email="other@example.com", password="MotDePasseTresSolide123!")
    app = Application.objects.create(owner=owner, name="Acme2")
    # Device belongs to `owner`, not to `other`.
    device = Device.objects.create(
        user=owner, push_token="fcm_owner", push_token_status=DeviceTokenStatus.ACTIVE
    )
    notif = Notification.objects.create(
        application=app, title="Secret", message="msg", status=NotificationStatus.SENT
    )
    NotificationDelivery.objects.create(notification=notif, device=device)

    # `other` authenticates but asks for `owner`'s device push token → no leak.
    _auth(client, other)
    assert client.get(URL, {"push_token": "fcm_owner"}).data == []
