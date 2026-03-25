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