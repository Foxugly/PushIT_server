import pytest
from rest_framework.test import APIClient

from accounts.models import User
from applications.models import Application


@pytest.mark.django_db
def test_revoke_token_endpoint():
    client = APIClient()

    user = User.objects.create_user(
        email="u1@example.com",
        username="u1",
        password="1234Test!!",
    )

    login_response = client.post("/api/v1/auth/login/", {
        "email": "u1@example.com",
        "password": "1234Test!!",
    }, format="json")

    access = login_response.data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    app = Application.objects.create(owner=user, name="App")

    response = client.post(f"/api/v1/apps/{app.id}/revoke-token/", {}, format="json")

    assert response.status_code == 200

    app.refresh_from_db()
    assert app.revoked_at is not None