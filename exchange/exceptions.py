"""Custom exceptions for the Exchange alias service."""

from __future__ import annotations


class ExchangeError(Exception):
    """Base class for all Exchange alias service errors."""

    error_code: str = "exchange_error"

    def __init__(self, message: str = "", *, error_code: str | None = None) -> None:
        super().__init__(message)
        if error_code is not None:
            self.error_code = error_code


class ExchangeConfigError(ExchangeError):
    """Raised when required configuration (settings or env vars) is missing."""

    error_code = "missing_config"


class InvalidAliasInput(ExchangeError):
    """Raised when a mailbox or alias argument fails Python-side validation."""

    error_code = "invalid_input"


class ExchangeAuthError(ExchangeError):
    """Raised when PowerShell fails to authenticate against Exchange Online."""

    error_code = "auth_failed"


class MailboxNotFound(ExchangeError):
    """Raised when the target mailbox cannot be resolved by Exchange."""

    error_code = "mailbox_not_found"


class AliasAlreadyExists(ExchangeError):
    """Raised when adding an alias that is already attached to the mailbox."""

    error_code = "alias_already_exists"


class AliasNotFound(ExchangeError):
    """Raised when removing an alias that is not attached to the mailbox."""

    error_code = "alias_not_found"


class ExchangeTimeoutError(ExchangeError):
    """Raised when the PowerShell subprocess exceeds its timeout."""

    error_code = "timeout"


_ERROR_CODE_TO_EXC: dict[str, type[ExchangeError]] = {
    "missing_param":         InvalidAliasInput,
    "missing_env":           ExchangeConfigError,
    "auth_failed":           ExchangeAuthError,
    "mailbox_not_found":     MailboxNotFound,
    "alias_already_exists":  AliasAlreadyExists,
    "alias_not_found":       AliasNotFound,
    "exchange_error":        ExchangeError,
}


def exception_for_code(code: str) -> type[ExchangeError]:
    """Map a script error_code to a Python exception class."""
    return _ERROR_CODE_TO_EXC.get(code, ExchangeError)
