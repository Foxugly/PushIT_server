import pytest
from rest_framework.test import APIClient


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
