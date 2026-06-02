# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PushIT Server is a Django REST API for managing push notification delivery. It handles user/app registration, device management, notification creation & scheduling (with quiet periods and templates), delivery via FCM, and inbound email as a notification source via Microsoft Graph API polling.

**Stack:** Python 3.12, Django 6.0.3, DRF 3.17.1, Celery 5.6.3 (Redis broker), SQLite (dev/CI) + PostgreSQL (prod), SimpleJWT, drf-spectacular, Prometheus metrics, MSAL (Microsoft Graph API), Firebase Admin SDK.

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

Settings are in `config/settings/` with `base.py`, `dev.py`, `test.py`, `prod.py`. The dispatch logic in `config/settings/__init__.py` uses two env vars:

- `DJANGO_ENV` (lowercase: `prod`, `test`) — takes priority for prod/test selection
- `STATE` (uppercase: `DEV`, `TEST`, `PROD`) — `STATE=PROD` also activates prod settings

**Caveat:** `STATE=TEST` alone does **not** activate test settings — you need `DJANGO_ENV=test` for that. The `else` branch falls through to dev.

Key env vars: `STATE`, `DJANGO_SECRET_KEY`, `ALLOWED_HOSTS`, `DATABASE_ENGINE`, `DATABASE_NAME`, `DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_USER`, `DATABASE_PASSWORD`, `REDIS_URL`, `FCM_SERVICE_ACCOUNT_PATH`, `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`, `GRAPH_MAILBOX_USER_ID`, `INBOUND_EMAIL_DOMAIN`, `METRICS_AUTH_TOKEN`. See `.env_template` for the full list.

**DEV/TEST behavior:** Celery runs eagerly (synchronous, no broker needed), passwords use MD5 for speed. Graph API calls are silently skipped when `GRAPH_CLIENT_ID` is empty. FCM uses a mock when `FCM_SERVICE_ACCOUNT_PATH` is empty.

**PROD enforcement:** `DJANGO_SECRET_KEY` and `ALLOWED_HOSTS` are mandatory — startup fails if missing. Full HSTS, SSL redirect, secure cookies are enabled.

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

### CORS (frontend cross-origin)

The frontend (`https://pushit.foxugly.com`) calls the API on a different origin, so it must be allowed via `CORS_ALLOWED_ORIGINS` (comma-separated, exact scheme+host). Auth is JWT Bearer (no cookies), so there is **no CSRF/cookie concern**: `CORS_ALLOW_CREDENTIALS` stays `False`, and the `Authorization` header is already whitelisted (django-cors-headers' `default_headers`, plus `x-app-token` added in `base.py:24`). To authorize a new frontend origin in prod, set `/pushit/prod/CORS_ALLOWED_ORIGINS` in SSM and re-fetch (`systemctl restart pushit-env-fetch pushit-api-gunicorn`) — no code change needed.

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
- `notifications/creation.py` — notification creation with idempotency (separate SQLite/PostgreSQL paths)
- `notifications/inbound_mailbox.py` — Graph API inbox polling and email processing
- `notifications/inbound_reply.py` — auto-reply builder for unknown recipient addresses
- `notifications/webhooks.py` — webhook callback delivery with HMAC signing
- `notifications/push.py` — FCM provider (Firebase Admin SDK or mock)
- `applications/graph_mail.py` — Microsoft Graph API client (aliases, inbox, send)
- `config/exceptions.py` — standardized error response format

### Error Contract

All API errors follow a standardized format via `config/exceptions.py`:

```json
{"code": "not_found", "detail": "Not found."}
```

Validation errors add an `errors` dict:

```json
{"code": "validation_error", "detail": "Validation error.", "errors": {"field": ["..."]}}
```

Unhandled exceptions return `500` with an `incident_id`:

```json
{"code": "internal_error", "detail": "Internal server error.", "incident_id": "inc_xxxxxxxxxxxx"}
```

When raising DRF exceptions in views, pass a `code` kwarg to control the `code` field: `raise NotFound("App not found.", code="app_not_found")`.

### API URL Structure

All endpoints under `/api/v1/`. URL modules per app (`*/api_urls.py`), wired through `config/urls.py`. API docs at `/api/docs/` (Swagger) and `/api/redoc/`.

## Testing

Pytest with `pytest-django`. Config in `pytest.ini`. Tests live in each app's `tests/` directory plus top-level `tests/` for integration/cross-cutting tests. Use `@pytest.mark.integration` for tests needing a fresh database. CI runs tests on SQLite, so the concurrent tests (send + the two app-token create tests) are skipped there (`DB_SUPPORTS_ROW_LOCKING=False`); they only run against PostgreSQL.

## Deployment

### Production (EC2)

Deployed on a shared Ubuntu 24.04 EC2 alongside QuizOnline.

- **URL:** `https://pushit-api.foxugly.com` (API), `https://pushit.foxugly.com` (frontend)
- **Path:** `/var/www/django_websites/PushIT_server/`
- **User:** `django:www-data`
- **Services:** Gunicorn (TCP `127.0.0.1:8001`) + nginx reverse proxy + Celery worker + Celery beat + `pushit-env-fetch` (SSM → `/run/pushit/.env` at boot)
- **Redis:** DB `/2` (broker), DB `/3` (result backend), queue `pushit` (isolated from QuizOnline on `/0`-`/1`)
- **Database:** PostgreSQL 16 (local on the shared EC2) — database `pushit`, role `pushit`, connected over `127.0.0.1:5432`. Credentials in SSM (`DATABASE_*`, password as SecureString). Migrated from SQLite on 2026-06-02 (dump/loaddata).

### CI/CD (GitHub Actions)

Push to `main` triggers: tests (Python 3.12) → SSH deploy to EC2.

- SSH as `ubuntu` → `sudo -u django` for deploy operations
- Env vars come from AWS SSM (`/pushit/prod/*`), fetched into `/run/pushit/.env` at boot — the deploy no longer writes a `.env` or uses `DOTENV_PROD`
- Integration tests (`@pytest.mark.integration`) are excluded from CI (`-m "not integration"`)

### Adding / changing an environment variable — IMPORTANT

The source of truth for prod env vars is **AWS SSM Parameter Store**
(`/pushit/prod/*`, region `eu-west-1`), **not** a `.env` on the server. At boot
`pushit-env-fetch.service` runs `deploy/fetch-env-from-ssm.sh`, which reads SSM
via the EC2 instance role (IMDS, no keys on disk) and writes `/run/pushit/.env`
(tmpfs, `640 django:www-data`). gunicorn / celery / beat load it via
`EnvironmentFile=` + `Requires=pushit-env-fetch.service` (they refuse to start
if the fetch fails); `deploy.sh` sources the same file for `manage.py`.

To add or change a variable:

1. Add it to `.env_template` (the catalog of all vars).
2. If code reads it, add `settings.X = env("X", default=...)` to `config/settings/base.py`.
3. Decide String vs SecureString — secrets go in `SECRET_KEYS` inside `deploy/seed-parameter-store.{sh,ps1}`.
4. Seed SSM from your machine (needs `ssm:PutParameter`):
   `bash deploy/seed-parameter-store.sh ./prod.env` (or `.ps1` on Windows), or a single value with `aws ssm put-parameter`.

**The trap (same mechanism as QuizOnline):** `pushit-env-fetch` is
`Type=oneshot` + `RemainAfterExit=yes`, so it stays "active" after its first
run. A normal code deploy (`deploy.sh`) restarts gunicorn/celery but **does NOT
re-fetch** — the processes keep the old env. To apply a changed variable,
re-fetch explicitly, then restart the apps, in this order:

```bash
# 1. update SSM (one var, or bulk: bash deploy/seed-parameter-store.sh ./prod.env)
aws ssm put-parameter --name /pushit/prod/MY_VAR --value "..." \
    --type SecureString --overwrite --region eu-west-1
# 2. re-fetch -> rewrites /run/pushit/.env
sudo systemctl restart pushit-env-fetch
# 3. make the processes reload the new config
sudo systemctl restart pushit-api-gunicorn pushit-celery-worker pushit-celery-beat
```

> `--overwrite` does **not** change a parameter's Type. To promote a String to
> SecureString, `aws ssm delete-parameter` first, then re-seed.

**IAM:** the EC2 instance role (shared with QuizOnline) must allow
`ssm:GetParametersByPath` on `arn:aws:ssm:eu-west-1:*:parameter/pushit/prod/*`
plus `kms:Decrypt` on the `aws/ssm` key. `/run` is tmpfs (cleared on reboot),
so the file is re-fetched every boot — SSM must be reachable then.

### Git permissions on the server

`deploy.sh` runs as `django` and does `git fetch origin main` /
`git reset --hard origin/main` inside `/var/www/django_websites/PushIT_server`.
If a previous git operation was performed by another user (root or ubuntu),
the new objects in `.git/objects` may be unwritable by `django` and the
fetch fails with *"insufficient permission for adding an object to repository
database .git/objects"*. Recover with:

```bash
sudo chown -R django:www-data /var/www/django_websites/PushIT_server/.git
sudo chmod -R g+w /var/www/django_websites/PushIT_server/.git
sudo -u django git -C /var/www/django_websites/PushIT_server config core.sharedRepository group
```

### Deploy files

- `deploy/nginx/pushit-api.conf` — nginx reverse proxy (`proxy_pass http://127.0.0.1:8001`, static/media)
- `deploy/gunicorn.conf.py` — Gunicorn config (TCP `127.0.0.1:8001`, 3 workers)
- `deploy/systemd/pushit-api-gunicorn.service` — Gunicorn service (`Restart=always`, `EnvironmentFile=/run/pushit/.env`)
- `deploy/systemd/pushit-env-fetch.service` — oneshot, fetches env from SSM at boot
- `deploy/fetch-env-from-ssm.sh` — SSM → `/run/pushit/.env` (run on server as root)
- `deploy/seed-parameter-store.sh` / `.ps1` — seed SSM `/pushit/prod/*` from a local `.env` (run from your machine)
- `deploy/systemd/pushit-celery-worker.service` — Celery worker (queue `pushit`, concurrency 2)
- `deploy/systemd/pushit-celery-beat.service` — Celery beat scheduler
- `deploy/setup-server.sh` — One-time server provisioning
- `deploy/deploy.sh` — Deploy script (pull, deps, source env, migrate, collectstatic, restart)
- `.github/workflows/deploy.yml` — CI/CD pipeline

### Server commands

```bash
# Check service status
sudo systemctl status pushit-api-gunicorn pushit-celery-worker pushit-celery-beat pushit-env-fetch

# View logs
journalctl -u pushit-api-gunicorn -f
journalctl -u pushit-env-fetch -f
journalctl -u pushit-celery-worker -f
tail -f /var/log/pushit/gunicorn-access.log
tail -f /var/log/nginx/pushit-error.log

# Manual deploy
sudo -u django /var/www/django_websites/PushIT_server/deploy/deploy.sh
```

## Current Limitations

- SQLite in dev/CI, PostgreSQL in prod (migrated 2026-06-02). CI tests run on SQLite, so the concurrency tests stay skipped there; the `SQLITE_TIMEOUT` busy-timeout setting only matters for SQLite (no-op on PostgreSQL).
- Celery eager mode in DEV/TEST — no actual async task processing
