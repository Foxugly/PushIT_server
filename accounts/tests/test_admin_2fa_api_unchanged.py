"""The admin 2FA (admin_2fa/) must not change anything for API users, staff included."""
import pytest
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework.test import APIClient

from accounts.models import User

PASSWORD = "MotDePasseTresSolide123!"


@pytest.mark.django_db
@pytest.mark.parametrize("enrolled", [False, True])
def test_staff_uses_the_api_with_a_password_only(enrolled):
    user = User.objects.create_user(
        email="ops@example.com", password=PASSWORD, email_confirmed=True, is_staff=True
    )
    if enrolled:
        TOTPDevice.objects.create(user=user, name="admin-2fa", confirmed=True)

    client = APIClient()
    login = client.post("/api/v1/auth/login/", {"email": user.email, "password": PASSWORD}, format="json")
    assert login.status_code == 200
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}")
    me = client.get("/api/v1/auth/me/")
    assert me.status_code == 200
    assert me.json()["email"] == "ops@example.com"
    assert me.json()["is_staff"] is True


@pytest.mark.django_db
def test_health_stays_public():
    assert APIClient().get("/health/live/").status_code == 200
