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


def _is_configured() -> bool:
    return bool(
        getattr(settings, "EXCHANGE_PS_SCRIPT_PATH", "")
        and getattr(settings, "EXCHANGE_APP_ID", "")
        and getattr(settings, "EXCHANGE_TENANT", "")
        and getattr(settings, "EXCHANGE_SHARED_MAILBOX", "")
    )


def provision_alias_for_application(alias_email: str) -> None:
    if not _is_configured():
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
    if not _is_configured():
        logger.warning("exchange_alias_skipped", extra={"reason": "not configured", "alias": alias_email})
        return
    try:
        service = ExchangeAliasService()
        service.remove_alias(mailbox=settings.EXCHANGE_SHARED_MAILBOX, alias=alias_email)
    except ExchangeConfigError:
        logger.exception("exchange_alias_config_error", extra={"alias": alias_email})
    except ExchangeError:
        logger.exception("exchange_alias_deprovision_failed", extra={"alias": alias_email})
