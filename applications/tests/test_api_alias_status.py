import pytest
from unittest.mock import patch

from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application


def _auth(client, user):
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")


@pytest.mark.django_db
def test_alias_status_owner_gets_status():
    user = User.objects.create_user(
        email="owner@example.com", password="MotDePasseTresSolide123!"
    )
    app = Application.objects.create(owner=user, name="PushIT")
    client = APIClient()
    _auth(client, user)

    with patch(
        "exchange.integration.alias_status",
        return_value={"configured": True, "provisioned": True, "detail": "provisioned"},
    ) as mocked:
        response = client.get(f"/api/v1/apps/{app.id}/alias-status/")

    assert response.status_code == 200
    body = response.json()
    assert body["alias"] == app.inbound_email_address
    assert body["configured"] is True
    assert body["provisioned"] is True
    assert body["detail"] == "provisioned"
    mocked.assert_called_once_with(app.inbound_email_address)


@pytest.mark.django_db
def test_alias_status_non_owner_gets_404():
    owner = User.objects.create_user(
        email="owner2@example.com", password="MotDePasseTresSolide123!"
    )
    other = User.objects.create_user(
        email="intruder@example.com", password="MotDePasseTresSolide123!"
    )
    app = Application.objects.create(owner=owner, name="PushIT")
    client = APIClient()
    _auth(client, other)

    response = client.get(f"/api/v1/apps/{app.id}/alias-status/")

    assert response.status_code == 404
    assert response.json()["code"] == "application_not_found"


@pytest.mark.django_db
def test_alias_status_not_configured_returns_provisioned_none():
    user = User.objects.create_user(
        email="owner3@example.com", password="MotDePasseTresSolide123!"
    )
    app = Application.objects.create(owner=user, name="PushIT")
    client = APIClient()
    _auth(client, user)

    # Exchange unconfigured in tests -> real alias_status returns provisioned=None.
    response = client.get(f"/api/v1/apps/{app.id}/alias-status/")

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["provisioned"] is None
    assert body["detail"] == "exchange_not_configured"
