"""Webhook delivery + HMAC signing contract, and the SSRF guard.

External contract (consumed by customer endpoints):
  - POST is skipped entirely when webhook_url is empty.
  - X-PushIT-Signature = HMAC-SHA256(secret=app_token_hash) over the exact body.
  - The JSON body has a fixed, sorted, compact layout.
  - A webhook_url that resolves to a private/loopback/IMDS address is never hit
    (SSRF guard, incl. anti-DNS-rebinding at send time).
"""

import hashlib
import hmac
import json
from unittest.mock import patch

import pytest

from accounts.models import User
from applications.models import Application
from notifications.webhooks import send_webhook_callback


def _make_app(webhook_url: str) -> Application:
    owner = User.objects.create_user(
        email="hook-owner@example.com",
        password="MotDePasseTresSolide123!",
    )
    app = Application.objects.create(owner=owner, name="Hook App")
    # Set the URL directly (bypassing the write-time validator) so we can exercise
    # the send-time guard against values that should never have been stored.
    Application.objects.filter(id=app.id).update(webhook_url=webhook_url)
    app.refresh_from_db()
    return app


@pytest.mark.django_db
@patch("notifications.webhooks.requests.post")
def test_no_request_when_url_empty(mock_post):
    app = _make_app("")
    send_webhook_callback(app, notification_id=1, final_status="sent")
    mock_post.assert_not_called()


@pytest.mark.django_db
@patch("notifications.webhooks.assert_webhook_url_safe", return_value=None)
@patch("notifications.webhooks.requests.post")
def test_signature_matches_recomputed_hmac(mock_post, _mock_safe):
    app = _make_app("https://hooks.example.com/pushit")
    mock_post.return_value.status_code = 200

    send_webhook_callback(app, notification_id=42, final_status="sent")

    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    sent_body = kwargs["data"]
    header_sig = kwargs["headers"]["X-PushIT-Signature"]

    expected = hmac.new(
        app.app_token_hash.encode("utf-8"), sent_body, hashlib.sha256
    ).hexdigest()
    assert header_sig == expected


@pytest.mark.django_db
@patch("notifications.webhooks.assert_webhook_url_safe", return_value=None)
@patch("notifications.webhooks.requests.post")
def test_payload_layout_is_exact(mock_post, _mock_safe):
    app = _make_app("https://hooks.example.com/pushit")
    mock_post.return_value.status_code = 200

    send_webhook_callback(app, notification_id=7, final_status="partial", sent_at=None)

    _, kwargs = mock_post.call_args
    body_bytes = kwargs["data"]
    payload = json.loads(body_bytes)

    assert set(payload.keys()) == {
        "event",
        "notification_id",
        "application_id",
        "status",
        "sent_at",
        "timestamp",
    }
    assert payload["event"] == "notification.status_changed"
    assert payload["notification_id"] == 7
    assert payload["application_id"] == app.id
    assert payload["status"] == "partial"
    assert payload["sent_at"] is None

    # Compact + sorted layout — the signature is computed over these exact bytes.
    assert b", " not in body_bytes and b": " not in body_bytes
    assert kwargs["headers"]["X-PushIT-Event"] == "notification.status_changed"
    # Redirects must never be followed (SSRF bypass vector).
    assert kwargs["allow_redirects"] is False


@pytest.mark.django_db
@patch("notifications.webhooks.requests.post")
def test_private_url_is_rejected_without_request(mock_post):
    # IMDS endpoint — must be blocked by the SSRF guard, no POST issued.
    app = _make_app("http://169.254.169.254/latest/meta-data/")
    send_webhook_callback(app, notification_id=99, final_status="sent")
    mock_post.assert_not_called()


@pytest.mark.django_db
@patch("notifications.webhooks.requests.post")
def test_loopback_url_is_rejected_without_request(mock_post):
    app = _make_app("http://127.0.0.1:6379/")
    send_webhook_callback(app, notification_id=100, final_status="sent")
    mock_post.assert_not_called()
