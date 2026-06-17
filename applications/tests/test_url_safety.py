"""SSRF guard for user-controlled webhook URLs (write-time + send-time)."""

from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError

from applications.url_safety import (
    UnsafeWebhookURL,
    assert_webhook_url_safe,
    validate_webhook_url,
)


def _fake_getaddrinfo(ip):
    return lambda *a, **k: [(2, 1, 6, "", (ip, 0))]


def test_empty_url_is_allowed():
    # No webhook configured — must be a no-op, never raises.
    validate_webhook_url("")


@pytest.mark.parametrize("url", ["ftp://example.com", "file:///etc/passwd", "gopher://x"])
def test_non_http_scheme_rejected(url):
    with pytest.raises(UnsafeWebhookURL):
        assert_webhook_url_safe(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # IMDS
        "http://127.0.0.1/",  # loopback
        "http://10.0.0.5/hook",  # RFC1918
        "http://192.168.1.10/",  # RFC1918
        "http://[::1]/",  # IPv6 loopback
        "http://0.0.0.0/",  # unspecified
    ],
)
def test_literal_private_ip_rejected(url):
    with pytest.raises(UnsafeWebhookURL):
        assert_webhook_url_safe(url)


def test_public_literal_ip_allowed():
    assert_webhook_url_safe("https://1.1.1.1/hook")


def test_dns_resolving_to_private_is_rejected():
    with patch(
        "applications.url_safety.socket.getaddrinfo",
        side_effect=_fake_getaddrinfo("169.254.169.254"),
    ):
        with pytest.raises(UnsafeWebhookURL):
            assert_webhook_url_safe("https://evil.example.com/hook")


def test_dns_resolving_to_public_is_allowed():
    with patch(
        "applications.url_safety.socket.getaddrinfo",
        side_effect=_fake_getaddrinfo("93.184.216.34"),
    ):
        assert_webhook_url_safe("https://good.example.com/hook")


def test_validator_raises_django_validationerror_for_private():
    with pytest.raises(ValidationError):
        validate_webhook_url("http://127.0.0.1/")
