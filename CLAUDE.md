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

Key env vars: `STATE`, `SECRET_KEY`, `ALLOWED_HOSTS`, `DB_ENGINE`, `DB_NAME`, `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD` (fleet DB_* convention, OPERATIONS.md §3.13), `REDIS_URL`, `FCM_SERVICE_ACCOUNT_PATH`, `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`, `GRAPH_SENDER` (canonical fleet name, §3.14 — read into `settings.GRAPH_MAILBOX_USER_ID`), `INBOUND_EMAIL_DOMAIN`, `METRICS_AUTH_TOKEN`. See `.env_template` for the full list.

**DEV/TEST behavior:** Celery runs eagerly (synchronous, no broker needed), passwords use MD5 for speed. Graph API calls are silently skipped when `GRAPH_CLIENT_ID` is empty. FCM uses a mock when `FCM_SERVICE_ACCOUNT_PATH` is empty.

**PROD enforcement:** `SECRET_KEY` and `ALLOWED_HOSTS` are mandatory — startup fails if missing. Full HSTS, SSL redirect, secure cookies are enabled.

`DB_SUPPORTS_ROW_LOCKING` is derived from the database engine — `True` for PostgreSQL, `False` for SQLite. This controls whether `select_for_update()` is used in the notification send flow.

## Architecture

### Django Apps

- **accounts** — Custom User model (email as username), JWT auth (login/register/refresh/logout), profile management. `/me/` exposes read-only `is_staff` + `is_superuser` so the SPA can gate its admin area.
- **applications** — Application CRUD, app token generation (`apt_` prefix, SHA256 hashed), quiet period scheduling, QR code generation for device onboarding, webhook URL configuration (SSRF-guarded, see `url_safety.py`), and the **inbound email alias** lifecycle (provisioning is delegated to the `exchange` app; `GET apps/<id>/alias-status/` lets the owner verify the alias is live in Exchange)
- **exchange** — Exchange Online alias management via a PowerShell Core script (`scripts/exchange/manage_alias.ps1`) executed with `subprocess.run`. `services.ExchangeAliasService` has `add_alias`/`remove_alias`/`list_aliases`; `integration.py` wires it into `Application` save/delete (`provision_alias_for_application` / `deprovision_alias_for_application`) and exposes `is_configured()` + `alias_status(alias_email)` (used by the alias-status endpoint). Swallows non-critical failures so a save never blocks on an Exchange outage.
- **devices** — Device registration (push tokens), platform tracking (Android/iOS), linking devices to applications via app token
- **notifications** — Core business logic: notification creation (with idempotency keys), templates with `{{variable}}` substitution, scheduling, quiet period shifting, delivery tracking, retry logic, bulk send, webhook callbacks, inbound email ingestion with auto-reply
- **health** — Public liveness (`/health/live/`), readiness (`/health/ready/`), Prometheus metrics (`/health/metrics/`), **plus** the staff-gated `GET /api/v1/admin/status/` (`api_views.py`, `IsAdminUser`) aggregating DB / Celery broker / Celery workers / Exchange health + cheap metrics for the SPA admin panel
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

`applications/graph_mail.py` handles **inbox polling and email sending** via Graph API (MSAL client credentials flow):
- `fetch_unread_emails` / `mark_email_read` — poll the shared mailbox for inbound emails (also fetches `internetMessageHeaders` → `authentication_results` for the optional inbound-DMARC check)
- `send_email` — send auto-reply when a known user emails an unknown address

**Alias management lives in the `exchange` app, not here** (it moved off Graph `proxyAddresses` onto the PowerShell `ExchangeAliasService`). `Application.save()` calls `exchange.integration.provision_alias_for_application`, `Application.delete()` calls `deprovision_alias_for_application`, and the owner can verify an alias is live via `GET apps/<id>/alias-status/` (→ `exchange.integration.alias_status`, which lists the mailbox aliases and checks membership). Inbox polling is driven by `notifications/inbound_mailbox.py`.

### Sessions & admin access

- **Long-lived sessions ("stay logged in" like WhatsApp).** `SIMPLE_JWT.REFRESH_TOKEN_LIFETIME` defaults to **365 days** (`JWT_REFRESH_DAYS`). With `ROTATE_REFRESH_TOKENS` + `BLACKLIST_AFTER_ROTATION`, every `auth/refresh/` issues a new refresh token and blacklists the old one, sliding the window — so any client (web SPA + mobile) that opens the app within the window stays logged in indefinitely. **Every client MUST persist the rotated refresh token** or it self-ejects on the next refresh. `flush_expired_tokens_task` (beat, 03:30 daily) prunes the outstanding/blacklisted token tables so they stay bounded. Security trade-off: a stolen refresh is valid up to the window — shorten `JWT_REFRESH_DAYS` if needed.
- **Django admin.** Enabled at the backend origin: `https://pushit-api.foxugly.com/admin/` (session/CSRF cookies + `/static/admin/` already served there). For convenience `pushit.foxugly.com/admin` **301-redirects** to it (nginx, in the frontend repo's vhost) — a redirect rather than a reverse proxy so admin cookies stay on one origin. The SPA admin area also links to it.
- **Admin status panel.** `GET /api/v1/admin/status/` (`IsAdminUser`) backs the SPA's `dashboard/admin` page: per-dependency checks (DB / Celery broker / Celery workers / Exchange) + metrics, each isolated so one failure degrades rather than 500s.

### Firebase Cloud Messaging

`notifications/push.py` sends push notifications via the Firebase Admin SDK when `FCM_SERVICE_ACCOUNT_PATH` is configured. Falls back to a mock provider when unconfigured. Maps Firebase exceptions to `InvalidPushTokenError` / `TemporaryPushProviderError` / `PushProviderError`.

### Celery Tasks (beat schedule)

Every minute:
- `dispatch_scheduled_notifications_task` — picks up SCHEDULED notifications ready to send
- `retry_pending_deliveries_task` — retries failed deliveries (3 attempts, exponential backoff)
- `poll_inbound_mailbox_task` — Graph API polling for inbound email notifications
- `requeue_stuck_processing_notifications_task` — watchdog: resets notifications stranded in PROCESSING (worker recycle/crash mid-send) older than `NOTIFICATION_PROCESSING_STUCK_MINUTES` (default 15) back to QUEUED; re-dispatch is delivery-idempotent (`processing_started_at` gates it)

Daily:
- `flush_expired_tokens_task` (03:30) — prunes expired SimpleJWT outstanding/blacklisted tokens (kept bounded despite rotation + long refresh lifetimes)

Worker children recycle to bound memory on the shared EC2: `CELERY_WORKER_MAX_TASKS_PER_CHILD` (default 200) and `CELERY_WORKER_MAX_MEMORY_PER_CHILD` (KB, default ~195 MB) turn a slow leak or a fat task into a graceful recycle instead of a kernel OOM SIGKILL. The Celery worker/beat run as the systemd units `pushit-api-celery` / `pushit-api-celery-beat`.

> **Not in SSM (by design):** these two vars are intentionally left out of `/pushit/prod/*`. Their `base.py` defaults already encode the prod-intended values, so prod picks them up with nothing seeded. Add them to SSM only if you need to tune the memory ceiling in prod *without a code deploy* — they are non-secret (`String` type, in the defaults block of `seed-parameter-store.*`, not `SECRET_KEYS`), and after seeding you must re-fetch + restart (`systemctl restart pushit-env-fetch pushit-api-celery pushit-api-celery-beat`) for the worker to reload. Listing them in `.env_template` is just catalog documentation and does not imply they must be seeded.

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
- **Database:** PostgreSQL 16 (local on the shared EC2) — database `pushit`, role `pushit`, connected over `127.0.0.1:5432`. Credentials in SSM via the fleet **`DB_*` 6-var convention** (`DB_ENGINE`/`DB_NAME`/`DB_USER`/`DB_PASSWORD`/`DB_HOST`/`DB_PORT`, password as SecureString; OPERATIONS.md §3.13). Migrated from SQLite on 2026-06-02 (dump/loaddata).

### CI/CD (GitHub Actions)

Push to `main` triggers: tests (Python 3.12) → **OIDC → SSM deploy** (no long-lived SSH key).

- The deploy job runs under `environment: production`, assumes the per-repo IAM role
  `pushit-deploy` via GitHub OIDC, then `aws ssm send-command` runs `sudo -u django deploy.sh`
  on the EC2 instance (AWS-RunShellScript runs the command as root; deploy.sh does the work as django).
- Env vars come from AWS SSM (`/pushit/prod/*`), fetched into `/run/pushit/.env` at boot — the deploy no longer writes a `.env` or uses `DOTENV_PROD`
- Integration tests (`@pytest.mark.integration`) are excluded from CI (`-m "not integration"`)
- *History:* migrated SSH-action → OIDC→SSM on 2026-06-03; dropped the `EC2_SSH_KEY` / `EC2_HOST` / `EC2_USER` / `DOTENV_PROD` secrets (now only `AWS_DEPLOY_ROLE_ARN` + `EC2_INSTANCE_ID`). Fleet model: **OPERATIONS.md §3.11**.

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
sudo systemctl restart pushit-api-gunicorn pushit-api-celery pushit-api-celery-beat
```

> `--overwrite` does **not** change a parameter's Type. To promote a String to
> SecureString, `aws ssm delete-parameter` first, then re-seed.

**IAM (two distinct roles):**
- **Instance role** (`foxugly-fleet-ec2`, shared) — used by `pushit-env-fetch` at boot: must allow
  `ssm:GetParametersByPath` on `arn:aws:ssm:eu-west-1:*:parameter/pushit/prod/*` plus `kms:Decrypt`
  on the `aws/ssm` key. `/run` is tmpfs (cleared on reboot), so the file is re-fetched every boot.
- **Deploy role** (`pushit-deploy`, OIDC) — used by the GitHub Actions deploy: trust pinned to
  `repo:Foxugly/PushIT_server:environment:production`, perms `ssm:SendCommand` (instance +
  `AWS-RunShellScript` doc) + `ssm:GetCommandInvocation` only. See OPERATIONS.md §3.11.

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

### File permissions convention

The deployed tree under `/var/www/django_websites/PushIT_server` is owned by
`django:www-data` and must follow: **dirs `750`, files `640`, no "other" perms,
no group-write** (nginx serves static/media as the `www-data` *group*, so it
only needs group `r`/`x` — never "other" or group-write). This is enforced at
three layers so a stray `umask 022` from a build/`pip`/`git` run can't leave
~hundreds of world-readable `644` files behind:

1. **At creation** — `deploy.sh` and `setup-server.sh` set `umask 027` at the
   top (before `git`/`pip`/`collectstatic`). In `setup-server.sh` the
   `sudo -u django` steps inherit it via sudo's default umask *union*.
2. **At deploy end** — `deploy.sh` normalizes idempotently after
   `collectstatic`: `chown -R django:www-data` then `chmod -R g-w,o-rwx`.
   **No `sudo`**: `deploy.sh` runs as `django`, which owns the tree (primary
   group `www-data`), so it can chgrp/chmod its own files — do **not** add a
   broad `chown`/`chmod` rule to the `pushit-deploy` sudoers. Owner bits are
   left intact, so execute is preserved on `.venv/bin`, `manage.py`, etc.
   `setup-server.sh` runs the same step via the provisioning user's (`ubuntu`)
   own `sudo`.
3. **At runtime** — `UMask=0027` in the `[Service]` block of
   `pushit-api-{gunicorn,celery,celery-beat}.service` keeps runtime-created
   files (`__pycache__`, `celerybeat-schedule`, caches) conformant. Units live
   in `/etc/systemd/system`, so changes need a manual root step
   (`cp` → `daemon-reload` → `restart`); a code deploy does **not** apply them.

> A code deploy that *changes* `deploy.sh` runs the **old** script that turn
> (bash reads it before its own `git reset`), so umask/normalization take effect
> on the *next* deploy. To fix existing drift immediately, run as root:
> `sudo chmod -R g-w,o-rwx /var/www/django_websites/PushIT_server`, then verify
> `sudo find <tree> ! -type l -perm /007 | wc -l` and `-perm /020` both report `0`.

> **Note vs. the git-recovery above:** that recovery sets `.git` group-write
> (`g+w` + `core.sharedRepository group`) for a broken multi-user state; the
> normalization strips it again. That's fine — `django` is the sole writer in
> normal operation, so `.git` does not need group-write. `/run/pushit` (`750
> root:www-data`) and the `pushit-deploy` sudoers are governed separately and
> are out of scope for this normalization.

### Deploy files

- `deploy/nginx/pushit-api.conf` — nginx reverse proxy (`proxy_pass http://127.0.0.1:8001`, static/media)
- `deploy/gunicorn.conf.py` — Gunicorn config (TCP `127.0.0.1:8001`, 3 workers)
- `deploy/systemd/pushit-api-gunicorn.service` — Gunicorn service (`Restart=always`, `EnvironmentFile=/run/pushit/.env`)
- `deploy/systemd/pushit-env-fetch.service` — oneshot; `ExecStart=/usr/local/sbin/pushit-env-fetch.sh` (root-owned, §3.10), fetches env from SSM at boot
- `deploy/fetch-env-from-ssm.sh` — versioned **source** for that script; installed `root:root 0755` to `/usr/local/sbin/pushit-env-fetch.sh` out-of-band (never executed from the django-writable tree)
- `deploy/seed-parameter-store.sh` / `.ps1` — seed SSM `/pushit/prod/*` from a local `.env` (run from your machine)
- `deploy/systemd/pushit-api-celery.service` — Celery worker (queue `pushit`, concurrency 2)
- `deploy/systemd/pushit-api-celery-beat.service` — Celery beat scheduler
- `deploy/setup-server.sh` — One-time server provisioning
- `deploy/deploy.sh` — Deploy script (pull, deps, source env, migrate, collectstatic, normalize perms, restart)
- `.github/workflows/deploy.yml` — CI/CD pipeline

### Server commands

```bash
# Check service status
sudo systemctl status pushit-api-gunicorn pushit-api-celery pushit-api-celery-beat pushit-env-fetch

# View logs
journalctl -u pushit-api-gunicorn -f
journalctl -u pushit-env-fetch -f
journalctl -u pushit-api-celery -f
tail -f /var/log/pushit/gunicorn-access.log
tail -f /var/log/nginx/pushit-error.log

# Manual deploy
sudo -u django /var/www/django_websites/PushIT_server/deploy/deploy.sh
```

## Current Limitations

- SQLite in dev/CI, PostgreSQL in prod (migrated 2026-06-02). CI tests run on SQLite, so the concurrency tests stay skipped there; the `SQLITE_TIMEOUT` busy-timeout setting only matters for SQLite (no-op on PostgreSQL).
- Celery eager mode in DEV/TEST — no actual async task processing
