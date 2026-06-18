"""Email-confirmation flow (send link + confirm).

Mirrors ``password_reset.py``: stateless tokens via Django's
``default_token_generator`` — no extra token model. Registration sends a link to
``{FRONTEND_BASE_URL}/auth/confirm-email/{uid}/{token}``; the SPA POSTs the
``uid`` + ``token`` back to the confirm endpoint, which flips ``email_confirmed``
and auto-logs the user in. ``resend_confirmation_email`` is anti-leak.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from applications.graph_mail import send_email

logger = logging.getLogger("pushit.api")
User = get_user_model()


def _confirm_link(uidb64: str, token: str) -> str:
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    return f"{base}/auth/confirm-email/{uidb64}/{token}"


def send_confirmation_email(user) -> None:
    """Send the email-confirmation link to ``user``. Best-effort: relies on
    ``graph_mail.send_email`` (no-op if Graph isn't configured)."""
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = _confirm_link(uidb64, token)

    subject = "Confirm your PushIT email"
    body = (
        "Welcome to PushIT!\n\n"
        "Please confirm your email address to activate your account by opening "
        "the link below:\n\n"
        f"{link}\n\n"
        "If you didn't create a PushIT account, you can safely ignore this email.\n"
    )
    send_email(to=user.email, subject=subject, body=body)
    logger.info("email_confirmation_sent", extra={"user_id": user.pk})


def send_duplicate_registration_email(user) -> None:
    """Notify an existing user that someone tried to register with their address.

    Sent on the anti-enumeration registration path: when a duplicate email is
    submitted we return the same pending-verification body as a fresh signup (so
    the caller can't tell the address is taken) and, instead of creating a second
    account, send this neutral heads-up to the genuine owner. No link, nothing
    actionable — if it was them, the message tells them to just sign in; if not,
    it is a benign security notice. Best-effort (no-op if Graph isn't configured).
    """
    subject = "Someone tried to register with your PushIT email"
    body = (
        "Hello,\n\n"
        "Someone just tried to create a PushIT account using this email address, "
        "but an account already exists for it.\n\n"
        "If this was you, there's nothing to do — simply sign in with your "
        "existing password, or use \"forgot password\" if you don't remember it.\n\n"
        "If this wasn't you, you can safely ignore this email; no changes were "
        "made to your account.\n"
    )
    send_email(to=user.email, subject=subject, body=body)
    logger.info("duplicate_registration_notice_sent", extra={"user_id": user.pk})


def resend_confirmation_email(email: str) -> None:
    """Re-send the confirmation link to ``email`` if it matches an
    not-yet-confirmed active account. Silent no-op otherwise (anti-leak)."""
    user = User.objects.filter(
        email__iexact=(email or "").strip(), is_active=True, email_confirmed=False
    ).first()
    if user:
        send_confirmation_email(user)


def confirm_email(uidb64: str, token: str):
    """Validate the uid+token pair and flip ``email_confirmed``. Returns the
    user on success, or None if the link is invalid/expired or the user is gone.
    Idempotent: re-confirming an already-confirmed user still returns the user."""
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return None

    if not user.is_active or not default_token_generator.check_token(user, token):
        return None

    if not user.email_confirmed:
        user.email_confirmed = True
        user.save(update_fields=["email_confirmed"])
        logger.info("email_confirmed", extra={"user_id": user.pk})
    return user
