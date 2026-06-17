"""Unit tests for exchange.integration.alias_status / is_configured."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import override_settings

from exchange.exceptions import ExchangeError
from exchange.integration import _is_configured, alias_status, is_configured


_CONFIGURED = dict(
    EXCHANGE_PS_SCRIPT_PATH="/tmp/manage_alias.ps1",
    EXCHANGE_APP_ID="app-id",
    EXCHANGE_TENANT="contoso.onmicrosoft.com",
    EXCHANGE_SHARED_MAILBOX="shared@foxugly.com",
)


def test_is_configured_alias_points_to_public_name():
    assert _is_configured is is_configured


@override_settings(EXCHANGE_APP_ID="", EXCHANGE_SHARED_MAILBOX="")
def test_alias_status_not_configured():
    result = alias_status("app_demo_abcd@foxugly.com")
    assert result == {
        "configured": False,
        "provisioned": None,
        "detail": "exchange_not_configured",
    }


@override_settings(**_CONFIGURED)
def test_alias_status_provisioned_smtp_prefixed_case_insensitive():
    addresses = ["SMTP:shared@foxugly.com", "smtp:App_Demo_ABCD@foxugly.com"]
    with patch(
        "exchange.integration.ExchangeAliasService.list_aliases",
        return_value=addresses,
    ):
        result = alias_status("app_demo_abcd@foxugly.com")
    assert result["configured"] is True
    assert result["provisioned"] is True
    assert result["detail"] == "provisioned"


@override_settings(**_CONFIGURED)
def test_alias_status_not_provisioned():
    with patch(
        "exchange.integration.ExchangeAliasService.list_aliases",
        return_value=["SMTP:shared@foxugly.com"],
    ):
        result = alias_status("app_demo_abcd@foxugly.com")
    assert result["configured"] is True
    assert result["provisioned"] is False
    assert result["detail"] == "not_provisioned"


@override_settings(**_CONFIGURED)
def test_alias_status_exchange_error_does_not_raise():
    with patch(
        "exchange.integration.ExchangeAliasService.list_aliases",
        side_effect=ExchangeError("boom", error_code="mailbox_not_found"),
    ):
        result = alias_status("app_demo_abcd@foxugly.com")
    assert result["configured"] is True
    assert result["provisioned"] is None
    assert result["detail"] == "mailbox_not_found"
