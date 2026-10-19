"""Audit 2026-10-19 — item 1 (+ dispatch half of item 9).

A delivery row is created when the notification is created (JWT path) or on
its first send. Between that moment and the actual push (scheduled send,
quiet-period deferral, retry backoff) the recipient may have unlinked, the
token may have been invalidated, the application deactivated or the owner's
subscription may have lapsed. Eligibility is therefore rechecked right before
each push, never only at creation.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone

from accounts.models import User
from applications.models import Application
from billing.models import Subscription
from devices.models import Device, DeviceApplicationLink, DeviceTokenStatus
from notifications.models import (
    DeliveryStatus,
    Notification,
    NotificationDelivery,
    NotificationStatus,
)
from notifications.services import send_notification
from notifications.tasks import (
    dispatch_scheduled_notifications_task,
    retry_pending_deliveries_task,
)

BILLING_ON = {
    "BILLING_BASE_URL": "https://billing-api.foxugly.com",
    "BILLING_APP_SECRET": "secret-de-test",
    "BILLING_APP_SLUG": "pushit",
}

TOKEN_A = "token_recheck_aaaaaaaaaaaaaaaaaaaa"
TOKEN_B = "token_recheck_bbbbbbbbbbbbbbbbbbbb"


def _setup(n_devices=2):
    owner = User.objects.create_user(email="owner@example.com", password="pw12345678")
    app = Application.objects.create(owner=owner, name="App")
    devices = []
    for token in [TOKEN_A, TOKEN_B][:n_devices]:
        device = Device.objects.create(push_token=token)
        DeviceApplicationLink.objects.create(device=device, application=app)
        devices.append(device)
    notification = Notification.objects.create(
        application=app, title="T", message="M", status=NotificationStatus.DRAFT
    )
    for device in devices:
        NotificationDelivery.objects.create(notification=notification, device=device)
    return owner, app, devices, notification


@pytest.mark.django_db
@patch("notifications.services.send_push_to_device", return_value="pid")
def test_unlinked_device_is_not_pushed_and_delivery_is_cancelled(mock_send):
    _, app, (dev_a, dev_b), notification = _setup()
    DeviceApplicationLink.objects.filter(device=dev_b, application=app).update(is_active=False)

    result = send_notification(notification.id)

    pushed_tokens = [c.kwargs["push_token"] for c in mock_send.call_args_list]
    assert pushed_tokens == [TOKEN_A]
    cancelled = NotificationDelivery.objects.get(notification=notification, device=dev_b)
    assert cancelled.status == DeliveryStatus.FAILED
    assert "device_unlinked" in cancelled.error_message
    assert cancelled.next_retry_at is None
    assert result["sent_count"] == 1
    assert result["failed_count"] == 1


@pytest.mark.django_db
@patch("notifications.services.send_push_to_device", return_value="pid")
def test_inactive_push_token_is_not_pushed(mock_send):
    _, _, (dev_a, dev_b), notification = _setup()
    Device.objects.filter(pk=dev_b.pk).update(push_token_status=DeviceTokenStatus.REVOKED)

    send_notification(notification.id)

    assert mock_send.call_count == 1
    cancelled = NotificationDelivery.objects.get(notification=notification, device=dev_b)
    assert cancelled.status == DeliveryStatus.FAILED
    assert "push_token_inactive" in cancelled.error_message


@pytest.mark.django_db
@patch("notifications.services.send_push_to_device", return_value="pid")
def test_inactive_device_is_not_pushed(mock_send):
    _, _, (dev_a, dev_b), notification = _setup()
    Device.objects.filter(pk=dev_b.pk).update(is_active=False)

    send_notification(notification.id)

    assert mock_send.call_count == 1
    cancelled = NotificationDelivery.objects.get(notification=notification, device=dev_b)
    assert cancelled.status == DeliveryStatus.FAILED


@pytest.mark.django_db
@patch("notifications.services.send_push_to_device", return_value="pid")
def test_inactive_application_sends_nothing(mock_send):
    _, app, _, notification = _setup()
    Application.objects.filter(pk=app.pk).update(is_active=False)

    send_notification(notification.id)

    assert mock_send.call_count == 0
    notification.refresh_from_db()
    assert notification.status == NotificationStatus.FAILED
    reasons = set(notification.deliveries.values_list("error_message", flat=True))
    assert all("application_inactive" in r for r in reasons)


@pytest.mark.django_db
@patch("notifications.services.send_push_to_device", return_value="pid")
def test_scheduled_dispatch_rechecks_links(mock_send):
    _, app, (dev_a, dev_b), notification = _setup()
    Notification.objects.filter(pk=notification.pk).update(
        status=NotificationStatus.SCHEDULED,
        scheduled_for=timezone.now() - timedelta(minutes=1),
    )
    DeviceApplicationLink.objects.filter(device=dev_a, application=app).update(is_active=False)

    dispatch_scheduled_notifications_task()

    pushed_tokens = [c.kwargs["push_token"] for c in mock_send.call_args_list]
    assert pushed_tokens == [TOKEN_B]


@pytest.mark.django_db
@patch("notifications.services.send_push_to_device", return_value="pid")
def test_retry_rechecks_links(mock_send):
    _, app, (dev_a, dev_b), notification = _setup()
    Notification.objects.filter(pk=notification.pk).update(status=NotificationStatus.PARTIAL)
    NotificationDelivery.objects.filter(notification=notification).update(
        status=DeliveryStatus.PENDING,
        attempt_count=1,
        next_retry_at=timezone.now() - timedelta(minutes=1),
    )
    DeviceApplicationLink.objects.filter(device=dev_b, application=app).update(is_active=False)

    retry_pending_deliveries_task()

    pushed_tokens = [c.kwargs["push_token"] for c in mock_send.call_args_list]
    assert pushed_tokens == [TOKEN_A]


# ---------------------------------------------------------------- item 9 (dispatch)


@pytest.mark.django_db
@override_settings(**BILLING_ON)
@patch("notifications.services.send_push_to_device", return_value="pid")
def test_scheduled_dispatch_skips_when_owner_subscription_expired(mock_send):
    owner, _, _, notification = _setup()
    Subscription.objects.create(user=owner, is_paid=False)
    Notification.objects.filter(pk=notification.pk).update(
        status=NotificationStatus.SCHEDULED,
        scheduled_for=timezone.now() - timedelta(minutes=1),
    )

    dispatch_scheduled_notifications_task()

    assert mock_send.call_count == 0
    notification.refresh_from_db()
    assert notification.status == NotificationStatus.FAILED
    reasons = set(notification.deliveries.values_list("error_message", flat=True))
    assert reasons and all("subscription_required" in r for r in reasons)
    assert set(notification.deliveries.values_list("status", flat=True)) == {DeliveryStatus.FAILED}


@pytest.mark.django_db
@override_settings(**BILLING_ON)
@patch("notifications.services.send_push_to_device", return_value="pid")
def test_retry_skips_when_owner_subscription_expired(mock_send):
    owner, _, _, notification = _setup()
    Subscription.objects.create(user=owner, is_paid=False)
    Notification.objects.filter(pk=notification.pk).update(status=NotificationStatus.PARTIAL)
    NotificationDelivery.objects.filter(notification=notification).update(
        attempt_count=1, next_retry_at=timezone.now() - timedelta(minutes=1)
    )

    retry_pending_deliveries_task()

    assert mock_send.call_count == 0
    notification.refresh_from_db()
    assert notification.status == NotificationStatus.FAILED


@pytest.mark.django_db
@override_settings(**BILLING_ON)
@patch("notifications.services.send_push_to_device", return_value="pid")
def test_paid_owner_still_dispatches(mock_send):
    owner, _, _, notification = _setup()
    Subscription.objects.create(user=owner, is_paid=True, status="active")

    send_notification(notification.id)

    assert mock_send.call_count == 2


@pytest.mark.django_db
@patch("notifications.services.send_push_to_device", return_value="pid")
def test_billing_unconfigured_dispatches_without_subscription(mock_send):
    """Billing not wired (dev/CI, or before go-live): everything stays open."""
    _, _, _, notification = _setup()

    send_notification(notification.id)

    assert mock_send.call_count == 2
