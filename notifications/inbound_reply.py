import hashlib
import logging
import re

from django.conf import settings
from django.core.cache import cache

from applications import inbound_alias
from applications.graph_mail import GraphEmail, send_email

logger = logging.getLogger(__name__)

# Senders that are machines, not people: replying to them is at best useless
# and at worst starts a mail loop (RFC 3834 §2).
_ROBOT_SENDER_RE = re.compile(
    r"^(mailer-daemon|postmaster|bounces?|no[-_.]?reply|do[-_.]?not[-_.]?reply)([-_.+].*)?$"
)
_BULK_PRECEDENCE = frozenset({"bulk", "junk", "list", "auto_reply"})
AUTO_REPLY_CACHE_PREFIX = "inbound:auto-reply:"


def auto_reply_block_reason(email: GraphEmail) -> str:
    """Why we must NOT auto-reply to ``email`` ("" = a reply is acceptable).

    Implements RFC 3834 loop protection: never answer an automated message,
    a mailing list, a bounce address, or ourselves.
    """
    headers = email.headers or {}
    sender = inbound_alias.normalize_address(email.sender)
    mailbox = inbound_alias.mailbox_addresses()

    if not sender or "@" not in sender:
        return "no_sender"
    auto_submitted = headers.get("auto-submitted", "").strip().lower()
    if auto_submitted and auto_submitted != "no":
        return "auto_submitted"
    if headers.get("precedence", "").strip().lower() in _BULK_PRECEDENCE:
        return "precedence"
    if "x-auto-response-suppress" in headers:
        return "auto_response_suppressed"
    if "list-id" in headers or "list-unsubscribe" in headers:
        return "mailing_list"
    if _ROBOT_SENDER_RE.match(sender.split("@", 1)[0]):
        return "robot_sender"
    if sender in mailbox:
        return "own_mailbox"
    recipients = set(email.recipients) or ({email.recipient} if email.recipient else set())
    if recipients and recipients <= mailbox:
        return "addressed_to_mailbox_only"
    return ""


def claim_auto_reply_slot(sender: str) -> bool:
    """At most one auto-reply per sender per interval (shared cache: Redis in
    prod when CACHE_URL is set). Returns False when the slot is already taken."""
    interval = int(getattr(settings, "INBOUND_EMAIL_AUTO_REPLY_INTERVAL_SECONDS", 3600))
    if interval <= 0:
        return True
    digest = hashlib.sha256(inbound_alias.normalize_address(sender).encode("utf-8")).hexdigest()
    return cache.add(f"{AUTO_REPLY_CACHE_PREFIX}{digest}", 1, timeout=interval)


def build_unknown_address_reply(sender_email: str, tried_recipient: str) -> tuple[str, str]:
    from accounts.models import User
    from applications.models import Application

    user = User.objects.filter(email=sender_email).first()
    if user is None:
        return "", ""

    apps = Application.objects.filter(
        owner=user,
        is_active=True,
        revoked_at__isnull=True,
    ).order_by("name")

    if not apps.exists():
        body = (
            f"Your email to {tried_recipient} could not be delivered.\n\n"
            "You don't have any active applications configured.\n"
            "Please create an application first on PushIT."
        )
        return "Undeliverable: no active application", body

    lines = [
        f"Your email to {tried_recipient} could not be delivered "
        "because this address does not match any of your applications.",
        "",
        "Here are your valid inbound email addresses:",
        "",
    ]
    for app in apps:
        lines.append(f"  - {app.name}: {app.inbound_email_address}")

    lines.append("")
    lines.append("Please resend your email to the correct address.")

    return "Undeliverable: unknown recipient address", "\n".join(lines)


def send_unknown_address_reply(sender_email: str, tried_recipient: str) -> None:
    subject, body = build_unknown_address_reply(sender_email, tried_recipient)
    if subject and body:
        send_email(to=sender_email, subject=subject, body=body)
        logger.info(
            "inbound_email_unknown_address_reply_sent",
            extra={"sender": sender_email, "recipient": tried_recipient},
        )
