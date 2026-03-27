from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application, QuietPeriodType
from devices.models import Device, DeviceApplicationLink, DeviceTokenStatus


VALID_PUSH_TOKEN = "token_12345678901234567890"


@pytest.mark.django_db
def test_create_update_and_delete_device_quiet_period():
    client = APIClient()
    user = User.objects.create_user(
        email="device-quiet@example.com",
        username="device-quiet",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="App")
    device = Device.objects.create(
        push_token=VALID_PUSH_TOKEN,
        push_token_status=DeviceTokenStatus.ACTIVE,
    )
    DeviceApplicationLink.objects.create(device=device, application=app)
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    start_at = timezone.now() + timedelta(hours=1)
    end_at = start_at + timedelta(hours=2)

    create_response = client.post(
        f"/api/v1/devices/{device.id}/quiet-periods/",
        {
            "name": "Focus",
            "period_type": QuietPeriodType.ONCE,
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "is_active": True,
        },
        format="json",
    )

    assert create_response.status_code == 201
    quiet_period_id = create_response.data["id"]
    assert create_response.data["period_type"] == QuietPeriodType.ONCE

    patch_response = client.patch(
        f"/api/v1/devices/{device.id}/quiet-periods/{quiet_period_id}/",
        {
            "period_type": QuietPeriodType.RECURRING,
            "recurrence_days": [0, 1, 2, 3, 4],
            "start_time": "22:00:00",
            "end_time": "08:00:00",
        },
        format="json",
    )

    assert patch_response.status_code == 200
    assert patch_response.data["period_type"] == QuietPeriodType.RECURRING
    assert patch_response.data["recurrence_days"] == [0, 1, 2, 3, 4]
    assert patch_response.data["start_at"] is None
    assert patch_response.data["end_at"] is None

    delete_response = client.delete(f"/api/v1/devices/{device.id}/quiet-periods/{quiet_period_id}/")
    assert delete_response.status_code == 204


@pytest.mark.django_db
def test_device_quiet_period_validation_error_contract():
    client = APIClient()
    user = User.objects.create_user(
        email="device-quiet2@example.com",
        username="device-quiet2",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="App")
    device = Device.objects.create(
        push_token="token_22345678901234567890",
        push_token_status=DeviceTokenStatus.ACTIVE,
    )
    DeviceApplicationLink.objects.create(device=device, application=app)
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.post(
        f"/api/v1/devices/{device.id}/quiet-periods/",
        {
            "period_type": QuietPeriodType.RECURRING,
            "is_active": True,
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "validation_error"
    assert "recurrence_days" in response.data["errors"]
    assert "start_time" in response.data["errors"]
    assert "end_time" in response.data["errors"]
