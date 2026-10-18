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


# --- I3: email-confirmation token ------------------------------------------


@pytest.mark.django_db
def test_confirmation_link_is_single_use():
    user = User.objects.create_user(email="pending@example.com", password=PASSWORD)
    token = _confirm_token(user)
    client = APIClient()

    first = client.post(CONFIRM_URL, {"uid": _uid(user), "token": token}, format="json")
    assert first.status_code == 200
    assert "access" in first.data

    second = client.post(CONFIRM_URL, {"uid": _uid(user), "token": token}, format="json")
    assert second.status_code == 400
    assert second.data["code"] == "confirmation_link_invalid"
    assert "access" not in second.data


@pytest.mark.django_db
def test_confirmation_token_is_not_accepted_as_reset_token():
    user = User.objects.create_user(email="pending@example.com", password=PASSWORD)
    token = _confirm_token(user)
    response = APIClient().post(
        RESET_URL, {"uid": _uid(user), "token": token, "password": "BrandNewPass456!"}, format="json"
    )
    assert response.status_code == 400
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


@pytest.mark.django_db
def test_reset_token_is_not_accepted_as_confirmation_token():
    user = User.objects.create_user(email="pending@example.com", password=PASSWORD)
    token = default_token_generator.make_token(user)
    response = APIClient().post(CONFIRM_URL, {"uid": _uid(user), "token": token}, format="json")
    assert response.status_code == 400
    user.refresh_from_db()
    assert user.email_confirmed is False


@pytest.mark.django_db
def test_confirmation_of_already_confirmed_account_issues_no_tokens():
    user = User.objects.create_user(email="pending@example.com", password=PASSWORD)
    token = _confirm_token(user)
    user.email_confirmed = True
    user.save(update_fields=["email_confirmed"])
    response = APIClient().post(CONFIRM_URL, {"uid": _uid(user), "token": token}, format="json")
    assert response.status_code == 400
    assert "access" not in response.data
