"""GET /api/v1/apps/{id}/inbound-email-logs/: the owner's inbound e-mail journal."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from applications.models import Application
from notifications.api_views_inbound_logs import INBOUND_EMAIL_LOG_LIMIT
from notifications.models import (
    InboundEmailIngestionLog,
    InboundEmailIngestionStatus,
    InboundEmailSource,
    Notification,
)

PWD = "MotDePasseTresSolide123!"


def _url(app):
    return f"/api/v1/apps/{app.id}/inbound-email-logs/"


def _client(user=None):
    client = APIClient()
    if user is not None:
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def _app(owner, name="Journal App"):
    with patch("applications.models.Application._provision_exchange_alias"):
        return Application.objects.create(owner=owner, name=name)


def _log(app, minutes_ago=0, **fields):
    defaults = dict(
        source=InboundEmailSource.POLLING,
        status=InboundEmailIngestionStatus.CREATED,
        application=app,
        sender="ally@partner.io",
        recipient="alias@pushit.com",
        subject="Hello",
    )
    defaults.update(fields)
    log = InboundEmailIngestionLog.objects.create(**defaults)
    # processed_at is auto_now_add: backdate with an UPDATE.
    InboundEmailIngestionLog.objects.filter(pk=log.pk).update(
        processed_at=timezone.now() - timedelta(minutes=minutes_ago)
    )
    log.refresh_from_db()
    return log


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="owner@example.com", password=PWD)


def test_owner_sees_own_logs_with_the_documented_shape(owner):
    app = _app(owner)
    notification = Notification.objects.create(application=app, title="Hello", message="Body")
    created = _log(app, minutes_ago=5, notification=notification)
    rejected = _log(
        app,
        minutes_ago=1,
        status=InboundEmailIngestionStatus.REJECTED,
        sender="stranger@else.org",
        subject="Spam",
        error_message="sender_not_allowed: Sender refused by the application's inbound policy (not_allowed).",
    )
    other_app = _app(owner, name="Other")
    _log(other_app)
    _log(None)  # unknown alias: no application

    response = _client(owner).get(_url(app))

    assert response.status_code == 200
    assert [row["id"] for row in response.data] == [rejected.id, created.id]
    assert set(response.data[0]) == {
        "id",
        "processed_at",
        "status",
        "sender",
        "subject",
        "error_message",
        "notification_id",
    }
    assert response.data[0]["status"] == "rejected"
    assert response.data[0]["sender"] == "stranger@else.org"
    assert response.data[0]["subject"] == "Spam"
    assert response.data[0]["error_message"].startswith("sender_not_allowed: ")
    assert response.data[0]["notification_id"] is None
    assert response.data[1]["status"] == "created"
    assert response.data[1]["error_message"] == ""
    assert response.data[1]["notification_id"] == notification.id


def test_logs_are_newest_first_and_capped(owner):
    app = _app(owner)
    logs = [_log(app, minutes_ago=i, subject=f"m{i}") for i in range(INBOUND_EMAIL_LOG_LIMIT + 5)]

    response = _client(owner).get(_url(app))

    assert response.status_code == 200
    assert INBOUND_EMAIL_LOG_LIMIT == 50
    assert len(response.data) == 50
    # minutes_ago=0 is the newest.
    assert [row["id"] for row in response.data] == [log.id for log in logs[:50]]


def test_other_user_gets_404(owner):
    app = _app(owner)
    _log(app)
    intruder = User.objects.create_user(email="intruder@example.com", password=PWD)

    response = _client(intruder).get(_url(app))

    assert response.status_code == 404
    assert response.data["code"] == "application_not_found"


def test_unknown_app_is_404(owner):
    response = _client(owner).get("/api/v1/apps/999999/inbound-email-logs/")
    assert response.status_code == 404


def test_anonymous_is_401(owner):
    app = _app(owner)
    response = _client().get(_url(app))
    assert response.status_code == 401
