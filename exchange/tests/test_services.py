"""Unit tests for ExchangeAliasService.

These tests mock subprocess.run entirely; they never invoke pwsh.
For a real integration test, see the manual procedure documented in
exchange/README.md.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from exchange.exceptions import (
    AliasAlreadyExists,
    AliasNotFound,
    ExchangeAuthError,
    ExchangeConfigError,
    ExchangeError,
    ExchangeTimeoutError,
    InvalidAliasInput,
    MailboxNotFound,
)
from exchange.services import AliasResult, ExchangeAliasService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _completed(stdout: str, *, returncode: int = 0, stderr: str = "") -> MagicMock:
    """Build a fake CompletedProcess returned by subprocess.run."""
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.stdout = stdout
    cp.stderr = stderr
    cp.returncode = returncode
    return cp


@pytest.fixture
def service(tmp_path):
    """Return a service pointed at a fake script path (subprocess is mocked)."""
    fake_script = tmp_path / "manage_alias.ps1"
    fake_script.write_text("# fake")
    return ExchangeAliasService(script_path=str(fake_script), timeout=5)


# ---------------------------------------------------------------------------
# Construction / configuration
# ---------------------------------------------------------------------------

@override_settings(EXCHANGE_PS_SCRIPT_PATH="")
def test_constructor_raises_when_script_path_missing():
    with pytest.raises(ExchangeConfigError):
        ExchangeAliasService()


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-an-email",
        "no@tld",
        "spaces in@example.com",
        "shell$injection@example.com",
        "back`tick@example.com",
        "semi;colon@example.com",
        "pipe|here@example.com",
        "newline\n@example.com",
        "ampersand&@example.com",
        "quote\"@example.com",
        "single'quote@example.com",
        "a" * 320 + "@example.com",  # too long
    ],
)
def test_validation_rejects_suspicious_inputs(service, value):
    with pytest.raises(InvalidAliasInput):
        service.add_alias(mailbox="ok@example.com", alias=value)


def test_validation_rejects_bad_mailbox(service):
    with pytest.raises(InvalidAliasInput):
        service.add_alias(mailbox="bad mailbox", alias="ok@example.com")


# ---------------------------------------------------------------------------
# add_alias
# ---------------------------------------------------------------------------

@patch("exchange.services.subprocess.run")
def test_add_alias_success(mock_run, service):
    payload = {
        "success": True,
        "data": {"mailbox": "shared@example.com", "alias": "myapp@example.com", "action": "added"},
    }
    mock_run.return_value = _completed(json.dumps(payload))

    result = service.add_alias(mailbox="shared@example.com", alias="myapp@example.com")

    assert isinstance(result, AliasResult)
    assert result.action == "added"
    assert result.alias == "myapp@example.com"

    args, kwargs = mock_run.call_args
    cmd = args[0]
    assert cmd[0] == "pwsh"
    assert "-Action" in cmd and cmd[cmd.index("-Action") + 1] == "add"
    assert kwargs["shell"] is False
    assert kwargs["timeout"] == 5


@patch("exchange.services.subprocess.run")
def test_add_alias_already_exists(mock_run, service):
    payload = {
        "success": False,
        "error_code": "alias_already_exists",
        "error": "Alias 'x@example.com' already exists on mailbox 'shared@example.com'.",
    }
    mock_run.return_value = _completed(json.dumps(payload), returncode=6)

    with pytest.raises(AliasAlreadyExists) as excinfo:
        service.add_alias(mailbox="shared@example.com", alias="x@example.com")
    assert excinfo.value.error_code == "alias_already_exists"


@patch("exchange.services.subprocess.run")
def test_add_alias_mailbox_not_found(mock_run, service):
    payload = {"success": False, "error_code": "mailbox_not_found", "error": "Mailbox not found."}
    mock_run.return_value = _completed(json.dumps(payload), returncode=5)

    with pytest.raises(MailboxNotFound):
        service.add_alias(mailbox="missing@example.com", alias="x@example.com")


# ---------------------------------------------------------------------------
# remove_alias
# ---------------------------------------------------------------------------

@patch("exchange.services.subprocess.run")
def test_remove_alias_success(mock_run, service):
    payload = {
        "success": True,
        "data": {"mailbox": "shared@example.com", "alias": "x@example.com", "action": "removed"},
    }
    mock_run.return_value = _completed(json.dumps(payload))

    result = service.remove_alias(mailbox="shared@example.com", alias="x@example.com")

    assert result.action == "removed"


@patch("exchange.services.subprocess.run")
def test_remove_alias_not_found(mock_run, service):
    payload = {"success": False, "error_code": "alias_not_found", "error": "Alias not found."}
    mock_run.return_value = _completed(json.dumps(payload), returncode=8)

    with pytest.raises(AliasNotFound):
        service.remove_alias(mailbox="shared@example.com", alias="x@example.com")


# ---------------------------------------------------------------------------
# list_aliases
# ---------------------------------------------------------------------------

@patch("exchange.services.subprocess.run")
def test_list_aliases_returns_list(mock_run, service):
    payload = {
        "success": True,
        "data": ["SMTP:shared@example.com", "smtp:alias1@example.com"],
    }
    mock_run.return_value = _completed(json.dumps(payload))

    result = service.list_aliases("shared@example.com")

    assert result == ["SMTP:shared@example.com", "smtp:alias1@example.com"]


@patch("exchange.services.subprocess.run")
def test_list_aliases_unexpected_payload(mock_run, service):
    payload = {"success": True, "data": {"oops": "not a list"}}
    mock_run.return_value = _completed(json.dumps(payload))

    with pytest.raises(ExchangeError):
        service.list_aliases("shared@example.com")


# ---------------------------------------------------------------------------
# Auth / timeout / malformed JSON
# ---------------------------------------------------------------------------

@patch("exchange.services.subprocess.run")
def test_auth_failed(mock_run, service):
    payload = {"success": False, "error_code": "auth_failed", "error": "Cert expired."}
    mock_run.return_value = _completed(json.dumps(payload), returncode=4)

    with pytest.raises(ExchangeAuthError):
        service.add_alias(mailbox="shared@example.com", alias="a@example.com")


@patch("exchange.services.subprocess.run")
def test_timeout_raises_typed_exception(mock_run, service):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="pwsh", timeout=5)

    with pytest.raises(ExchangeTimeoutError):
        service.add_alias(mailbox="shared@example.com", alias="a@example.com")


@patch("exchange.services.subprocess.run")
def test_malformed_json_raises_exchange_error(mock_run, service):
    mock_run.return_value = _completed("this is not JSON at all")

    with pytest.raises(ExchangeError):
        service.add_alias(mailbox="shared@example.com", alias="a@example.com")


@patch("exchange.services.subprocess.run")
def test_empty_stdout_raises_exchange_error(mock_run, service):
    mock_run.return_value = _completed("", returncode=1, stderr="something blew up")

    with pytest.raises(ExchangeError) as excinfo:
        service.add_alias(mailbox="shared@example.com", alias="a@example.com")
    assert "empty stdout" in str(excinfo.value)


@patch("exchange.services.subprocess.run")
def test_last_line_is_used_as_json(mock_run, service):
    """If the script writes warnings before the JSON line, only the last line counts."""
    payload = {
        "success": True,
        "data": {"mailbox": "shared@example.com", "alias": "a@example.com", "action": "added"},
    }
    stdout = "WARNING: something\n" + json.dumps(payload)
    mock_run.return_value = _completed(stdout)

    result = service.add_alias(mailbox="shared@example.com", alias="a@example.com")
    assert result.action == "added"


# ---------------------------------------------------------------------------
# Subprocess argument hygiene
# ---------------------------------------------------------------------------

@patch("exchange.services.subprocess.run")
def test_subprocess_uses_list_form_no_shell(mock_run, service):
    payload = {
        "success": True,
        "data": {"mailbox": "shared@example.com", "alias": "a@example.com", "action": "added"},
    }
    mock_run.return_value = _completed(json.dumps(payload))

    service.add_alias(mailbox="shared@example.com", alias="a@example.com")

    args, kwargs = mock_run.call_args
    assert isinstance(args[0], list)
    assert kwargs["shell"] is False
    # Secrets and credentials must never appear on the command line.
    cmd_str = " ".join(args[0])
    assert "EXCHANGE_CERT_PASSWORD" not in cmd_str
    assert "EXCHANGE_APP_ID" not in cmd_str


@patch("exchange.services.subprocess.run")
@override_settings(EXCHANGE_APP_ID="app-id-123", EXCHANGE_TENANT="contoso.onmicrosoft.com")
def test_subprocess_forwards_secrets_via_env(mock_run, service):
    payload = {
        "success": True,
        "data": {"mailbox": "shared@example.com", "alias": "a@example.com", "action": "added"},
    }
    mock_run.return_value = _completed(json.dumps(payload))

    service.add_alias(mailbox="shared@example.com", alias="a@example.com")

    _, kwargs = mock_run.call_args
    env = kwargs["env"]
    assert env["EXCHANGE_APP_ID"] == "app-id-123"
    assert env["EXCHANGE_TENANT"] == "contoso.onmicrosoft.com"
