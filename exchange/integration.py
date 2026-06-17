"""Integration helpers wiring ExchangeAliasService into Django models.

These helpers swallow non-critical failures so that creating or deleting an
Application never blocks on an Exchange outage. Real errors are logged at
ERROR level — operators should monitor the ``exchange_alias_*`` log records.
"""

from __future__ import annotations

import logging

from django.conf import settings

from .exceptions import ExchangeConfigError, ExchangeError
from .services import ExchangeAliasService

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(
        getattr(settings, "EXCHANGE_PS_SCRIPT_PATH", "")
        and getattr(settings, "EXCHANGE_APP_ID", "")
        and getattr(settings, "EXCHANGE_TENANT", "")
        and getattr(settings, "EXCHANGE_SHARED_MAILBOX", "")
    )


# Backwards-compatible alias for the previously-private name.
_is_configured = is_configured


def alias_status(alias_email: str) -> dict:
    """Report whether ``alias_email`` is provisioned on the shared mailbox.

    Returns ``{"configured": bool, "provisioned": bool | None, "detail": str}``:
    - Exchange not configured -> ``configured=False, provisioned=None``.
    - Configured: list the shared mailbox's EmailAddresses and check membership
      case-insensitively against both the bare address and an ``smtp:``-prefixed
      form (Exchange stores secondary aliases as ``smtp:alias@domain`` and the
      primary as ``SMTP:...``).
    - On an Exchange error -> ``provisioned=None`` with the error code as detail;
      never raises (callers are status endpoints, not save paths).
    """
    if not is_configured():
        return {"configured": False, "provisioned": None, "detail": "exchange_not_configured"}

    target = alias_email.strip().lower()
    candidates = {target, f"smtp:{target}"}
    try:
        addresses = ExchangeAliasService().list_aliases(settings.EXCHANGE_SHARED_MAILBOX)
    except (ExchangeConfigError, ExchangeError) as exc:
        detail = getattr(exc, "error_code", None) or "exchange_error"
        logger.error(
            "exchange_alias_status_failed",
            extra={"alias": alias_email, "error_code": detail, "error": str(exc)},
        )
        return {"configured": True, "provisioned": None, "detail": detail}

    provisioned = any((addr or "").strip().lower() in candidates for addr in addresses)
    return {
        "configured": True,
        "provisioned": provisioned,
        "detail": "provisioned" if provisioned else "not_provisioned",
    }


def provision_alias_for_application(alias_email: str) -> None:
    if not is_configured():
        logger.warning("exchange_alias_skipped", extra={"reason": "not configured", "alias": alias_email})
        return
    try:
        service = ExchangeAliasService()
        service.add_alias(mailbox=settings.EXCHANGE_SHARED_MAILBOX, alias=alias_email)
    except ExchangeConfigError:
        # Misconfiguration is operational and should not crash a save().
        logger.exception("exchange_alias_config_error", extra={"alias": alias_email})
    except ExchangeError:
        logger.exception("exchange_alias_provision_failed", extra={"alias": alias_email})


def deprovision_alias_for_application(alias_email: str) -> None:
    if not is_configured():
        logger.warning("exchange_alias_skipped", extra={"reason": "not configured", "alias": alias_email})
        return
    try:
        service = ExchangeAliasService()
        service.remove_alias(mailbox=settings.EXCHANGE_SHARED_MAILBOX, alias=alias_email)
    except ExchangeConfigError:
        logger.exception("exchange_alias_config_error", extra={"alias": alias_email})
    except ExchangeError:
        logger.exception("exchange_alias_deprovision_failed", extra={"alias": alias_email})
