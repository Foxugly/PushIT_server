"""Audit 2026-10-19 — item 9 (API half) and the inactive-app half of item 1.

An owner whose subscription has lapsed can no longer create or send
notifications: 402 ``subscription_required``, same shape as the 402 returned
at application creation. Inert while billing is not configured.
"""
from unittest.mock import patch

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from accounts.models import User
from applications.models import Application
from applications.models_send_token import AppSendToken
from billing.models import Subscription
from devices.models import Device, DeviceApplicationLink
from notifications.models import Notification, NotificationStatus

BILLING_ON = {
    "BILLING_BASE_URL": "https://billing-api.foxugly.com",
    "BILLING_APP_SECRET": "secret-de-test",
    "BILLING_APP_SLUG": "pushit",
}


@pytest.fixture
def setup(db):
    owner = User.objects.create_user(email="owner@example.com", password="pw12345678")
    app = Application.objects.create(owner=owner, name="App")
    device = Device.objects.create(push_token="token_entitlement_111111111111")
    DeviceApplicationLink.objects.create(device=device, application=app)
    client = APIClient()
    client.force_authenticate(owner)
    return owner, app, device, client


def _expired(owner):
    Subscription.objects.create(user=owner, is_paid=False)


def _assert_402(response):
    assert response.status_code == 402, response.content
    body = response.json()
    assert body["code"] == "subscription_required"
    assert body["detail"]


@override_settings(**BILLING_ON)
def test_jwt_create_requires_subscription(setup):
    owner, app, device, client = setup
    _expired(owner)
    r = client.post(
        "/api/v1/notifications/",
        {"application_id": app.id, "device_ids": [device.id], "title": "T", "message": "M"},
        format="json",
    )
    _assert_402(r)
    assert Notification.objects.count() == 0


@override_settings(**BILLING_ON)
def test_jwt_send_now_requires_subscription(setup):
    owner, app, device, client = setup
    _expired(owner)
    r = client.post(
        "/api/v1/notifications/send/",
        {"application_id": app.id, "device_ids": [device.id], "title": "T", "message": "M"},
        format="json",
    )
    _assert_402(r)
    assert Notification.objects.count() == 0


@override_settings(**BILLING_ON)
def test_jwt_send_existing_requires_subscription(setup):
    owner, app, _, client = setup
    notification = Notification.objects.create(application=app, title="T", message="M")
    _expired(owner)
    r = client.post(f"/api/v1/notifications/{notification.id}/send/")
    _assert_402(r)
    notification.refresh_from_db()
    assert notification.status == NotificationStatus.DRAFT


@override_settings(**BILLING_ON)
def test_jwt_bulk_send_requires_subscription(setup):
    owner, app, _, client = setup
    notification = Notification.objects.create(application=app, title="T", message="M")
    _expired(owner)
    r = client.post(
        "/api/v1/notifications/bulk-send/", {"notification_ids": [notification.id]}, format="json"
    )
    _assert_402(r)


@override_settings(**BILLING_ON)
def test_app_token_create_requires_subscription(setup):
    owner, app, _, _ = setup
    _expired(owner)
    _, raw = AppSendToken.issue(app, "tests")
    r = APIClient().post(
        "/api/v1/notifications/app/create/",
        {"title": "T", "message": "M"},
        format="json",
        HTTP_X_APP_TOKEN=raw,
        HTTP_IDEMPOTENCY_KEY="k1",
    )
    _assert_402(r)
    assert Notification.objects.count() == 0


@override_settings(**BILLING_ON)
def test_app_token_send_requires_subscription(setup):
    owner, app, _, _ = setup
    _expired(owner)
    _, raw = AppSendToken.issue(app, "tests")
    r = APIClient().post(
        "/api/v1/notifications/app/send/",
        {"title": "T", "message": "M"},
        format="json",
        HTTP_X_APP_TOKEN=raw,
        HTTP_IDEMPOTENCY_KEY="k1",
    )
    _assert_402(r)
    assert Notification.objects.count() == 0


@override_settings(**BILLING_ON)
def test_app_token_bulk_send_requires_subscription(setup):
    owner, app, _, _ = setup
    notification = Notification.objects.create(application=app, title="T", message="M")
    _expired(owner)
    _, raw = AppSendToken.issue(app, "tests")
    r = APIClient().post(
        "/api/v1/notifications/app/bulk-send/",
        {"notification_ids": [notification.id]},
        format="json",
        HTTP_X_APP_TOKEN=raw,
    )
    _assert_402(r)


@override_settings(**BILLING_ON)
def test_subscription_bypass_can_send(setup):
    owner, app, device, client = setup
    owner.subscription_bypass = True
    owner.save(update_fields=["subscription_bypass"])
    r = client.post(
        "/api/v1/notifications/",
        {"application_id": app.id, "device_ids": [device.id], "title": "T", "message": "M"},
        format="json",
    )
    assert r.status_code == 201


@override_settings(**BILLING_ON)
def test_paid_owner_can_create(setup):
    owner, app, device, client = setup
    Subscription.objects.create(user=owner, is_paid=True, status="active")
    r = client.post(
        "/api/v1/notifications/",
        {"application_id": app.id, "device_ids": [device.id], "title": "T", "message": "M"},
        format="json",
    )
    assert r.status_code == 201


def test_billing_unconfigured_is_inert(setup):
    _, app, device, client = setup
    r = client.post(
        "/api/v1/notifications/",
        {"application_id": app.id, "device_ids": [device.id], "title": "T", "message": "M"},
        format="json",
    )
    assert r.status_code == 201


# ------------------------------------------------------------ item 1: inactive app


def test_jwt_create_on_inactive_application_is_400(setup):
    _, app, device, client = setup
    Application.objects.filter(pk=app.pk).update(is_active=False)
    r = client.post(
        "/api/v1/notifications/",
        {"application_id": app.id, "device_ids": [device.id], "title": "T", "message": "M"},
        format="json",
    )
    assert r.status_code == 400
    assert "application_id" in r.json()["errors"]


@patch("notifications.services.send_push_to_device", return_value="pid")
def test_jwt_send_existing_on_inactive_application_is_400(mock_send, setup):
    _, app, _, client = setup
    notification = Notification.objects.create(application=app, title="T", message="M")
    Application.objects.filter(pk=app.pk).update(is_active=False)
    r = client.post(f"/api/v1/notifications/{notification.id}/send/")
    assert r.status_code == 400
    assert r.json()["code"] == "application_inactive"
    assert mock_send.call_count == 0
