import io
import tempfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application

_TMP_MEDIA = tempfile.mkdtemp()


def _auth(client: APIClient, user: User) -> None:
    access = RefreshToken.for_user(user).access_token
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")


def _png() -> SimpleUploadedFile:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (16, 185, 129)).save(buf, format="PNG")
    return SimpleUploadedFile("logo.png", buf.getvalue(), content_type="image/png")


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
@pytest.mark.django_db
def test_upload_then_read_then_delete_application_logo():
    client = APIClient()
    user = User.objects.create_user(email="logo@example.com", password="MotDePasseTresSolide123!")
    app = Application.objects.create(owner=user, name="Acme")
    _auth(client, user)

    # No logo initially.
    assert client.get(f"/api/v1/apps/{app.id}/").data["logo"] is None

    # Upload.
    resp = client.post(f"/api/v1/apps/{app.id}/logo/", {"logo": _png()}, format="multipart")
    assert resp.status_code == 200
    assert resp.data["logo"]  # absolute URL
    assert "/media/app_logo/" in resp.data["logo"]

    # Read back on detail.
    assert client.get(f"/api/v1/apps/{app.id}/").data["logo"]

    # Delete.
    assert client.delete(f"/api/v1/apps/{app.id}/logo/").status_code == 204
    assert client.get(f"/api/v1/apps/{app.id}/").data["logo"] is None


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
@pytest.mark.django_db
def test_logo_upload_requires_an_image():
    client = APIClient()
    user = User.objects.create_user(email="logo2@example.com", password="MotDePasseTresSolide123!")
    app = Application.objects.create(owner=user, name="Acme2")
    _auth(client, user)

    resp = client.post(f"/api/v1/apps/{app.id}/logo/", {}, format="multipart")
    assert resp.status_code == 400


def _png_of(width: int, height: int) -> SimpleUploadedFile:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (16, 185, 129)).save(buf, format="PNG")
    return SimpleUploadedFile("logo.png", buf.getvalue(), content_type="image/png")


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
@pytest.mark.django_db
def test_logo_upload_rejects_oversized_dimensions():
    client = APIClient()
    user = User.objects.create_user(email="logo3@example.com", password="MotDePasseTresSolide123!")
    app = Application.objects.create(owner=user, name="Acme3")
    _auth(client, user)

    # 4096x4096 is over the 2048 px per-side cap.
    resp = client.post(
        f"/api/v1/apps/{app.id}/logo/", {"logo": _png_of(4096, 4096)}, format="multipart"
    )
    assert resp.status_code == 400
    assert "logo" in resp.data["errors"]


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
@pytest.mark.django_db
def test_logo_upload_rejects_oversized_file():
    client = APIClient()
    user = User.objects.create_user(email="logo4@example.com", password="MotDePasseTresSolide123!")
    app = Application.objects.create(owner=user, name="Acme4")
    _auth(client, user)

    # A valid PNG whose raw bytes exceed the ~2 MB cap (in-bounds dimensions, but
    # padded with incompressible random noise so it weighs more than 2 MB).
    import os

    buf = io.BytesIO()
    Image.frombytes("RGB", (1024, 1024), os.urandom(1024 * 1024 * 3)).save(buf, format="PNG")
    payload = buf.getvalue()
    assert len(payload) > 2 * 1024 * 1024  # sanity: the fixture really is too big
    big = SimpleUploadedFile("logo.png", payload, content_type="image/png")

    resp = client.post(f"/api/v1/apps/{app.id}/logo/", {"logo": big}, format="multipart")
    assert resp.status_code == 400
    assert "logo" in resp.data["errors"]


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
@pytest.mark.django_db
def test_logo_upload_accepts_normal_image():
    client = APIClient()
    user = User.objects.create_user(email="logo5@example.com", password="MotDePasseTresSolide123!")
    app = Application.objects.create(owner=user, name="Acme5")
    _auth(client, user)

    resp = client.post(
        f"/api/v1/apps/{app.id}/logo/", {"logo": _png_of(256, 256)}, format="multipart"
    )
    assert resp.status_code == 200
    assert resp.data["logo"]
