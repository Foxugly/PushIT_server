"""Client-IP trust: anonymous throttles must key on the address seen by nginx.

nginx is the single reverse proxy in front of gunicorn. Whatever the client puts
in its own ``X-Forwarded-For`` header must not change the throttle identity, nor
the IP passed to Turnstile.
"""
import pytest
from django.test import RequestFactory
from rest_framework.settings import api_settings
from rest_framework.test import APIClient

from accounts.turnstile import get_remote_ip

LOGIN_URL = "/api/v1/auth/login/"
PROXY_SEEN_IP = "203.0.113.5"


def test_num_proxies_is_one():
    assert api_settings.NUM_PROXIES == 1


@pytest.mark.django_db
def test_forged_x_forwarded_for_does_not_reset_login_throttle():
    client = APIClient()
    codes = []
    for i in range(12):
        # What gunicorn receives when a client forges XFF and nginx appends the
        # real peer address ($proxy_add_x_forwarded_for).
        response = client.post(
            LOGIN_URL,
            {"email": "nobody@example.com", "password": "wrong-password"},
            format="json",
            HTTP_X_FORWARDED_FOR=f"10.0.0.{i}, {PROXY_SEEN_IP}",
            REMOTE_ADDR="127.0.0.1",
        )
        codes.append(response.status_code)

    assert 429 in codes


def test_turnstile_remote_ip_ignores_client_supplied_entries():
    request = RequestFactory().post(
        "/", HTTP_X_FORWARDED_FOR=f"10.9.9.9, {PROXY_SEEN_IP}", REMOTE_ADDR="127.0.0.1"
    )
    assert get_remote_ip(request) == PROXY_SEEN_IP


def test_turnstile_remote_ip_falls_back_to_remote_addr():
    request = RequestFactory().post("/", REMOTE_ADDR="198.51.100.7")
    assert get_remote_ip(request) == "198.51.100.7"


def test_nginx_template_overwrites_client_forwarded_for():
    from pathlib import Path

    conf = (Path(__file__).resolve().parent.parent / "deploy" / "nginx" / "pushit-api.conf").read_text(
        encoding="utf-8"
    )
    assert "$proxy_add_x_forwarded_for" not in conf
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in conf
