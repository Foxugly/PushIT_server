import pytest
from rest_framework.test import APIClient

from accounts.models import User


@pytest.mark.django_db
def test_register_validation_errors_follow_global_contract():
    client = APIClient()

    response = client.post(
        "/api/v1/auth/register/",
        {},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "validation_error"
    assert response.data["detail"] == "Validation error."
    assert "errors" in response.data
    assert "email" in response.data["errors"]
    assert "username" in response.data["errors"]
    assert "password" in response.data["errors"]


@pytest.mark.django_db
def test_login_invalid_credentials_follow_global_contract():
    client = APIClient()

    response = client.post(
        "/api/v1/auth/login/",
        {
            "email": "missing@example.com",
            "password": "wrong-password",
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "validation_error"
    assert response.data["detail"] == "Validation error."
    assert "errors" in response.data


@pytest.mark.django_db
def test_me_patch_invalid_language_follows_global_contract():
    client = APIClient()

    authenticated_user = User.objects.create_user(
        email="renaud@example.com",
        username="renaud",
        password="MotDePasseTresSolide123!",
    )
    client.force_authenticate(user=authenticated_user)

    response = client.patch(
        "/api/v1/auth/me/",
        {
            "language": "DE",
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "validation_error"
    assert response.data["detail"] == "Validation error."
    assert "errors" in response.data
    assert "language" in response.data["errors"]
