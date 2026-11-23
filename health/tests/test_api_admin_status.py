import pytest
from rest_framework.test import APIClient

from accounts.models import User


ADMIN_STATUS_URL = "/api/v1/admin/status/"


def _auth(client, user):
    # Issue a JWT for the user directly (avoids the email-confirmation login gate).
    from rest_framework_simplejwt.tokens import RefreshToken

    access = str(RefreshToken.for_user(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")


@pytest.fixture
def _mock_celery(monkeypatch):
    """Stub the Celery broker connection + worker ping so the test never needs a
    real broker; one worker is reported as responding."""
    from config.celery import app

    class _Conn:
        def ensure_connection(self, *args, **kwargs):
            return True

    monkeypatch.setattr(app, "connection", lambda *a, **k: _Conn())
    monkeypatch.setattr(app.control, "ping", lambda *a, **k: [{"worker@host": {"ok": "pong"}}])


@pytest.mark.django_db
def test_admin_status_anonymous_forbidden():
    client = APIClient()
    response = client.get(ADMIN_STATUS_URL)
    # No credentials -> 401/403 depending on auth layer; either way not 200.
    assert response.status_code in (401, 403)


@pytest.mark.django_db
def test_admin_status_normal_user_forbidden(_mock_celery):
    user = User.objects.create_user(
        email="user@example.com", password="MotDePasseTresSolide123!"
    )
    client = APIClient()
    _auth(client, user)
    response = client.get(ADMIN_STATUS_URL)
    assert response.status_code == 403


@pytest.mark.django_db
def test_admin_status_staff_user_ok(_mock_celery):
    user = User.objects.create_user(
        email="staff@example.com", password="MotDePasseTresSolide123!"
    )
    user.is_staff = True
    user.save(update_fields=["is_staff"])

    client = APIClient()
    _auth(client, user)
    response = client.get(ADMIN_STATUS_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"

    checks = body["checks"]
    assert set(checks.keys()) == {"database", "celery_broker", "celery_workers", "exchange"}
    assert checks["database"]["status"] == "ok"
    assert checks["celery_broker"]["status"] == "ok"
    assert checks["celery_workers"]["status"] == "ok"
    assert checks["exchange"]["status"] == "ok"
    assert checks["exchange"]["configured"] is False  # not configured in tests

    metrics = body["metrics"]
    assert "applications" in metrics
    assert "devices" in metrics
    assert "notifications" in metrics
    assert "processing_stuck" in metrics
    # All notification statuses present in the breakdown.
    assert set(metrics["notifications"].keys()) == {
        "draft", "scheduled", "queued", "processing",
        "sent", "failed", "partial", "no_target",
    }


@pytest.mark.django_db
def test_admin_status_does_not_expose_exception_text(monkeypatch, caplog):
    """CodeQL py/stack-trace-exposure: a failing check/metric returns a fixed
    string; the exception text (hosts, DSNs, SQL...) only goes to the logs."""
    from config.celery import app
    from devices.models import Device

    secret = "redis://:hunter2@internal-host:6379/2 refused"

    class _Conn:
        def ensure_connection(self, *args, **kwargs):
            raise ConnectionError(secret)

    def _boom(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(app, "connection", lambda *a, **k: _Conn())
    monkeypatch.setattr(app.control, "ping", _boom)
    monkeypatch.setattr(Device.objects, "count", _boom)

    user = User.objects.create_user(
        email="staff2@example.com", password="MotDePasseTresSolide123!"
    )
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    client = APIClient()
    _auth(client, user)

    with caplog.at_level("WARNING", logger="pushit"):
        response = client.get(ADMIN_STATUS_URL)

    assert response.status_code == 200
    assert secret not in response.content.decode()
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["celery_broker"] == {"status": "error", "detail": "unavailable"}
    assert body["checks"]["celery_workers"] == {"status": "error", "detail": "unavailable"}
    assert body["checks"]["database"]["status"] == "ok"
    assert body["metrics"]["devices"] == {"error": "unavailable"}

    logged = [r for r in caplog.records if r.msg in ("admin_status_check_failed", "admin_status_metric_failed")]
    assert {getattr(r, "check", None) or getattr(r, "metric", None) for r in logged} == {
        "celery_broker", "celery_workers", "devices",
    }
    assert all(r.exc_info and secret in str(r.exc_info[1]) for r in logged)
