"""Daily GDPR retention purge (purge_expired_personal_data_task)."""
from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models import User
from applications.models import Application
from devices.models import Device, DeviceTokenStatus
from notifications.models import (
    InboundEmailIngestionLog,
    InboundEmailIngestionStatus,
    InboundEmailSource,
    Notification,
    NotificationDelivery,
    NotificationStatus,
)
from notifications.tasks import purge_expired_personal_data_task


@pytest.fixture(autouse=True)
def _no_exchange(monkeypatch):
    monkeypatch.setattr("exchange.integration.provision_alias_for_application", lambda *a, **k: None)


@pytest.fixture
def app(db):
    owner = User.objects.create_user(email="owner@example.com", password="MotDePasseTresSolide123!")
    return Application.objects.create(owner=owner, name="App")


def _age(model, obj, field, days):
    model.objects.filter(pk=obj.pk).update(**{field: timezone.now() - timedelta(days=days)})


@pytest.mark.django_db
def test_purges_old_finished_notifications_and_their_deliveries(app):
    device = Device.objects.create(push_token="tok")
    old = Notification.objects.create(application=app, title="o", message="m", status=NotificationStatus.SENT)
    NotificationDelivery.objects.create(notification=old, device=device)
    _age(Notification, old, "created_at", 366)
    recent = Notification.objects.create(application=app, title="r", message="m", status=NotificationStatus.SENT)
    _age(Notification, recent, "created_at", 300)
    # Still pending in the future: never purged, whatever its age.
    scheduled = Notification.objects.create(
        application=app, title="s", message="m", status=NotificationStatus.SCHEDULED
    )
    _age(Notification, scheduled, "created_at", 400)

    result = purge_expired_personal_data_task()

    assert set(Notification.objects.values_list("pk", flat=True)) == {recent.pk, scheduled.pk}
    assert not NotificationDelivery.objects.exists()
    assert result["notifications_deleted"] == 1


@pytest.mark.django_db
def test_purges_old_inbound_email_logs(app):
    old = InboundEmailIngestionLog.objects.create(
        source=InboundEmailSource.POLLING, status=InboundEmailIngestionStatus.CREATED, sender="a@x.com"
    )
    _age(InboundEmailIngestionLog, old, "processed_at", 91)
    recent = InboundEmailIngestionLog.objects.create(
        source=InboundEmailSource.POLLING, status=InboundEmailIngestionStatus.CREATED, sender="b@x.com"
    )
    _age(InboundEmailIngestionLog, recent, "processed_at", 89)

    result = purge_expired_personal_data_task()

    assert list(InboundEmailIngestionLog.objects.values_list("pk", flat=True)) == [recent.pk]
    assert result["inbound_email_logs_deleted"] == 1


@pytest.mark.django_db
def test_purges_devices_invalid_or_revoked_for_more_than_30_days():
    now = timezone.now()
    old_invalid = Device.objects.create(
        push_token="t1", push_token_status=DeviceTokenStatus.INVALID, invalidated_at=now - timedelta(days=31)
    )
    old_revoked = Device.objects.create(
        push_token="t2", push_token_status=DeviceTokenStatus.REVOKED, invalidated_at=now - timedelta(days=45)
    )
    recent_invalid = Device.objects.create(
        push_token="t3", push_token_status=DeviceTokenStatus.INVALID, invalidated_at=now - timedelta(days=5)
    )
    # Invalid with no invalidated_at: falls back on created_at.
    legacy_invalid = Device.objects.create(push_token="t4", push_token_status=DeviceTokenStatus.INVALID)
    _age(Device, legacy_invalid, "created_at", 60)
    old_active = Device.objects.create(push_token="t5")
    _age(Device, old_active, "created_at", 400)

    result = purge_expired_personal_data_task()

    remaining = set(Device.objects.values_list("pk", flat=True))
    assert remaining == {recent_invalid.pk, old_active.pk}
    assert {old_invalid.pk, old_revoked.pk, legacy_invalid.pk}.isdisjoint(remaining)
    assert result["devices_deleted"] == 3


@pytest.mark.django_db
def test_retention_durations_come_from_settings(app, settings):
    settings.RETENTION_NOTIFICATION_DAYS = 10
    settings.RETENTION_INBOUND_EMAIL_LOG_DAYS = 10
    settings.RETENTION_INVALID_DEVICE_DAYS = 10
    notif = Notification.objects.create(application=app, title="o", message="m", status=NotificationStatus.FAILED)
    _age(Notification, notif, "created_at", 11)
    log = InboundEmailIngestionLog.objects.create(
        source=InboundEmailSource.POLLING, status=InboundEmailIngestionStatus.REJECTED
    )
    _age(InboundEmailIngestionLog, log, "processed_at", 11)
    Device.objects.create(
        push_token="t", push_token_status=DeviceTokenStatus.INVALID,
        invalidated_at=timezone.now() - timedelta(days=11),
    )

    result = purge_expired_personal_data_task()

    assert result == {"notifications_deleted": 1, "inbound_email_logs_deleted": 1, "devices_deleted": 1}


def test_retention_task_is_in_beat_schedule(settings):
    entry = settings.CELERY_BEAT_SCHEDULE["pushit-purge-expired-personal-data"]
    assert entry["task"] == "notifications.tasks.purge_expired_personal_data_task"
