from __future__ import annotations
from dataclasses import dataclass
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from config.metrics import increment_counter
from devices.models import Device, DeviceTokenStatus
from .models import Notification, NotificationDelivery, NotificationStatus, DeliveryStatus
from .push import (
    send_push_to_device,
    InvalidPushTokenError,
    TemporaryPushProviderError,
    PushProviderError,
)
from .scheduling import get_quiet_period_end_for_application, get_quiet_period_end_from_iterable
from .webhooks import send_webhook_callback

logger = logging.getLogger(__name__)

MAX_DELIVERY_ATTEMPTS = 3

ALLOWED_NOTIFICATION_STATUSES_TO_START = {
    NotificationStatus.DRAFT,
    NotificationStatus.QUEUED,
    NotificationStatus.FAILED,
    NotificationStatus.PARTIAL,
}

@dataclass(frozen=True)
class SendNotificationResult:
    notification_id: int
    target_count: int
    sent_count: int
    failed_count: int
    skipped_count: int

    def as_dict(self) -> dict:
        return {
            "notification_id": self.notification_id,
            "target_count": self.target_count,
            "sent_count": self.sent_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
        }

def _can_retry_delivery(delivery: NotificationDelivery) -> bool:
    if delivery.status == DeliveryStatus.SENT:
        return False
    return delivery.attempt_count < MAX_DELIVERY_ATTEMPTS


def _compute_next_retry(attempt_count: int):
    """
    Backoff simple : 1 min, 5 min, 15 min
    """
    delays = {
        1: 1,
        2: 5,
        3: 15,
    }
    minutes = delays.get(attempt_count, 30)
    return timezone.now() + timedelta(minutes=minutes)

def get_target_devices_for_notification(notification: Notification):
    return (
        Device.objects.filter(
            application_links__application=notification.application,
            application_links__is_active=True,
            push_token_status=DeviceTokenStatus.ACTIVE,
        )
        .distinct()
        .order_by("id")
    )


def get_target_deliveries_for_notification(notification: Notification) -> list[NotificationDelivery]:
    deliveries = list(
        notification.deliveries.select_related("device")
        .prefetch_related("device__quiet_periods")
        .order_by("device_id")
    )
    if deliveries:
        return deliveries

    devices = list(
        get_target_devices_for_notification(notification).prefetch_related("quiet_periods")
    )
    deliveries = []
    for device in devices:
        delivery = _get_or_create_delivery(notification, device)
        delivery.device = device
        deliveries.append(delivery)
    return deliveries


def get_current_quiet_period_end(notification: Notification, at=None):
    at = at or timezone.now()
    return get_quiet_period_end_for_application(notification.application, at)


def get_current_device_quiet_period_end(device: Device, at=None):
    at = at or timezone.now()
    quiet_periods = getattr(device, "_prefetched_objects_cache", {}).get("quiet_periods")
    if quiet_periods is None:
        quiet_periods = device.quiet_periods.filter(is_active=True).order_by("id")
    return get_quiet_period_end_from_iterable(quiet_periods, at)

def _acquire_notification_for_processing(notification_id: int) -> Notification | None:
    """
    Passage atomique QUEUED/FAILED/PARTIAL -> PROCESSING.
    Si 2 workers arrivent en même temps, un seul gagne.
    """
    with transaction.atomic():
        notification = (
            Notification.objects
            .select_for_update(skip_locked=True)
            .filter(id=notification_id)
            .first()
        )

        if notification is None:
            return None

        if notification.status not in ALLOWED_NOTIFICATION_STATUSES_TO_START:
            return None

        notification.status = NotificationStatus.PROCESSING
        notification.save(update_fields=["status"])

        logger.info(
            "notification_acquired_for_processing",
            extra={
                "notification_id": notification.id,
                "application_id": notification.application_id,
            },
        )
        increment_counter(
            "pushit_notification_processing_total",
            labels={"outcome": "acquired"},
        )
        return notification

def _get_or_create_delivery(notification: Notification, device: Device) -> NotificationDelivery:
    delivery, _ = NotificationDelivery.objects.get_or_create(
        notification=notification,
        device=device,
        defaults={"status": DeliveryStatus.PENDING, "attempt_count":0,},
    )
    return delivery


def _should_skip_delivery(delivery: NotificationDelivery) -> bool:
    return delivery.status == DeliveryStatus.SENT


def _should_wait_for_retry(delivery: NotificationDelivery) -> bool:
    return delivery.next_retry_at is not None and delivery.next_retry_at > timezone.now()


def _mark_delivery_as_sent(delivery: NotificationDelivery, provider_message_id: str) -> None:
    now = timezone.now()
    delivery.status = DeliveryStatus.SENT
    delivery.provider_message_id = provider_message_id
    delivery.error_message = ""
    delivery.sent_at = now
    delivery.last_attempt_at = now
    delivery.next_retry_at = None
    delivery.save(
        update_fields=[
            "status",
            "provider_message_id",
            "error_message",
            "sent_at",
            "last_attempt_at",
            "next_retry_at",
        ]
    )


def _mark_delivery_as_deferred(delivery: NotificationDelivery, retry_at) -> None:
    delivery.status = DeliveryStatus.PENDING
    delivery.last_attempt_at = timezone.now()
    delivery.error_message = "Deferred by device quiet period."
    delivery.next_retry_at = retry_at
    delivery.save(
        update_fields=[
            "status",
            "last_attempt_at",
            "error_message",
            "next_retry_at",
        ]
    )

def _mark_delivery_as_failed(delivery: NotificationDelivery, exc: Exception) -> None:
    delivery.attempt_count += 1
    delivery.last_attempt_at = timezone.now()
    delivery.error_message = str(exc)

    if delivery.attempt_count >= MAX_DELIVERY_ATTEMPTS:
        delivery.status = DeliveryStatus.FAILED
        delivery.next_retry_at = None
    else:
        delivery.status = DeliveryStatus.PENDING
        delivery.next_retry_at = _compute_next_retry(delivery.attempt_count)

    delivery.save(
        update_fields=[
            "attempt_count",
            "last_attempt_at",
            "error_message",
            "status",
            "next_retry_at",
        ]
    )


def send_notification(notification_id: int) -> dict:
    """
    Envoie une notification à tous les devices cibles.

    Objectifs :
    - éviter les appels réseau dans une transaction DB globale
    - éviter de renvoyer une delivery déjà marquée SENT
    - recalculer proprement le statut final de la notification
    - invalider réellement les tokens device en cas d'erreur permanente
    """

    notification = _acquire_notification_for_processing(notification_id)
    if notification is None:
        increment_counter(
            "pushit_notification_processing_total",
            labels={"outcome": "skipped"},
        )
        return SendNotificationResult(
            notification_id=notification_id,
            target_count=0,
            sent_count=0,
            failed_count=0,
            skipped_count=0,
        ).as_dict()

    quiet_period_end = get_current_quiet_period_end(notification, at=timezone.now())
    if quiet_period_end is not None:
        Notification.objects.filter(id=notification.id).update(
            status=NotificationStatus.SCHEDULED,
            scheduled_for=quiet_period_end,
            sent_at=None,
        )
        logger.info(
            "notification_deferred_quiet_period",
            extra={
                "notification_id": notification.id,
                "application_id": notification.application_id,
            },
        )
        increment_counter(
            "pushit_notification_send_total",
            labels={"outcome": "deferred_quiet_period"},
        )
        return SendNotificationResult(
            notification_id=notification.id,
            target_count=0,
            sent_count=0,
            failed_count=0,
            skipped_count=0,
        ).as_dict()

    deliveries = get_target_deliveries_for_notification(notification)
    logger.info(
        "notification_send_started",
        extra={
            "notification_id": notification.id,
            "application_id": notification.application_id,
            "target_count": len(deliveries),
        },
    )
    increment_counter(
        "pushit_notification_send_total",
        labels={"outcome": "started"},
    )

    if not deliveries:
        Notification.objects.filter(id=notification.id).update(
            status=NotificationStatus.NO_TARGET,
            sent_at=None,
        )
        send_webhook_callback(
            application=notification.application,
            notification_id=notification.id,
            final_status=NotificationStatus.NO_TARGET,
        )
        increment_counter(
            "pushit_notification_send_total",
            labels={"outcome": NotificationStatus.NO_TARGET},
        )
        return SendNotificationResult(
            notification_id=notification.id,
            target_count=0,
            sent_count=0,
            failed_count=0,
            skipped_count=0,
        ).as_dict()

    sent_count = 0
    failed_count = 0
    skipped_count = 0
    deferred_count = 0
    waiting_count = 0

    for delivery in deliveries:
        device = delivery.device

        if _should_skip_delivery(delivery):
            increment_counter(
                "pushit_notification_delivery_total",
                labels={"outcome": "skipped"},
            )
            skipped_count += 1
            continue

        if _should_wait_for_retry(delivery):
            waiting_count += 1
            continue

        device_quiet_period_end = get_current_device_quiet_period_end(device, at=timezone.now())
        if device_quiet_period_end is not None:
            _mark_delivery_as_deferred(delivery, device_quiet_period_end)
            increment_counter(
                "pushit_notification_delivery_total",
                labels={"outcome": "deferred_quiet_period_device"},
            )
            deferred_count += 1
            continue

        if not _can_retry_delivery(delivery):
            increment_counter(
                "pushit_notification_delivery_total",
                labels={"outcome": "max_attempts_reached"},
            )
            failed_count += 1
            continue

        try:
            provider_message_id = send_push_to_device(
                push_token=device.push_token,
                title=notification.title,
                message=notification.message,
            )
            _mark_delivery_as_sent(delivery, provider_message_id)

            if device.failure_count != 0:
                device.failure_count = 0
                device.save(update_fields=["failure_count"])

            increment_counter(
                "pushit_notification_delivery_total",
                labels={"outcome": "sent"},
            )
            sent_count += 1

        except InvalidPushTokenError as exc:
            logger.warning(
                "delivery_invalid_push_token",
                extra={
                    "notification_id": notification.id,
                    "device_id": device.id,
                    "attempt_count": delivery.attempt_count + 1,
                    "error": str(exc),
                },
            )

            device.push_token_status = DeviceTokenStatus.INVALID
            device.is_active = False
            device.invalidated_at = timezone.now()
            device.invalidation_reason = "invalid_token"
            device.failure_count += 1
            device.save(
                update_fields=[
                    "push_token_status",
                    "is_active",
                    "invalidated_at",
                    "invalidation_reason",
                    "failure_count",
                ]
            )

            _mark_delivery_as_failed(delivery, exc)
            increment_counter(
                "pushit_notification_delivery_total",
                labels={"outcome": "invalid_token"},
            )
            failed_count += 1

        except TemporaryPushProviderError as exc:
            logger.warning(
                "delivery_temporary_provider_error",
                extra={
                    "notification_id": notification.id,
                    "device_id": device.id,
                    "attempt_count": delivery.attempt_count + 1,
                    "error": str(exc),
                },
            )

            device.failure_count += 1
            device.save(update_fields=["failure_count"])

            _mark_delivery_as_failed(delivery, exc)
            increment_counter(
                "pushit_notification_delivery_total",
                labels={"outcome": "temporary_provider_error"},
            )
            failed_count += 1

        except PushProviderError as exc:
            logger.warning(
                "delivery_provider_error",
                extra={
                    "notification_id": notification.id,
                    "device_id": device.id,
                    "attempt_count": delivery.attempt_count + 1,
                    "error": str(exc),
                },
            )

            device.failure_count += 1
            device.save(update_fields=["failure_count"])

            _mark_delivery_as_failed(delivery, exc)
            increment_counter(
                "pushit_notification_delivery_total",
                labels={"outcome": "provider_error"},
            )
            failed_count += 1

        except Exception as exc:
            logger.warning(
                "delivery_unexpected_error",
                extra={
                    "notification_id": notification.id,
                    "device_id": device.id,
                    "attempt_count": delivery.attempt_count + 1,
                    "error": str(exc),
                },
            )

            device.failure_count += 1
            device.save(update_fields=["failure_count"])

            _mark_delivery_as_failed(delivery, exc)
            increment_counter(
                "pushit_notification_delivery_total",
                labels={"outcome": "unexpected_error"},
            )
            failed_count += 1

    successful_count_for_status = sent_count + skipped_count

    if deferred_count > 0 or waiting_count > 0:
        final_status = NotificationStatus.PARTIAL
    elif failed_count == 0 and successful_count_for_status > 0:
        final_status = NotificationStatus.SENT
    elif successful_count_for_status == 0 and failed_count > 0:
        final_status = NotificationStatus.FAILED
    else:
        final_status = NotificationStatus.PARTIAL

    sent_at_value = timezone.now() if final_status == NotificationStatus.SENT else None
    Notification.objects.filter(id=notification.id).update(
        status=final_status,
        sent_at=sent_at_value,
    )

    send_webhook_callback(
        application=notification.application,
        notification_id=notification.id,
        final_status=final_status,
        sent_at=sent_at_value,
    )

    logger.info(
        "notification_send_finished",
        extra={
            "notification_id": notification.id,
            "status": final_status,
            "target_count": len(deliveries),
            "sent_count": sent_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "deferred_count": deferred_count,
        },
    )
    increment_counter(
        "pushit_notification_send_total",
        labels={"outcome": final_status},
    )

    return SendNotificationResult(
        notification_id=notification.id,
        target_count=len(deliveries),
        sent_count=sent_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
    ).as_dict()
