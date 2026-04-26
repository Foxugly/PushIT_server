from unittest.mock import patch

import pytest
from django.test import override_settings

from accounts.models import User
from applications.graph_mail import GraphEmail
from applications.models import Application
from notifications.inbound_mailbox import poll_inbound_mailbox
from notifications.inbound_reply import build_unknown_address_reply
from notifications.models import InboundEmailIngestionLog, Notification, NotificationStatus


@pytest.mark.django_db
@override_settings(
    INBOUND_EMAIL_DOMAIN="pushit.com",
    GRAPH_CLIENT_ID="fake-client-id",
    GRAPH_TENANT_ID="fake-tenant",
    GRAPH_CLIENT_SECRET="fake-secret",
    GRAPH_MAILBOX_USER_ID="mailbox@pushit.com",
)
@patch("applications.models.Application._provision_exchange_alias")
@patch("notifications.inbound_mailbox.mark_email_read")
@patch("notifications.inbound_mailbox.send_unknown_address_reply")
@patch("notifications.inbound_mailbox.fetch_unread_emails")
def test_poll_inbound_mailbox_creates_notification_for_matching_owner(
    mock_fetch, mock_send_reply, mock_mark_read, _mock_add_alias,
):
    owner = User.objects.create_user(
        email="owner@example.com",
        username="owner",
        password="MotDePasseTresSolide123!",
    )
    other_user = User.objects.create_user(
        email="other@example.com",
        username="other",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=owner, name="Inbound App")

    mock_fetch.return_value = [
        GraphEmail(
            graph_id="graph-101",
            sender=owner.email,
            recipient=f"{app.inbound_email_alias}@pushit.com",
            subject="Production alert",
            text="The batch is done.",
            message_id="mail-001@example.com",
        ),
        GraphEmail(
            graph_id="graph-102",
            sender=other_user.email,
            recipient=f"{app.inbound_email_alias}@pushit.com",
            subject="Rejected alert",
            text="This mail should be rejected.",
            message_id="mail-002@example.com",
        ),
    ]

    result = poll_inbound_mailbox()

    assert result["status"] == "ok"
    assert result["processed_count"] == 2
    assert result["created_count"] == 1
    assert result["rejected_count"] == 1
    assert Notification.objects.count() == 1
    notification = Notification.objects.get()
    assert notification.application_id == app.id
    assert notification.title == "Production alert"
    assert notification.message == "The batch is done."
    assert notification.status == NotificationStatus.DRAFT
    assert InboundEmailIngestionLog.objects.count() == 2
    created_log = InboundEmailIngestionLog.objects.get(status="created")
    assert created_log.application_id == app.id
    assert created_log.notification_id == notification.id
    rejected_log = InboundEmailIngestionLog.objects.get(status="rejected")
    assert rejected_log.application_id is None
    assert rejected_log.mailbox_uid == "graph-102"
    assert mock_mark_read.call_count == 2
    mock_mark_read.assert_any_call("graph-101")
    mock_mark_read.assert_any_call("graph-102")


@pytest.mark.django_db
@override_settings(GRAPH_CLIENT_ID="")
def test_poll_inbound_mailbox_returns_skipped_when_not_configured():
    result = poll_inbound_mailbox()

    assert result == {
        "status": "skipped",
        "reason": "not configured",
        "processed_count": 0,
    }


@pytest.mark.django_db
@override_settings(
    INBOUND_EMAIL_DOMAIN="pushit.com",
    GRAPH_CLIENT_ID="fake-client-id",
    GRAPH_TENANT_ID="fake-tenant",
    GRAPH_CLIENT_SECRET="fake-secret",
    GRAPH_MAILBOX_USER_ID="mailbox@pushit.com",
)
@patch("applications.models.Application._provision_exchange_alias")
@patch("notifications.inbound_mailbox.mark_email_read")
@patch("notifications.inbound_mailbox.send_unknown_address_reply")
@patch("notifications.inbound_mailbox.fetch_unread_emails")
def test_poll_sends_reply_when_known_user_sends_to_unknown_address(
    mock_fetch, mock_send_reply, mock_mark_read, _mock_add_alias,
):
    owner = User.objects.create_user(
        email="owner@example.com",
        username="owner",
        password="MotDePasseTresSolide123!",
    )
    Application.objects.create(owner=owner, name="My App")

    mock_fetch.return_value = [
        GraphEmail(
            graph_id="graph-201",
            sender=owner.email,
            recipient="nonexistent@pushit.com",
            subject="Test",
            text="Some content.",
            message_id="mail-201@example.com",
        ),
    ]

    result = poll_inbound_mailbox()

    assert result["status"] == "ok"
    assert result["rejected_count"] == 1
    assert Notification.objects.count() == 0
    mock_send_reply.assert_called_once_with("owner@example.com", "nonexistent@pushit.com")


@pytest.mark.django_db
@override_settings(
    INBOUND_EMAIL_DOMAIN="pushit.com",
    GRAPH_CLIENT_ID="fake-client-id",
    GRAPH_TENANT_ID="fake-tenant",
    GRAPH_CLIENT_SECRET="fake-secret",
    GRAPH_MAILBOX_USER_ID="mailbox@pushit.com",
)
@patch("applications.models.Application._provision_exchange_alias")
@patch("notifications.inbound_mailbox.mark_email_read")
@patch("notifications.inbound_mailbox.send_unknown_address_reply")
@patch("notifications.inbound_mailbox.fetch_unread_emails")
def test_poll_sends_reply_when_known_user_sends_to_other_owners_app(
    mock_fetch, mock_send_reply, mock_mark_read, _mock_add_alias,
):
    owner = User.objects.create_user(
        email="owner@example.com",
        username="owner",
        password="MotDePasseTresSolide123!",
    )
    other = User.objects.create_user(
        email="other@example.com",
        username="other",
        password="MotDePasseTresSolide123!",
    )
    other_app = Application.objects.create(owner=other, name="Other App")
    Application.objects.create(owner=owner, name="Owner App")

    mock_fetch.return_value = [
        GraphEmail(
            graph_id="graph-301",
            sender=owner.email,
            recipient=f"{other_app.inbound_email_alias}@pushit.com",
            subject="Wrong app",
            text="Some content.",
            message_id="mail-301@example.com",
        ),
    ]

    result = poll_inbound_mailbox()

    assert result["rejected_count"] == 1
    mock_send_reply.assert_called_once_with("owner@example.com", f"{other_app.inbound_email_alias}@pushit.com")


@pytest.mark.django_db
@override_settings(
    INBOUND_EMAIL_DOMAIN="pushit.com",
    GRAPH_CLIENT_ID="fake-client-id",
    GRAPH_TENANT_ID="fake-tenant",
    GRAPH_CLIENT_SECRET="fake-secret",
    GRAPH_MAILBOX_USER_ID="mailbox@pushit.com",
)
@patch("notifications.inbound_mailbox.mark_email_read")
@patch("notifications.inbound_mailbox.send_unknown_address_reply")
@patch("notifications.inbound_mailbox.fetch_unread_emails")
def test_poll_does_not_send_reply_for_unknown_sender(
    mock_fetch, mock_send_reply, mock_mark_read,
):
    mock_fetch.return_value = [
        GraphEmail(
            graph_id="graph-401",
            sender="stranger@example.com",
            recipient="nonexistent@pushit.com",
            subject="Test",
            text="Some content.",
            message_id="mail-401@example.com",
        ),
    ]

    result = poll_inbound_mailbox()

    assert result["rejected_count"] == 1
    mock_send_reply.assert_not_called()


@pytest.mark.django_db
@override_settings(INBOUND_EMAIL_DOMAIN="pushit.com")
def test_build_unknown_address_reply_lists_user_apps():
    owner = User.objects.create_user(
        email="owner@example.com",
        username="owner",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=owner, name="My App")

    subject, body = build_unknown_address_reply("owner@example.com", "wrong@pushit.com")

    assert "unknown recipient" in subject.lower()
    assert app.inbound_email_address in body
    assert "wrong@pushit.com" in body


@pytest.mark.django_db
@override_settings(INBOUND_EMAIL_DOMAIN="pushit.com")
def test_build_unknown_address_reply_returns_empty_for_unknown_user():
    subject, body = build_unknown_address_reply("stranger@example.com", "whatever@pushit.com")

    assert subject == ""
    assert body == ""
