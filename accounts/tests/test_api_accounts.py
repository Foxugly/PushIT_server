import pytest
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient
from accounts.models import User


@pytest.mark.django_db
def test_register_success():
    client = APIClient()

    response = client.post("/api/v1/auth/register/", {
        "email": "renaud@example.com",
        "password": "MotDePasseTresSolide123!",
    }, format="json")

    assert response.status_code == 201
    assert User.objects.count() == 1
    user = User.objects.first()
    assert response.data["language"] == "FR"


def test_register_preflight_allows_local_frontend_origin():
    client = APIClient()

    response = client.options(
        "/api/v1/auth/register/",
        HTTP_ORIGIN="http://127.0.0.1:4200",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type",
    )

    assert response.status_code in {200, 204}
    assert response["access-control-allow-origin"] == "http://127.0.0.1:4200"
    assert "POST" in response["access-control-allow-methods"]
    assert "content-type" in response["access-control-allow-headers"].lower()


@pytest.mark.django_db
def test_register_without_turnstile_secret_skips_captcha():
    """Rollout gate: with no TURNSTILE_SECRET_KEY configured (default), register
    succeeds without a token — the captcha is not yet enforced."""
    client = APIClient()
    response = client.post("/api/v1/auth/register/", {
        "email": "nocaptcha@example.com",
        "password": "MotDePasseTresSolide123!",
    }, format="json")
    assert response.status_code == 201
    assert User.objects.count() == 1


@pytest.mark.django_db
def test_register_with_turnstile_enabled_and_valid_token(settings, monkeypatch):
    settings.TURNSTILE_SECRET_KEY = "test-secret"
    monkeypatch.setattr(
        "accounts.api_views.verify_turnstile_token",
        lambda token, remote_ip=None: True,
    )
    client = APIClient()
    response = client.post("/api/v1/auth/register/", {
        "email": "ok@example.com",
        "password": "MotDePasseTresSolide123!",
        "turnstile_token": "tok",
    }, format="json")
    assert response.status_code == 201
    assert User.objects.count() == 1


@pytest.mark.django_db
def test_register_with_turnstile_enabled_invalid_token_returns_400(settings, monkeypatch):
    """Fail-closed: a rejected token blocks register before any DB write."""
    settings.TURNSTILE_SECRET_KEY = "test-secret"
    monkeypatch.setattr(
        "accounts.api_views.verify_turnstile_token",
        lambda token, remote_ip=None: False,
    )
    client = APIClient()
    response = client.post("/api/v1/auth/register/", {
        "email": "blocked@example.com",
        "password": "MotDePasseTresSolide123!",
        "turnstile_token": "bad",
    }, format="json")
    assert response.status_code == 400
    assert response.data["code"] == "captcha_failed"
    assert User.objects.count() == 0


@pytest.mark.django_db
def test_register_with_turnstile_enabled_missing_token_returns_400(settings, monkeypatch):
    settings.TURNSTILE_SECRET_KEY = "test-secret"
    # verify_turnstile_token would return False for an empty token anyway, but
    # assert the view short-circuits cleanly without calling Cloudflare.
    monkeypatch.setattr(
        "accounts.api_views.verify_turnstile_token",
        lambda token, remote_ip=None: bool(token),
    )
    client = APIClient()
    response = client.post("/api/v1/auth/register/", {
        "email": "notoken@example.com",
        "password": "MotDePasseTresSolide123!",
    }, format="json")
    assert response.status_code == 400
    assert response.data["code"] == "captcha_failed"
    assert User.objects.count() == 0


# --- Password reset (forgot-password + confirm) ---------------------------
FORGOT_URL = "/api/v1/auth/forgot-password/"
RESET_URL = "/api/v1/auth/reset-password/"


def _reset_tokens(user):
    return urlsafe_base64_encode(force_bytes(user.pk)), default_token_generator.make_token(user)


@pytest.mark.django_db
def test_forgot_password_unknown_email_returns_200_antileak():
    client = APIClient()
    response = client.post(FORGOT_URL, {"email": "nobody@example.com"}, format="json")
    assert response.status_code == 200


@pytest.mark.django_db
def test_forgot_password_known_email_returns_200():
    User.objects.create_user(
        email="renaud@example.com", password="MotDePasseTresSolide123!"
    )
    client = APIClient()
    response = client.post(FORGOT_URL, {"email": "renaud@example.com"}, format="json")
    assert response.status_code == 200


@pytest.mark.django_db
def test_forgot_password_with_turnstile_invalid_token_returns_400(settings, monkeypatch):
    settings.TURNSTILE_SECRET_KEY = "test-secret"
    monkeypatch.setattr(
        "accounts.api_views.verify_turnstile_token", lambda token, remote_ip=None: False
    )
    client = APIClient()
    response = client.post(
        FORGOT_URL, {"email": "renaud@example.com", "turnstile_token": "bad"}, format="json"
    )
    assert response.status_code == 400
    assert response.data["code"] == "captcha_failed"


@pytest.mark.django_db
def test_reset_password_confirm_success_changes_password():
    user = User.objects.create_user(
        email="renaud@example.com", password="OldPassword123!"
    )
    uid, token = _reset_tokens(user)
    client = APIClient()
    response = client.post(
        RESET_URL, {"uid": uid, "token": token, "password": "BrandNewPass456!"}, format="json"
    )
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.check_password("BrandNewPass456!")


@pytest.mark.django_db
def test_reset_password_confirm_invalid_token_returns_400():
    user = User.objects.create_user(
        email="renaud@example.com", password="OldPassword123!"
    )
    uid, _ = _reset_tokens(user)
    client = APIClient()
    response = client.post(
        RESET_URL, {"uid": uid, "token": "not-a-valid-token", "password": "BrandNewPass456!"},
        format="json",
    )
    assert response.status_code == 400
    assert response.data["code"] == "reset_link_invalid"
    user.refresh_from_db()
    assert user.check_password("OldPassword123!")


@pytest.mark.django_db
def test_reset_password_confirm_weak_password_returns_400():
    user = User.objects.create_user(
        email="renaud@example.com", password="OldPassword123!"
    )
    uid, token = _reset_tokens(user)
    client = APIClient()
    response = client.post(
        RESET_URL, {"uid": uid, "token": token, "password": "12345678"}, format="json"
    )
    assert response.status_code == 400
    assert response.data["code"] == "password_invalid"


@pytest.mark.django_db
def test_login_success():
    client = APIClient()
    User.objects.create_user(
        email="renaud@example.com",
        password="MotDePasseTresSolide123!",
    )

    response = client.post("/api/v1/auth/login/", {
        "email": "renaud@example.com",
        "password": "MotDePasseTresSolide123!",
    }, format="json")

    assert response.status_code == 200
    assert "access" in response.data
    assert "refresh" in response.data
    assert response.data["user"]["email"] == "renaud@example.com"
    assert response.data["user"]["language"] == "FR"


@pytest.mark.django_db
def test_login_fails_with_bad_password():
    client = APIClient()
    User.objects.create_user(
        email="renaud@example.com",
        password="MotDePasseTresSolide123!",
    )

    response = client.post("/api/v1/auth/login/", {
        "email": "renaud@example.com",
        "password": "mauvais",
    }, format="json")

    assert response.status_code == 400


@pytest.mark.django_db
def test_me_requires_authentication():
    client = APIClient()

    response = client.get("/api/v1/auth/me/")

    assert response.status_code == 401


@pytest.mark.django_db
def test_me_returns_current_user():
    client = APIClient()
    user = User.objects.create_user(
        email="renaud@example.com",
        password="MotDePasseTresSolide123!",
    )

    login_response = client.post("/api/v1/auth/login/", {
        "email": "renaud@example.com",
        "password": "MotDePasseTresSolide123!",
    }, format="json")

    access = login_response.data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.get("/api/v1/auth/me/")

    assert response.status_code == 200
    assert response.data["id"] == user.id
    assert response.data["email"] == user.email
    assert response.data["language"] == "FR"


@pytest.mark.django_db
def test_me_patch_updates_language():
    client = APIClient()
    User.objects.create_user(
        email="renaud@example.com",
        password="MotDePasseTresSolide123!",
    )

    login_response = client.post("/api/v1/auth/login/", {
        "email": "renaud@example.com",
        "password": "MotDePasseTresSolide123!",
    }, format="json")

    access = login_response.data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.patch("/api/v1/auth/me/", {
        "language": "EN",
    }, format="json")

    assert response.status_code == 200
    assert response.data["language"] == "EN"
    assert User.objects.get(email="renaud@example.com").language == "EN"
