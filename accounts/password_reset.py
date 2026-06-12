"""Password-reset flow (request + confirm).

Stateless tokens via Django's PasswordResetTokenGenerator — no extra model
fields. ``request_password_reset`` is anti-leak: it always returns without
signalling whether the email matched a user; the view returns the same 200
either way. The reset link points at the SPA route
``{FRONTEND_BASE_URL}/reset-password/{uid}/{token}``, which collects the new
password and POSTs it back to the confirm endpoint.
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from applications.graph_mail import send_email

logger = logging.getLogger("pushit.api")
User = get_user_model()


def _reset_link(uidb64: str, token: str) -> str:
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    return f"{base}/reset-password/{uidb64}/{token}"


def request_password_reset(email: str) -> None:
    """Send a reset link to ``email`` if it matches an active user. Silent
    no-op otherwise — never reveals whether an account exists."""
    user = User.objects.filter(email__iexact=(email or "").strip(), is_active=True).first()
    if not user:
        return

    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = _reset_link(uidb64, token)

    subject = "Reset your PushIT password"
    body = (
        "Hello,\n\n"
        "We received a request to reset your PushIT password. "
        "Open the link below to choose a new one:\n\n"
        f"{link}\n\n"
        "If you didn't request this, you can safely ignore this email — "
        "your password won't change.\n"
    )
    send_email(to=user.email, subject=subject, body=body)
    logger.info("password_reset_requested", extra={"user_id": user.pk})


def confirm_password_reset(uidb64: str, token: str, new_password: str) -> bool:
    """Validate the uid+token pair and set ``new_password``. Returns True on
    success, False if the link is invalid/expired or the user is gone.
    Raises django ValidationError if the password fails the validators."""
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return False

    if not user.is_active or not default_token_generator.check_token(user, token):
        return False

    validate_password(new_password, user)  # may raise ValidationError
    user.set_password(new_password)
    user.save(update_fields=["password"])
    logger.info("password_reset_confirmed", extra={"user_id": user.pk})
    return True
