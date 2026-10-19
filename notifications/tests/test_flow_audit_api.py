"""Audit 2026-10-19 — items 3, 4 and 8 (stats)."""
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from applications.models import Application
from devices.models import Device, DeviceApplicationLink
from notifications.models import Notification, NotificationDelivery, NotificationStatus


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def world(db):
    owner = User.objects.create_user(email="owner@example.com", password="pw12345678")
    recipient = User.objects.create_user(email="rcpt@example.com", password="pw12345678")
    stranger = User.objects.create_user(email="stranger@example.com", password="pw12345678")
    app = Application.objects.create(owner=owner, name="App")
    mine = Device.objects.create(user=recipient, push_token="token_recipient_11111111111", device_name="Mine")
    theirs = Device.objects.create(user=stranger, push_token="token_stranger_222222222222", device_name="Theirs")
    for d in (mine, theirs):
        DeviceApplicationLink.objects.create(device=d, application=app)
    notification = Notification.objects.create(
        application=app, title="Hello", message="World", status=NotificationStatus.SENT
    )
    NotificationDelivery.objects.create(notification=notification, device=mine)
    NotificationDelivery.objects.create(notification=notification, device=theirs)
    return owner, recipient, stranger, app, mine, theirs, notification


# ------------------------------------------------------------------ item 3


def test_recipient_can_read_a_notification_delivered_to_their_device(world):
    _, recipient, _, app, mine, theirs, notification = world

    r = _client(recipient).get(f"/api/v1/notifications/{notification.id}/")

    assert r.status_code == 200, r.content
    body = r.json()
    assert body["id"] == notification.id
    assert body["title"] == "Hello"
    assert body["message"] == "World"
    assert body["application_id"] == app.id
    # No other recipient's data.
    assert body["device_ids"] == [mine.id]
    assert [d["device_id"] for d in body["deliveries"]] == [mine.id]
    assert "Theirs" not in r.content.decode()


def test_user_without_delivery_still_gets_404(world):
    _, _, _, app, _, _, _ = world
    outsider = User.objects.create_user(email="out@example.com", password="pw12345678")
    other = Notification.objects.create(application=app, title="X", message="Y")
    r = _client(outsider).get(f"/api/v1/notifications/{other.id}/")
    assert r.status_code == 404
    assert r.json()["code"] == "notification_not_found"


def test_owner_still_sees_every_delivery(world):
    owner, _, _, _, mine, theirs, notification = world
    r = _client(owner).get(f"/api/v1/notifications/{notification.id}/")
    assert r.status_code == 200
    assert r.json()["device_ids"] == sorted([mine.id, theirs.id])
    assert len(r.json()["deliveries"]) == 2


# ------------------------------------------------------------------ item 4


def test_patch_future_notification_returns_read_serializer(world):
    owner, _, _, app, mine, _, _ = world
    future = Notification.objects.create(
        application=app,
        title="Later",
        message="M",
        status=NotificationStatus.SCHEDULED,
        scheduled_for=timezone.now() + timedelta(days=1),
    )
    NotificationDelivery.objects.create(notification=future, device=mine)

    r = _client(owner).patch(
        f"/api/v1/notifications/future/{future.id}/", {"title": "Renamed"}, format="json"
    )

    assert r.status_code == 200, r.content
    body = r.json()
    assert body["title"] == "Renamed"
    assert body["device_ids"] == [mine.id]
    for key in ("application_id", "application_name", "effective_scheduled_for"):
        assert key in body


# ------------------------------------------------------------------ item 8


def test_stats_with_non_integer_application_id_is_400(world):
    owner = world[0]
    r = _client(owner).get("/api/v1/notifications/stats/?application_id=abc")
    assert r.status_code == 400
    assert r.json()["code"] == "validation_error"
    assert "application_id" in r.json()["errors"]
