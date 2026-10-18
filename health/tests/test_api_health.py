import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from config.metrics import reset_metrics


@pytest.mark.django_db
def test_health_live_returns_ok():
    client = APIClient()

    response = client.get("/health/live/")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["check"] == "live"
    assert response["X-Request-ID"]


@pytest.mark.django_db
def test_health_ready_returns_ok():
    client = APIClient()

    response = client.get("/health/ready/")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["check"] == "ready"
    assert response["X-Request-ID"]


@pytest.mark.django_db
def test_health_root_is_the_ready_check():
    client = APIClient()

    response = client.get("/health/")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["check"] == "ready"


@pytest.mark.django_db
def test_metrics_endpoint_returns_prometheus_payload():
    reset_metrics()
    client = APIClient()

    live_response = client.get("/health/live/")
    assert live_response.status_code == 200

    response = client.get("/health/metrics/")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain; version=")
    body = response.content.decode("utf-8")
    assert "pushit_process_uptime_seconds" in body
    assert 'pushit_http_requests_total{method="GET",route="health-live",status="200"} 1.0' in body


@pytest.mark.django_db
@override_settings(METRICS_AUTH_TOKEN="secret-metrics-token")
def test_metrics_endpoint_can_be_protected_by_token():
    reset_metrics()
    client = APIClient()

    forbidden_response = client.get("/health/metrics/")
    assert forbidden_response.status_code == 403
    assert forbidden_response.json()["detail"] == "metrics_token_invalid"

    allowed_response = client.get("/health/metrics/", HTTP_X_METRICS_TOKEN="secret-metrics-token")
    assert allowed_response.status_code == 200


@pytest.mark.django_db
@override_settings(METRICS_AUTH_TOKEN="", METRICS_REQUIRE_TOKEN=True)
def test_metrics_endpoint_denied_when_token_required_but_unset():
    reset_metrics()
    response = APIClient().get("/health/metrics/")
    assert response.status_code == 403
    assert response.json()["detail"] == "metrics_disabled"
    assert b"pushit_process_uptime_seconds" not in response.content


@pytest.mark.django_db
@override_settings(METRICS_AUTH_TOKEN=None, METRICS_REQUIRE_TOKEN=True)
def test_metrics_endpoint_denied_when_token_none_and_required():
    response = APIClient().get("/health/metrics/", HTTP_X_METRICS_TOKEN="")
    assert response.status_code == 403


def test_prod_settings_require_metrics_token():
    import os
    import subprocess
    import sys
    from pathlib import Path

    env = os.environ.copy()
    env.update(
        {
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "DJANGO_ENV": "prod",
            "SECRET_KEY": "x" * 50,
            "ALLOWED_HOSTS": "example.com",
            "METRICS_AUTH_TOKEN": "",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", "from django.conf import settings; print(settings.METRICS_REQUIRE_TOKEN)"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"

