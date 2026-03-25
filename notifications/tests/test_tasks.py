import pytest
from unittest.mock import patch

from accounts.models import User
from applications.models import Application
from devices.models import Device, DeviceApplicationLink, DeviceTokenStatus
from notifications.models import Notification, NotificationDelivery, NotificationStatus
from notifications.tasks import send_notification_task

VALID_PUSH_TOKEN = "token_11111111111111111111"

@pytest.mark.django_db
@patch("notifications.services.send_push_to_device")
def test_send_notification_task(mock_send, settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True

    mock_send.return_value = "provider-123"

    user = User.objects.create_user(username="u1", password="1234")
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