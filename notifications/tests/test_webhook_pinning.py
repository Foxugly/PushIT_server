"""Webhook DNS pinning (audit S-M4): connect to the IP the SSRF guard validated.

Before, the guard resolved the host, then ``requests`` resolved it *again* to
connect. A name with a 0-TTL record could answer a public IP to the guard and
127.0.0.1 / 169.254.169.254 to the connect (DNS rebinding TOCTOU). The callback
now connects to the validated address while keeping the original name for the
``Host`` header, SNI and certificate verification.
"""

import socket
from unittest.mock import patch

import pytest
import requests
import urllib3.util.connection

from accounts.models import User
from applications.models import Application
from notifications.webhooks import _PinnedHostAdapter, _post_pinned, send_webhook_callback_task

PUBLIC_IP = "93.184.216.34"


def _make_app(webhook_url: str) -> Application:
    owner = User.objects.create_user(email="pin-owner@example.com", password="MotDePasseTresSolide123!")
    app = Application.objects.create(owner=owner, name="Pin App")
    Application.objects.filter(id=app.id).update(webhook_url=webhook_url)
    app.refresh_from_db()
    return app


class _RebindingResolver:
    """Answers a public IP to the first lookup of ``name``, loopback afterwards."""

    def __init__(self, name: str):
        self.name = name
        self.lookups = 0
        self._real = socket.getaddrinfo

    def __call__(self, host, port, *args, **kwargs):
        if host == self.name:
            self.lookups += 1
            ip = PUBLIC_IP if self.lookups == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))]
        return self._real(host, port, *args, **kwargs)


class _ConnectRecorder:
    """Stands in for urllib3's socket connect: records the address, then refuses."""

    def __init__(self):
        self.addresses = []

    def __call__(self, address, *args, **kwargs):
        self.addresses.append(address)
        raise ConnectionRefusedError("recorded, not connected")


@pytest.mark.django_db
@pytest.mark.parametrize("scheme,port", [("http", 80), ("https", 443)])
def test_rebinding_resolver_cannot_redirect_the_connect(scheme, port):
    app = _make_app(f"{scheme}://rebind.example.com/hook")
    resolver = _RebindingResolver("rebind.example.com")
    recorder = _ConnectRecorder()

    with patch("socket.getaddrinfo", side_effect=resolver), patch.object(
        urllib3.util.connection, "create_connection", side_effect=recorder
    ):
        with pytest.raises(requests.ConnectionError):
            send_webhook_callback_task(
                application_id=app.id, notification_id=1, final_status="sent", sent_at=None
            )

    # The guard did the only lookup; the connect went to the validated IP, never
    # to the loopback answer a second lookup would have returned.
    assert resolver.lookups == 1
    assert recorder.addresses
    assert all(addr[0] == PUBLIC_IP and addr[1] == port for addr in recorder.addresses)


@pytest.mark.django_db
def test_unsafe_first_answer_still_blocks_without_connecting():
    app = _make_app("https://evil.example.com/hook")
    recorder = _ConnectRecorder()

    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))],
    ), patch.object(urllib3.util.connection, "create_connection", side_effect=recorder):
        send_webhook_callback_task(application_id=app.id, notification_id=2, final_status="sent", sent_at=None)

    assert recorder.addresses == []


def test_pinned_post_keeps_host_header_and_port():
    import ipaddress

    captured = {}

    def fake_send(self, request, **kwargs):
        captured["url"] = request.url
        captured["headers"] = dict(request.headers)
        captured["kwargs"] = kwargs
        response = requests.Response()
        response.status_code = 204
        return response

    with patch.object(requests.adapters.HTTPAdapter, "send", fake_send):
        response = _post_pinned(
            "http://hooks.example.com:8080/p?x=1",
            [ipaddress.ip_address(PUBLIC_IP)],
            data=b"{}",
            headers={"X-PushIT-Event": "e"},
            timeout=10,
        )

    assert response.status_code == 204
    assert captured["url"] == f"http://{PUBLIC_IP}:8080/p?x=1"
    assert captured["headers"]["Host"] == "hooks.example.com:8080"
    assert captured["headers"]["X-PushIT-Event"] == "e"


def test_pinned_post_ipv6_is_bracketed():
    import ipaddress

    captured = {}

    def fake_send(self, request, **kwargs):
        captured["url"] = request.url
        captured["host"] = request.headers["Host"]
        response = requests.Response()
        response.status_code = 200
        return response

    with patch.object(requests.adapters.HTTPAdapter, "send", fake_send):
        _post_pinned(
            "https://v6.example.com/hook",
            [ipaddress.ip_address("2606:2800:220:1::248")],
            data=b"{}",
            headers={},
            timeout=10,
        )

    assert captured["url"] == "https://[2606:2800:220:1::248]/hook"
    assert captured["host"] == "v6.example.com"


def test_pinned_post_never_follows_redirects():
    import ipaddress

    captured = {}

    def fake_send(self, request, **kwargs):
        captured.setdefault("calls", 0)
        captured["calls"] += 1
        response = requests.Response()
        response.status_code = 302
        response.headers["Location"] = "http://169.254.169.254/"
        response.request = request
        response.url = request.url
        return response

    with patch.object(requests.adapters.HTTPAdapter, "send", fake_send):
        response = _post_pinned(
            "https://hooks.example.com/hook",
            [ipaddress.ip_address(PUBLIC_IP)],
            data=b"{}",
            headers={},
            timeout=10,
        )

    assert response.status_code == 302
    assert captured["calls"] == 1


def test_https_adapter_verifies_the_certificate_against_the_original_name():
    adapter = _PinnedHostAdapter("hooks.example.com")
    request = requests.Request("POST", f"https://{PUBLIC_IP}/hook").prepare()

    pool = adapter.get_connection_with_tls_context(request, verify=True)

    # SNI and hostname verification use the webhook's name, not the IP literal;
    # certificate verification stays mandatory.
    assert pool.conn_kw.get("server_hostname") == "hooks.example.com"
    assert pool.assert_hostname == "hooks.example.com"
    assert pool.cert_reqs == "CERT_REQUIRED"
    assert pool.host == PUBLIC_IP
