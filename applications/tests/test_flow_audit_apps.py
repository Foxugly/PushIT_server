"""Audit 2026-10-19 — items 5 (enrolment errors are 403) and 8 (PATCH logo URL)."""
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from accounts.models import User
from applications.models import Application

PUSH = "token_enrolment_403_1111111111"


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def user(db):
    return User.objects.create_user(email="u@example.com", password="pw12345678")


@pytest.mark.parametrize(
    "path,payload_extra,expected_code",
    [
        ("/api/v1/devices/link/", {}, "app_token_missing"),
        ("/api/v1/devices/link/", {"app_token": "apk_doesnotexist"}, "app_token_invalid"),
        ("/api/v1/devices/link/", {"app_token": "garbage"}, "app_token_invalid_format"),
        ("/api/v1/devices/unlink/", {"app_token": "apk_doesnotexist"}, "app_token_invalid"),
    ],
)
def test_enrolment_problems_are_403_with_a_valid_jwt(user, path, payload_extra, expected_code):
    r = _client(user).post(path, {"push_token": PUSH, **payload_extra}, format="json")
    assert r.status_code == 403, r.content
    assert r.json()["code"] == expected_code


def test_inactive_application_enrolment_is_403(user):
    app = Application.objects.create(owner=user, name="App", is_active=False)
    r = _client(user).post(
        "/api/v1/devices/link/", {"push_token": PUSH, "app_token": app.enrolment_code}, format="json"
    )
    assert r.status_code == 403
    assert r.json()["code"] == "app_token_inactive"


def test_missing_jwt_is_still_401(db):
    r = APIClient().post("/api/v1/devices/link/", {"push_token": PUSH, "app_token": "apk_x"}, format="json")
    assert r.status_code == 401


# ------------------------------------------------------------------ item 8


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, format="PNG")
    return SimpleUploadedFile("logo.png", buf.getvalue(), content_type="image/png")


def test_patch_app_returns_absolute_logo_url_like_get(user, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    app = Application.objects.create(owner=user, name="App")
    app.logo.save("logo.png", _png(), save=True)
    client = _client(user)

    get_logo = client.get(f"/api/v1/apps/{app.id}/").json()["logo"]
    patched = client.patch(f"/api/v1/apps/{app.id}/", {"name": "Renamed"}, format="json")

    assert patched.status_code == 200
    assert get_logo.startswith("http")
    assert patched.json()["logo"] == get_logo
