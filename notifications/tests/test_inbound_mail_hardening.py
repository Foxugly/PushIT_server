"""Inbound mail hardening: alias format/matching, recipient extraction, silent
ignore, auto-reply loop protection + rate limit, poll lock, retry cap, HTML."""

from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.test import override_settings

from accounts.models import User
from applications import inbound_alias
from applications.graph_mail import GraphEmail, fetch_unread_emails, html_to_text
from applications.models import Application
from notifications.inbound_mailbox import poll_inbound_mailbox
from notifications.inbound_reply import auto_reply_block_reason
from notifications.models import InboundEmailIngestionLog, Notification
from notifications.tasks import INBOUND_POLL_LOCK_KEY, poll_inbound_mailbox_task

PWD = "MotDePasseTresSolide123!"

GRAPH = {
    "INBOUND_EMAIL_DOMAIN": "pushit.com",
    "GRAPH_CLIENT_ID": "fake-client-id",
    "GRAPH_TENANT_ID": "fake-tenant",
    "GRAPH_CLIENT_SECRET": "fake-secret",
    "GRAPH_MAILBOX_USER_ID": "mailbox@pushit.com",
}


def _email(**kwargs) -> GraphEmail:
    defaults = {
        "graph_id": "graph-1",
        "sender": "owner@example.com",
        "recipient": "",
        "subject": "Hello",
        "text": "Body.",
        "message_id": "m-1@example.com",
    }
    defaults.update(kwargs)
    if "recipients" not in kwargs and defaults["recipient"]:
        defaults["recipients"] = (defaults["recipient"],)
    return GraphEmail(**defaults)


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="owner@example.com", password=PWD)


@pytest.fixture
def app(owner):
    with patch("applications.models.Application._provision_exchange_alias"):
        return Application.objects.create(owner=owner, name="Inbound App")


@pytest.fixture
def mailbox():
    with (
        patch("notifications.inbound_mailbox.mark_email_read") as mark_read,
        patch("notifications.inbound_mailbox.send_unknown_address_reply") as send_reply,
        patch("notifications.inbound_mailbox.fetch_unread_emails") as fetch,
    ):
        yield MagicMock(mark_read=mark_read, send_reply=send_reply, fetch=fetch)


# --- 1. alias format & matching ---------------------------------------------


@override_settings(**GRAPH)
def test_alias_pattern_recognition():
    assert inbound_alias.is_inbound_alias_address("mon-app-3f9a2c1b.pushit@pushit.com")
    assert inbound_alias.is_inbound_alias_address("Mon-App-3F9A2C1B.PUSHIT@PushIT.com")
    assert inbound_alias.is_inbound_alias_address("app_mon_app_3f9a2c1b@pushit.com")
    assert inbound_alias.is_inbound_alias_address("Bob <app_x_1@pushit.com>")
    assert not inbound_alias.is_inbound_alias_address("mon-app-3f9a2c1b.pushit@other.com")
    assert not inbound_alias.is_inbound_alias_address("contact@pushit.com")
    assert not inbound_alias.is_inbound_alias_address(".pushit@pushit.com")
    assert not inbound_alias.is_inbound_alias_address("app_@pushit.com")


@override_settings(**{**GRAPH, "GRAPH_MAILBOX_USER_ID": "inbox.pushit@pushit.com"})
def test_mailbox_address_is_never_an_alias():
    assert not inbound_alias.is_inbound_alias_address("inbox.pushit@pushit.com")


@override_settings(**{**GRAPH, "INBOUND_EMAIL_ALIAS_SUFFIX": ".inbound"})
def test_alias_suffix_is_configurable():
    alias = inbound_alias.generate_alias("Mon App")
    assert alias.endswith(".inbound")
    assert inbound_alias.is_inbound_alias_address(f"{alias}@pushit.com")
    assert not inbound_alias.is_inbound_alias_address("mon-app-3f9a2c1b.pushit@pushit.com")


@pytest.mark.django_db
@override_settings(**GRAPH)
def test_matching_is_case_insensitive(app, mailbox):
    mailbox.fetch.return_value = [_email(recipient=app.inbound_email_address.upper())]

    result = poll_inbound_mailbox()

    assert result["created_count"] == 1
    assert Notification.objects.get().application_id == app.id


@pytest.mark.django_db
@override_settings(**GRAPH)
def test_legacy_app_prefix_alias_still_routes(app, mailbox):
    Application.objects.filter(id=app.id).update(
        inbound_email_alias="app_inbound_app_deadbeef", inbound_email_suffix="deadbeef"
    )
    mailbox.fetch.return_value = [_email(recipient="app_inbound_app_deadbeef@pushit.com")]

    result = poll_inbound_mailbox()

    assert result["created_count"] == 1


@pytest.mark.django_db
@override_settings(**GRAPH)
def test_alias_equal_to_mailbox_never_routes(app, mailbox, settings):
    # Even if an app somehow holds the mailbox's own local part, mail to the
    # mailbox itself is never ingested.
    settings.GRAPH_MAILBOX_USER_ID = f"{app.inbound_email_alias}@pushit.com"
    mailbox.fetch.return_value = [_email(recipient=app.inbound_email_address)]

    result = poll_inbound_mailbox()

    assert result["created_count"] == 0
    assert result["ignored_count"] == 1


# --- 2. recipient extraction --------------------------------------------------


def _graph_message(**overrides):
    msg = {
        "id": "graph-x",
        "from": {"emailAddress": {"address": "Owner@Example.com"}},
        "toRecipients": [],
        "ccRecipients": [],
        "subject": "S",
        "body": {"contentType": "text", "content": "B"},
        "internetMessageId": "<m@x>",
        "internetMessageHeaders": [],
    }
    msg.update(overrides)
    return msg


def _fetch_with(messages):
    response = MagicMock()
    response.json.return_value = {"value": messages}
    with (
        patch("applications.graph_mail._headers", return_value={}),
        patch("applications.graph_mail.requests.get", return_value=response),
    ):
        return fetch_unread_emails()


@override_settings(**GRAPH)
def test_recipient_prefers_alias_over_other_domain_addresses():
    [email] = _fetch_with([_graph_message(
        toRecipients=[
            {"emailAddress": {"address": "mailbox@pushit.com"}},
            {"emailAddress": {"address": "someone@else.com"}},
        ],
        ccRecipients=[{"emailAddress": {"address": "My-App-3F9A2C1B.pushit@PUSHIT.com"}}],
    )])
    assert email.recipient == "my-app-3f9a2c1b.pushit@pushit.com"
    assert email.sender == "owner@example.com"
    assert email.recipients == (
        "mailbox@pushit.com", "someone@else.com", "my-app-3f9a2c1b.pushit@pushit.com",
    )


@override_settings(**GRAPH)
@pytest.mark.parametrize("header", ["Delivered-To", "X-Original-To", "Resent-To"])
def test_recipient_found_in_envelope_headers(header):
    [email] = _fetch_with([_graph_message(
        toRecipients=[{"emailAddress": {"address": "list@else.com"}}],
        internetMessageHeaders=[
            {"name": header, "value": "Bcc Target <app-1234abcd.pushit@pushit.com>"},
            {"name": "Auto-Submitted", "value": "auto-generated"},
        ],
    )])
    assert email.recipient == "app-1234abcd.pushit@pushit.com"
    assert email.headers["auto-submitted"] == "auto-generated"


@override_settings(**GRAPH)
def test_recipient_falls_back_to_domain_address_when_no_alias():
    [email] = _fetch_with([_graph_message(
        toRecipients=[{"emailAddress": {"address": "Mailbox@pushit.com"}}],
    )])
    assert email.recipient == "mailbox@pushit.com"


# --- 7. silent ignore -----------------------------------------------------------


@pytest.mark.django_db
@override_settings(**GRAPH)
def test_mail_without_alias_recipient_is_ignored_silently(owner, mailbox):
    mailbox.fetch.return_value = [
        _email(graph_id="g-a", recipient="contact@pushit.com"),
        _email(graph_id="g-b", recipient=""),
    ]

    result = poll_inbound_mailbox()

    assert result["ignored_count"] == 2
    assert result["rejected_count"] == 0
    assert InboundEmailIngestionLog.objects.count() == 0
    mailbox.send_reply.assert_not_called()
    mailbox.mark_read.assert_any_call("g-a")
    mailbox.mark_read.assert_any_call("g-b")


@pytest.mark.django_db
@override_settings(**GRAPH)
def test_alias_shaped_mail_failing_validation_is_still_rejected(app, mailbox):
    mailbox.fetch.return_value = [_email(sender="stranger@example.com", recipient=app.inbound_email_address)]

    result = poll_inbound_mailbox()

    assert result["rejected_count"] == 1
    assert InboundEmailIngestionLog.objects.get().status == "rejected"


# --- 3. auto-reply loop protection -----------------------------------------------


@override_settings(**GRAPH)
@pytest.mark.parametrize(
    "kwargs, reason",
    [
        ({"headers": {"auto-submitted": "auto-replied"}}, "auto_submitted"),
        ({"headers": {"precedence": "Bulk"}}, "precedence"),
        ({"headers": {"precedence": "auto_reply"}}, "precedence"),
        ({"headers": {"x-auto-response-suppress": "All"}}, "auto_response_suppressed"),
        ({"headers": {"list-id": "<l.example.com>"}}, "mailing_list"),
        ({"headers": {"list-unsubscribe": "<mailto:x>"}}, "mailing_list"),
        ({"sender": "MAILER-DAEMON@example.com"}, "robot_sender"),
        ({"sender": "postmaster@example.com"}, "robot_sender"),
        ({"sender": "no-reply@example.com"}, "robot_sender"),
        ({"sender": "noreply+abc@example.com"}, "robot_sender"),
        ({"sender": "donotreply@example.com"}, "robot_sender"),
        ({"sender": "mailbox@pushit.com"}, "own_mailbox"),
        ({"recipient": "mailbox@pushit.com"}, "addressed_to_mailbox_only"),
    ],
)
def test_auto_reply_block_reasons(kwargs, reason):
    params = {"recipient": "x-deadbeef.pushit@pushit.com", **kwargs}
    assert auto_reply_block_reason(_email(**params)) == reason


@override_settings(**GRAPH)
def test_auto_reply_allowed_for_a_human():
    email = _email(
        recipient="x-deadbeef.pushit@pushit.com",
        headers={"auto-submitted": "no", "precedence": "normal"},
    )
    assert auto_reply_block_reason(email) == ""


@pytest.mark.django_db
@override_settings(**GRAPH)
def test_auto_reply_skipped_for_automated_mail(app, mailbox):
    mailbox.fetch.return_value = [_email(
        recipient="unknown-deadbeef.pushit@pushit.com",
        headers={"auto-submitted": "auto-replied"},
    )]

    result = poll_inbound_mailbox()

    assert result["rejected_count"] == 1
    mailbox.send_reply.assert_not_called()


@pytest.mark.django_db
@override_settings(**GRAPH)
def test_auto_reply_rate_limited_per_sender(app, mailbox):
    mailbox.fetch.return_value = [
        _email(graph_id=f"g-{i}", message_id=f"m-{i}@x", recipient="unknown-deadbeef.pushit@pushit.com")
        for i in range(3)
    ]

    result = poll_inbound_mailbox()

    assert result["rejected_count"] == 3
    mailbox.send_reply.assert_called_once_with("owner@example.com", "unknown-deadbeef.pushit@pushit.com")


@pytest.mark.django_db
@override_settings(**{**GRAPH, "INBOUND_EMAIL_AUTO_REPLY_INTERVAL_SECONDS": 0})
def test_auto_reply_rate_limit_can_be_disabled(app, mailbox):
    mailbox.fetch.return_value = [
        _email(graph_id=f"g-{i}", message_id=f"m-{i}@x", recipient="unknown-deadbeef.pushit@pushit.com")
        for i in range(2)
    ]

    poll_inbound_mailbox()

    assert mailbox.send_reply.call_count == 2


# --- 4. overlap lock -------------------------------------------------------------


@pytest.mark.django_db
@patch("notifications.tasks.poll_inbound_mailbox")
def test_poll_task_skips_when_lock_held(mock_poll):
    cache.add(INBOUND_POLL_LOCK_KEY, "someone-else", timeout=60)

    result = poll_inbound_mailbox_task()

    assert result == {"status": "skipped", "reason": "locked", "processed_count": 0}
    mock_poll.assert_not_called()
    assert cache.get(INBOUND_POLL_LOCK_KEY) == "someone-else", "must not release a foreign lock"


@pytest.mark.django_db
@patch("notifications.tasks.poll_inbound_mailbox", side_effect=RuntimeError("boom"))
def test_poll_task_releases_lock_on_failure(mock_poll):
    with pytest.raises(RuntimeError):
        poll_inbound_mailbox_task()

    assert cache.get(INBOUND_POLL_LOCK_KEY) is None


@pytest.mark.django_db
@patch("notifications.tasks.poll_inbound_mailbox", return_value={"status": "ok"})
def test_poll_task_releases_lock_after_run(mock_poll):
    poll_inbound_mailbox_task()
    poll_inbound_mailbox_task()

    assert mock_poll.call_count == 2
    assert cache.get(INBOUND_POLL_LOCK_KEY) is None


# --- 5. retry cap ----------------------------------------------------------------


@pytest.mark.django_db
@override_settings(**{**GRAPH, "INBOUND_EMAIL_MAX_PROCESSING_ATTEMPTS": 3})
@patch("notifications.inbound_mailbox._process_email", side_effect=RuntimeError("poison"))
def test_failing_mail_is_marked_read_after_retry_cap(_mock_process, mailbox):
    mailbox.fetch.return_value = [_email(graph_id="g-poison", recipient="x-deadbeef.pushit@pushit.com")]

    for _ in range(2):
        result = poll_inbound_mailbox()
        assert result["failed_count"] == 1
    mailbox.mark_read.assert_not_called()

    poll_inbound_mailbox()

    mailbox.mark_read.assert_called_once_with("g-poison")
    logs = InboundEmailIngestionLog.objects.filter(mailbox_uid="g-poison", status="error")
    assert logs.count() == 3
    assert "Giving up after 3 failed attempts" in logs.order_by("-id").first().error_message


# --- 6. HTML -> text -------------------------------------------------------------


def test_html_to_text_drops_scripts_and_keeps_structure():
    html = (
        "<html><head><title>T</title><style>p{color:red}</style></head><body>"
        "<script>alert('x')</script>"
        "<p>Hello&nbsp;<b>World</b> &amp; co</p><div>Line&eacute;2<br>Line3</div>"
        "<ul><li>one</li><li>two</li></ul><p></p><p></p><p>End</p>"
        "</body></html>"
    )
    assert html_to_text(html) == "Hello World & co\n\nLineé2\nLine3\n- one\n- two\n\nEnd"


def test_html_to_text_handles_plain_and_empty():
    assert html_to_text("") == ""
    assert html_to_text("just   text") == "just text"
