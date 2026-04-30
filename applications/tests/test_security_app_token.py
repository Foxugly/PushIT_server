import pytest
from rest_framework.test import APIRequestFactory
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken


from accounts.models import User
from applications.authentication import AppTokenAuthentication, AppTokenPrincipal
from applications.models import Application
from applications.permissions import HasAppToken

VALID_PUSH_TOKEN = "token_12345678901234567890"


def _authenticate(client: APIClient, user: User) -> None:
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")


@pytest.mark.django_db
def test_app_auth_updates_last_used_at():
    client = APIClient()

    user = User.objects.create_user(
        email="u1@example.com",
        username="u1",
        password="1234Test!!",
    )

    app = Application(owner=user, name="App")
    raw_token = app.set_new_app_token()
    app.save()
    _authenticate(client, user)

    assert app.last_used_at is None

    response = client.post(
        "/api/v1/devices/link/",
        {
            "app_token": raw_token,
            "device_name": "Samsung",
            "platform": "android",
            "push_token": VALID_PUSH_TOKEN,
        },
        format="json",
    )

    assert response.status_code == 200

    app.refresh_from_db()
    assert app.last_used_at is not None


@pytest.mark.django_db
def test_inactive_app_token_is_rejected():
    client = APIClient()

    user = User.objects.create_user(
        email="u1@example.com",
        username="u1",
        password="1234Test!!",
    )

    app = Application(owner=user, name="App", is_active=False)
    raw_token = app.set_new_app_token()
    app.save()
    _authenticate(client, user)

    response = client.post(
        "/api/v1/devices/link/",
        {
            "app_token": raw_token,
            "device_name": "Samsung",
            "platform": "android",
            "push_token": VALID_PUSH_TOKEN,
        },
        format="json",
    )

    assert response.status_code == 401
    assert response.data["code"] == "app_token_inactive"


@pytest.mark.django_db
def test_revoked_app_token_is_rejected():
    client = APIClient()

    user = User.objects.create_user(
        email="u1@example.com",
        username="u1",
        password="1234Test!!",
    )

    app = Application(owner=user, name="App")
    raw_token = app.set_new_app_token()
    app.save()
    app.revoke_token()
    _authenticate(client, user)

    response = client.post(
        "/api/v1/devices/link/",
        {
            "app_token": raw_token,
            "device_name": "Samsung",
            "platform": "android",
            "push_token": VALID_PUSH_TOKEN,
        },
        format="json",
    )

    assert response.status_code == 401
    assert response.data["code"] == "app_token_revoked"


@pytest.mark.django_db
def test_missing_app_token_returns_explicit_business_code():
    client = APIClient()
    user = User.objects.create_user(
        email="u1@example.com",
        username="u1",
        password="1234Test!!",
    )
    _authenticate(client, user)

    response = client.post(
        "/api/v1/devices/link/",
        {
            "device_name": "Samsung",
            "platform": "android",
            "push_token": VALID_PUSH_TOKEN,
        },
        format="json",
    )

    assert response.status_code == 401
    assert response.data["code"] == "app_token_missing"


@pytest.mark.django_db
def test_app_token_auth_does_not_expose_owner_as_request_user():
    user = User.objects.create_user(
        email="u1@example.com",
        username="u1",
        password="1234Test!!",
    )
    app = Application(owner=user, name="App")
    raw_token = app.set_new_app_token()
    app.save()

    request = APIRequestFactory().post(
        "/api/v1/devices/link/",
        HTTP_X_APP_TOKEN=raw_token,
    )

    authenticator = AppTokenAuthentication()
    principal, authenticated_app = authenticator.authenticate(request)

    assert isinstance(principal, AppTokenPrincipal)
    assert principal != user
    assert principal.owner_id == user.id
    assert principal.application_id == app.id
    assert principal.auth_kind == "application"
    assert principal.is_authenticated is True
    assert principal.is_staff is False
    assert principal.is_superuser is False
    assert principal.is_app_token_principal is True
    assert principal.get_username() == f"app:{app.id}"
    assert str(principal) == f"app:{app.id}"
    assert authenticated_app == app
    assert request.auth_application == app


@pytest.mark.django_db
def test_app_token_principal_rejects_user_like_attributes():
    principal = AppTokenPrincipal(
        pk="app:12",
        application_id=12,
        owner_id=34,
    )

    with pytest.raises(AttributeError) as exc_info:
        _ = principal.email

    assert "request.auth_application" in str(exc_info.value)


@pytest.mark.django_db
def test_has_app_token_permission_requires_consistent_principal_and_application():
    user = User.objects.create_user(
        email="u1@example.com",
        username="u1",
        password="1234Test!!",
    )
    app = Application.objects.create(owner=user, name="App")

    request = APIRequestFactory().get("/api/v1/notifications/app/")
    request.user = AppTokenPrincipal(
        pk=f"app:{app.id}",
        application_id=app.id,
        owner_id=user.id,
    )
    request.auth = app
    request.auth_application = app

    assert HasAppToken().has_permission(request, view=None) is True

    request.user = user
    assert HasAppToken().has_permission(request, view=None) is False


def test_has_app_token_permission_reports_missing_token_code():
    request = APIRequestFactory().get("/api/v1/notifications/app/")

    permission = HasAppToken()

    assert permission.has_permission(request, view=None) is False
    assert permission.code == "app_token_missing"
