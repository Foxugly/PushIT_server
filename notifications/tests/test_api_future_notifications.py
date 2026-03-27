from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application, ApplicationQuietPeriod, QuietPeriodType
from devices.models import Device, DeviceApplicationLink, DeviceTokenStatus, DeviceQuietPeriod
from notifications.models import Notification, NotificationStatus


def _future_local_datetime(*, days: int = 1, hour: int = 10, minute: int = 30):
    local_candidate = timezone.localtime(timezone.now() + timedelta(days=days)).replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )
    if local_candidate <= timezone.localtime():
        local_candidate += timedelta(days=1)
    return local_candidate


def _create_target_device(app: Application, token: str) -> Device:
    device = Device.objects.create(
        push_token=token,
        push_token_status=DeviceTokenStatus.ACTIVE,
    )
    DeviceApplicationLink.objects.create(device=device, application=app)
    return device


@pytest.mark.django_db
def test_create_and_list_future_notifications():
    client = APIClient()
    user = User.objects.create_user(
        email="future@example.com",
        username="future",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Future App")
    device = _create_target_device(app, "token_future_11111111111111111111")
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    scheduled_for = timezone.now() + timedelta(hours=3)
    create_response = client.post(
        "/api/v1/notifications/",
        {
            "application_id": app.id,
            "device_ids": [device.id],
            "title": "Planifiée",
            "message": "Envoi plus tard",
            "scheduled_for": scheduled_for.isoformat(),
        },
        format="json",
    )

    assert create_response.status_code == 201
    assert create_response.data["status"] == NotificationStatus.SCHEDULED
    assert create_response.data["device_ids"] == [device.id]
    assert create_response.data["scheduled_for"] is not None

    list_response = client.get("/api/v1/notifications/future/")
    assert list_response.status_code == 200
    assert len(list_response.data) == 1
    assert list_response.data[0]["title"] == "Planifiée"


@pytest.mark.django_db
def test_update_and_delete_future_notification():
    client = APIClient()
    user = User.objects.create_user(
        email="future2@example.com",
        username="future2",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Future App")
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    notification = Notification.objects.create(
        application=app,
        title="Ancien titre",
        message="Ancien message",
        status=NotificationStatus.SCHEDULED,
        scheduled_for=timezone.now() + timedelta(hours=4),
    )

    patch_response = client.patch(
        f"/api/v1/notifications/future/{notification.id}/",
        {
            "title": "Nouveau titre",
            "scheduled_for": (timezone.now() + timedelta(hours=6)).isoformat(),
        },
        format="json",
    )
    assert patch_response.status_code == 200
    assert patch_response.data["title"] == "Nouveau titre"
    assert patch_response.data["status"] == NotificationStatus.SCHEDULED

    delete_response = client.delete(f"/api/v1/notifications/future/{notification.id}/")
    assert delete_response.status_code == 204
    assert not Notification.objects.filter(id=notification.id).exists()


@pytest.mark.django_db
def test_cannot_send_notification_scheduled_in_future():
    client = APIClient()
    user = User.objects.create_user(
        email="future3@example.com",
        username="future3",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Future App")
    notification = Notification.objects.create(
        application=app,
        title="Planifiée",
        message="Plus tard",
        status=NotificationStatus.SCHEDULED,
        scheduled_for=timezone.now() + timedelta(hours=2),
    )
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.post(f"/api/v1/notifications/{notification.id}/send/", {}, format="json")

    assert response.status_code == 409
    assert response.data["code"] == "notification_not_sendable"


@pytest.mark.django_db
def test_effective_scheduled_for_is_shifted_by_existing_quiet_period():
    client = APIClient()
    user = User.objects.create_user(
        email="future4@example.com",
        username="future4",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Future App")
    device = _create_target_device(app, "token_future_22222222222222222222")
    scheduled_for = timezone.now() + timedelta(hours=2)
    quiet_end = scheduled_for + timedelta(hours=3)
    ApplicationQuietPeriod.objects.create(
        application=app,
        name="Quiet window",
        start_at=scheduled_for - timedelta(minutes=15),
        end_at=quiet_end,
        is_active=True,
    )

    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.post(
        "/api/v1/notifications/",
        {
            "application_id": app.id,
            "device_ids": [device.id],
            "title": "Planifiee",
            "message": "Envoi plus tard",
            "scheduled_for": scheduled_for.isoformat(),
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.data["scheduled_for"] is not None
    assert response.data["effective_scheduled_for"] is not None
    assert response.data["effective_scheduled_for"] != response.data["scheduled_for"]

    notification = Notification.objects.get(id=response.data["id"])
    assert response.data["effective_scheduled_for"] == quiet_end
    assert notification.scheduled_for == scheduled_for


@pytest.mark.django_db
def test_effective_scheduled_for_reflects_quiet_period_created_after_notification():
    client = APIClient()
    user = User.objects.create_user(
        email="future5@example.com",
        username="future5",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Future App")
    notification = Notification.objects.create(
        application=app,
        title="Planifiee",
        message="Plus tard",
        status=NotificationStatus.SCHEDULED,
        scheduled_for=timezone.now() + timedelta(hours=4),
    )
    quiet_end = notification.scheduled_for + timedelta(hours=2)
    ApplicationQuietPeriod.objects.create(
        application=app,
        name="Late quiet window",
        start_at=notification.scheduled_for - timedelta(minutes=10),
        end_at=quiet_end,
        is_active=True,
    )
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.get(f"/api/v1/notifications/future/{notification.id}/")

    assert response.status_code == 200
    assert response.data["status"] == NotificationStatus.SCHEDULED
    assert response.data["effective_scheduled_for"] == quiet_end
    notification.refresh_from_db()
    assert notification.scheduled_for < quiet_end


@pytest.mark.django_db
def test_effective_scheduled_for_is_shifted_by_existing_recurring_quiet_period():
    client = APIClient()
    user = User.objects.create_user(
        email="future-recurring@example.com",
        username="future-recurring",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Future App")
    device = _create_target_device(app, "token_future_33333333333333333333")
    scheduled_for = _future_local_datetime(hour=10, minute=30)
    quiet_end = scheduled_for.replace(hour=12, minute=0, second=0, microsecond=0)
    ApplicationQuietPeriod.objects.create(
        application=app,
        name="Recurring quiet window",
        period_type=QuietPeriodType.RECURRING,
        recurrence_days=[scheduled_for.weekday()],
        start_time=scheduled_for.replace(hour=10, minute=0).time(),
        end_time=quiet_end.time(),
        is_active=True,
    )

    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.post(
        "/api/v1/notifications/",
        {
            "application_id": app.id,
            "device_ids": [device.id],
            "title": "Planifiee",
            "message": "Envoi plus tard",
            "scheduled_for": scheduled_for.isoformat(),
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.data["effective_scheduled_for"] == quiet_end


@pytest.mark.django_db
def test_device_quiet_period_does_not_change_notification_effective_scheduled_for():
    client = APIClient()
    user = User.objects.create_user(
        email="future-device-quiet@example.com",
        username="future-device-quiet",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Future App")
    device = Device.objects.create(
        push_token="token_32345678901234567890",
        push_token_status=DeviceTokenStatus.ACTIVE,
    )
    DeviceApplicationLink.objects.create(device=device, application=app)
    scheduled_for = _future_local_datetime(hour=10, minute=30)

    DeviceQuietPeriod.objects.create(
        device=device,
        name="Device DND",
        period_type=QuietPeriodType.RECURRING,
        recurrence_days=[scheduled_for.weekday()],
        start_time=scheduled_for.replace(hour=10, minute=0).time(),
        end_time=scheduled_for.replace(hour=12, minute=0).time(),
        is_active=True,
    )

    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.post(
        "/api/v1/notifications/",
        {
            "application_id": app.id,
            "device_ids": [device.id],
            "title": "Planifiee",
            "message": "Envoi plus tard",
            "scheduled_for": scheduled_for.isoformat(),
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.data["effective_scheduled_for"] == scheduled_for


@pytest.mark.django_db
def test_list_future_notifications_can_filter_by_effective_scheduled_range():
    client = APIClient()
    user = User.objects.create_user(
        email="future6@example.com",
        username="future6",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Future App")
    base_time = timezone.now() + timedelta(hours=2)

    ApplicationQuietPeriod.objects.create(
        application=app,
        name="Night quiet window",
        start_at=base_time - timedelta(minutes=10),
        end_at=base_time + timedelta(hours=4),
        is_active=True,
    )

    shifted_notification = Notification.objects.create(
        application=app,
        title="Shifted",
        message="Shifted by quiet period",
        status=NotificationStatus.SCHEDULED,
        scheduled_for=base_time,
    )
    direct_notification = Notification.objects.create(
        application=app,
        title="Direct",
        message="No quiet period impact",
        status=NotificationStatus.SCHEDULED,
        scheduled_for=base_time + timedelta(hours=5),
    )

    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.get(
        "/api/v1/notifications/future/",
        {
            "effective_scheduled_from": (base_time + timedelta(hours=3, minutes=30)).isoformat(),
            "effective_scheduled_to": (base_time + timedelta(hours=4, minutes=30)).isoformat(),
        },
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.data] == [shifted_notification.id]
    assert response.data[0]["effective_scheduled_for"] == base_time + timedelta(hours=4)
    assert direct_notification.id not in [item["id"] for item in response.data]


@pytest.mark.django_db
def test_list_future_notifications_can_order_by_effective_scheduled_for_desc():
    client = APIClient()
    user = User.objects.create_user(
        email="future8@example.com",
        username="future8",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Future App")
    base_time = timezone.now() + timedelta(hours=2)

    ApplicationQuietPeriod.objects.create(
        application=app,
        name="Short quiet window",
        start_at=base_time - timedelta(minutes=5),
        end_at=base_time + timedelta(hours=1),
        is_active=True,
    )

    earlier_effective = Notification.objects.create(
        application=app,
        title="Earlier effective",
        message="Earlier",
        status=NotificationStatus.SCHEDULED,
        scheduled_for=base_time,
    )
    later_effective = Notification.objects.create(
        application=app,
        title="Later effective",
        message="Later",
        status=NotificationStatus.SCHEDULED,
        scheduled_for=base_time + timedelta(hours=3),
    )

    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.get(
        "/api/v1/notifications/future/",
        {"ordering": "-effective_scheduled_for"},
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.data] == [later_effective.id, earlier_effective.id]


@pytest.mark.django_db
def test_list_future_notifications_can_filter_on_quiet_period_shift_flag():
    client = APIClient()
    user = User.objects.create_user(
        email="future9@example.com",
        username="future9",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=user, name="Future App")
    base_time = timezone.now() + timedelta(hours=2)

    ApplicationQuietPeriod.objects.create(
        application=app,
        name="Shifting quiet window",
        start_at=base_time - timedelta(minutes=5),
        end_at=base_time + timedelta(hours=1),
        is_active=True,
    )

    shifted_notification = Notification.objects.create(
        application=app,
        title="Shifted",
        message="Shifted",
        status=NotificationStatus.SCHEDULED,
        scheduled_for=base_time,
    )
    direct_notification = Notification.objects.create(
        application=app,
        title="Direct",
        message="Direct",
        status=NotificationStatus.SCHEDULED,
        scheduled_for=base_time + timedelta(hours=3),
    )

    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    shifted_response = client.get(
        "/api/v1/notifications/future/",
        {"has_quiet_period_shift": "true"},
    )
    direct_response = client.get(
        "/api/v1/notifications/future/",
        {"has_quiet_period_shift": "false"},
    )

    assert shifted_response.status_code == 200
    assert [item["id"] for item in shifted_response.data] == [shifted_notification.id]
    assert direct_response.status_code == 200
    assert direct_notification.id in [item["id"] for item in direct_response.data]
    assert shifted_notification.id not in [item["id"] for item in direct_response.data]


@pytest.mark.django_db
def test_list_future_notifications_rejects_invalid_effective_range():
    client = APIClient()
    user = User.objects.create_user(
        email="future7@example.com",
        username="future7",
        password="MotDePasseTresSolide123!",
    )
    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.get(
        "/api/v1/notifications/future/",
        {
            "effective_scheduled_from": (timezone.now() + timedelta(hours=3)).isoformat(),
            "effective_scheduled_to": (timezone.now() + timedelta(hours=2)).isoformat(),
        },
    )

    assert response.status_code == 400
    assert response.data["code"] == "validation_error"
    assert "effective_scheduled_to" in response.data["errors"]


@pytest.mark.django_db
def test_list_notifications_can_filter_by_application_status_and_shift_flag():
    client = APIClient()
    user = User.objects.create_user(
        email="future10@example.com",
        username="future10",
        password="MotDePasseTresSolide123!",
    )
    first_app = Application.objects.create(owner=user, name="First App")
    second_app = Application.objects.create(owner=user, name="Second App")
    base_time = timezone.now() + timedelta(hours=2)

    ApplicationQuietPeriod.objects.create(
        application=first_app,
        name="First quiet window",
        start_at=base_time - timedelta(minutes=5),
        end_at=base_time + timedelta(hours=1),
        is_active=True,
    )

    shifted_notification = Notification.objects.create(
        application=first_app,
        title="Shifted",
        message="Shifted",
        status=NotificationStatus.SCHEDULED,
        scheduled_for=base_time,
    )
    Notification.objects.create(
        application=first_app,
        title="Draft",
        message="Draft",
        status=NotificationStatus.DRAFT,
    )
    other_notification = Notification.objects.create(
        application=second_app,
        title="Other app",
        message="Other",
        status=NotificationStatus.SCHEDULED,
        scheduled_for=base_time + timedelta(hours=3),
    )

    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = client.get(
        "/api/v1/notifications/",
        {
            "application_id": first_app.id,
            "status": NotificationStatus.SCHEDULED,
            "has_quiet_period_shift": "true",
        },
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.data] == [shifted_notification.id]
    assert other_notification.id not in [item["id"] for item in response.data]
