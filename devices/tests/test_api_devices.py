import pytest
from rest_framework.test import APIClient

from accounts.models import User
from applications.models import Application


@pytest.mark.django_db
def test_device_detail_not_found_returns_device_not_found_code():
    client = APIClient()
    user = User.objects.create_user(
        email="owner@example.com",
        username="owner",
        password="MotDePasseTresSolide123!",
    )
    Application.objects.create(owner=user, name="Mon App")

    login_response = client.post(
        "/api/v1/auth/login/",
        {
            "email": "owner@example.com",
            "password": "MotDePasseTresSolide123!",
        },
        format="json",
    )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login_response.data['access']}")

    response = client.get("/api/v1/devices/999999/")

    assert response.status_code == 404
    assert response.data["code"] == "device_not_found"
    assert response.data["detail"] == "Device not found."
