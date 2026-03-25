from celery import shared_task
from django.utils import timezone

from .models import NotificationDelivery
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