"""Cloudflare Turnstile server-side verification.

The frontend renders the Turnstile widget with TURNSTILE_SITE_KEY (public),
captures the user-completed token, and POSTs it alongside the registration
payload. The backend then exchanges that token with Cloudflare via this helper
before honouring the request.

Rollout is gated on TURNSTILE_SECRET_KEY: callers skip verification entirely
when no secret is configured (so the captcha can be shipped to prod and
activated later by seeding the SSM secret, without breaking register in the
meantime). Once a secret IS configured, verification is fail-closed: any
timeout, network error, or non-success Cloudflare response returns False.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger("pushit.api")

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT_SECONDS = 5.0


def turnstile_enabled() -> bool:
    """True when a secret is configured, i.e. verification should be enforced."""
    return bool(getattr(settings, "TURNSTILE_SECRET_KEY", ""))


def verify_turnstile_token(token: str, remote_ip: str | None = None) -> bool:
    """Exchange the client-side Turnstile token for a verification result.

    Returns True only when Cloudflare reports {"success": true}. Any other
    outcome — empty token, missing secret, network failure, timeout, malformed
    response, explicit failure — returns False (fail-closed).
    """
    if not token:
        return False

    secret = getattr(settings, "TURNSTILE_SECRET_KEY", "")
    if not secret:
        logger.error("TURNSTILE_SECRET_KEY is not configured; refusing to verify token.")
        return False

    payload = {"secret": secret, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        response = requests.post(VERIFY_URL, data=payload, timeout=TIMEOUT_SECONDS)
    except requests.RequestException:
        logger.exception("Turnstile siteverify network error; treating as failure.")
        return False

    if response.status_code != 200:
        logger.warning("Turnstile siteverify returned %s: %s", response.status_code, response.text)
        return False

    try:
        data = response.json()
    except ValueError:
        logger.warning("Turnstile siteverify returned non-JSON body: %r", response.text)
        return False

    return bool(data.get("success"))


def get_remote_ip(request) -> str | None:
    """Best-effort client IP. Honours X-Forwarded-For (proxy chain) when
    present; otherwise falls back to REMOTE_ADDR."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
