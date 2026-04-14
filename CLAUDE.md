# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PushIT Server is a Django REST API for managing push notification delivery. It handles user/app registration, device management, notification creation & scheduling (with quiet periods and templates), delivery via FCM, and inbound email as a notification source via Microsoft Graph API polling.

**Stack:** Python 3.14, Django 6.0.3, DRF 3.17.1, Celery 5.6.3 (Redis broker), SQLite (dev) / PostgreSQL (prod), SimpleJWT, drf-spectacular, Prometheus metrics, MSAL (Microsoft Graph API), Firebase Admin SDK.

## Common Commands

```bash
# Run dev server
python manage.py runserver

# Run all tests
pytest -q

# Run tests for a single app
pytest notifications/tests/ -q

# Run a single test file
pytest notifications/tests/test_api_notifications.py -q

# Run a single test
pytest notifications/tests/test_api_notifications.py::TestClassName::test_method -q

# Run integration tests (marked tests that use fresh DB)
pytest -m integration -q

# Generate/apply migrations
python manage.py makemigrations
python manage.py migrate

# Regenerate OpenAPI schema
python manage.py spectacular --file schema.yaml

# Start observability stack (Prometheus + Grafana)
docker compose -f docker-compose.observability.yml up -d
```

## Settings & Environment

Settings are in `config/settings/` with `base.py`, `dev.py`, `test.py`, `prod.py`. The `STATE` env var (`DEV`/`TEST`/`PROD`) selects the active settings module via `config/settings/__init__.py`.

Key env vars: `STATE`, `DJANGO_SECRET_KEY`, `ALLOWED_HOSTS`, `DATABASE_ENGINE`, `DATABASE_NAME`, `DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_USER`, `DATABASE_PASSWORD`, `REDIS_URL`, `FCM_SERVICE_ACCOUNT_PATH`, `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`, `GRAPH_MAILBOX_USER_ID`, `INBOUND_EMAIL_DOMAIN`, `METRICS_AUTH_TOKEN`. See `.env_template` for the full list.

In DEV/TEST, Celery runs eagerly (synchronous, no broker needed) and passwords use MD5 for speed. Graph API calls are silently skipped when `GRAPH_CLIENT_ID` is empty. FCM uses a mock when `FCM_SERVICE_ACCOUNT_PATH` is empty.

`DB_SUPPORTS_ROW_LOCKING` is derived from the database engine — `True` for PostgreSQL, `False` for SQLite. This controls whether `select_for_update()` is used in the notification send flow.

## Architecture

### Django Apps

- **accounts** — Custom User model (email as username), JWT auth (login/register/refresh/logout), profile management
- **applications** — Application CRUD, app token generation (`apt_` prefix, SHA256 hashed), quiet period scheduling, Graph API email alias management (`graph_mail.py`), QR code generation for device onboarding, webhook URL configuration
- **devices** — Device registration (push tokens), platform tracking (Android/iOS), linking devices to applications via app token
- **notifications** — Core business logic: notification creation (with idempotency keys), templates with `{{variable}}` substitution, scheduling, quiet period shifting, delivery tracking, retry logic, bulk send, webhook callbacks, inbound email ingestion with auto-reply
- **health** — Liveness (`/health/live/`), readiness (`/health/ready/`), Prometheus metrics (`/health/metrics/`)
- **config** — Settings, custom exception handler, middleware (RequestId, Metrics), Prometheus counters, JSON logging

### Two Authentication Mechanisms

1. **JWT (user auth)** — `Authorization: Bearer <token>` via SimpleJWT. Used for user-facing endpoints (app/device management, notification CRUD).
2. **App Token (machine auth)** — `X-App-Token: apt_...` header. Authenticated via `AppTokenAuthentication` in `applications/authentication.py`. Sets `request.auth_application` (not `request.user`). Rate-limited to 300/min per application. Used for server-to-server notification creation, device linking, and bulk send.

### Notification Lifecycle

`DRAFT → QUEUED → PROCESSING → SENT/PARTIAL/FAILED` (also `SCHEDULED` for future notifications, `NO_TARGET` when no linked devices).

Quiet periods (one-time or recurring) can shift `scheduled_for` → `effective_scheduled_for`. Idempotency enforced via `idempotency_key` unique constraint. Webhook callbacks are sent on terminal states (SENT/FAILED/PARTIAL/NO_TARGET) with HMAC-SHA256 signature.

### Notification Templates

`NotificationTemplate` model linked to `Application` with `title_template` and `message_template` fields supporting `{{variable}}` placeholders. Templates are CRUD-managed at `/api/v1/apps/{app_id}/templates/`. Notification creation accepts `template_id` + `variables` as an alternative to direct `title`/`message`.

### Microsoft Graph API Integration

`applications/graph_mail.py` manages email aliases, inbox polling, and email sending via Graph API (MSAL client credentials flow):
- `add_email_alias` / `remove_email_alias` — manage `proxyAddresses` on the shared mailbox
- `fetch_unread_emails` / `mark_email_read` — poll inbox for inbound emails
- `send_email` — send auto-reply when a known user emails an unknown address

Called from `Application.save()` (add alias), `Application.delete()` (remove alias), and `notifications/inbound_mailbox.py` (polling).

### Firebase Cloud Messaging

`notifications/push.py` sends push notifications via the Firebase Admin SDK when `FCM_SERVICE_ACCOUNT_PATH` is configured. Falls back to a mock provider when unconfigured. Maps Firebase exceptions to `InvalidPushTokenError` / `TemporaryPushProviderError` / `PushProviderError`.

### Celery Tasks (beat schedule, every minute each)

- `dispatch_scheduled_notifications_task` — picks up SCHEDULED notifications ready to send
- `retry_pending_deliveries_task` — retries failed deliveries (3 attempts, exponential backoff)
- `poll_inbound_mailbox_task` — Graph API polling for inbound email notifications

### Key Business Logic Locations

- `notifications/services.py` — send_notification, delivery orchestration, webhook callbacks
- `notifications/scheduling.py` — quiet period calculation
- `notifications/creation.py` — notification creation with idempotency
- `notifications/inbound_mailbox.py` — Graph API inbox polling and email processing
- `notifications/inbound_reply.py` — auto-reply builder for unknown recipient addresses
- `notifications/webhooks.py` — webhook callback delivery with HMAC signing
- `notifications/push.py` — FCM provider (Firebase Admin SDK or mock)
- `applications/graph_mail.py` — Microsoft Graph API client (aliases, inbox, send)
- `config/exceptions.py` — standardized error response format: `{code, detail, [incident_id]}`

### API URL Structure

All endpoints under `/api/v1/`. URL modules per app (`*/api_urls.py`), wired through `config/urls.py`. API docs at `/api/docs/` (Swagger) and `/api/redoc/`.

## Testing

Pytest with `pytest-django`. Config in `pytest.ini`. Tests live in each app's `tests/` directory plus top-level `tests/` for integration/cross-cutting tests. Use `@pytest.mark.integration` for tests needing a fresh database. Concurrent send test is skipped on SQLite (`DB_SUPPORTS_ROW_LOCKING=False`).

## Current Limitations

- SQLite in dev/test — production should use PostgreSQL (configured via `DATABASE_ENGINE`)
- Celery eager mode in DEV/TEST — no actual async task processing
