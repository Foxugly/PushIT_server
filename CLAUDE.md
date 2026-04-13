# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PushIT Server is a Django REST API for managing push notification delivery. It handles user/app registration, device management, notification creation & scheduling (with quiet periods), and delivery via FCM. It also supports inbound email as a notification source via IMAP polling.

**Stack:** Python 3.14, Django 6.0.3, DRF 3.17.1, Celery 5.6.3 (Redis broker), SQLite (dev/test), SimpleJWT, drf-spectacular, Prometheus metrics.

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

# Regenerate OpenAPI schema
python manage.py spectacular --file schema.yaml

# Start observability stack (Prometheus + Grafana)
docker compose -f docker-compose.observability.yml up -d
```

## Settings & Environment

Settings are in `config/settings/` with `base.py`, `dev.py`, `test.py`, `prod.py`. The `STATE` env var (`DEV`/`TEST`/`PROD`) selects the active settings module via `config/settings/__init__.py`.

Key env vars: `STATE`, `DJANGO_SECRET_KEY`, `ALLOWED_HOSTS`, `REDIS_URL`, `FCM_API_KEY`, `INBOUND_EMAIL_DOMAIN`, `INBOUND_EMAIL_IMAP_*`, `METRICS_AUTH_TOKEN`. See `.env_template` for the full list.

In DEV/TEST, Celery runs eagerly (synchronous, no broker needed) and passwords use MD5 for speed.

## Architecture

### Django Apps

- **accounts** — Custom User model (email as username), JWT auth (login/register/refresh/logout), profile management
- **applications** — Application CRUD, app token generation (`apt_` prefix, SHA256 hashed), quiet period scheduling
- **devices** — Device registration (push tokens), platform tracking (Android/iOS), linking devices to applications
- **notifications** — Core business logic: notification creation (with idempotency keys), scheduling, quiet period shifting, delivery tracking, retry logic, inbound email ingestion
- **health** — Liveness (`/health/live/`), readiness (`/health/ready/`), Prometheus metrics (`/health/metrics/`)
- **config** — Settings, custom exception handler, middleware (RequestId, Metrics), Prometheus counters, JSON logging

### Two Authentication Mechanisms

1. **JWT (user auth)** — `Authorization: Bearer <token>` via SimpleJWT. Used for user-facing endpoints (app/device management).
2. **App Token (machine auth)** — `X-App-Token: apt_...` header. Authenticated via `AppTokenAuthentication` in `applications/authentication.py`. Sets `request.auth_application` (not `request.user`). Used by `api_views_app_token.py` endpoints for server-to-server notification creation.

### Notification Lifecycle

`DRAFT → QUEUED → PROCESSING → SENT/PARTIAL/FAILED` (also `SCHEDULED` for future notifications, `NO_TARGET` when no linked devices).

Quiet periods (one-time or recurring) can shift `scheduled_for` → `effective_scheduled_for`. Idempotency enforced via `idempotency_key` unique constraint.

### Celery Tasks (beat schedule, every minute each)

- `dispatch_scheduled_notifications_task` — picks up SCHEDULED notifications ready to send
- `retry_pending_deliveries_task` — retries failed deliveries (3 attempts, exponential backoff)
- `poll_inbound_mailbox_task` — IMAP polling for inbound email notifications

### Key Business Logic Locations

- `notifications/services.py` — send_notification, delivery orchestration
- `notifications/scheduling.py` — quiet period calculation
- `notifications/creation.py` — notification creation with idempotency
- `notifications/inbound_mailbox.py` — IMAP polling and email parsing
- `notifications/push.py` — FCM provider interface (currently mocked)
- `config/exceptions.py` — standardized error response format: `{code, detail, [incident_id]}`

### API URL Structure

All endpoints under `/api/v1/`. URL modules per app (`*/api_urls.py`), wired through `config/urls.py`. API docs at `/api/docs/` (Swagger) and `/api/redoc/`.

## Testing

Pytest with `pytest-django`. Config in `pytest.ini`. Tests live in each app's `tests/` directory plus top-level `tests/` for integration/cross-cutting tests. Use `@pytest.mark.integration` for tests needing a fresh database.

## Current Limitations

- FCM provider is mocked (`notifications/push.py`) — no real push delivery
- SQLite only in dev/test — production should use PostgreSQL
- Celery eager mode in DEV/TEST — no actual async task processing
