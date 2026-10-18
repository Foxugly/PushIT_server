"""Inbound e-mail sender authentication (audit S-I4).

The `From` of an inbound mail is what authorizes the push to every subscriber of
the owner's application, and it is forgeable. With INBOUND_EMAIL_REQUIRE_DMARC
on (the prod default), a mail is turned into a notification only when M365's
Authentication-Results shows an aligned DMARC pass, or when Exchange stamped it
as sent by an authenticated user of our own tenant.
"""

from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import override_settings

from accounts.models import User
from applications.graph_mail import GraphEmail
from applications.models import Application
from notifications.inbound_auth import evaluate_sender_authentication
from notifications.inbound_mailbox import poll_inbound_mailbox
from notifications.models import InboundEmailIngestionLog, Notification

AR_PASS = "spf=pass smtp.mailfrom=example.com; dkim=pass header.d=example.com; dmarc=pass action=none header.from=example.com;compauth=pass reason=100"
AR_BESTGUESS = "spf=pass smtp.mailfrom=example.com; dkim=none; dmarc=bestguesspass action=none header.from=example.com;compauth=pass reason=109"
AR_NONE = "spf=softfail smtp.mailfrom=example.com; dkim=none; dmarc=none action=none header.from=example.com;compauth=softpass reason=201"
AR_FAIL = "spf=fail smtp.mailfrom=example.com; dkim=none; dmarc=fail action=none header.from=example.com;compauth=fail reason=000"
AR_PASS_OTHER_DOMAIN = "spf=pass; dkim=pass; dmarc=pass action=none header.from=attacker.test"


# ------------------------------------------------------------------ the verdict


@pytest.mark.parametrize(
    "auth_results,auth_as,accepted,reason",
    [
        (AR_PASS, "", True, "dmarc_pass"),
        (AR_BESTGUESS, "", True, "dmarc_bestguesspass"),
        (AR_NONE, "", False, "dmarc_none"),
        (AR_FAIL, "", False, "dmarc_fail"),
        (AR_PASS_OTHER_DOMAIN, "", False, "misaligned"),
        ("", "", False, "no_auth_results"),
        ("spf=pass; dkim=pass", "", False, "no_dmarc_verdict"),
        # Mail from an authenticated user of our own M365 tenant carries no
        # SMTP-boundary verdict; Exchange stamps AuthAs=Internal instead (and
        # strips that header from anything arriving from outside).
        ("", "Internal", True, "internal_org"),
        (AR_FAIL, "Internal", True, "internal_org"),
        ("", "Anonymous", False, "no_auth_results"),
    ],
)
def test_verdict(auth_results, auth_as, accepted, reason):
    verdict = evaluate_sender_authentication(auth_results, "example.com", auth_as)
    assert (verdict.accepted, verdict.reason) == (accepted, reason)


def test_verdict_is_case_insensitive():
    verdict = evaluate_sender_authentication(AR_PASS.upper(), "Example.COM")
    assert verdict.accepted


def test_header_from_must_match_exactly_not_as_a_prefix():
    ar = "dmarc=pass action=none header.from=example.com.attacker.test"
    assert not evaluate_sender_authentication(ar, "example.com").accepted


@override_settings(INBOUND_EMAIL_DMARC_ALLOW_NO_POLICY=True)
def test_no_policy_domains_can_be_allowed_by_setting():
    assert evaluate_sender_authentication(AR_NONE, "example.com").accepted
    # ...but never a real failure.
    assert not evaluate_sender_authentication(AR_FAIL, "example.com").accepted


@override_settings(INBOUND_EMAIL_DMARC_ACCEPT_BESTGUESSPASS=False)
def test_bestguesspass_can_be_refused_by_setting():
    assert not evaluate_sender_authentication(AR_BESTGUESS, "example.com").accepted


@override_settings(INBOUND_EMAIL_DMARC_TRUST_INTERNAL=False)
def test_internal_trust_can_be_disabled():
    assert not evaluate_sender_authentication("", "example.com", "Internal").accepted


def test_prod_settings_enforce_by_default():
    import re
    from pathlib import Path

    prod = (Path(__file__).resolve().parents[2] / "config" / "settings" / "prod.py").read_text(encoding="utf-8")
    assert re.search(r'INBOUND_EMAIL_REQUIRE_DMARC\s*=\s*env\.bool\("INBOUND_EMAIL_REQUIRE_DMARC",\s*default=True\)', prod)


# ------------------------------------------------------------- the poller path

_GRAPH = dict(
    INBOUND_EMAIL_DOMAIN="pushit.com",
    GRAPH_CLIENT_ID="fake-client-id",
    GRAPH_TENANT_ID="fake-tenant",
    GRAPH_CLIENT_SECRET="fake-secret",
    GRAPH_MAILBOX_USER_ID="mailbox@pushit.com",
)


@pytest.fixture
def owner_app(db):
    cache.clear()
    with patch("applications.models.Application._provision_exchange_alias"):
        owner = User.objects.create_user(email="owner@example.com", password="MotDePasseTresSolide123!")
        app = Application.objects.create(owner=owner, name="DMARC App")
    return owner, app


def _mail(app, graph_id, auth_results="", headers=None):
    return GraphEmail(
        graph_id=graph_id,
        sender="owner@example.com",
        recipient=f"{app.inbound_email_alias}@pushit.com",
        subject="Alert",
        text="Body.",
        message_id=f"{graph_id}@example.com",
        authentication_results=auth_results,
        headers=headers or {},
    )


@pytest.mark.django_db
@override_settings(**_GRAPH, INBOUND_EMAIL_REQUIRE_DMARC=True, INBOUND_EMAIL_DMARC_NOTIFY_OWNER=True)
@patch("notifications.inbound_mailbox.send_email")
@patch("notifications.inbound_mailbox.send_unknown_address_reply")
@patch("notifications.inbound_mailbox.mark_email_read")
@patch("notifications.inbound_mailbox.fetch_unread_emails")
def test_spoofed_mail_is_rejected_logged_masked_and_owner_warned(
    mock_fetch, mock_mark_read, mock_reply, mock_send, owner_app, caplog
):
    owner, app = owner_app
    mock_fetch.return_value = [_mail(app, "g-1", AR_FAIL)]

    with caplog.at_level("WARNING"):
        result = poll_inbound_mailbox()

    assert result["rejected_count"] == 1
    assert Notification.objects.count() == 0
    log = InboundEmailIngestionLog.objects.get()
    assert log.status == "rejected"
    assert "DMARC" in log.error_message
    mock_mark_read.assert_called_once_with("g-1")
    # No "unknown address" auto-reply for a forged mail.
    mock_reply.assert_not_called()

    record = next(r for r in caplog.records if r.getMessage() == "inbound_email_dmarc_rejected")
    assert record.sender == "o***@example.com"
    assert record.sender_domain == "example.com"
    assert record.auth_reason == "dmarc_fail"
    assert "owner@example.com" not in caplog.text

    # The owner is told (at their real address, not a reply-to path).
    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["to"] == owner.email
    assert app.inbound_email_address in mock_send.call_args.kwargs["body"]


@pytest.mark.django_db
@override_settings(**_GRAPH, INBOUND_EMAIL_REQUIRE_DMARC=True, INBOUND_EMAIL_DMARC_NOTIFY_OWNER=True)
@patch("notifications.inbound_mailbox.send_email")
@patch("notifications.inbound_mailbox.mark_email_read")
@patch("notifications.inbound_mailbox.fetch_unread_emails")
def test_owner_warning_is_rate_limited(mock_fetch, _mark, mock_send, owner_app):
    _, app = owner_app
    mock_fetch.return_value = [_mail(app, "g-2", AR_FAIL), _mail(app, "g-3", "")]

    result = poll_inbound_mailbox()

    assert result["rejected_count"] == 2
    assert mock_send.call_count == 1


@pytest.mark.django_db
@override_settings(**_GRAPH, INBOUND_EMAIL_REQUIRE_DMARC=True, INBOUND_EMAIL_DMARC_NOTIFY_OWNER=False)
@patch("notifications.inbound_mailbox.send_email")
@patch("notifications.inbound_mailbox.mark_email_read")
@patch("notifications.inbound_mailbox.fetch_unread_emails")
def test_owner_warning_can_be_disabled(mock_fetch, _mark, mock_send, owner_app):
    _, app = owner_app
    mock_fetch.return_value = [_mail(app, "g-4", AR_FAIL)]

    poll_inbound_mailbox()

    mock_send.assert_not_called()


@pytest.mark.django_db
@override_settings(**_GRAPH, INBOUND_EMAIL_REQUIRE_DMARC=True)
@patch("notifications.inbound_mailbox.send_email")
@patch("notifications.inbound_mailbox.mark_email_read")
@patch("notifications.inbound_mailbox.fetch_unread_emails")
def test_internal_tenant_mail_without_auth_results_is_accepted(mock_fetch, _mark, mock_send, owner_app):
    _, app = owner_app
    mock_fetch.return_value = [
        _mail(app, "g-5", "", headers={"x-ms-exchange-organization-authas": "Internal"})
    ]

    result = poll_inbound_mailbox()

    assert result["created_count"] == 1
    mock_send.assert_not_called()


@pytest.mark.django_db
@override_settings(**_GRAPH, INBOUND_EMAIL_REQUIRE_DMARC=True)
@patch("notifications.inbound_mailbox.mark_email_read")
@patch("notifications.inbound_mailbox.fetch_unread_emails")
def test_aligned_pass_and_bestguesspass_are_accepted(mock_fetch, _mark, owner_app):
    _, app = owner_app
    mock_fetch.return_value = [_mail(app, "g-6", AR_PASS), _mail(app, "g-7", AR_BESTGUESS)]

    result = poll_inbound_mailbox()

    assert result["created_count"] == 2


@pytest.mark.django_db
@override_settings(**_GRAPH, INBOUND_EMAIL_REQUIRE_DMARC=False)
@patch("notifications.inbound_mailbox.send_email")
@patch("notifications.inbound_mailbox.mark_email_read")
@patch("notifications.inbound_mailbox.fetch_unread_emails")
def test_observation_mode_still_creates_and_logs_the_reason(mock_fetch, _mark, mock_send, owner_app, caplog):
    _, app = owner_app
    mock_fetch.return_value = [_mail(app, "g-8", AR_NONE)]

    with caplog.at_level("INFO"):
        result = poll_inbound_mailbox()

    assert result["created_count"] == 1
    record = next(r for r in caplog.records if r.getMessage() == "inbound_dmarc_would_reject")
    assert record.auth_reason == "dmarc_none"
    assert record.sender_domain == "example.com"
    mock_send.assert_not_called()


def test_json_log_keeps_the_dmarc_fields():
    import json
    import logging

    from config.logging_utils import JsonFormatter

    record = logging.LogRecord("x", logging.INFO, __file__, 1, "inbound_dmarc_would_reject", None, None)
    record.sender_domain = "example.com"
    record.auth_reason = "dmarc_none"
    record.dmarc_verdict = "none"
    record.has_auth_results = True
    payload = json.loads(JsonFormatter().format(record))
    assert payload["sender_domain"] == "example.com"
    assert payload["auth_reason"] == "dmarc_none"
    assert payload["dmarc_verdict"] == "none"
    assert payload["has_auth_results"] is True
