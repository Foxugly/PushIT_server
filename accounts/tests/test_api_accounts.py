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
    # Registration no longer returns a profile/tokens — it's pending email confirmation.
    assert response.data["code"] == "registration_pending_verification"
    assert response.data["email"] == "renaud@example.com"
    assert User.objects.count() == 1
    user = User.objects.first()
    assert user.email_confirmed is False


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
        email_confirmed=True,
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
        email_confirmed=True,
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
        email_confirmed=True,
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
    # is_staff / is_superuser are now exposed (read-only) so the SPA can gate an
    # admin area; a freshly registered user is neither.
    assert response.data["is_staff"] is False
    assert response.data["is_superuser"] is False
    assert set(response.data.keys()) == {
        "id", "email", "userkey", "is_active", "email_confirmed", "language",
        "is_staff", "is_superuser",
    }


@pytest.mark.django_db
def test_me_does_not_allow_writing_is_staff():
    client = APIClient()
    User.objects.create_user(
        email="renaud@example.com",
        password="MotDePasseTresSolide123!",
        email_confirmed=True,
    )
    login_response = client.post("/api/v1/auth/login/", {
        "email": "renaud@example.com",
        "password": "MotDePasseTresSolide123!",
    }, format="json")
    access = login_response.data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    # is_staff is read-only on /me/: the PATCH serializer only accepts `language`,
    # so an attempt to escalate via the profile endpoint is silently ignored.
    response = client.patch(
        "/api/v1/auth/me/", {"language": "EN", "is_staff": True}, format="json"
    )

    assert response.status_code == 200
    assert User.objects.get(email="renaud@example.com").is_staff is False


@pytest.mark.django_db
def test_me_patch_updates_language():
    client = APIClient()
    User.objects.create_user(
        email="renaud@example.com",
        password="MotDePasseTresSolide123!",
        email_confirmed=True,
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


# --- Email confirmation ---------------------------------------------------
CONFIRM_URL = "/api/v1/auth/email/confirm/"
RESEND_URL = "/api/v1/auth/email/resend/"


def _confirm_tokens(user):
    return urlsafe_base64_encode(force_bytes(user.pk)), default_token_generator.make_token(user)


@pytest.mark.django_db
def test_register_duplicate_email_does_not_leak_and_notifies_existing_user(monkeypatch):
    # Anti-enumeration: a duplicate-email registration must return the SAME
    # pending-verification body as a fresh signup (no 400, no "in use" leak),
    # must NOT create a second account, and must email the existing owner a
    # neutral heads-up instead of the confirmation link.
    existing = User.objects.create_user(
        email="taken@example.com", password="MotDePasseTresSolide123!"
    )

    confirmations = []
    notices = []
    monkeypatch.setattr(
        "accounts.api_views.send_confirmation_email",
        lambda user: confirmations.append(user.email),
    )
    monkeypatch.setattr(
        "accounts.api_views.send_duplicate_registration_email",
        lambda user: notices.append(user.email),
    )

    client = APIClient()
    response = client.post("/api/v1/auth/register/", {
        "email": "taken@example.com", "password": "UnAutreMotDePasseSolide456!",
    }, format="json")

    # Indistinguishable from a fresh registration.
    assert response.status_code == 201
    assert response.data["code"] == "registration_pending_verification"
    assert response.data["email"] == "taken@example.com"
    # No second account created; the existing user's password is untouched.
    assert User.objects.filter(email="taken@example.com").count() == 1
    assert User.objects.get(email="taken@example.com").pk == existing.pk
    assert existing.check_password("MotDePasseTresSolide123!")
    # The existing owner was notified; no confirmation link was sent.
    assert notices == ["taken@example.com"]
    assert confirmations == []


@pytest.mark.django_db
def test_register_new_email_still_creates_account(monkeypatch):
    confirmations = []
    notices = []
    monkeypatch.setattr(
        "accounts.api_views.send_confirmation_email",
        lambda user: confirmations.append(user.email),
    )
    monkeypatch.setattr(
        "accounts.api_views.send_duplicate_registration_email",
        lambda user: notices.append(user.email),
    )

    client = APIClient()
    response = client.post("/api/v1/auth/register/", {
        "email": "fresh@example.com", "password": "MotDePasseTresSolide123!",
    }, format="json")

    assert response.status_code == 201
    assert response.data["code"] == "registration_pending_verification"
    assert User.objects.filter(email="fresh@example.com").count() == 1
    # A genuinely new email gets the confirmation link, not the duplicate notice.
    assert confirmations == ["fresh@example.com"]
    assert notices == []


@pytest.mark.django_db
def test_register_sends_confirmation_email_and_creates_unconfirmed(monkeypatch):
    sent = {}
    monkeypatch.setattr(
        "accounts.email_confirmation.send_email",
        lambda to, subject, body: sent.update(to=to, body=body),
    )
    client = APIClient()
    response = client.post("/api/v1/auth/register/", {
        "email": "new@example.com", "password": "MotDePasseTresSolide123!",
    }, format="json")

    assert response.status_code == 201
    user = User.objects.get(email="new@example.com")
    assert user.email_confirmed is False
    assert sent["to"] == "new@example.com"
    assert "/auth/confirm-email/" in sent["body"]


@pytest.mark.django_db
def test_login_blocked_until_email_confirmed():
    User.objects.create_user(email="pending@example.com", password="MotDePasseTresSolide123!")
    client = APIClient()
    response = client.post("/api/v1/auth/login/", {
        "email": "pending@example.com", "password": "MotDePasseTresSolide123!",
    }, format="json")

    assert response.status_code == 403
    assert response.data["code"] == "email_not_verified"


@pytest.mark.django_db
def test_confirm_email_flips_flag_and_returns_tokens():
    user = User.objects.create_user(email="pending@example.com", password="MotDePasseTresSolide123!")
    uid, token = _confirm_tokens(user)
    client = APIClient()
    response = client.post(CONFIRM_URL, {"uid": uid, "token": token}, format="json")

    assert response.status_code == 200
    assert "access" in response.data and "refresh" in response.data
    assert response.data["user"]["email_confirmed"] is True
    user.refresh_from_db()
    assert user.email_confirmed is True


@pytest.mark.django_db
def test_confirm_email_invalid_token_returns_400():
    user = User.objects.create_user(email="pending@example.com", password="MotDePasseTresSolide123!")
    uid, _ = _confirm_tokens(user)
    client = APIClient()
    response = client.post(CONFIRM_URL, {"uid": uid, "token": "not-a-valid-token"}, format="json")

    assert response.status_code == 400
    assert response.data["code"] == "confirmation_link_invalid"
    user.refresh_from_db()
    assert user.email_confirmed is False


@pytest.mark.django_db
def test_resend_confirmation_is_antileak_for_unknown_email():
    client = APIClient()
    response = client.post(RESEND_URL, {"email": "nobody@example.com"}, format="json")
    assert response.status_code == 200


@pytest.mark.django_db
def test_resend_confirmation_emails_unconfirmed_user(monkeypatch):
    User.objects.create_user(email="pending@example.com", password="MotDePasseTresSolide123!")
    sent = {}
    monkeypatch.setattr(
        "accounts.email_confirmation.send_email",
        lambda to, subject, body: sent.update(to=to),
    )
    client = APIClient()
    response = client.post(RESEND_URL, {"email": "pending@example.com"}, format="json")

    assert response.status_code == 200
    assert sent.get("to") == "pending@example.com"


# --- Token refresh (rotation) ---------------------------------------------
@pytest.mark.django_db
def test_refresh_returns_rotated_refresh_token():
    """With ROTATE_REFRESH_TOKENS the refresh endpoint returns both a new access
    AND a rotated refresh token; the response must expose `refresh` so clients
    can persist it (and the OpenAPI schema matches)."""
    client = APIClient()
    User.objects.create_user(
        email="renaud@example.com",
        password="MotDePasseTresSolide123!",
        email_confirmed=True,
    )
    login_response = client.post("/api/v1/auth/login/", {
        "email": "renaud@example.com",
        "password": "MotDePasseTresSolide123!",
    }, format="json")
    original_refresh = login_response.data["refresh"]

    response = client.post(
        "/api/v1/auth/refresh/", {"refresh": original_refresh}, format="json"
    )

    assert response.status_code == 200
    assert "access" in response.data
    # The rotated refresh token is returned and differs from the original.
    assert "refresh" in response.data
    assert response.data["refresh"] != original_refresh
