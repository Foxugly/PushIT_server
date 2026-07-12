"""Passwordless magic-link login (request + verify).

Mirrors the fleet reference (Poker_server ``accounts/magic_link.py``): a
dedicated single-use, short-TTL token model. ``request_magic_link`` is anti-leak
— it always returns without signalling whether the email matched a user; the
view returns the same 200 either way. Only active, email-confirmed accounts get
a link (same gate as password login). The link points at the SPA route
``{FRONTEND_BASE_URL}/auth/magic-link/{token}``, which POSTs the token back to
the verify endpoint; verify consumes the token and issues the JWT pair.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from applications.graph_mail import send_email
from .models import MagicLinkToken

logger = logging.getLogger("pushit.api")
User = get_user_model()


def _magic_link(token: str) -> str:
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    return f"{base}/auth/magic-link/{token}"


def request_magic_link(email: str) -> None:
    """Send a sign-in link to ``email`` if it matches an active, confirmed user.
    Silent no-op otherwise — never reveals whether an account exists."""
    user = User.objects.filter(
        email__iexact=(email or "").strip(), is_active=True, email_confirmed=True
    ).first()
    if not user:
        return

    ttl_minutes = getattr(settings, "MAGIC_LINK_TTL_MINUTES", 15)
    link_token = MagicLinkToken.objects.create(
        user=user,
        expires_at=timezone.now() + timezone.timedelta(minutes=ttl_minutes),
    )
    subject = "Your PushIT sign-in link"
    body = (
        "Hello,\n\n"
        "Use the link below to sign in to PushIT (valid for "
        f"{ttl_minutes} minutes, single use):\n\n"
        f"{_magic_link(link_token.token)}\n\n"
        "If you didn't request this, you can safely ignore this email.\n"
    )
    send_email(to=user.email, subject=subject, body=body)
    logger.info("magic_link_requested", extra={"user_id": user.pk})


def verify_magic_link(token: str):
    """Consume the token and return the user, or None if it is
    invalid/expired/already used."""
    link_token = (
        MagicLinkToken.objects.select_related("user").filter(token=token or "").first()
    )
    if link_token is None or not link_token.is_valid:
        return None
    link_token.consume()
    logger.info("magic_link_verified", extra={"user_id": link_token.user_id})
    return link_token.user
