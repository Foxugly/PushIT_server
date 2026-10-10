from __future__ import annotations

import logging

from django.conf import settings
from rest_framework import serializers

from applications import inbound_alias
from applications.graph_mail import (
    GraphEmail,
    _is_configured,
    fetch_unread_emails,
    mark_email_read,
)

from .creation import create_notification_with_optional_idempotency
from .inbound_journal import record_inbound_email_ingestion
from .inbound_reply import (
    auto_reply_block_reason,
    claim_auto_reply_slot,
    send_unknown_address_reply,
)
from .models import (
    InboundEmailIngestionLog,
    InboundEmailIngestionStatus,
    InboundEmailSource,
)
from .serializers import NotificationInboundEmailSerializer
from .utils import compute_request_fingerprint

logger = logging.getLogger(__name__)


def _maybe_send_unknown_address_reply(email: GraphEmail) -> None:
    reason = auto_reply_block_reason(email)
    if not reason and not claim_auto_reply_slot(email.sender):
        reason = "rate_limited"
    if reason:
        logger.info(
            "inbound_email_auto_reply_skipped",
            extra={"reason": reason, "mailbox_uid": email.graph_id},
        )
        return
    send_unknown_address_reply(email.sender.strip().lower(), email.recipient)


def _process_email(email: GraphEmail) -> tuple[bool, str]:
    # Mail that targets no PushIT alias (newsletters, replies to the mailbox,
    # spam to random addresses...) is not ours to judge: mark it read and move
    # on, without polluting the ingestion journal or auto-replying.
    if not inbound_alias.is_inbound_alias_address(email.recipient):
        logger.debug(
            "inbound_email_ignored_no_alias",
            extra={"mailbox_uid": email.graph_id, "recipient": email.recipient},
        )
        return True, "ignored"

    serializer = NotificationInboundEmailSerializer(
        data={
            "sender": email.sender,
            "recipient": email.recipient,
            "subject": email.subject,
            "text": email.text,
            "message_id": email.message_id,
            # SPF/DKIM/DMARC verdicts stamped by M365; consulted only when
            # INBOUND_EMAIL_REQUIRE_DMARC is enabled (anti-`From`-spoofing).
            "authentication_results": email.authentication_results,
        }
    )

    try:
        serializer.is_valid(raise_exception=True)
    except serializers.ValidationError:
        errors = serializer.errors

        # Check if this is a known user sending to an unknown/unauthorized address
        recipient_errors = errors.get("recipient", [])
        sender_errors = errors.get("sender", [])

        is_known_user_wrong_address = (
            any("No application matches" in str(e) for e in recipient_errors)
            or any("must match the owner" in str(e) for e in sender_errors)
        ) and not any("No user matches" in str(e) for e in sender_errors)

        if is_known_user_wrong_address:
            _maybe_send_unknown_address_reply(email)

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


def _record_processing_failure(email: GraphEmail, exc: Exception) -> None:
    """Journal an ERROR; past the retry cap, mark the mail read so a poison
    message stops being retried every minute forever."""
    logger.exception("inbound_mailbox_processing_failed", extra={"error": str(exc)})
    log = record_inbound_email_ingestion(
        source=InboundEmailSource.POLLING,
        status=InboundEmailIngestionStatus.ERROR,
        sender=email.sender,
        recipient=email.recipient,
        subject=email.subject,
        message_id=email.message_id,
        mailbox_uid=email.graph_id,
        error_message=str(exc),
    )
    if not email.graph_id:
        return
    max_attempts = int(getattr(settings, "INBOUND_EMAIL_MAX_PROCESSING_ATTEMPTS", 5))
    attempts = InboundEmailIngestionLog.objects.filter(
        source=InboundEmailSource.POLLING,
        status=InboundEmailIngestionStatus.ERROR,
        mailbox_uid=email.graph_id,
    ).count()
    if max_attempts <= 0 or attempts < max_attempts:
        return
    log.error_message = f"Giving up after {attempts} failed attempts (marked read): {exc}"
    log.save(update_fields=["error_message"])
    logger.error(
        "inbound_mailbox_retry_cap_reached",
        extra={"mailbox_uid": email.graph_id, "attempts": attempts, "error": str(exc)},
    )
    mark_email_read(email.graph_id)


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
    ignored_count = 0
    failed_count = 0

    for email in emails:
        try:
            mark_seen, outcome = _process_email(email)
        except Exception as exc:
            failed_count += 1
            _record_processing_failure(email, exc)
            continue

        processed_count += 1
        if outcome == "rejected":
            rejected_count += 1
        elif outcome == "created":
            created_count += 1
        elif outcome == "ignored":
            ignored_count += 1

        if mark_seen:
            mark_email_read(email.graph_id)

    return {
        "status": "ok",
        "processed_count": processed_count,
        "created_count": created_count,
        "rejected_count": rejected_count,
        "ignored_count": ignored_count,
        "failed_count": failed_count,
    }
