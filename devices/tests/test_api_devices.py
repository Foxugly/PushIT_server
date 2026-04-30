import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

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

    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.get("/api/v1/devices/999999/")

    assert response.status_code == 404
    assert response.data["code"] == "device_not_found"
    assert response.data["detail"] == "Device not found."
