import hashlib
import hmac
import json
import logging

import requests
from celery import shared_task
from django.utils import timezone

from applications.models import Application
from applications.url_safety import UnsafeWebhookURL, assert_webhook_url_safe

logger = logging.getLogger(__name__)


def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


def send_webhook_callback(application, notification_id: int, final_status: str, sent_at=None) -> None:
    """Enqueue the webhook callback (non-blocking).

    The actual HTTP POST is a blocking call with a multi-second timeout, so it
    must never run inline in the send worker — a slow/hung customer endpoint
    would stall notification delivery. We hand it to a dedicated Celery task. In
    DEV/TEST Celery runs eagerly, so the dispatch is still synchronous there.
    """
    if not application.webhook_url:
        return

    send_webhook_callback_task.delay(
        application_id=application.id,
        notification_id=notification_id,
        final_status=final_status,
        sent_at=sent_at.isoformat() if sent_at else None,
    )


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    retry_backoff=True,
    retry_backoff_max=300,
)
def send_webhook_callback_task(
    self, application_id: int, notification_id: int, final_status: str, sent_at=None
) -> None:
    """Deliver the webhook callback as its own task so it never blocks the send
    worker. `sent_at` is an ISO string (JSON-serializable for the broker) or None.

    Retries on transient transport errors with a modest exponential backoff. The
    SSRF guard (re-validated here, at send time, against DNS rebinding) and
    ``allow_redirects=False`` are preserved — a guard rejection is terminal, not
    retried.
    """
    application = Application.objects.filter(id=application_id).first()
    if application is None or not application.webhook_url:
        return

    webhook_url = application.webhook_url

    # Re-validate the resolved host right before sending (anti-DNS-rebinding): the
    # URL passed the write-time validator, but the name could now resolve to IMDS
    # / loopback / a private host. If it does, drop the callback rather than let
    # the worker be used as a confused deputy. Terminal — do not retry.
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
        "sent_at": sent_at,
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
    except requests.RequestException as exc:
        logger.warning(
            "webhook_callback_failed",
            extra={
                "notification_id": notification_id,
                "application_id": application.id,
                "webhook_url": webhook_url,
                "error": str(exc),
                "retries": self.request.retries,
            },
        )
        raise self.retry(exc=exc)
