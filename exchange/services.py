"""Service layer for Exchange Online alias management.

Wraps a PowerShell Core script (``manage_alias.ps1``) executed via
``subprocess.run``. The script authenticates to Exchange Online with an
Azure AD app + certificate, then manipulates the ``EmailAddresses`` collection
of a target shared mailbox.

All inputs are validated Python-side before invoking ``pwsh`` to avoid argument
injection. Secrets are passed via the subprocess environment, never on the
command line.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Final

from django.conf import settings

from .exceptions import (
    ExchangeConfigError,
    ExchangeError,
    ExchangeTimeoutError,
    InvalidAliasInput,
    exception_for_code,
)

logger = logging.getLogger(__name__)

# RFC 5321 is permissive but for our purposes a tight subset is safer:
# local-part: letters, digits, dot, dash, underscore, plus
# domain:     letters, digits, dot, dash
_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9._+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"
)

# Forbid characters that could cause argument confusion with pwsh, even though
# we use shell=False. Defence in depth.
_FORBIDDEN_CHARS: Final[frozenset[str]] = frozenset("\"'`$;|&<>\n\r\t\\")

# Env vars forwarded to the PowerShell subprocess.
_FORWARDED_ENV_KEYS: Final[tuple[str, ...]] = (
    "EXCHANGE_APP_ID",
    "EXCHANGE_TENANT",
    "EXCHANGE_CERT_THUMBPRINT",
    "EXCHANGE_CERT_FILE_PATH",
    "EXCHANGE_CERT_PASSWORD",
)


@dataclass(frozen=True)
class AliasResult:
    """Result of a successful add/remove operation."""

    mailbox: str
    alias: str
    action: str  # "added" | "removed"


class ExchangeAliasService:
    """Synchronous client around ``scripts/exchange/manage_alias.ps1``.

    Intended to be instantiated once per call site (cheap — no shared state
    other than the resolved settings). Methods raise ``ExchangeError`` subclasses
    on failure.
    """

    def __init__(
        self,
        *,
        script_path: str | None = None,
        timeout: int | None = None,
        pwsh_executable: str = "pwsh",
    ) -> None:
        self._script_path = script_path or getattr(settings, "EXCHANGE_PS_SCRIPT_PATH", "")
        self._timeout = timeout if timeout is not None else int(getattr(settings, "EXCHANGE_PS_TIMEOUT", 60))
        self._pwsh = pwsh_executable

        if not self._script_path:
            raise ExchangeConfigError("EXCHANGE_PS_SCRIPT_PATH is not configured.")

    # ---------------------------------------------------------------- public

    def add_alias(self, mailbox: str, alias: str) -> AliasResult:
        """Attach ``alias`` (full email) to ``mailbox`` as an SMTP proxy address."""
        self._validate_email(mailbox, field="mailbox")
        self._validate_email(alias, field="alias")
        data = self._run("add", mailbox=mailbox, alias=alias)
        return AliasResult(
            mailbox=str(data.get("mailbox", mailbox)),
            alias=str(data.get("alias", alias)),
            action=str(data.get("action", "added")),
        )

    def remove_alias(self, mailbox: str, alias: str) -> AliasResult:
        """Detach ``alias`` from ``mailbox``."""
        self._validate_email(mailbox, field="mailbox")
        self._validate_email(alias, field="alias")
        data = self._run("remove", mailbox=mailbox, alias=alias)
        return AliasResult(
            mailbox=str(data.get("mailbox", mailbox)),
            alias=str(data.get("alias", alias)),
            action=str(data.get("action", "removed")),
        )

    def list_aliases(self, mailbox: str) -> list[str]:
        """Return the raw ``EmailAddresses`` collection on ``mailbox``."""
        self._validate_email(mailbox, field="mailbox")
        data = self._run("list", mailbox=mailbox)
        if not isinstance(data, list):
            raise ExchangeError(f"Unexpected list payload from script: {data!r}")
        return [str(item) for item in data]

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _validate_email(value: str, *, field: str) -> None:
        if not isinstance(value, str) or not value:
            raise InvalidAliasInput(f"{field} must be a non-empty string.")
        if any(ch in _FORBIDDEN_CHARS for ch in value):
            raise InvalidAliasInput(f"{field} contains forbidden characters.")
        if len(value) > 320:  # RFC 5321 max length
            raise InvalidAliasInput(f"{field} exceeds 320 characters.")
        if not _EMAIL_RE.match(value):
            raise InvalidAliasInput(f"{field} is not a valid email address: {value!r}")

    def _build_env(self) -> dict[str, str]:
        # Start from a minimal, explicit env: forward only what the script needs
        # plus the basics for pwsh to load profiles/modules.
        import os

        passthrough = ("PATH", "HOME", "USER", "USERNAME", "USERPROFILE", "TEMP", "TMP", "TMPDIR", "PSModulePath")
        env: dict[str, str] = {k: os.environ[k] for k in passthrough if k in os.environ}

        for key in _FORWARDED_ENV_KEYS:
            value = getattr(settings, key, "") or os.environ.get(key, "")
            if value:
                env[key] = value
        return env

    def _run(self, action: str, *, mailbox: str, alias: str | None = None) -> object:
        cmd = [
            self._pwsh,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            self._script_path,
            "-Action",
            action,
            "-Mailbox",
            mailbox,
        ]
        if alias is not None:
            cmd += ["-Alias", alias]

        logger.info(
            "exchange_alias_invoke",
            extra={"action": action, "mailbox": mailbox, "alias": alias},
        )

        try:
            completed = subprocess.run(  # noqa: S603 — shell=False, args is a list
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
                env=self._build_env(),
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            logger.error(
                "exchange_alias_timeout",
                extra={"action": action, "mailbox": mailbox, "alias": alias, "timeout": self._timeout},
            )
            raise ExchangeTimeoutError(
                f"PowerShell timed out after {self._timeout}s while running '{action}'."
            ) from exc

        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()

        if not stdout:
            logger.error(
                "exchange_alias_empty_stdout",
                extra={"action": action, "returncode": completed.returncode, "stderr": stderr[:500]},
            )
            raise ExchangeError(
                f"PowerShell returned empty stdout (exit={completed.returncode}). stderr: {stderr[:500]}"
            )

        # The script may write multiple lines; the last non-empty line is the JSON result.
        last_line = next((line for line in reversed(stdout.splitlines()) if line.strip()), "")
        try:
            payload = json.loads(last_line)
        except json.JSONDecodeError as exc:
            logger.error(
                "exchange_alias_bad_json",
                extra={"action": action, "stdout": stdout[:500], "stderr": stderr[:500]},
            )
            raise ExchangeError(f"Could not parse JSON from PowerShell: {exc}; stdout={stdout[:200]!r}") from exc

        if not isinstance(payload, dict):
            raise ExchangeError(f"Unexpected JSON shape from PowerShell: {payload!r}")

        if payload.get("success") is True:
            logger.info(
                "exchange_alias_success",
                extra={"action": action, "mailbox": mailbox, "alias": alias},
            )
            return payload.get("data")

        code = str(payload.get("error_code") or "exchange_error")
        message = str(payload.get("error") or "Unknown Exchange error.")
        logger.error(
            "exchange_alias_failure",
            extra={
                "action": action,
                "mailbox": mailbox,
                "alias": alias,
                "error_code": code,
                "error": message,
            },
        )
        exc_cls = exception_for_code(code)
        raise exc_cls(message, error_code=code)
