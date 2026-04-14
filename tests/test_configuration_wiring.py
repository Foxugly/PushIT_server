import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import resolve, reverse

from applications.authentication import AppTokenAuthentication


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_core_url_wiring():
    assert reverse("health-live") == "/health/live/"
    assert reverse("health-ready") == "/health/ready/"
    assert reverse("health-metrics") == "/health/metrics/"
    assert reverse("auth-register") == "/api/v1/auth/register/"
    assert reverse("app-list-create") == "/api/v1/apps/"
    assert reverse("app-detail", kwargs={"app_id": 1}) == "/api/v1/apps/1/"
    assert reverse("app-quiet-period-list-create", kwargs={"app_id": 1}) == "/api/v1/apps/1/quiet-periods/"
    assert reverse("device-link") == "/api/v1/devices/link/"
    assert reverse("device-quiet-period-list-create", kwargs={"device_id": 1}) == "/api/v1/devices/1/quiet-periods/"
    assert reverse("notification-list-create") == "/api/v1/notifications/"
    assert reverse("notification-create-app-token") == "/api/v1/notifications/app/create/"
    assert reverse("notification-future-list") == "/api/v1/notifications/future/"
    assert reverse("schema") == "/api/schema/"

    assert resolve("/health/live/").view_name == "health-live"
    assert resolve("/health/ready/").view_name == "health-ready"
    assert resolve("/health/metrics/").view_name == "health-metrics"
    assert resolve("/api/v1/auth/register/").view_name == "auth-register"
    assert resolve("/api/v1/apps/").view_name == "app-list-create"
    assert resolve("/api/v1/apps/1/").view_name == "app-detail"
    assert resolve("/api/v1/apps/1/quiet-periods/").view_name == "app-quiet-period-list-create"
    assert resolve("/api/v1/devices/link/").view_name == "device-link"
    assert resolve("/api/v1/devices/1/quiet-periods/").view_name == "device-quiet-period-list-create"
    assert resolve("/api/v1/notifications/future/").view_name == "notification-future-list"
    assert resolve("/api/v1/notifications/app/create/").view_name == "notification-create-app-token"


def test_rest_framework_and_schema_wiring():
    assert settings.AUTH_USER_MODEL == "accounts.User"
    assert "corsheaders" in settings.INSTALLED_APPS
    assert settings.MIDDLEWARE.index("corsheaders.middleware.CorsMiddleware") < settings.MIDDLEWARE.index(
        "django.middleware.common.CommonMiddleware"
    )
    assert (
        "rest_framework_simplejwt.authentication.JWTAuthentication"
        in settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]
    )
    assert settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] == (
        "rest_framework.permissions.IsAuthenticated",
    )
    assert (
        "rest_framework.throttling.UserRateThrottle"
        in settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"]
    )
    assert settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["login"] == "10/min"
    assert settings.SPECTACULAR_SETTINGS["COMPONENTS"]["securitySchemes"]["ApiKeyAuth"]["name"] == "X-App-Token"
    assert "config.middleware.RequestIdMiddleware" in settings.MIDDLEWARE
    assert "http://localhost:4200" in settings.CORS_ALLOWED_ORIGINS
    assert "http://127.0.0.1:4200" in settings.CORS_ALLOWED_ORIGINS
    assert settings.INBOUND_EMAIL_DOMAIN == "pushit.com"
    assert "pushit-poll-inbound-mailbox" in settings.CELERY_BEAT_SCHEDULE
    assert settings.CELERY_BEAT_SCHEDULE["pushit-poll-inbound-mailbox"]["task"] == "notifications.tasks.poll_inbound_mailbox_task"


def test_dev_state_enables_eager_celery():
    assert settings.STATE == "DEV"
    assert settings.CELERY_TASK_ALWAYS_EAGER is True
    assert settings.CELERY_TASK_EAGER_PROPAGATES is True


def test_notification_app_views_use_custom_app_token_authentication():
    from notifications.api_views_app_token import (
        NotificationCreateWithAppTokenApiView,
        NotificationListWithAppTokenApiView,
    )

    assert NotificationCreateWithAppTokenApiView.authentication_classes == [AppTokenAuthentication]
    assert NotificationListWithAppTokenApiView.authentication_classes == [AppTokenAuthentication]


@pytest.mark.integration
def test_sqlite_name_environment_override_is_applied(tmp_path):
    db_path = tmp_path / "config-check.sqlite3"
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = "config.settings"
    env["SQLITE_NAME"] = str(db_path)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from django.conf import settings; "
                "from pathlib import Path; "
                "print(Path(settings.DATABASES['default']['NAME']))"
            ),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert Path(result.stdout.strip()) == db_path


@pytest.mark.integration
def test_manage_check_passes():
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = "config.settings"
    env["DJANGO_ENV"] = "test"

    result = subprocess.run(
        [sys.executable, "manage.py", "check"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "System check identified no issues" in result.stdout


@pytest.mark.integration
def test_prod_settings_require_explicit_secret_key_and_allowed_hosts():
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = "config.settings"
    env["DJANGO_ENV"] = "prod"
    env["DJANGO_SECRET_KEY"] = ""
    env["ALLOWED_HOSTS"] = ""

    result = subprocess.run(
        [sys.executable, "manage.py", "check"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode != 0
    assert (
        "DJANGO_SECRET_KEY must be explicitly set in PROD." in result.stderr
        or "ALLOWED_HOSTS must be explicitly set in PROD." in result.stderr
    )


@pytest.mark.integration
def test_full_openapi_schema_generation_passes(tmp_path):
    output_file = tmp_path / "schema.yaml"
    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = "config.settings"
    env["DJANGO_ENV"] = "test"

    result = subprocess.run(
        [
            sys.executable,
            "manage.py",
            "spectacular",
            "--file",
            str(output_file),
            "--validate",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert output_file.exists()
    assert output_file.read_text(encoding="utf-8").strip().startswith("openapi:")
