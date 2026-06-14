import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application
from devices.models import Device, DeviceApplicationLink, DeviceTokenStatus, UnlinkSource

PUSH = "token_12345678901234567890"


def _auth(user: User) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def _setup(email="renaud@example.com"):
    user = User.objects.create_user(email=email, password="MotDePasseTresSolide123!")
    app = Application.objects.create(owner=user, name="Mon App")
    device = Device.objects.create(
        user=user, push_token=PUSH, push_token_status=DeviceTokenStatus.ACTIVE
    )
    link = DeviceApplicationLink.objects.create(device=device, application=app, is_active=True)
    return user, app, device, link


@pytest.mark.django_db
def test_device_button_unlink_records_source_and_timestamp():
    user, app, _device, link = _setup()
    raw_token = app.set_new_app_token()
    app.save()

    resp = _auth(user).post(
        "/api/v1/devices/unlink/",
        {"app_token": raw_token, "push_token": PUSH},
        format="json",
    )
    assert resp.status_code == 200 and resp.data["unlinked"] is True

    link.refresh_from_db()
    assert link.is_active is False
    assert link.unlink_source == UnlinkSource.DEVICE_BUTTON
    assert link.unlinked_at is not None


@pytest.mark.django_db
def test_inbox_unlink_records_source_inbox():
    user, app, _device, link = _setup()

    resp = _auth(user).post(
        "/api/v1/devices/unlink-app/",
        {"push_token": PUSH, "application_id": app.id},
        format="json",
    )
    assert resp.status_code == 200 and resp.data["unlinked"] is True

    link.refresh_from_db()
    assert link.is_active is False
    assert link.unlink_source == UnlinkSource.INBOX
    assert link.unlinked_at is not None


@pytest.mark.django_db
def test_takeover_by_another_user_records_source_takeover():
    _user, app, device, link = _setup(email="old@example.com")
    other = User.objects.create_user(email="new@example.com", password="MotDePasseTresSolide123!")

    resp = _auth(other).post(
        "/api/v1/devices/identify/",
        {"device_name": "New", "platform": "android", "push_token": PUSH},
        format="json",
    )
    assert resp.status_code == 200

    link.refresh_from_db()
    device.refresh_from_db()
    assert device.user == other
    assert link.is_active is False
    assert link.unlink_source == UnlinkSource.TAKEOVER
    assert link.unlinked_at is not None


@pytest.mark.django_db
def test_device_detail_surfaces_unlinked_applications():
    user, app, device, _link = _setup()
    client = _auth(user)
    client.post(
        "/api/v1/devices/unlink-app/",
        {"push_token": PUSH, "application_id": app.id},
        format="json",
    )

    resp = client.get(f"/api/v1/devices/{device.id}/")
    assert resp.status_code == 200
    assert resp.data["application_ids"] == []
    unlinked = resp.data["unlinked_applications"]
    assert len(unlinked) == 1
    assert unlinked[0]["application_id"] == app.id
    assert unlinked[0]["application_name"] == "Mon App"
    assert unlinked[0]["unlink_source"] == UnlinkSource.INBOX
    assert unlinked[0]["unlinked_at"] is not None


@pytest.mark.django_db
def test_relink_clears_the_unlink_audit():
    user, app, _device, link = _setup()
    raw_token = app.set_new_app_token()
    app.save()
    client = _auth(user)

    # Unlink (records audit), then re-link via QR/app-token (must clear it).
    client.post("/api/v1/devices/unlink/", {"app_token": raw_token, "push_token": PUSH}, format="json")
    link.refresh_from_db()
    assert link.unlinked_at is not None and link.unlink_source == UnlinkSource.DEVICE_BUTTON

    resp = client.post(
        "/api/v1/devices/link/",
        {"app_token": raw_token, "push_token": PUSH, "device_name": "X", "platform": "android"},
        format="json",
    )
    assert resp.status_code == 200

    link.refresh_from_db()
    assert link.is_active is True
    assert link.unlinked_at is None, "reactivation must clear the stale unlink time"
    assert link.unlink_source == "", "reactivation must clear the stale unlink source"
