from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import Notification, NotificationDelivery, NotificationStatus
from .inbound_mailbox import poll_inbound_mailbox
from .services import send_notification


@shared_task
def send_notification_task(notification_id: int):
    return send_notification(notification_id)


@shared_task
def retry_pending_deliveries_task():
    """
    Reprend les notifications ayant au moins une delivery en attente de retry.
    """
    notification_ids = (
        NotificationDelivery.objects
        .filter(
            status="pending",
            next_retry_at__isnull=False,
            next_retry_at__lte=timezone.now(),
        )
        .values_list("notification_id", flat=True)
        .distinct()
    )

    for notification_id in notification_ids:
        send_notification_task.delay(notification_id)


@shared_task
def dispatch_scheduled_notifications_task():
    scheduled_ids = list(
        Notification.objects.filter(
            status=NotificationStatus.SCHEDULED,
            scheduled_for__isnull=False,
            scheduled_for__lte=timezone.now(),
        )
        .values_list("id", flat=True)
        .order_by("id")
    )

    queued_count = 0
    for notification_id in scheduled_ids:
        with transaction.atomic():
            notification = (
                Notification.objects.select_for_update(skip_locked=True)
                .filter(id=notification_id)
                .first()
            )
            if notification is None:
                continue
            if notification.status != NotificationStatus.SCHEDULED:
                continue
            if notification.scheduled_for is None or notification.scheduled_for > timezone.now():
                continue

            notification.status = NotificationStatus.QUEUED
            notification.save(update_fields=["status"])

        send_notification_task.delay(notification_id)
        queued_count += 1

    return {"queued_count": queued_count}


@shared_task
def poll_inbound_mailbox_task():
    return poll_inbound_mailbox()
