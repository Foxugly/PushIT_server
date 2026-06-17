"""Server-Side Request Forgery (SSRF) guards for user-controlled outbound URLs.

The application lets a user configure a `webhook_url` that the worker POSTs to on
terminal notification states. Without a guard, a user could point it at the EC2
instance metadata service (`169.254.169.254`), loopback gunicorn/Redis, or any
RFC1918 host and use the server as a confused deputy.

We defend in two layers:
  1. At write time (model/serializer field validator) — reject obviously unsafe
     schemes/hosts so a bad value never gets persisted.
  2. At send time (`assert_webhook_url_safe`) — re-resolve the host right before
     the request to defeat DNS rebinding (the name could resolve to a public IP
     at write time and to 169.254.169.254 at send time).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError

ALLOWED_SCHEMES = {"http", "https"}


class UnsafeWebhookURL(ValueError):
    """Raised at send time when a webhook URL resolves to a forbidden address."""


def _is_forbidden_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # Reject anything that is not a normal, routable public address: loopback,
    # private (RFC1918 / ULA), link-local (incl. 169.254.0.0/16 = IMDS),
    # reserved, multicast, unspecified. IPv4-mapped IPv6 is unwrapped first so a
    # `::ffff:169.254.169.254` can't slip through.
    if getattr(ip, "ipv4_mapped", None) is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolve_host_ips(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    # A literal IP host is its own "resolution"; otherwise resolve via DNS and
    # check *every* answer (a name can return both a safe and an unsafe record).
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeWebhookURL(f"Could not resolve webhook host: {host}") from exc

    ips = []
    for info in infos:
        sockaddr = info[4]
        try:
            ips.append(ipaddress.ip_address(sockaddr[0]))
        except ValueError:
            continue
    if not ips:
        raise UnsafeWebhookURL(f"Could not resolve webhook host: {host}")
    return ips


def assert_webhook_url_safe(url: str) -> None:
    """Raise ``UnsafeWebhookURL`` if ``url`` is not safe to request.

    Used at send time (anti-DNS-rebinding) and shared by the write-time validator.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeWebhookURL("Webhook URL must use http or https.")

    host = parts.hostname
    if not host:
        raise UnsafeWebhookURL("Webhook URL must include a host.")

    for ip in _resolve_host_ips(host):
        if _is_forbidden_ip(ip):
            raise UnsafeWebhookURL(
                f"Webhook host resolves to a forbidden address ({ip})."
            )


def validate_webhook_url(value: str) -> None:
    """Django field validator: reject unsafe webhook URLs at write time.

    Empty is allowed (no webhook configured). Re-uses the send-time check so the
    two layers can never disagree on what counts as "safe".
    """
    if not value:
        return
    try:
        assert_webhook_url_safe(value)
    except UnsafeWebhookURL as exc:
        raise ValidationError(str(exc)) from exc
