from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Notification, NotificationDelivery, NotificationStatus
from .inbound_mailbox import poll_inbound_mailbox
from .services import send_notification
# Re-exported so Celery's autodiscover (which scans each app's tasks module)
# reliably registers the webhook callback task defined in webhooks.py.
from .webhooks import send_webhook_callback_task  # noqa: F401

# A notification that has been PROCESSING longer than this is considered stranded
# (the worker child was recycled/crashed mid-send) and is requeued. Generous by
# default so a genuinely slow send is never requeued under it.
DEFAULT_PROCESSING_STUCK_MINUTES = 15


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
def requeue_stuck_processing_notifications_task():
    """Watchdog: requeue notifications stranded in PROCESSING.

    If a worker child is recycled (CELERY_WORKER_MAX_*_PER_CHILD) or crashes
    mid-send, the notification stays PROCESSING forever and its PENDING
    deliveries are never retried. This periodic task resets rows that have been
    PROCESSING beyond a threshold back to QUEUED so the dispatcher re-sends them.

    Safety / idempotency:
    - The reset is a single conditional UPDATE filtered on status=PROCESSING +
      a stale `processing_started_at`, so it never races a fresh acquisition
      (which always rewrites `processing_started_at` to now()).
    - Re-dispatching is idempotent w.r.t. deliveries: `send_notification`
      re-acquires the notification and skips any delivery already marked SENT,
      so already-pushed devices are not pushed again.
    """
    stuck_minutes = getattr(
        settings, "NOTIFICATION_PROCESSING_STUCK_MINUTES", DEFAULT_PROCESSING_STUCK_MINUTES
    )
    cutoff = timezone.now() - timedelta(minutes=stuck_minutes)

    stuck_ids = list(
        Notification.objects.filter(
            status=NotificationStatus.PROCESSING,
            processing_started_at__isnull=False,
            processing_started_at__lt=cutoff,
        )
        .values_list("id", flat=True)
        .order_by("id")
    )

    requeued_count = 0
    for notification_id in stuck_ids:
        # Conditional UPDATE: only requeue if it is *still* stuck (guards against
        # a concurrent legitimate finish between the scan and the update).
        updated = Notification.objects.filter(
            id=notification_id,
            status=NotificationStatus.PROCESSING,
            processing_started_at__isnull=False,
            processing_started_at__lt=cutoff,
        ).update(
            status=NotificationStatus.QUEUED,
            processing_started_at=None,
        )
        if updated:
            send_notification_task.delay(notification_id)
            requeued_count += 1

    return {"requeued_count": requeued_count}


@shared_task
def poll_inbound_mailbox_task():
    return poll_inbound_mailbox()


@shared_task
def flush_expired_tokens_task():
    """Prune expired simplejwt outstanding/blacklisted tokens.

    Mirrors `manage.py flushexpiredtokens`: with ROTATE_REFRESH_TOKENS +
    BLACKLIST_AFTER_ROTATION the OutstandingToken / BlacklistedToken tables grow
    one row per refresh and are never reclaimed on their own. Delete every token
    already past its `expires_at` — blacklisted rows first to respect the FK from
    BlacklistedToken -> OutstandingToken. Returns the row counts deleted.
    """
    from rest_framework_simplejwt.token_blacklist.models import (
        BlacklistedToken,
        OutstandingToken,
    )

    now = timezone.now()
    blacklisted_deleted, _ = BlacklistedToken.objects.filter(
        token__expires_at__lt=now
    ).delete()
    outstanding_deleted, _ = OutstandingToken.objects.filter(
        expires_at__lt=now
    ).delete()
    return {
        "blacklisted_deleted": blacklisted_deleted,
        "outstanding_deleted": outstanding_deleted,
    }
