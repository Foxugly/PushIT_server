import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application
from devices.models import Device, DeviceApplicationLink, DeviceTokenStatus

URL = "/api/v1/devices/unlink-app/"


def _auth(client: APIClient, user: User) -> None:
    access = RefreshToken.for_user(user).access_token
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")


@pytest.mark.django_db
def test_unlink_by_application_deactivates_the_link_and_is_idempotent():
    client = APIClient()
    user = User.objects.create_user(email="u@example.com", password="MotDePasseTresSolide123!")
    app = Application.objects.create(owner=user, name="Acme")
    device = Device.objects.create(
        user=user, push_token="fcm_x", push_token_status=DeviceTokenStatus.ACTIVE
    )
    link = DeviceApplicationLink.objects.create(device=device, application=app, is_active=True)

    _auth(client, user)
    resp = client.post(URL, {"push_token": "fcm_x", "application_id": app.id}, format="json")

    assert resp.status_code == 200
    assert resp.data["unlinked"] is True
    assert resp.data["application_id"] == app.id
    link.refresh_from_db()
    assert link.is_active is False

    # Idempotent: a second unlink reports nothing to do.
    resp2 = client.post(URL, {"push_token": "fcm_x", "application_id": app.id}, format="json")
    assert resp2.status_code == 200
    assert resp2.data["unlinked"] is False


@pytest.mark.django_db
def test_unlink_by_application_does_not_touch_another_users_device():
    client = APIClient()
    owner = User.objects.create_user(email="owner@example.com", password="MotDePasseTresSolide123!")
    other = User.objects.create_user(email="other@example.com", password="MotDePasseTresSolide123!")
    app = Application.objects.create(owner=owner, name="Acme")
    device = Device.objects.create(
        user=owner, push_token="fcm_owner", push_token_status=DeviceTokenStatus.ACTIVE
    )
    link = DeviceApplicationLink.objects.create(device=device, application=app, is_active=True)

    _auth(client, other)
    resp = client.post(URL, {"push_token": "fcm_owner", "application_id": app.id}, format="json")

    assert resp.status_code == 200
    assert resp.data["unlinked"] is False
    link.refresh_from_db()
    assert link.is_active is True
