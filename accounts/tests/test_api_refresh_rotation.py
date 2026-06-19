"""Tests for JWT refresh-token rotation + blacklist.

SIMPLE_JWT is configured with ROTATE_REFRESH_TOKENS + BLACKLIST_AFTER_ROTATION
(config/settings/base.py). These tests pin that contract: each refresh issues a
new refresh token and *invalidates the old one*. This is a known fleet footgun —
a client that doesn't persist the rotated refresh token gets ejected, and a
regression that disabled rotation/blacklisting would silently widen the window a
stolen refresh token stays valid.
"""

import pytest
from rest_framework.test import APIClient

from accounts.models import User

REFRESH_URL = "/api/v1/auth/refresh/"
LOGIN_URL = "/api/v1/auth/login/"


def _make_confirmed_user(email="rotate@example.com"):
    return User.objects.create_user(
        email=email,
        password="MotDePasseTresSolide123!",
        email_confirmed=True,
    )


@pytest.mark.django_db
def test_refresh_rotates_and_returns_new_refresh_token():
    _make_confirmed_user()
    client = APIClient()

    login = client.post(
        LOGIN_URL,
        {"email": "rotate@example.com", "password": "MotDePasseTresSolide123!"},
        format="json",
    )
    assert login.status_code == 200
    original_refresh = login.data["refresh"]

    resp = client.post(REFRESH_URL, {"refresh": original_refresh}, format="json")
    assert resp.status_code == 200
    assert "access" in resp.data
    # Rotation is on: a brand-new refresh token must come back, distinct from the
    # one we sent.
    assert "refresh" in resp.data
    assert resp.data["refresh"] != original_refresh


@pytest.mark.django_db
def test_old_refresh_token_is_rejected_after_rotation():
    _make_confirmed_user()
    client = APIClient()

    login = client.post(
        LOGIN_URL,
        {"email": "rotate@example.com", "password": "MotDePasseTresSolide123!"},
        format="json",
    )
    original_refresh = login.data["refresh"]

    # First use rotates + blacklists the original token.
    first = client.post(REFRESH_URL, {"refresh": original_refresh}, format="json")
    assert first.status_code == 200

    # Reusing the now-blacklisted original token must fail.
    replay = client.post(REFRESH_URL, {"refresh": original_refresh}, format="json")
    assert replay.status_code == 401


@pytest.mark.django_db
def test_rotated_refresh_token_still_works():
    _make_confirmed_user()
    client = APIClient()

    login = client.post(
        LOGIN_URL,
        {"email": "rotate@example.com", "password": "MotDePasseTresSolide123!"},
        format="json",
    )
    refresh = login.data["refresh"]

    first = client.post(REFRESH_URL, {"refresh": refresh}, format="json")
    assert first.status_code == 200
    rotated = first.data["refresh"]

    # The *new* (rotated) token is the valid one going forward.
    second = client.post(REFRESH_URL, {"refresh": rotated}, format="json")
    assert second.status_code == 200
