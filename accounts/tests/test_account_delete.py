"""DELETE /api/v1/auth/me/ — self-service account erasure (GDPR art. 17).

Contract mirrors trainingmanager_server's account deletion: body
{current_password}; 204 on success; 400 validation_error when the field is
missing; 400 current_password_invalid when it is wrong; 401 unauthenticated.
"""
import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application
from devices.models import Device, DeviceApplicationLink
from notifications.models import (
    InboundEmailIngestionLog,
    InboundEmailIngestionStatus,
    InboundEmailSource,
    Notification,
    NotificationDelivery,
    NotificationTemplate,
)

URL = "/api/v1/auth/me/"
PASSWORD = "MotDePasseTresSolide123!"


def _user(email):
    return User.objects.create_user(email=email, password=PASSWORD, email_confirmed=True)


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture(autouse=True)
def _no_exchange(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "exchange.integration.provision_alias_for_application", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "exchange.integration.deprovision_alias_for_application",
        lambda alias: calls.append(alias),
    )
    return calls


@pytest.mark.django_db
def test_delete_requires_authentication():
    response = APIClient().delete(URL, {"current_password": PASSWORD}, format="json")
    assert response.status_code == 401


@pytest.mark.django_db
def test_delete_without_password_returns_validation_error():
    user = _user("nopw@example.com")
    response = _client(user).delete(URL, {}, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert "current_password" in response.json()["errors"]
    assert User.objects.filter(pk=user.pk).exists()


@pytest.mark.django_db
def test_delete_with_wrong_password_is_refused_and_keeps_user():
    user = _user("wrong@example.com")
    response = _client(user).delete(URL, {"current_password": "nope"}, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "current_password_invalid"
    assert User.objects.filter(pk=user.pk).exists()


@pytest.mark.django_db
def test_delete_erases_user_owned_apps_devices_and_blacklists_tokens(
    _no_exchange, django_capture_on_commit_callbacks
):
    user = _user("gone@example.com")
    other = _user("stays@example.com")

    owned = Application.objects.create(owner=user, name="Mine")
    alias = owned.inbound_email_address
    NotificationTemplate.objects.create(
        application=owned, name="t", title_template="x", message_template="y"
    )
    foreign = Application.objects.create(owner=other, name="Theirs")

    my_device = Device.objects.create(user=user, push_token="tok-mine")
    their_device = Device.objects.create(user=other, push_token="tok-theirs")
    # The other user subscribes to my app; I subscribe to theirs.
    DeviceApplicationLink.objects.create(device=their_device, application=owned)
    DeviceApplicationLink.objects.create(device=my_device, application=foreign)

    mine_notif = Notification.objects.create(application=owned, title="a", message="b")
    NotificationDelivery.objects.create(notification=mine_notif, device=their_device)
    foreign_notif = Notification.objects.create(application=foreign, title="c", message="d")
    NotificationDelivery.objects.create(notification=foreign_notif, device=my_device)
    InboundEmailIngestionLog.objects.create(
        source=InboundEmailSource.POLLING,
        status=InboundEmailIngestionStatus.CREATED,
        application=owned,
        sender="gone@example.com",
    )

    refresh = RefreshToken.for_user(user)

    with django_capture_on_commit_callbacks(execute=True):
        response = _client(user).delete(URL, {"current_password": PASSWORD}, format="json")

    assert response.status_code == 204
    assert not User.objects.filter(pk=user.pk).exists()
    # Owned app + everything hanging off it is gone, even with other subscribers.
    assert not Application.objects.filter(pk=owned.pk).exists()
    assert not Notification.objects.filter(pk=mine_notif.pk).exists()
    assert not NotificationTemplate.objects.filter(application_id=owned.pk).exists()
    assert not InboundEmailIngestionLog.objects.exists()
    # My devices are deleted (not orphaned with their push token).
    assert not Device.objects.filter(pk=my_device.pk).exists()
    assert not NotificationDelivery.objects.filter(device_id=my_device.pk).exists()
    # The other user's data is untouched.
    assert Application.objects.filter(pk=foreign.pk).exists()
    assert Device.objects.filter(pk=their_device.pk).exists()
    assert Notification.objects.filter(pk=foreign_notif.pk).exists()
    # Every refresh token issued to the user is blacklisted.
    outstanding = OutstandingToken.objects.get(jti=refresh["jti"])
    assert BlacklistedToken.objects.filter(token=outstanding).exists()
    # The Exchange alias of the owned app is deprovisioned.
    assert _no_exchange == [alias]


@pytest.mark.django_db
def test_refresh_token_unusable_after_deletion():
    user = _user("refresh@example.com")
    refresh = str(RefreshToken.for_user(user))
    assert _client(user).delete(URL, {"current_password": PASSWORD}, format="json").status_code == 204

    response = APIClient().post("/api/v1/auth/refresh/", {"refresh": refresh}, format="json")
    assert response.status_code in (400, 401)
