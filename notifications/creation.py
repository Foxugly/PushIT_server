from dataclasses import dataclass

from django.db import IntegrityError, OperationalError, connection, transaction

from applications.models import Application

from .models import Notification, NotificationStatus


@dataclass(frozen=True)
class NotificationCreationOutcome:
    notification: Notification
    created: bool
    conflict: bool


def build_notification_status(scheduled_for):
    if scheduled_for is not None:
        return NotificationStatus.SCHEDULED
    return NotificationStatus.DRAFT


def create_notification_with_optional_idempotency(
    *,
    application: Application,
    title: str,
    message: str,
    scheduled_for,
    idempotency_key: str = "",
    request_fingerprint: str = "",
) -> NotificationCreationOutcome:
    create_kwargs = {
        "application": application,
        "title": title,
        "message": message,
        "status": build_notification_status(scheduled_for),
        "scheduled_for": scheduled_for,
        "idempotency_key": idempotency_key,
        "request_fingerprint": request_fingerprint,
    }

    if not idempotency_key:
        return NotificationCreationOutcome(
            notification=Notification.objects.create(**create_kwargs),
            created=True,
            conflict=False,
        )

    if connection.vendor == "sqlite":
        notification, created = Notification.objects.get_or_create(
            application=application,
            idempotency_key=idempotency_key,
            defaults={
                "title": title,
                "message": message,
                "status": build_notification_status(scheduled_for),
                "scheduled_for": scheduled_for,
                "request_fingerprint": request_fingerprint,
            },
        )
        return NotificationCreationOutcome(
            notification=notification,
            created=created,
            conflict=not created and notification.request_fingerprint != request_fingerprint,
        )

    created = False
    savepoint_failed = False
    try:
        with transaction.atomic():
            notification = Notification.objects.create(**create_kwargs)
        created = True
    except OperationalError:
        if connection.vendor != "sqlite":
            raise
        savepoint_failed = True
    except IntegrityError:
        pass

    if savepoint_failed:
        try:
            notification = Notification.objects.create(**create_kwargs)
            created = True
        except IntegrityError:
            connection.rollback()
            pass

    if created:
        if notification.pk is None and idempotency_key:
            notification = Notification.objects.get(
                application=application,
                idempotency_key=idempotency_key,
            )
            return NotificationCreationOutcome(
                notification=notification,
                created=False,
                conflict=notification.request_fingerprint != request_fingerprint,
            )
        return NotificationCreationOutcome(
            notification=notification,
            created=True,
            conflict=False,
        )

    notification = Notification.objects.get(
        application=application,
        idempotency_key=idempotency_key,
    )
    return NotificationCreationOutcome(
        notification=notification,
        created=False,
        conflict=notification.request_fingerprint != request_fingerprint,
    )
