import pytest
from unittest.mock import patch

from accounts.models import User
from config.metrics import render_metrics, reset_metrics
from applications.models import Application
from devices.models import Device, DeviceApplicationLink, DeviceTokenStatus
from notifications.models import Notification, NotificationDelivery, NotificationStatus, DeliveryStatus
from notifications.push import InvalidPushTokenError, TemporaryPushProviderError
from notifications.services import send_notification

VALID_TOKEN_1 = "token_11111111111111111111"
VALID_TOKEN_2 = "token_22222222222222222222"


@pytest.mark.django_db
def test_get_devices_for_app():
    user = User.objects.create_user(username="u1", password="1234")
    app = Application.objects.create(owner=user, name="App")

    device1 = Device.objects.create(push_token=VALID_TOKEN_1, push_token_status=DeviceTokenStatus.ACTIVE)
    device2 = Device.objects.create(push_token=VALID_TOKEN_2, push_token_status=DeviceTokenStatus.ACTIVE)

    DeviceApplicationLink.objects.create(device=device1, application=app)
    DeviceApplicationLink.objects.create(device=device2, application=app)

    devices = Device.objects.filter(
        application_links__application=app,
        application_links__is_active=True,
    )

    assert devices.count() == 2


@pytest.mark.django_db
@patch("notifications.services.send_push_to_device")
def test_send_notification_success(mock_send):
    reset_metrics()
    mock_send.return_value = "provider-123"

    user = User.objects.create_user(username="u1", password="1234")
    app = Application.objects.create(owner=user, name="App")

    device1 = Device.objects.create(
        push_token=VALID_TOKEN_1,
        push_token_status=DeviceTokenStatus.ACTIVE,
    )
    device2 = Device.objects.create(
        push_token=VALID_TOKEN_2,
        push_token_status=DeviceTokenStatus.ACTIVE,
    )

    DeviceApplicationLink.objects.create(device=device1, application=app)
    DeviceApplicationLink.objects.create(device=device2, application=app)

    notification = Notification.objects.create(
        application=app,
        title="Hello",
        message="World",
        status=NotificationStatus.DRAFT,
    )

    result = send_notification(notification.id)

    assert result["target_count"] == 2
    assert result["sent_count"] == 2
    assert result["failed_count"] == 0
    assert result["skipped_count"] == 0

    notification.refresh_from_db()
    assert notification.status == NotificationStatus.SENT

    assert NotificationDelivery.objects.count() == 2
    assert NotificationDelivery.objects.filter(status=DeliveryStatus.SENT).count() == 2
    assert mock_send.call_count == 2

    metrics = render_metrics()
    assert 'pushit_notification_send_total{outcome="started"} 1.0' in metrics
    assert 'pushit_notification_send_total{outcome="sent"} 1.0' in metrics
    assert 'pushit_notification_delivery_total{outcome="sent"} 2.0' in metrics


@pytest.mark.django_db
@patch("notifications.services.send_push_to_device")
def test_send_notification_partial_failure(mock_send):
    def fake_send(push_token, title, message):
        if push_token == VALID_TOKEN_2:
            raise RuntimeError("FCM error")
        return "provider-ok"

    mock_send.side_effect = fake_send

    user = User.objects.create_user(username="u1", password="1234")
    app = Application.objects.create(owner=user, name="App")

    device1 = Device.objects.create(
        push_token=VALID_TOKEN_1,
        push_token_status=DeviceTokenStatus.ACTIVE,
    )
    device2 = Device.objects.create(
        push_token=VALID_TOKEN_2,
        push_token_status=DeviceTokenStatus.ACTIVE,
    )

    DeviceApplicationLink.objects.create(device=device1, application=app)
    DeviceApplicationLink.objects.create(device=device2, application=app)

    notification = Notification.objects.create(
        application=app,
        title="Hello",
        message="World",
        status=NotificationStatus.DRAFT,
    )

    result = send_notification(notification.id)

    assert result["target_count"] == 2
    assert result["sent_count"] == 1
    assert result["failed_count"] == 1
    assert result["skipped_count"] == 0

    notification.refresh_from_db()
    assert notification.status == NotificationStatus.PARTIAL

    assert NotificationDelivery.objects.filter(status=DeliveryStatus.SENT).count() == 1
    assert NotificationDelivery.objects.filter(status=DeliveryStatus.PENDING).count() == 1

    failed_delivery = NotificationDelivery.objects.get(device=device2, notification=notification)
    assert failed_delivery.status == DeliveryStatus.PENDING
    assert failed_delivery.attempt_count == 1
    assert failed_delivery.next_retry_at is not None
    assert failed_delivery.error_message == "FCM error"


@pytest.mark.django_db
@patch("notifications.services.send_push_to_device")
def test_send_notification_without_devices(mock_send):
    user = User.objects.create_user(username="u1", password="1234")
    app = Application.objects.create(owner=user, name="App")

    notification = Notification.objects.create(
        application=app,
        title="Hello",
        message="World",
        status=NotificationStatus.DRAFT,
    )

    result = send_notification(notification.id)

    assert result["target_count"] == 0
    assert result["sent_count"] == 0
    assert result["failed_count"] == 0
    assert result["skipped_count"] == 0

    notification.refresh_from_db()
    assert notification.status == NotificationStatus.NO_TARGET

    mock_send.assert_not_called()


@pytest.mark.django_db
@patch("notifications.services.send_push_to_device")
def test_send_notification_is_idempotent_for_already_sent_deliveries(mock_send):
    mock_send.return_value = "provider-123"

    user = User.objects.create_user(username="u1", password="1234")
    app = Application.objects.create(owner=user, name="App")

    device = Device.objects.create(
        push_token=VALID_TOKEN_1,
        push_token_status=DeviceTokenStatus.ACTIVE,
    )
    DeviceApplicationLink.objects.create(device=device, application=app)

    notification = Notification.objects.create(
        application=app,
        title="Hello",
        message="World",
        status=NotificationStatus.DRAFT,
    )

    first_result = send_notification(notification.id)
    second_result = send_notification(notification.id)

    assert first_result["target_count"] == 1
    assert first_result["sent_count"] == 1
    assert first_result["failed_count"] == 0
    assert first_result["skipped_count"] == 0

    assert second_result["target_count"] == 0
    assert second_result["sent_count"] == 0
    assert second_result["failed_count"] == 0
    assert second_result["skipped_count"] == 0

    assert mock_send.call_count == 1

    notification.refresh_from_db()
    assert notification.status == NotificationStatus.SENT

    assert NotificationDelivery.objects.count() == 1
    assert NotificationDelivery.objects.filter(status=DeliveryStatus.SENT).count() == 1

@pytest.mark.django_db
@patch("notifications.services.send_push_to_device")
def test_failed_delivery_is_scheduled_for_retry(mock_send):
    mock_send.side_effect = RuntimeError("Temporary provider error")

    user = User.objects.create_user(username="u1", password="1234")
    app = Application.objects.create(owner=user, name="App")
    device = Device.objects.create(
        push_token="token_11111111111111111111",
        push_token_status=DeviceTokenStatus.ACTIVE,
    )
    DeviceApplicationLink.objects.create(device=device, application=app)

    notification = Notification.objects.create(
        application=app,
        title="Hello",
        message="World",
        status=NotificationStatus.QUEUED,
    )

    result = send_notification(notification.id)

    delivery = NotificationDelivery.objects.get(notification=notification, device=device)

    assert result["failed_count"] == 1
    assert delivery.status == DeliveryStatus.PENDING
    assert delivery.attempt_count == 1
    assert delivery.next_retry_at is not None

@pytest.mark.django_db
@patch("notifications.services.send_push_to_device")
def test_invalid_push_token_invalidates_device(mock_send):
    mock_send.side_effect = InvalidPushTokenError("Token invalid or unregistered")

    user = User.objects.create_user(username="u1", password="1234")
    app = Application.objects.create(owner=user, name="App")

    device = Device.objects.create(
        push_token=VALID_TOKEN_1,
        push_token_status=DeviceTokenStatus.ACTIVE,
        is_active=True,
    )
    DeviceApplicationLink.objects.create(device=device, application=app)

    notification = Notification.objects.create(
        application=app,
        title="Hello",
        message="World",
        status=NotificationStatus.DRAFT,
    )

    result = send_notification(notification.id)

    device.refresh_from_db()
    delivery = NotificationDelivery.objects.get(notification=notification, device=device)
    notification.refresh_from_db()

    assert result["target_count"] == 1
    assert result["sent_count"] == 0
    assert result["failed_count"] == 1

    assert device.push_token_status == DeviceTokenStatus.INVALID
    assert device.is_active is False
    assert device.invalidated_at is not None
    assert device.invalidation_reason == "invalid_token"
    assert device.failure_count == 1

    assert delivery.status == DeliveryStatus.PENDING
    assert delivery.attempt_count == 1
    assert "Token invalid or unregistered" in delivery.error_message

    assert notification.status == NotificationStatus.FAILED

@pytest.mark.django_db
@patch("notifications.services.send_push_to_device")
def test_temporary_provider_error_does_not_invalidate_device(mock_send):
    mock_send.side_effect = TemporaryPushProviderError("Temporary provider error")

    user = User.objects.create_user(username="u1", password="1234")
    app = Application.objects.create(owner=user, name="App")

    device = Device.objects.create(
        push_token=VALID_TOKEN_1,
        push_token_status=DeviceTokenStatus.ACTIVE,
        is_active=True,
    )
    DeviceApplicationLink.objects.create(device=device, application=app)

    notification = Notification.objects.create(
        application=app,
        title="Hello",
        message="World",
        status=NotificationStatus.DRAFT,
    )

    result = send_notification(notification.id)

    device.refresh_from_db()
    delivery = NotificationDelivery.objects.get(notification=notification, device=device)
    notification.refresh_from_db()

    assert result["target_count"] == 1
    assert result["sent_count"] == 0
    assert result["failed_count"] == 1

    assert device.push_token_status == DeviceTokenStatus.ACTIVE
    assert device.is_active is True
    assert device.invalidated_at is None
    assert device.invalidation_reason == ""
    assert device.failure_count == 1

    assert delivery.status == DeliveryStatus.PENDING
    assert delivery.attempt_count == 1
    assert "Temporary provider error" in delivery.error_message

    assert notification.status == NotificationStatus.FAILED
