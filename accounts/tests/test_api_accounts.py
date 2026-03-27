import pytest
from rest_framework.test import APIClient
from accounts.models import User


@pytest.mark.django_db
def test_register_success():
    client = APIClient()

    response = client.post("/api/v1/auth/register/", {
        "email": "renaud@example.com",
        "username": "renaud",
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
def test_login_success():
    client = APIClient()
    User.objects.create_user(
        email="renaud@example.com",
        username="renaud",
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
        username="renaud",
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
        username="renaud",
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
        username="renaud",
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
