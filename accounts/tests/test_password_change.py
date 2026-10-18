"""POST /api/v1/auth/password/change/ — authenticated password change.

Contract mirrors trainingmanager_server / billing_server (#51): body
{current_password, new_password}; Django password validators; stable error
codes. PushIT-specific: on success every other outstanding refresh token of the
user is blacklisted (other sessions are signed out) and a fresh access+refresh
pair is returned so the calling session stays signed in.
"""
import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from accounts.models import User

URL = "/api/v1/auth/password/change/"
PASSWORD = "MotDePasseTresSolide123!"
NEW_PASSWORD = "UnAutreSecretSolide456?"


def _user(email="change@example.com"):
    return User.objects.create_user(email=email, password=PASSWORD, email_confirmed=True)


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _post(user, body):
    return _client(user).post(URL, body, format="json")


@pytest.mark.django_db
def test_requires_authentication():
    response = APIClient().post(
        URL, {"current_password": PASSWORD, "new_password": NEW_PASSWORD}, format="json"
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_missing_fields_return_validation_error():
    user = _user()
    response = _post(user, {})
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "validation_error"
    assert set(body["errors"]) == {"current_password", "new_password"}


@pytest.mark.django_db
def test_wrong_current_password_is_refused():
    user = _user()
    response = _post(user, {"current_password": "nope", "new_password": NEW_PASSWORD})
    assert response.status_code == 400
    assert response.json()["code"] == "current_password_invalid"
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


@pytest.mark.django_db
def test_same_password_is_refused():
    user = _user()
    response = _post(user, {"current_password": PASSWORD, "new_password": PASSWORD})
    assert response.status_code == 400
    assert response.json()["code"] == "password_unchanged"


@pytest.mark.django_db
def test_weak_password_is_refused_by_validators():
    user = _user()
    response = _post(user, {"current_password": PASSWORD, "new_password": "123"})
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "password_invalid"
    assert body["errors"]["new_password"]
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


@pytest.mark.django_db
def test_password_similar_to_email_is_refused():
    user = _user("jean.dupont.unique@example.com")
    response = _post(
        user, {"current_password": PASSWORD, "new_password": "jean.dupont.unique"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "password_invalid"


@pytest.mark.django_db
def test_success_changes_password_and_returns_fresh_tokens():
    user = _user()
    response = _post(user, {"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "password_changed"
    assert body["detail"]
    assert body["user"]["email"] == user.email
    assert str(AccessToken(body["access"])["user_id"]) == str(user.pk)
    fresh = RefreshToken(body["refresh"])  # valid, not blacklisted
    fresh.check_blacklist()

    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)
    assert not user.check_password(PASSWORD)


@pytest.mark.django_db
def test_success_blacklists_every_other_refresh_token():
    user = _user()
    other = _user("other@example.com")
    old_a = RefreshToken.for_user(user)
    old_b = RefreshToken.for_user(user)
    foreign = RefreshToken.for_user(other)

    response = _post(user, {"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert response.status_code == 200
    new_jti = RefreshToken(response.json()["refresh"])["jti"]

    blacklisted = set(
        BlacklistedToken.objects.values_list("token__jti", flat=True)
    )
    assert {old_a["jti"], old_b["jti"]} <= blacklisted
    assert new_jti not in blacklisted
    assert foreign["jti"] not in blacklisted
    assert OutstandingToken.objects.filter(jti=new_jti, user=user).exists()


@pytest.mark.django_db
def test_old_refresh_token_can_no_longer_be_used():
    user = _user()
    old = RefreshToken.for_user(user)
    _post(user, {"current_password": PASSWORD, "new_password": NEW_PASSWORD})

    response = APIClient().post("/api/v1/auth/refresh/", {"refresh": str(old)}, format="json")
    assert response.status_code == 401


@pytest.mark.django_db
def test_new_refresh_token_can_be_used():
    user = _user()
    response = _post(user, {"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    refresh = response.json()["refresh"]
    response = APIClient().post("/api/v1/auth/refresh/", {"refresh": refresh}, format="json")
    assert response.status_code == 200


@pytest.mark.django_db
def test_login_works_with_new_password_only():
    user = _user()
    _post(user, {"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    client = APIClient()
    ok = client.post(
        "/api/v1/auth/login/", {"email": user.email, "password": NEW_PASSWORD}, format="json"
    )
    assert ok.status_code == 200
    ko = client.post(
        "/api/v1/auth/login/", {"email": user.email, "password": PASSWORD}, format="json"
    )
    assert ko.status_code == 400


@pytest.mark.django_db
def test_is_throttled_after_five_attempts_per_minute():
    user = _user()
    client = _client(user)
    statuses = [
        client.post(
            URL, {"current_password": "wrong", "new_password": NEW_PASSWORD}, format="json"
        ).status_code
        for _ in range(6)
    ]
    assert statuses[:5] == [400] * 5
    assert statuses[5] == 429
    assert client.post(
        URL, {"current_password": PASSWORD, "new_password": NEW_PASSWORD}, format="json"
    ).json()["code"] == "throttled"
