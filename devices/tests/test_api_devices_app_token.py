import pytest
from rest_framework.test import APIClient

from accounts.models import User
from applications.models import Application
from devices.models import Device, DeviceApplicationLink


@pytest.mark.django_db
def test_link_device_with_app_token():
    client = APIClient()

    user = User.objects.create_user(
        email="renaud@example.com",
        username="renaud",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Mon App")
    raw_token = app.set_new_app_token()
    app.save()

    response = client.post(
        "/api/v1/devices/link/",
        {
            "device_name": "Samsung",
            "platform": "android",
            "push_token": "token_12345678901234567890",
        },
        format="json",
        HTTP_X_APP_TOKEN=raw_token,
    )

    assert response.status_code == 200
    assert response.data["status"] == "ok"
    assert Device.objects.count() == 1
    assert DeviceApplicationLink.objects.count() == 1


@pytest.mark.django_db
def test_link_device_reactivates_existing_inactive_link():
    client = APIClient()

    user = User.objects.create_user(
        email="renaud@example.com",
        username="renaud",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Mon App")
    raw_token = app.set_new_app_token()
    app.save()

    device = Device.objects.create(
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
            "device_name": "Samsung",
            "platform": "android",
            "push_token": "token_12345678901234567890",
        },
        format="json",
        HTTP_X_APP_TOKEN=raw_token,
    )

    assert response.status_code == 200
    assert response.data["status"] == "ok"

    link.refresh_from_db()
    assert link.is_active is True

    assert Device.objects.count() == 1
    assert DeviceApplicationLink.objects.count() == 1