"""Authenticated password change behind POST /api/v1/auth/password/change/."""
import logging

from django.db import transaction
from rest_framework_simplejwt.tokens import RefreshToken

logger = logging.getLogger(__name__)


def change_password_and_rotate_sessions(user, new_password: str) -> RefreshToken:
    """Set ``new_password``, sign every other session out, return a fresh refresh.

    Every refresh token issued to the user so far is blacklisted (a session
    opened with the old password — possibly by whoever learnt it — cannot
    refresh any more). The new refresh token is minted after the sweep, so it
    is the only one left valid: the caller hands it back to the session that
    made the change, which therefore stays signed in. Access tokens already
    issued stay valid until they expire (stateless, `ACCESS_TOKEN_LIFETIME`).
    """
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

    with transaction.atomic():
        user.set_password(new_password)
        user.save(update_fields=["password"])
        pending = OutstandingToken.objects.filter(user=user, blacklistedtoken__isnull=True)
        BlacklistedToken.objects.bulk_create(
            [BlacklistedToken(token=token) for token in pending], ignore_conflicts=True
        )
        refresh = RefreshToken.for_user(user)

    logger.info("password_changed", extra={"user_id": user.pk})
    return refresh
