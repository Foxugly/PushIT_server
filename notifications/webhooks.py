import hashlib
import hmac
import json
import logging

import requests
from django.utils import timezone

from applications.url_safety import UnsafeWebhookURL, assert_webhook_url_safe

logger = logging.getLogger(__name__)


def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


def send_webhook_callback(application, notification_id: int, final_status: str, sent_at=None) -> None:
    webhook_url = application.webhook_url
    if not webhook_url:
        return

    # Re-validate the resolved host right before sending (anti-DNS-rebinding): the
    # URL passed the write-time validator, but the name could now resolve to IMDS
    # / loopback / a private host. If it does, drop the callback rather than let
    # the worker be used as a confused deputy.
    try:
        assert_webhook_url_safe(webhook_url)
    except UnsafeWebhookURL as exc:
        logger.warning(
            "webhook_callback_blocked_unsafe_url",
            extra={
                "notification_id": notification_id,
                "application_id": application.id,
                "error": str(exc),
            },
        )
        return

    payload = {
        "event": "notification.status_changed",
        "notification_id": notification_id,
        "application_id": application.id,
        "status": final_status,
        "sent_at": sent_at.isoformat() if sent_at else None,
        "timestamp": timezone.now().isoformat(),
    }

    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = _sign_payload(payload_bytes, application.app_token_hash)

    try:
        response = requests.post(
            webhook_url,
            data=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-PushIT-Signature": signature,
                "X-PushIT-Event": "notification.status_changed",
            },
            timeout=10,
            # Never follow redirects: a 3xx to 169.254.169.254 / loopback would
            # bypass the pre-flight SSRF check above.
            allow_redirects=False,
        )
        logger.info(
            "webhook_callback_sent",
            extra={
                "notification_id": notification_id,
                "application_id": application.id,
                "webhook_url": webhook_url,
                "response_status": response.status_code,
            },
        )
    except Exception:
        logger.exception(
            "webhook_callback_failed",
            extra={
                "notification_id": notification_id,
                "application_id": application.id,
                "webhook_url": webhook_url,
            },
        )
