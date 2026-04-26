"""Exchange Online alias management via PowerShell Core."""

from .exceptions import (
    AliasAlreadyExists,
    AliasNotFound,
    ExchangeAuthError,
    ExchangeConfigError,
    ExchangeError,
    ExchangeTimeoutError,
    InvalidAliasInput,
    MailboxNotFound,
)
from .services import ExchangeAliasService

__all__ = [
    "AliasAlreadyExists",
    "AliasNotFound",
    "ExchangeAliasService",
    "ExchangeAuthError",
    "ExchangeConfigError",
    "ExchangeError",
    "ExchangeTimeoutError",
    "InvalidAliasInput",
    "MailboxNotFound",
]
