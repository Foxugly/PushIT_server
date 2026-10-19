"""Audit 2026-10-19 — items 2 (FCM token rotation) and 6 (reactivation reset)."""
import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from applications.models import Application
from devices.models import Device, DeviceApplicationLink, DeviceQuietPeriod, DeviceTokenStatus
from notifications.models import Notification, NotificationDelivery

OLD = "token_old_rotation_1111111111111111"
NEW = "token_new_rotation_2222222222222222"


def _client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def world(db):
    user = User.objects.create_user(email="me@example.com", password="pw12345678")
    other = User.objects.create_user(email="other@example.com", password="pw12345678")
    owner = User.objects.create_user(email="owner@example.com", password="pw12345678")
    app = Application.objects.create(owner=owner, name="App")
    return user, other, app


def _identify(user, push_token, **extra):
    return _client(user).post(
        "/api/v1/devices/identify/",
        {"push_token": push_token, "platform": "android", "device_name": "Pixel", **extra},
        format="json",
    )


def test_identify_with_previous_token_rotates_in_place(world):
    user, _, app = world
    device = Device.objects.create(user=user, push_token=OLD)
    DeviceApplicationLink.objects.create(device=device, application=app)
    notification = Notification.objects.create(application=app, title="T", message="M")
    NotificationDelivery.objects.create(notification=notification, device=device)

    r = _identify(user, NEW, previous_push_token=OLD)

    assert r.status_code == 200, r.content
    body = r.json()
    assert body["device_id"] == device.id
    assert body["device_created"] is False
    assert [a["id"] for a in body["linked_applications"]] == [app.id]
    device.refresh_from_db()
    assert device.push_token == NEW
    assert Device.objects.count() == 1
    assert NotificationDelivery.objects.filter(device=device).count() == 1


def test_link_with_previous_token_rotates_in_place(world):
    user, _, app = world
    device = Device.objects.create(user=user, push_token=OLD)
    DeviceApplicationLink.objects.create(device=device, application=app)

    r = _client(user).post(
        "/api/v1/devices/link/",
        {
            "app_token": app.enrolment_code,
            "push_token": NEW,
            "previous_push_token": OLD,
            "platform": "android",
        },
        format="json",
    )

    assert r.status_code == 200, r.content
    assert r.json()["device_id"] == device.id
    device.refresh_from_db()
    assert device.push_token == NEW
    assert Device.objects.count() == 1


def test_previous_token_of_another_user_is_ignored(world):
    user, other, app = world
    foreign = Device.objects.create(user=other, push_token=OLD)
    DeviceApplicationLink.objects.create(device=foreign, application=app)

    r = _identify(user, NEW, previous_push_token=OLD)

    assert r.status_code == 200
    body = r.json()
    assert body["device_created"] is True
    assert body["device_id"] != foreign.id
    assert body["linked_applications"] == []
    foreign.refresh_from_db()
    assert foreign.push_token == OLD
    assert foreign.user == other
    assert foreign.application_links.filter(is_active=True).count() == 1


def test_unknown_previous_token_is_ignored(world):
    user, _, _ = world
    r = _identify(user, NEW, previous_push_token="token_unknown_33333333333333")
    assert r.status_code == 200
    assert r.json()["device_created"] is True
    assert Device.objects.get().push_token == NEW


def test_previous_token_equal_to_new_token_is_plain_identify(world):
    user, _, _ = world
    device = Device.objects.create(user=user, push_token=NEW)
    r = _identify(user, NEW, previous_push_token=NEW)
    assert r.status_code == 200
    assert r.json()["device_id"] == device.id


def test_rotation_when_new_token_row_already_exists_keeps_the_old_device(world):
    """The app identified with the new token first (old client), then sends the
    rotation: the old device survives (links + inbox), the new row is merged in."""
    user, _, app = world
    other_app = Application.objects.create(owner=app.owner, name="Other")
    old_device = Device.objects.create(user=user, push_token=OLD)
    DeviceApplicationLink.objects.create(device=old_device, application=app)
    n1 = Notification.objects.create(application=app, title="T1", message="M")
    NotificationDelivery.objects.create(notification=n1, device=old_device)

    new_device = Device.objects.create(user=user, push_token=NEW)
    DeviceApplicationLink.objects.create(device=new_device, application=other_app)
    n2 = Notification.objects.create(application=other_app, title="T2", message="M")
    NotificationDelivery.objects.create(notification=n2, device=new_device)
    DeviceQuietPeriod.objects.create(device=new_device, period_type="DAILY", start_time="22:00", end_time="07:00")

    r = _identify(user, NEW, previous_push_token=OLD)

    assert r.status_code == 200, r.content
    assert r.json()["device_id"] == old_device.id
    assert Device.objects.count() == 1
    old_device.refresh_from_db()
    assert old_device.push_token == NEW
    assert set(
        old_device.application_links.filter(is_active=True).values_list("application_id", flat=True)
    ) == {app.id, other_app.id}
    assert set(
        NotificationDelivery.objects.filter(device=old_device).values_list("notification_id", flat=True)
    ) == {n1.id, n2.id}
    assert old_device.quiet_periods.count() == 1


def test_rotation_when_new_token_row_belongs_to_another_user_falls_back(world):
    """Never merge another user's device: normal takeover of the new-token row."""
    user, other, app = world
    old_device = Device.objects.create(user=user, push_token=OLD)
    foreign_new = Device.objects.create(user=other, push_token=NEW)
    DeviceApplicationLink.objects.create(device=foreign_new, application=app)

    r = _identify(user, NEW, previous_push_token=OLD)

    assert r.status_code == 200
    assert r.json()["device_id"] == foreign_new.id
    assert r.json()["linked_applications"] == []
    old_device.refresh_from_db()
    assert old_device.push_token == OLD


# ------------------------------------------------------------------ item 6


def test_identify_reactivates_an_invalidated_device(world):
    user, _, _ = world
    device = Device.objects.create(
        user=user,
        push_token=OLD,
        push_token_status=DeviceTokenStatus.INVALID,
        is_active=False,
        invalidated_at=timezone.now(),
        invalidation_reason="invalid_token",
        failure_count=4,
    )

    r = _identify(user, OLD)

    assert r.status_code == 200
    device.refresh_from_db()
    assert device.push_token_status == DeviceTokenStatus.ACTIVE
    assert device.is_active is True
    assert device.invalidated_at is None
    assert device.failure_count == 0
