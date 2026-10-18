import hashlib
import hmac
import ipaddress
import json
import logging
from urllib.parse import urlsplit, urlunsplit

import requests
from celery import shared_task
from django.utils import timezone
from requests.adapters import HTTPAdapter

from applications.models import Application
from applications.url_safety import UnsafeWebhookURL, assert_webhook_url_safe

logger = logging.getLogger(__name__)


def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


class _PinnedHostAdapter(HTTPAdapter):
    """HTTPS adapter for a request whose URL carries an IP literal: SNI and
    certificate hostname verification use the webhook's real name instead.
    Certificate verification itself is untouched (``verify=True`` by default)."""

    def __init__(self, server_hostname: str, **kwargs):
        self._server_hostname = server_hostname
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs["server_hostname"] = self._server_hostname
        pool_kwargs["assert_hostname"] = self._server_hostname
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)


def _post_pinned(url: str, ips, **kwargs) -> requests.Response:
    """POST ``url`` over a connection to ``ips[0]``, an address the SSRF guard has
    just validated, instead of letting ``requests`` resolve the name again.

    A second lookup is a TOCTOU (audit S-M4): a 0-TTL record can answer a public
    IP to the guard and 127.0.0.1 / 169.254.169.254 to the connect. The original
    name is kept for the ``Host`` header, SNI and certificate verification.
    Redirects are never followed and no proxy / netrc from the environment is
    used (a proxy would resolve the name itself).
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    ip = ipaddress.ip_address(ips[0])
    ip_literal = f"[{ip.compressed}]" if ip.version == 6 else ip.compressed
    # urlsplit strips the brackets of an IPv6 literal host; the Host header needs them.
    host_header = f"[{host}]" if ":" in host else host
    if parts.port is not None:
        host_header = f"{host_header}:{parts.port}"
    userinfo = parts.netloc.rpartition("@")[0]
    netloc = ip_literal + (f":{parts.port}" if parts.port is not None else "")
    if userinfo:
        netloc = f"{userinfo}@{netloc}"
    pinned_url = urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))

    headers = dict(kwargs.pop("headers", None) or {})
    headers["Host"] = host_header
    kwargs["allow_redirects"] = False

    with requests.Session() as session:
        session.trust_env = False
        session.mount("https://", _PinnedHostAdapter(host))
        return session.post(pinned_url, headers=headers, **kwargs)


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
        validated_ips = assert_webhook_url_safe(webhook_url)
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
    # Le secret propre a l'application, et surtout PAS `app_token_hash` : celui-ci
    # est l'empreinte du jeton qui partait dans le QR vers chaque destinataire,
    # donc recalculable par n'importe lequel d'entre eux.
    signature = _sign_payload(payload_bytes, application.webhook_secret)

    try:
        # Connect to the address validated above, not to a fresh DNS answer.
        response = _post_pinned(
            webhook_url,
            validated_ips,
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
