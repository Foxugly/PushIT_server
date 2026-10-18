"""Hardening of the account flows: reset revokes sessions, confirmation links are
single-use and distinct from reset links, magic links are consumed atomically."""
import pytest
from django.contrib.auth.tokens import default_token_generator
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient

from accounts.magic_link import verify_magic_link
from accounts.models import MagicLinkToken, User

LOGIN_URL = "/api/v1/auth/login/"
REFRESH_URL = "/api/v1/auth/refresh/"
RESET_URL = "/api/v1/auth/reset-password/"
CONFIRM_URL = "/api/v1/auth/email/confirm/"
PASSWORD = "MotDePasseTresSolide123!"


def _confirm_token(user):
    from accounts.email_confirmation import email_confirmation_token_generator

    return email_confirmation_token_generator.make_token(user)


def _uid(user):
    return urlsafe_base64_encode(force_bytes(user.pk))


# --- I2: password reset revokes outstanding refresh tokens -----------------


@pytest.mark.django_db
def test_password_reset_blacklists_existing_refresh_tokens():
    user = User.objects.create_user(email="renaud@example.com", password=PASSWORD, email_confirmed=True)
    client = APIClient()
    refreshes = []
    for _ in range(2):  # two sessions (e.g. web + mobile)
        login = client.post(LOGIN_URL, {"email": user.email, "password": PASSWORD}, format="json")
        assert login.status_code == 200
        refreshes.append(login.data["refresh"])

    reset = client.post(
        RESET_URL,
        {"uid": _uid(user), "token": default_token_generator.make_token(user), "password": "BrandNewPass456!"},
        format="json",
    )
    assert reset.status_code == 200

    for refresh in refreshes:
        response = client.post(REFRESH_URL, {"refresh": refresh}, format="json")
        assert response.status_code == 401
