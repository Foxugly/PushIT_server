from __future__ import annotations

import logging

from django.conf import settings
from rest_framework import serializers

from applications.graph_mail import (
    GraphEmail,
    fetch_unread_emails,
    mark_email_read,
    _is_configured,
)
from .creation import create_notification_with_optional_idempotency
from .inbound_journal import record_inbound_email_ingestion
from .inbound_reply import send_unknown_address_reply
from .models import InboundEmailIngestionStatus, InboundEmailSource
from .serializers import NotificationInboundEmailSerializer
from .utils import compute_request_fingerprint

logger = logging.getLogger(__name__)


def _process_email(email: GraphEmail) -> tuple[bool, str]:
    serializer = NotificationInboundEmailSerializer(
        data={
            "sender": email.sender,
            "recipient": email.recipient,
            "subject": email.subject,
            "text": email.text,
            "message_id": email.message_id,
        }
    )

    try:
        serializer.is_valid(raise_exception=True)
    except serializers.ValidationError:
        errors = serializer.errors

        # Check if this is a known user sending to an unknown/unauthorized address
        sender = email.sender.strip().lower()
        recipient_errors = errors.get("recipient", [])
        sender_errors = errors.get("sender", [])

        is_known_user_wrong_address = (
            any("No application matches" in str(e) for e in recipient_errors)
            or any("must match the owner" in str(e) for e in sender_errors)
        ) and not any("No user matches" in str(e) for e in sender_errors)

        if is_known_user_wrong_address:
            send_unknown_address_reply(sender, email.recipient)

        record_inbound_email_ingestion(
            source=InboundEmailSource.POLLING,
            status=InboundEmailIngestionStatus.REJECTED,
            sender=email.sender,
            recipient=email.recipient,
            subject=email.subject,
            message_id=email.message_id,
            mailbox_uid=email.graph_id,
            error_message=str(errors),
        )
        logger.warning("inbound_email_rejected", extra={"error": str(errors)})
        return True, "rejected"

    application = serializer.context["application"]
    scheduled_for = serializer.context["scheduled_for"]
    idempotency_key = serializer.validated_data["message_id"] or f"graph-{email.graph_id}"
    request_fingerprint = compute_request_fingerprint(
        {
            "sender": serializer.context["normalized_sender"],
            "recipient": serializer.context["normalized_recipient"],
            "title": serializer.context["normalized_title"],
            "message": serializer.validated_data["text"],
            "scheduled_for": scheduled_for,
        }
    )

    outcome = create_notification_with_optional_idempotency(
        application=application,
        title=serializer.context["normalized_title"],
        message=serializer.validated_data["text"],
        scheduled_for=scheduled_for,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
    )

    if outcome.conflict:
        record_inbound_email_ingestion(
            source=InboundEmailSource.POLLING,
            status=InboundEmailIngestionStatus.CONFLICT,
            sender=email.sender,
            recipient=email.recipient,
            subject=email.subject,
            message_id=email.message_id,
            mailbox_uid=email.graph_id,
            scheduled_for=scheduled_for,
            application=application,
            notification=outcome.notification,
            error_message="Message already processed with different content.",
        )
        logger.warning(
            "inbound_email_idempotency_conflict",
            extra={
                "application_id": application.id,
                "notification_id": outcome.notification.id,
            },
        )
        return True, "conflict"

    record_inbound_email_ingestion(
        source=InboundEmailSource.POLLING,
        status=InboundEmailIngestionStatus.CREATED if outcome.created else InboundEmailIngestionStatus.EXISTING,
        sender=email.sender,
        recipient=email.recipient,
        subject=email.subject,
        message_id=email.message_id,
        mailbox_uid=email.graph_id,
        scheduled_for=scheduled_for,
        application=application,
        notification=outcome.notification,
    )
    logger.info(
        "inbound_email_processed",
        extra={
            "application_id": application.id,
            "notification_id": outcome.notification.id,
            "status": outcome.notification.status,
        },
    )
    return True, "created" if outcome.created else "existing"


def poll_inbound_mailbox() -> dict:
    if not _is_configured():
        return {"status": "skipped", "reason": "not configured", "processed_count": 0}

    try:
        emails = fetch_unread_emails()
    except Exception as exc:
        logger.exception("inbound_mailbox_fetch_failed", extra={"error": str(exc)})
        return {"status": "error", "reason": str(exc), "processed_count": 0}

    processed_count = 0
    created_count = 0
    rejected_count = 0

    for email in emails:
        try:
            mark_seen, outcome = _process_email(email)
        except Exception as exc:
            record_inbound_email_ingestion(
                source=InboundEmailSource.POLLING,
                status=InboundEmailIngestionStatus.ERROR,
                sender=email.sender,
                recipient=email.recipient,
                subject=email.subject,
                message_id=email.message_id,
                mailbox_uid=email.graph_id,
                error_message=str(exc),
            )
            logger.exception("inbound_mailbox_processing_failed", extra={"error": str(exc)})
            continue

        processed_count += 1
        if outcome == "rejected":
            rejected_count += 1
        elif outcome == "created":
            created_count += 1

        if mark_seen:
            mark_email_read(email.graph_id)

    return {
        "status": "ok",
        "processed_count": processed_count,
        "created_count": created_count,
        "rejected_count": rejected_count,
    }
