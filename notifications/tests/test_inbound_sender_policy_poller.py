"""Inbound mailbox poller x per-application sender policy: who is accepted,
DMARC on every accepted sender, and how the owner is told about refusals."""

from unittest.mock import patch

import pytest
from django.test import override_settings

from accounts.models import User
from applications.graph_mail import GraphEmail
from applications.models import Application
from devices.models import Device, DeviceTokenStatus
from notifications.inbound_mailbox import poll_inbound_mailbox
from notifications.models import InboundEmailIngestionLog, Notification

AR_PASS = "spf=pass; dkim=pass; dmarc=pass action=none header.from={domain};compauth=pass reason=100"
AR_FAIL = "spf=fail; dkim=none; dmarc=fail action=none header.from={domain};compauth=fail reason=000"

GRAPH = dict(
    INBOUND_EMAIL_DOMAIN="pushit.com",
    GRAPH_CLIENT_ID="fake-client-id",
    GRAPH_TENANT_ID="fake-tenant",
    GRAPH_CLIENT_SECRET="fake-secret",
    GRAPH_MAILBOX_USER_ID="mailbox@pushit.com",
    INBOUND_EMAIL_REQUIRE_DMARC=True,
    INBOUND_EMAIL_DMARC_NOTIFY_OWNER=True,
)


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="owner@example.com", password="MotDePasseTresSolide123!")


@pytest.fixture
def make_app(owner):
    def _make(**fields):
        with patch("applications.models.Application._provision_exchange_alias"):
            return Application.objects.create(owner=owner, name="Policy App", **fields)

    return _make


@pytest.fixture
def mailbox():
    with (
        patch("notifications.inbound_mailbox.fetch_unread_emails") as fetch,
        patch("notifications.inbound_mailbox.mark_email_read") as mark_read,
        patch("notifications.inbound_mailbox.send_email") as send_email,
        patch("notifications.inbound_mailbox.send_unknown_address_reply") as unknown_reply,
        patch("notifications.inbound_mailbox.send_rejected_sender_reply") as rejected_reply,
        patch("notifications.inbound_mailbox.send_push_to_device", return_value="msg-1") as push,
    ):
        yield type(
            "Mailbox",
            (),
            dict(
                fetch=fetch,
                mark_read=mark_read,
                send_email=send_email,
                unknown_reply=unknown_reply,
                rejected_reply=rejected_reply,
                push=push,
            ),
        )


def _mail(app, sender, graph_id="g-1", auth="pass", headers=None):
    domain = sender.rpartition("@")[2]
    template = AR_PASS if auth == "pass" else AR_FAIL
    return GraphEmail(
        graph_id=graph_id,
        sender=sender,
        recipient=f"{app.inbound_email_alias}@pushit.com",
        subject="Alert",
        text="Body.",
        message_id=f"{graph_id}@x",
        authentication_results=template.format(domain=domain),
        headers=headers or {},
    )


def _log():
    return InboundEmailIngestionLog.objects.get()


# --------------------------------------------------------------- acceptance


@override_settings(**GRAPH)
def test_owner_is_accepted_by_default(make_app, mailbox):
    app = make_app()
    mailbox.fetch.return_value = [_mail(app, "owner@example.com")]
    assert poll_inbound_mailbox()["created_count"] == 1
    assert Notification.objects.get().application_id == app.id


@override_settings(**GRAPH)
def test_stranger_is_refused_in_owner_mode(make_app, mailbox):
    app = make_app()
    mailbox.fetch.return_value = [_mail(app, "stranger@else.org")]
    assert poll_inbound_mailbox()["rejected_count"] == 1
    assert Notification.objects.count() == 0
    log = _log()
    assert log.application_id == app.id
    assert "not_allowed" in log.error_message


@pytest.mark.parametrize("entry", ["ally@partner.io", "@partner.io"])
@override_settings(**GRAPH)
def test_allowlisted_non_user_is_accepted_and_journalled(make_app, mailbox, entry):
    app = make_app(inbound_sender_allowlist=[entry])
    mailbox.fetch.return_value = [_mail(app, "Ally@Partner.io")]

    assert poll_inbound_mailbox()["created_count"] == 1
    notification = Notification.objects.get()
    # Authored by the application (hence its owner); the real sender is on the log.
    assert notification.application.owner.email == "owner@example.com"
    log = _log()
    assert log.status == "created"
    assert log.sender.lower() == "ally@partner.io"
    assert log.notification_id == notification.id


@override_settings(**GRAPH)
def test_allowlisted_sender_must_pass_dmarc(make_app, mailbox):
    app = make_app(inbound_sender_allowlist=["@partner.io"])
    mailbox.fetch.return_value = [_mail(app, "ally@partner.io", auth="fail")]

    assert poll_inbound_mailbox()["rejected_count"] == 1
    assert Notification.objects.count() == 0
    assert "DMARC" in _log().error_message
    mailbox.send_email.assert_called_once()
    assert "not authenticated" in mailbox.send_email.call_args.kwargs["subject"]


@override_settings(**GRAPH)
def test_anyone_mode_accepts_any_authenticated_sender(make_app, mailbox):
    app = make_app(inbound_sender_mode="anyone")
    mailbox.fetch.return_value = [_mail(app, "random@else.org")]
    assert poll_inbound_mailbox()["created_count"] == 1


@override_settings(**GRAPH)
def test_anyone_mode_still_enforces_dmarc(make_app, mailbox):
    app = make_app(inbound_sender_mode="anyone")
    mailbox.fetch.return_value = [_mail(app, "random@else.org", auth="fail")]
    assert poll_inbound_mailbox()["rejected_count"] == 1
    assert "DMARC" in _log().error_message


@override_settings(**{**GRAPH, "INBOUND_EMAIL_REQUIRE_DMARC": False})
def test_observation_mode_lets_an_unauthenticated_allowlisted_sender_through(make_app, mailbox):
    app = make_app(inbound_sender_allowlist=["ally@partner.io"])
    mailbox.fetch.return_value = [_mail(app, "ally@partner.io", auth="fail")]
    assert poll_inbound_mailbox()["created_count"] == 1


@override_settings(**GRAPH)
def test_blocklist_refuses_in_anyone_mode(make_app, mailbox):
    app = make_app(inbound_sender_mode="anyone", inbound_sender_blocklist=["@else.org"])
    mailbox.fetch.return_value = [_mail(app, "random@else.org")]
    assert poll_inbound_mailbox()["rejected_count"] == 1
    assert "blocklisted" in _log().error_message


@override_settings(**GRAPH)
def test_blocklist_wins_over_allowlist(make_app, mailbox):
    app = make_app(inbound_sender_allowlist=["@partner.io"], inbound_sender_blocklist=["bad@partner.io"])
    mailbox.fetch.return_value = [_mail(app, "bad@partner.io")]
    assert poll_inbound_mailbox()["rejected_count"] == 1
    assert "blocklisted" in _log().error_message


@override_settings(**GRAPH)
def test_blocklist_never_blocks_the_owner(make_app, mailbox):
    app = make_app(inbound_sender_blocklist=["@example.com", "owner@example.com"])
    mailbox.fetch.return_value = [_mail(app, "owner@example.com")]
    assert poll_inbound_mailbox()["created_count"] == 1


# ------------------------------------------------------- refusal: owner notices


@override_settings(**GRAPH)
def test_refusal_mails_the_owner_with_reason_and_masked_sender(make_app, mailbox):
    app = make_app()
    mailbox.fetch.return_value = [_mail(app, "stranger@else.org")]

    poll_inbound_mailbox()

    mailbox.send_email.assert_called_once()
    kwargs = mailbox.send_email.call_args.kwargs
    assert kwargs["to"] == "owner@example.com"
    assert "s***@else.org" in kwargs["body"]
    assert "stranger@else.org" not in kwargs["body"]
    assert app.inbound_email_address in kwargs["body"]
    assert "allowlist" in kwargs["body"]
    mailbox.push.assert_not_called()  # push is opt-in


@override_settings(**GRAPH)
def test_email_notice_can_be_disabled_per_app(make_app, mailbox):
    app = make_app(inbound_rejection_notify_email=False)
    mailbox.fetch.return_value = [_mail(app, "stranger@else.org")]
    poll_inbound_mailbox()
    mailbox.send_email.assert_not_called()


@override_settings(**GRAPH)
def test_push_notice_goes_to_the_owners_active_devices_only(make_app, mailbox, owner):
    app = make_app(inbound_rejection_notify_email=False, inbound_rejection_notify_push=True)
    mine = Device.objects.create(user=owner, push_token="tok-mine", platform="android")
    Device.objects.create(user=owner, push_token="tok-off", is_active=False)
    Device.objects.create(user=owner, push_token="tok-invalid", push_token_status=DeviceTokenStatus.INVALID)
    other = User.objects.create_user(email="other@example.com", password="MotDePasseTresSolide123!")
    Device.objects.create(user=other, push_token="tok-other")
    mailbox.fetch.return_value = [_mail(app, "stranger@else.org")]

    poll_inbound_mailbox()

    mailbox.push.assert_called_once()
    kwargs = mailbox.push.call_args.kwargs
    assert kwargs["push_token"] == mine.push_token
    assert kwargs["data"]["type"] == "inbound_email_rejected"
    assert kwargs["data"]["application_id"] == app.id
    assert "s***@else.org" in kwargs["message"]
    # No Notification row: this is a system push, not an app notification.
    assert Notification.objects.count() == 0


@override_settings(**GRAPH)
def test_push_failure_does_not_break_processing(make_app, mailbox, owner):
    app = make_app(inbound_rejection_notify_push=True)
    Device.objects.create(user=owner, push_token="tok-a")
    Device.objects.create(user=owner, push_token="tok-b")
    mailbox.push.side_effect = [RuntimeError("fcm down"), "msg-2"]
    mailbox.fetch.return_value = [_mail(app, "stranger@else.org")]

    result = poll_inbound_mailbox()

    assert result["rejected_count"] == 1
    assert result["failed_count"] == 0
    assert mailbox.push.call_count == 2


@override_settings(**GRAPH)
def test_owner_notices_are_rate_limited_per_channel(make_app, mailbox, owner):
    app = make_app(inbound_rejection_notify_push=True)
    Device.objects.create(user=owner, push_token="tok-a")
    mailbox.fetch.return_value = [
        _mail(app, "a@else.org", graph_id="g-1"),
        _mail(app, "b@else.org", graph_id="g-2"),
        _mail(app, "c@else.org", graph_id="g-3", auth="fail"),
    ]

    assert poll_inbound_mailbox()["rejected_count"] == 3
    assert mailbox.send_email.call_count == 1
    assert mailbox.push.call_count == 1


@override_settings(**{**GRAPH, "INBOUND_EMAIL_DMARC_NOTIFY_OWNER": False})
def test_global_dmarc_kill_switch_only_mutes_dmarc_email_notices(make_app, mailbox):
    app = make_app(inbound_sender_mode="anyone")
    mailbox.fetch.return_value = [_mail(app, "random@else.org", auth="fail")]
    poll_inbound_mailbox()
    mailbox.send_email.assert_not_called()

    mailbox.fetch.return_value = [_mail(app, "x@else.org", graph_id="g-2")]
    app.inbound_sender_mode = "owner"
    app.save()
    poll_inbound_mailbox()
    mailbox.send_email.assert_called_once()


# ------------------------------------------------------- refusal: sender reply


@override_settings(**GRAPH)
def test_no_reply_to_refused_sender_by_default(make_app, mailbox):
    app = make_app()
    mailbox.fetch.return_value = [_mail(app, "stranger@else.org")]
    poll_inbound_mailbox()
    mailbox.rejected_reply.assert_not_called()
    mailbox.unknown_reply.assert_not_called()


@override_settings(**GRAPH)
def test_opt_in_reply_to_refused_non_user(make_app, mailbox):
    app = make_app(inbound_rejection_reply_to_sender=True)
    mailbox.fetch.return_value = [
        _mail(app, "stranger@else.org", graph_id="g-1"),
        _mail(app, "stranger@else.org", graph_id="g-2"),  # rate-limited
    ]
    poll_inbound_mailbox()
    mailbox.rejected_reply.assert_called_once_with("stranger@else.org", app.inbound_email_address)
    mailbox.unknown_reply.assert_not_called()


@override_settings(**GRAPH)
def test_opt_in_reply_respects_rfc3834_loop_protection(make_app, mailbox):
    app = make_app(inbound_rejection_reply_to_sender=True)
    mailbox.fetch.return_value = [_mail(app, "stranger@else.org", headers={"auto-submitted": "auto-replied"})]
    poll_inbound_mailbox()
    mailbox.rejected_reply.assert_not_called()


@override_settings(**GRAPH)
def test_never_reply_to_a_sender_that_failed_dmarc(make_app, mailbox):
    app = make_app(inbound_sender_mode="anyone", inbound_rejection_reply_to_sender=True)
    mailbox.fetch.return_value = [_mail(app, "random@else.org", auth="fail")]
    poll_inbound_mailbox()
    mailbox.rejected_reply.assert_not_called()
    mailbox.unknown_reply.assert_not_called()


@override_settings(**GRAPH)
def test_unknown_alias_keeps_the_unknown_address_reply(owner, mailbox):
    mailbox.fetch.return_value = [
        GraphEmail(
            graph_id="g-9",
            sender="owner@example.com",
            recipient="nope-deadbeef.pushit@pushit.com",
            subject="Hi",
            text="Body.",
            message_id="g-9@x",
        )
    ]
    poll_inbound_mailbox()
    mailbox.unknown_reply.assert_called_once_with("owner@example.com", "nope-deadbeef.pushit@pushit.com")
    mailbox.send_email.assert_not_called()
