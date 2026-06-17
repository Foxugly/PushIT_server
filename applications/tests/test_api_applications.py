import pytest
from django.conf import settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application


@pytest.mark.django_db
def test_get_patch_and_delete_application():
    client = APIClient()
    user = User.objects.create_user(
        email="app@example.com",
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
    assert get_response.data["inbound_email_alias"] == app.inbound_email_alias
    assert get_response.data["inbound_email_address"].endswith(f"@{settings.INBOUND_EMAIL_DOMAIN}")

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
    assert patch_response.data["inbound_email_alias"] == app.inbound_email_alias

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
def test_regenerate_inbound_email_allocates_a_new_alias():
    client = APIClient()
    user = User.objects.create_user(
        email="regen@example.com",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="PushIT")
    old_alias = app.inbound_email_alias
    old_suffix = app.inbound_email_suffix
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.post(f"/api/v1/apps/{app.id}/regenerate-email/")

    assert response.status_code == 200
    assert response.data["app_id"] == app.id
    assert response.data["inbound_email_alias"] != old_alias
    assert response.data["inbound_email_alias"].startswith(Application.ALIAS_PREFIX)
    assert response.data["inbound_email_address"].endswith(f"@{settings.INBOUND_EMAIL_DOMAIN}")
    assert response.data["inbound_email_address"].startswith(response.data["inbound_email_alias"])

    app.refresh_from_db()
    assert app.inbound_email_alias != old_alias
    assert app.inbound_email_suffix != old_suffix
    assert app.inbound_email_alias == response.data["inbound_email_alias"]


@pytest.mark.django_db
def test_regenerate_inbound_email_returns_not_found_for_other_user():
    client = APIClient()
    owner = User.objects.create_user(
        email="owner-regen@example.com",
        password="MotDePasseTresSolide123!",
    )
    other_user = User.objects.create_user(
        email="other-regen@example.com",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=owner, name="Private app")
    access = str(RefreshToken.for_user(other_user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.post(f"/api/v1/apps/{app.id}/regenerate-email/")

    assert response.status_code == 404
    assert response.data["code"] == "application_not_found"


@pytest.mark.django_db
def test_application_detail_returns_not_found_for_other_user():
    client = APIClient()
    owner = User.objects.create_user(
        email="owner@example.com",
        password="MotDePasseTresSolide123!",
    )
    other_user = User.objects.create_user(
        email="other@example.com",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=owner, name="Private app")
    access = str(RefreshToken.for_user(other_user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.delete(f"/api/v1/apps/{app.id}/")

    assert response.status_code == 404
    assert response.data["code"] == "application_not_found"
