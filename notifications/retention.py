"""GDPR retention: purge personal data past its retention window.

Durations are settings-driven (``RETENTION_*_DAYS``, see config/settings/base.py)
and run daily from ``purge_expired_personal_data_task``.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)


def delete_notification_image_files(names) -> None:
    """Remove notification images from storage (a row delete leaves the file)."""
    for name in names:
        if not name:
            continue
        try:
            default_storage.delete(name)
        except Exception:
            logger.exception("notification_image_delete_failed", extra={"image": name})


def purge_expired_personal_data() -> dict:
    from devices.models import Device, DeviceTokenStatus
    from notifications.models import InboundEmailIngestionLog, Notification, NotificationStatus

    now = timezone.now()
    notif_cutoff = now - timedelta(days=settings.RETENTION_NOTIFICATION_DAYS)
    log_cutoff = now - timedelta(days=settings.RETENTION_INBOUND_EMAIL_LOG_DAYS)
    device_cutoff = now - timedelta(days=settings.RETENTION_INVALID_DEVICE_DAYS)

    # Still-pending notifications (scheduled for later, queued, mid-send) are
    # kept whatever their age; deliveries cascade with their notification.
    notifications = Notification.objects.filter(created_at__lt=notif_cutoff).exclude(
        status__in=[NotificationStatus.SCHEDULED, NotificationStatus.QUEUED, NotificationStatus.PROCESSING]
    )
    with transaction.atomic():
        images = list(
            notifications.exclude(image="").exclude(image__isnull=True).values_list("image", flat=True)
        )
        notifications_deleted = notifications.delete()[1].get("notifications.Notification", 0)
        logs_deleted = InboundEmailIngestionLog.objects.filter(processed_at__lt=log_cutoff).delete()[1].get(
            "notifications.InboundEmailIngestionLog", 0
        )
        devices_deleted = Device.objects.filter(
            push_token_status__in=[DeviceTokenStatus.INVALID, DeviceTokenStatus.REVOKED],
        ).filter(
            Q(invalidated_at__lt=device_cutoff) | Q(invalidated_at__isnull=True, created_at__lt=device_cutoff)
        ).delete()[1].get("devices.Device", 0)
        transaction.on_commit(lambda: delete_notification_image_files(images))

    result = {
        "notifications_deleted": notifications_deleted,
        "inbound_email_logs_deleted": logs_deleted,
        "devices_deleted": devices_deleted,
    }
    logger.info("retention_purge_done", extra=result)
    return result
