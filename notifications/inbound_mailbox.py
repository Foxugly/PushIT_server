from __future__ import annotations

from dataclasses import dataclass
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses
import imaplib
import logging
import re

from django.conf import settings
from rest_framework import serializers

from .creation import create_notification_with_optional_idempotency
from .inbound_journal import record_inbound_email_ingestion
from .models import InboundEmailIngestionStatus, InboundEmailSource
from .serializers import NotificationInboundEmailSerializer
from .utils import compute_request_fingerprint

logger = logging.getLogger(__name__)

HTML_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class InboundMailboxConfig:
    enabled: bool
    host: str
    port: int
    username: str
    password: str
    folder: str
    use_ssl: bool
    domain: str


@dataclass(frozen=True)
class InboundMailboxEmail:
    uid: str
    sender: str
    recipient: str
    subject: str
    text: str
    message_id: str


def get_inbound_mailbox_config() -> InboundMailboxConfig:
    return InboundMailboxConfig(
        enabled=settings.INBOUND_EMAIL_IMAP_ENABLED,
        host=settings.INBOUND_EMAIL_IMAP_HOST.strip(),
        port=settings.INBOUND_EMAIL_IMAP_PORT,
        username=settings.INBOUND_EMAIL_IMAP_USERNAME.strip(),
        password=settings.INBOUND_EMAIL_IMAP_PASSWORD,
        folder=settings.INBOUND_EMAIL_IMAP_FOLDER.strip(),
        use_ssl=settings.INBOUND_EMAIL_IMAP_USE_SSL,
        domain=settings.INBOUND_EMAIL_DOMAIN.strip().lower(),
    )


def _decode_header_value(value: str) -> str:
    if not value:
        return ""
    return str(make_header(decode_header(value)))


def _extract_matching_recipient(message: Message, domain: str) -> str:
    for header_name in ("Delivered-To", "X-Original-To", "Envelope-To", "To", "Cc"):
        header_value = message.get(header_name, "")
        if not header_value:
            continue
        for _, address in getaddresses([header_value]):
            normalized = address.strip().lower()
            if normalized.endswith(f"@{domain}"):
                return normalized
    return ""


def _decode_bytes(payload: bytes, charset: str | None) -> str:
    charset = charset or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _extract_text_body(message: Message) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if message.is_multipart():
        for part in message.walk():
            if part.get_filename():
                continue
            content_type = part.get_content_type()
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            text = _decode_bytes(payload, part.get_content_charset()).strip()
            if not text:
                continue
            if content_type == "text/plain":
                plain_parts.append(text)
            elif content_type == "text/html":
                html_parts.append(text)
    else:
        payload = message.get_payload(decode=True)
        if payload is not None:
            text = _decode_bytes(payload, message.get_content_charset()).strip()
            if message.get_content_type() == "text/html":
                html_parts.append(text)
            elif text:
                plain_parts.append(text)

    if plain_parts:
        return "\n\n".join(plain_parts).strip()
    if html_parts:
        stripped = "\n\n".join(HTML_TAG_RE.sub(" ", part) for part in html_parts)
        return " ".join(stripped.split())
    return ""


def _parse_email(uid: str, raw_email: bytes, domain: str) -> InboundMailboxEmail:
    message = message_from_bytes(raw_email, policy=policy.default)
    sender_addresses = getaddresses([message.get("From", "")])
    sender = sender_addresses[0][1].strip().lower() if sender_addresses else ""
    recipient = _extract_matching_recipient(message, domain)
    subject = _decode_header_value(message.get("Subject", "")).strip()
    text = _extract_text_body(message)
    message_id = (message.get("Message-ID", "") or "").strip().strip("<>").strip()
    return InboundMailboxEmail(
        uid=uid,
        sender=sender,
        recipient=recipient,
        subject=subject,
        text=text,
        message_id=message_id,
    )


def _process_email(email_message: InboundMailboxEmail) -> tuple[bool, str]:
    serializer = NotificationInboundEmailSerializer(
        data={
            "sender": email_message.sender,
            "recipient": email_message.recipient,
            "subject": email_message.subject,
            "text": email_message.text,
            "message_id": email_message.message_id,
        }
    )

    try:
        serializer.is_valid(raise_exception=True)
    except serializers.ValidationError:
        record_inbound_email_ingestion(
            source=InboundEmailSource.IMAP,
            status=InboundEmailIngestionStatus.REJECTED,
            sender=email_message.sender,
            recipient=email_message.recipient,
            subject=email_message.subject,
            message_id=email_message.message_id,
            mailbox_uid=email_message.uid,
            error_message=str(serializer.errors),
        )
        logger.warning(
            "inbound_email_rejected",
            extra={
                "error": str(serializer.errors),
            },
        )
        return True, "rejected"

    application = serializer.context["application"]
    scheduled_for = serializer.context["scheduled_for"]
    idempotency_key = serializer.validated_data["message_id"] or f"imap-uid-{email_message.uid}"
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
            source=InboundEmailSource.IMAP,
            status=InboundEmailIngestionStatus.CONFLICT,
            sender=email_message.sender,
            recipient=email_message.recipient,
            subject=email_message.subject,
            message_id=email_message.message_id,
            mailbox_uid=email_message.uid,
            scheduled_for=scheduled_for,
            application=application,
            notification=outcome.notification,
            error_message="Message deja traite avec un contenu different.",
        )
        logger.warning(
            "inbound_email_idempotency_conflict",
            extra={
                "application_id": application.id,
                "notification_id": outcome.notification.id,
                "error": "Message deja traite avec un contenu different.",
            },
        )
        return True, "conflict"

    record_inbound_email_ingestion(
        source=InboundEmailSource.IMAP,
        status=InboundEmailIngestionStatus.CREATED if outcome.created else InboundEmailIngestionStatus.EXISTING,
        sender=email_message.sender,
        recipient=email_message.recipient,
        subject=email_message.subject,
        message_id=email_message.message_id,
        mailbox_uid=email_message.uid,
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
    config = get_inbound_mailbox_config()
    if not config.enabled:
        return {"status": "skipped", "reason": "disabled", "processed_count": 0}
    if not config.host or not config.username or not config.password:
        return {"status": "skipped", "reason": "missing_configuration", "processed_count": 0}

    client_cls = imaplib.IMAP4_SSL if config.use_ssl else imaplib.IMAP4
    mailbox = client_cls(config.host, config.port)
    processed_count = 0
    created_count = 0
    rejected_count = 0

    try:
        mailbox.login(config.username, config.password)
        status, _ = mailbox.select(config.folder)
        if status != "OK":
            raise RuntimeError(f"Impossible de selectionner le dossier IMAP {config.folder}.")

        status, data = mailbox.uid("search", None, "UNSEEN")
        if status != "OK":
            raise RuntimeError("Impossible de lister les emails non lus.")

        uid_list = [uid.decode("utf-8") for uid in data[0].split()] if data and data[0] else []

        for uid in uid_list:
            status, fetch_data = mailbox.uid("fetch", uid, "(RFC822)")
            if status != "OK":
                continue

            raw_email = next(
                (
                    chunk[1]
                    for chunk in fetch_data
                    if isinstance(chunk, tuple) and len(chunk) > 1 and isinstance(chunk[1], bytes)
                ),
                None,
            )
            if raw_email is None:
                continue

            email_message = None
            try:
                email_message = _parse_email(uid, raw_email, config.domain)
                mark_seen, outcome = _process_email(email_message)
            except Exception as exc:
                record_inbound_email_ingestion(
                    source=InboundEmailSource.IMAP,
                    status=InboundEmailIngestionStatus.ERROR,
                    sender="" if email_message is None else email_message.sender,
                    recipient="" if email_message is None else email_message.recipient,
                    subject="" if email_message is None else email_message.subject,
                    message_id="" if email_message is None else email_message.message_id,
                    mailbox_uid=uid,
                    error_message=str(exc),
                )
                logger.exception(
                    "inbound_mailbox_processing_failed",
                    extra={
                        "error": str(exc),
                    },
                )
                continue

            processed_count += 1
            if outcome == "rejected":
                rejected_count += 1
            elif outcome == "created":
                created_count += 1

            if mark_seen:
                mailbox.uid("store", uid, "+FLAGS", "(\\Seen)")

        return {
            "status": "ok",
            "processed_count": processed_count,
            "created_count": created_count,
            "rejected_count": rejected_count,
        }
    finally:
        try:
            mailbox.close()
        except Exception:
            pass
        mailbox.logout()
