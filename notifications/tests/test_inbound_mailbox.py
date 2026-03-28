from email.message import EmailMessage
from unittest.mock import patch

import pytest
from django.test import override_settings

from accounts.models import User
from applications.models import Application
from notifications.inbound_mailbox import poll_inbound_mailbox
from notifications.models import InboundEmailIngestionLog, Notification, NotificationStatus


def _build_email_bytes(*, sender: str, recipient: str, subject: str, text: str, message_id: str) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message.set_content(text)
    return message.as_bytes()


class FakeImapClient:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.seen_uids = []

    def login(self, username, password):
        self.username = username
        self.password = password
        return "OK", [b"logged"]

    def select(self, folder):
        self.folder = folder
        return "OK", [b"1"]

    def uid(self, action, *args):
        if action == "search":
            return "OK", [b"101 102"]
        if action == "fetch":
            uid = args[0]
            if uid == "101":
                return "OK", [(b"1 (RFC822 {123}", self.email_one)]
            if uid == "102":
                return "OK", [(b"2 (RFC822 {123}", self.email_two)]
        if action == "store":
            self.seen_uids.append(args[0])
            return "OK", [b"stored"]
        raise AssertionError(f"Unexpected IMAP action: {action}")

    def close(self):
        return "OK", [b"closed"]

    def logout(self):
        return "BYE", [b"logout"]


@pytest.mark.django_db
@override_settings(
    INBOUND_EMAIL_DOMAIN="pushit.com",
    INBOUND_EMAIL_IMAP_ENABLED=True,
    INBOUND_EMAIL_IMAP_HOST="imap.pushit.com",
    INBOUND_EMAIL_IMAP_PORT=993,
    INBOUND_EMAIL_IMAP_USERNAME="catchall@pushit.com",
    INBOUND_EMAIL_IMAP_PASSWORD="secret",
)
@patch("notifications.inbound_mailbox.imaplib.IMAP4_SSL")
def test_poll_inbound_mailbox_creates_notification_for_matching_owner(mock_imap):
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

    fake_client = FakeImapClient("imap.pushit.com", 993)
    fake_client.email_one = _build_email_bytes(
        sender=owner.email,
        recipient=f"{app.inbound_email_alias}@pushit.com",
        subject="Alerte production",
        text="Le batch est termine.",
        message_id="<mail-001@example.com>",
    )
    fake_client.email_two = _build_email_bytes(
        sender=other_user.email,
        recipient=f"{app.inbound_email_alias}@pushit.com",
        subject="Alerte rejetee",
        text="Ce mail doit etre rejete.",
        message_id="<mail-002@example.com>",
    )
    mock_imap.return_value = fake_client

    result = poll_inbound_mailbox()

    assert result["status"] == "ok"
    assert result["processed_count"] == 2
    assert result["created_count"] == 1
    assert result["rejected_count"] == 1
    assert Notification.objects.count() == 1
    notification = Notification.objects.get()
    assert notification.application_id == app.id
    assert notification.title == "Alerte production"
    assert notification.message == "Le batch est termine."
    assert notification.status == NotificationStatus.DRAFT
    assert InboundEmailIngestionLog.objects.count() == 2
    created_log = InboundEmailIngestionLog.objects.get(status="created")
    assert created_log.application_id == app.id
    assert created_log.notification_id == notification.id
    rejected_log = InboundEmailIngestionLog.objects.get(status="rejected")
    assert rejected_log.application_id is None
    assert rejected_log.mailbox_uid == "102"
    assert fake_client.seen_uids == ["101", "102"]


@pytest.mark.django_db
@override_settings(INBOUND_EMAIL_IMAP_ENABLED=False)
def test_poll_inbound_mailbox_returns_skipped_when_disabled():
    result = poll_inbound_mailbox()

    assert result == {
        "status": "skipped",
        "reason": "disabled",
        "processed_count": 0,
    }
