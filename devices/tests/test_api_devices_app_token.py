import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application
from devices.models import Device, DeviceApplicationLink


def _auth_client_for(user: User) -> APIClient:
    client = APIClient()
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client


@pytest.mark.django_db
def test_identify_device_creates_user_device_without_app_links():
    user = User.objects.create_user(
        email="renaud@example.com",
        username="renaud",
        password="MotDePasseTresSolide123!",
    )
    client = _auth_client_for(user)

    response = client.post(
        "/api/v1/devices/identify/",
        {
            "device_name": "Samsung",
            "platform": "android",
            "push_token": "token_12345678901234567890",
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["status"] == "ok"
    assert response.data["device_created"] is True
    assert response.data["linked_applications"] == []

    device = Device.objects.get()
    assert response.data["device_id"] == device.id
    assert device.user == user
    assert device.device_name == "Samsung"
    assert device.push_token_status == "active"


@pytest.mark.django_db
def test_identify_known_device_returns_linked_applications_for_authenticated_user():
    user = User.objects.create_user(
        email="renaud@example.com",
        username="renaud",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(
        owner=user,
        name="Mon App",
        description="Primary app",
    )
    inactive_app = Application.objects.create(owner=user, name="Inactive App", is_active=False)
    device = Device.objects.create(
        user=user,
        device_name="Old name",
        platform="android",
        push_token="token_12345678901234567890",
        push_token_status="active",
    )
    DeviceApplicationLink.objects.create(device=device, application=app, is_active=True)
    DeviceApplicationLink.objects.create(device=device, application=inactive_app, is_active=True)

    client = _auth_client_for(user)
    response = client.post(
        "/api/v1/devices/identify/",
        {
            "device_name": "New name",
            "platform": "android",
            "push_token": "token_12345678901234567890",
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["device_id"] == device.id
    assert response.data["device_created"] is False
    assert response.data["linked_applications"] == [
        {
            "id": app.id,
            "name": "Mon App",
            "description": "Primary app",
            "is_active": True,
            "linked_at": response.data["linked_applications"][0]["linked_at"],
        }
    ]

    device.refresh_from_db()
    assert device.user == user
    assert device.device_name == "New name"


@pytest.mark.django_db
def test_identify_existing_device_from_other_user_does_not_return_previous_links():
    previous_user = User.objects.create_user(
        email="old@example.com",
        username="old",
        password="MotDePasseTresSolide123!",
    )
    current_user = User.objects.create_user(
        email="new@example.com",
        username="new",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=previous_user, name="Previous App")
    device = Device.objects.create(
        user=previous_user,
        device_name="Old device",
        platform="android",
        push_token="token_12345678901234567890",
        push_token_status="active",
    )
    link = DeviceApplicationLink.objects.create(device=device, application=app, is_active=True)

    client = _auth_client_for(current_user)
    response = client.post(
        "/api/v1/devices/identify/",
        {
            "device_name": "New device",
            "platform": "android",
            "push_token": "token_12345678901234567890",
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["device_id"] == device.id
    assert response.data["device_created"] is False
    assert response.data["linked_applications"] == []

    device.refresh_from_db()
    link.refresh_from_db()
    assert device.user == current_user
    assert link.is_active is False


@pytest.mark.django_db
def test_link_device_requires_user_auth_and_app_token():
    user = User.objects.create_user(
        email="renaud@example.com",
        username="renaud",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Mon App")
    raw_token = app.set_new_app_token()
    app.save()
    client = _auth_client_for(user)

    response = client.post(
        "/api/v1/devices/link/",
        {
            "app_token": raw_token,
            "device_name": "Samsung",
            "platform": "android",
            "push_token": "token_12345678901234567890",
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["status"] == "ok"
    assert Device.objects.count() == 1
    assert Device.objects.get().user == user
    assert DeviceApplicationLink.objects.count() == 1


@pytest.mark.django_db
def test_link_device_reactivates_existing_inactive_link():
    user = User.objects.create_user(
        email="renaud@example.com",
        username="renaud",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Mon App")
    raw_token = app.set_new_app_token()
    app.save()
    client = _auth_client_for(user)

    device = Device.objects.create(
        user=user,
        device_name="Samsung",
        platform="android",
        push_token="token_12345678901234567890",
        push_token_status="active",
    )
    link = DeviceApplicationLink.objects.create(
        device=device,
        application=app,
        is_active=False,
    )

    response = client.post(
        "/api/v1/devices/link/",
        {
            "app_token": raw_token,
            "device_name": "Samsung",
            "platform": "android",
            "push_token": "token_12345678901234567890",
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["status"] == "ok"

    link.refresh_from_db()
    assert link.is_active is True

    assert Device.objects.count() == 1
    assert DeviceApplicationLink.objects.count() == 1
