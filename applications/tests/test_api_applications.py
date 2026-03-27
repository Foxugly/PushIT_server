import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application


@pytest.mark.django_db
def test_get_patch_and_delete_application():
    client = APIClient()
    user = User.objects.create_user(
        email="app@example.com",
        username="appuser",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(
        owner=user,
        name="PushIT",
        description="Initial description",
    )
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    get_response = client.get(f"/api/v1/apps/{app.id}/")
    assert get_response.status_code == 200
    assert get_response.data["name"] == "PushIT"

    patch_response = client.patch(
        f"/api/v1/apps/{app.id}/",
        {
            "name": "PushIT Mobile",
            "description": "Updated description",
        },
        format="json",
    )
    assert patch_response.status_code == 200
    assert patch_response.data["name"] == "PushIT Mobile"
    assert patch_response.data["description"] == "Updated description"

    app.refresh_from_db()
    assert app.name == "PushIT Mobile"
    assert app.description == "Updated description"

    delete_response = client.delete(f"/api/v1/apps/{app.id}/")
    assert delete_response.status_code == 204
    assert Application.objects.filter(id=app.id).exists() is False


@pytest.mark.django_db
def test_application_patch_validation_error_follows_contract():
    client = APIClient()
    user = User.objects.create_user(
        email="app2@example.com",
        username="appuser2",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="PushIT")
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.patch(
        f"/api/v1/apps/{app.id}/",
        {
            "name": "",
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.data["code"] == "validation_error"
    assert response.data["detail"] == "Validation error."
    assert "name" in response.data["errors"]


@pytest.mark.django_db
def test_application_detail_returns_not_found_for_other_user():
    client = APIClient()
    owner = User.objects.create_user(
        email="owner@example.com",
        username="owner",
        password="MotDePasseTresSolide123!",
    )
    other_user = User.objects.create_user(
        email="other@example.com",
        username="other",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=owner, name="Private app")
    access = str(RefreshToken.for_user(other_user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.delete(f"/api/v1/apps/{app.id}/")

    assert response.status_code == 404
    assert response.data["code"] == "application_not_found"
