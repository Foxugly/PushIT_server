import hashlib
import hmac
import json
import logging

import requests
from django.utils import timezone

logger = logging.getLogger(__name__)


def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


def send_webhook_callback(application, notification_id: int, final_status: str, sent_at=None) -> None:
    webhook_url = application.webhook_url
    if not webhook_url:
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
