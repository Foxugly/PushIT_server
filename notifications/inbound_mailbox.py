from __future__ import annotations

import hashlib
import logging

from django.conf import settings
from django.core.cache import cache
from rest_framework import serializers

from accounts.models import User
from applications import inbound_alias, inbound_sender_policy
from applications.graph_mail import (
    GraphEmail,
    _is_configured,
    fetch_unread_emails,
    mark_email_read,
    send_email,
)

from config.logging_utils import mask_email
from devices.models import Device, DeviceTokenStatus

from .creation import create_notification_with_optional_idempotency
from .inbound_auth import AUTH_AS_HEADER
from .inbound_journal import record_inbound_email_ingestion
from .inbound_reply import (
    auto_reply_block_reason,
    claim_auto_reply_slot,
    send_rejected_sender_reply,
    send_unknown_address_reply,
)
from .push import send_push_to_device
from .models import (
    InboundEmailIngestionLog,
    InboundEmailIngestionStatus,
    InboundEmailSource,
)
from .serializers import NotificationInboundEmailSerializer
from .utils import compute_request_fingerprint

logger = logging.getLogger(__name__)


def _maybe_send_unknown_address_reply(email: GraphEmail, send=None) -> None:
    """Auto-reply to the sender, unless RFC 3834 loop protection or the
    per-sender rate limit says no. ``send(sender, recipient)`` defaults to the
    "unknown address" reply."""
    reason = auto_reply_block_reason(email)
    if not reason and not claim_auto_reply_slot(email.sender):
        reason = "rate_limited"
    if reason:
        logger.info(
            "inbound_email_auto_reply_skipped",
            extra={"reason": reason, "mailbox_uid": email.graph_id},
        )
        return
    (send or send_unknown_address_reply)(email.sender.strip().lower(), email.recipient)


OWNER_NOTICE_CACHE_PREFIX = "inbound:owner-notice:"

_REASON_TEXT = {
    inbound_sender_policy.REASON_BLOCKLISTED: "the sender is on the application's blocklist",
    inbound_sender_policy.REASON_NOT_ALLOWED: (
        "the sender is neither the owner nor on the application's allowlist"
    ),
    inbound_sender_policy.REASON_NOT_AUTHENTICATED: "the sender failed authentication (SPF/DKIM/DMARC)",
}


def _claim_owner_notice_slot(user, channel: str) -> bool:
    """At most one owner notice per owner, per channel, per
    INBOUND_EMAIL_AUTO_REPLY_INTERVAL_SECONDS, so a spam or spoofing burst
    does not become a mail/push flood."""
    interval = int(getattr(settings, "INBOUND_EMAIL_AUTO_REPLY_INTERVAL_SECONDS", 3600))
    if interval <= 0:
        return True
    digest = hashlib.sha256(str(user.pk).encode("utf-8")).hexdigest()
    return cache.add(f"{OWNER_NOTICE_CACHE_PREFIX}{channel}:{digest}", 1, timeout=interval)


def _build_owner_notice(application, reason: str, sender: str, verdict=None) -> tuple[str, str, str]:
    """(mail subject, mail body, push text) for a refused inbound mail. The
    sender is masked: the notice may land on a lock screen."""
    masked = mask_email(sender)
    why = _REASON_TEXT.get(reason, reason)
    if verdict is not None:
        why = f"{why} ({verdict.reason})"
    lines = [
        f'PushIT refused an e-mail sent to {application.inbound_email_address} '
        f'(application "{application.name}").',
        f"Sender: {masked}",
        f"Reason: {why}.",
        "No notification was sent.",
        "",
    ]
    if reason == inbound_sender_policy.REASON_NOT_AUTHENTICATED:
        lines += [
            "If the sender is legitimate, their mail domain probably lacks SPF/DKIM",
            "alignment or a DMARC record; ask their mail administrator.",
            "If not, someone may be trying to send notifications in their name:",
            "regenerate the application's inbound address in PushIT.",
        ]
        subject = "PushIT: inbound e-mail refused (sender not authenticated)"
    else:
        lines += [
            "You can change who may send to this address in the application's",
            "sending settings in PushIT.",
        ]
        subject = "PushIT: inbound e-mail refused"
    push_text = f"Mail from {masked} to {application.name} refused: {why}."
    return subject, "\n".join(lines), push_text


def _push_to_owner_devices(owner, application, title: str, message: str) -> int:
    """System push to the owner's own active devices (Device.user == owner),
    outside any application's subscriber list and without a Notification row.
    Best effort: a provider error on one device never fails the mail
    processing. Returns the number of devices reached."""
    devices = Device.objects.filter(
        user=owner,
        is_active=True,
        push_token_status=DeviceTokenStatus.ACTIVE,
    ).order_by("id")
    sent = 0
    for device in devices:
        try:
            send_push_to_device(
                push_token=device.push_token,
                title=title,
                message=message,
                data={
                    "type": "inbound_email_rejected",
                    "application_id": application.id,
                    "application_name": application.name,
                    "title": title,
                    "message": message,
                },
                platform=device.platform,
            )
            sent += 1
        except Exception as exc:  # noqa: BLE001 - best effort, logged
            logger.warning(
                "inbound_email_owner_push_failed",
                extra={"device_id": device.id, "error": str(exc)},
            )
    return sent


def _notify_owner_of_rejection(application, reason: str, sender: str, verdict=None) -> None:
    """Tell the owner a mail to their alias was refused, on the channels the
    application enables: e-mail to the address on their account (never a reply
    path the sender controls) and/or push to their own devices."""
    if application is None:
        return
    owner = application.owner
    subject, body, push_text = _build_owner_notice(application, reason, sender, verdict)

    email_enabled = application.inbound_rejection_notify_email
    if reason == inbound_sender_policy.REASON_NOT_AUTHENTICATED and not getattr(
        settings, "INBOUND_EMAIL_DMARC_NOTIFY_OWNER", True
    ):
        email_enabled = False  # global kill switch for DMARC notices
    if email_enabled and owner.email and _claim_owner_notice_slot(owner, "email"):
        send_email(to=owner.email, subject=subject, body=body)
        logger.info(
            "inbound_email_owner_notified",
            extra={"user_id": owner.pk, "channel": "email", "reason": reason},
        )

    if application.inbound_rejection_notify_push and _claim_owner_notice_slot(owner, "push"):
        sent = _push_to_owner_devices(owner, application, "PushIT: inbound e-mail refused", push_text)
        logger.info(
            "inbound_email_owner_notified",
            extra={"user_id": owner.pk, "channel": "push", "reason": reason, "devices": sent},
        )


def _reply_to_refused_sender(email: GraphEmail) -> None:
    """Opt-in reply to a sender refused by the policy. A PushIT user gets the
    historical "unknown address" reply (lists their own applications); anyone
    else a generic "not accepted". Same loop protection and rate limit."""
    sender = email.sender.strip().lower()
    if User.objects.filter(email=sender).exists():
        _maybe_send_unknown_address_reply(email)
    else:
        _maybe_send_unknown_address_reply(email, send=send_rejected_sender_reply)


def _process_email(email: GraphEmail) -> tuple[bool, str]:
    # Mail that targets no PushIT alias (newsletters, replies to the mailbox,
    # spam to random addresses...) is not ours to judge: mark it read and move
    # on, without polluting the ingestion journal or auto-replying.
    if not inbound_alias.is_inbound_alias_address(email.recipient):
        logger.debug(
            "inbound_email_ignored_no_alias",
            extra={"mailbox_uid": email.graph_id, "recipient": mask_email(email.recipient)},
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
            "exchange_auth_as": (email.headers or {}).get(AUTH_AS_HEADER, ""),
        }
    )

    try:
        serializer.is_valid(raise_exception=True)
    except serializers.ValidationError:
        errors = serializer.errors

        rejection = serializer.context.get("inbound_rejection")
        if rejection is not None:
            # Refused by the application's sender policy, or forged /
            # unauthenticated `From`: journal it (with the application), no
            # notification; the owner is told per the application's flags.
            application = serializer.context.get("application")
            verdict = serializer.context.get("sender_auth_rejection")
            # "<stable code>: <human text>" -- the code (sender_not_allowed /
            # sender_blocked / dmarc_failed) is what the owner's journal
            # endpoint documents and clients may key on.
            code = inbound_sender_policy.log_code_for_rejection(rejection)
            if verdict is not None:
                error_message = f"{code}: Sender failed DMARC authentication ({verdict.reason})."
            else:
                error_message = f"{code}: Sender refused by the application's inbound policy ({rejection})."
            record_inbound_email_ingestion(
                source=InboundEmailSource.POLLING,
                status=InboundEmailIngestionStatus.REJECTED,
                sender=email.sender,
                recipient=email.recipient,
                subject=email.subject,
                message_id=email.message_id,
                mailbox_uid=email.graph_id,
                application=application,
                error_message=error_message,
            )
            _notify_owner_of_rejection(application, rejection, email.sender, verdict)
            # Never answer a mail that failed authentication: its `From` is
            # likely forged and the reply would hit an innocent third party.
            if (
                application is not None
                and application.inbound_rejection_reply_to_sender
                and rejection != inbound_sender_policy.REASON_NOT_AUTHENTICATED
            ):
                _reply_to_refused_sender(email)
            return True, "rejected"

        # A known user writing to an alias that matches no application gets the
        # "unknown address" reply (a refused sender on a KNOWN alias went
        # through the policy branch above).
        recipient_errors = errors.get("recipient", [])
        sender_errors = errors.get("sender", [])

        is_known_user_wrong_address = any(
            "No application matches" in str(e) for e in recipient_errors
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
