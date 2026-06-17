from datetime import timedelta

import pytest
from unittest.mock import patch

from accounts.models import User
from applications.models import Application
from devices.models import Device, DeviceApplicationLink, DeviceTokenStatus
from django.utils import timezone

from notifications.models import Notification, NotificationDelivery, NotificationStatus
from notifications.tasks import (
    dispatch_scheduled_notifications_task,
    poll_inbound_mailbox_task,
    requeue_stuck_processing_notifications_task,
    send_notification_task,
)

VALID_PUSH_TOKEN = "token_11111111111111111111"

@pytest.mark.django_db
@patch("notifications.services.send_push_to_device")
def test_send_notification_task(mock_send, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True

    mock_send.return_value = "provider-123"

    user = User.objects.create_user(email="u1@example.com", password="1234")
    app = Application.objects.create(owner=user, name="App")
    device = Device.objects.create(push_token=VALID_PUSH_TOKEN, push_token_status=DeviceTokenStatus.ACTIVE)
    DeviceApplicationLink.objects.create(device=device, application=app)

    notification = Notification.objects.create(
        application=app,
        title="Hello",
        message="World",
        status=NotificationStatus.DRAFT,
    )

    result = send_notification_task(notification.id)

    assert result["sent_count"] == 1
    assert NotificationDelivery.objects.count() == 1


@pytest.mark.django_db
@patch("notifications.tasks.send_notification_task.delay")
def test_dispatch_scheduled_notifications_task(mock_delay):
    user = User.objects.create_user(email="u2@example.com", password="1234")
    app = Application.objects.create(owner=user, name="App")
    notification = Notification.objects.create(
        application=app,
        title="Later",
        message="World",
        status=NotificationStatus.SCHEDULED,
        scheduled_for=timezone.now() - timedelta(minutes=1),
    )

    result = dispatch_scheduled_notifications_task()

    assert result["queued_count"] == 1
    notification.refresh_from_db()
    assert notification.status == NotificationStatus.QUEUED
    mock_delay.assert_called_once_with(notification.id)


@pytest.mark.django_db
@patch("notifications.tasks.send_notification_task.delay")
def test_requeue_stuck_processing_requeues_stale_rows(mock_delay):
    user = User.objects.create_user(email="stuck@example.com", password="1234")
    app = Application.objects.create(owner=user, name="App")

    stale = Notification.objects.create(
        application=app,
        title="Stuck",
        message="World",
        status=NotificationStatus.PROCESSING,
        processing_started_at=timezone.now() - timedelta(minutes=30),
    )
    # Recently entered PROCESSING -> must NOT be touched.
    fresh = Notification.objects.create(
        application=app,
        title="Fresh",
        message="World",
        status=NotificationStatus.PROCESSING,
        processing_started_at=timezone.now() - timedelta(minutes=1),
    )

    result = requeue_stuck_processing_notifications_task()

    assert result["requeued_count"] == 1
    stale.refresh_from_db()
    fresh.refresh_from_db()
    assert stale.status == NotificationStatus.QUEUED
    assert stale.processing_started_at is None
    assert fresh.status == NotificationStatus.PROCESSING
    mock_delay.assert_called_once_with(stale.id)


@pytest.mark.django_db
@patch("notifications.tasks.send_notification_task.delay")
def test_requeue_stuck_processing_noop_when_none_stale(mock_delay):
    user = User.objects.create_user(email="nostuck@example.com", password="1234")
    app = Application.objects.create(owner=user, name="App")
    Notification.objects.create(
        application=app,
        title="Sent",
        message="World",
        status=NotificationStatus.SENT,
    )

    result = requeue_stuck_processing_notifications_task()

    assert result["requeued_count"] == 0
    mock_delay.assert_not_called()


@pytest.mark.django_db
@patch("notifications.tasks.poll_inbound_mailbox")
def test_poll_inbound_mailbox_task(mock_poll):
    mock_poll.return_value = {
        "status": "ok",
        "processed_count": 2,
        "created_count": 1,
        "rejected_count": 1,
    }

    result = poll_inbound_mailbox_task()

    assert result["status"] == "ok"
    assert result["processed_count"] == 2
    mock_poll.assert_called_once_with()
