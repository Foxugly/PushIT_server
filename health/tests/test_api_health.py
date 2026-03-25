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
def test_metrics_endpoint_returns_prometheus_payload():
    reset_metrics()
    client = APIClient()

    live_response = client.get("/health/live/")
    assert live_response.status_code == 200

    response = client.get("/health/metrics/")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain; version=0.0.4")
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
