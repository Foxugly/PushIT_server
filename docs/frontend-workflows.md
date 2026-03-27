# Frontend Workflows

This document complements Swagger. It focuses on the sequence of calls a frontend is expected to make and the main business rules attached to each step.

## Authentication

### User session

1. `POST /api/v1/auth/register/`
2. `POST /api/v1/auth/login/`
3. Store `access` and send `Authorization: Bearer <token>` on user endpoints.

### Application token

1. `POST /api/v1/apps/` or `POST /api/v1/apps/{app_id}/regenerate-token/`
2. Store the raw token server-side only.
3. Send `X-App-Token: <token>` on application endpoints.

## Application setup

### Create an application

- `POST /api/v1/apps/`
- Response contains `id`, metadata, and `app_token` on initial creation.

### Manage quiet periods

- List: `GET /api/v1/apps/{app_id}/quiet-periods/`
- Create: `POST /api/v1/apps/{app_id}/quiet-periods/`
- Detail: `GET /api/v1/apps/{app_id}/quiet-periods/{quiet_period_id}/`
- Update: `PATCH /api/v1/apps/{app_id}/quiet-periods/{quiet_period_id}/`
- Delete: `DELETE /api/v1/apps/{app_id}/quiet-periods/{quiet_period_id}/`

Business rules:
- A quiet period is an absolute `[start_at, end_at]` window.
- `end_at` must be strictly after `start_at`.
- If a notification should be sent during an active quiet period, backend reschedules it to the quiet period end.
- Creating or updating a quiet period does not retroactively rewrite notification `scheduled_for`.
- Frontend should read `effective_scheduled_for` on notifications to know the next effective dispatch time according to the current quiet periods.

## Devices

### Link a device

- `POST /api/v1/devices/link/`
- Auth: `X-App-Token`

Expected payload:

```json
{
  "device_name": "Samsung S24",
  "platform": "android",
  "push_token": "token_123456789012345678901234567890"
}
```

## Notifications

### Create an immediate notification

- `POST /api/v1/notifications/`
- Auth: Bearer
- Omit `scheduled_for` or send `null`

Then queue it manually:

- `POST /api/v1/notifications/{notification_id}/send/`

Possible business errors:
- `409 notification_not_sendable`
- `503 notification_queue_unavailable`

### Create a scheduled notification

- `POST /api/v1/notifications/`
- Auth: Bearer
- Provide a future `scheduled_for`

Result:
- notification is created with `status=scheduled`
- it appears in `GET /api/v1/notifications/future/`
- `scheduled_for` is the requested date
- `effective_scheduled_for` is the effective date after applying the currently known quiet periods

### Edit or delete a future notification

- List: `GET /api/v1/notifications/future/`
- Detail: `GET /api/v1/notifications/future/{id}/`
- Update: `PATCH /api/v1/notifications/future/{id}/`
- Delete: `DELETE /api/v1/notifications/future/{id}/`

Optional list filters:
- `effective_scheduled_from`
- `effective_scheduled_to`
- `has_quiet_period_shift=true|false`
- `ordering=effective_scheduled_for` or `ordering=-effective_scheduled_for`

These filters apply to `effective_scheduled_for`, not to raw `scheduled_for`.

Concrete examples:
- `GET /api/v1/notifications/future/?has_quiet_period_shift=true`
- `GET /api/v1/notifications/future/?effective_scheduled_from=2026-03-28T00:00:00Z&effective_scheduled_to=2026-03-28T23:59:59Z`
- `GET /api/v1/notifications/future/?ordering=-effective_scheduled_for`

Business rules:
- These endpoints only work for notifications still in the future.
- Updating a future notification keeps it in `status=scheduled`.
- If a quiet period is added later, `scheduled_for` may stay unchanged while `effective_scheduled_for` moves.
- Once queued or already sent, the notification leaves this workflow.

### Create notifications via app token

- `POST /api/v1/notifications/app/create/`
- Auth: `X-App-Token`
- Required header: `Idempotency-Key`

If the same idempotency key is reused:
- same payload: backend returns the existing notification
- different payload: backend returns `409 idempotency_conflict`

Application-side listing:
- `GET /api/v1/notifications/app/`
- supports `status`
- supports `effective_scheduled_from`
- supports `effective_scheduled_to`
- supports `has_quiet_period_shift=true|false`
- supports `ordering=effective_scheduled_for` or `ordering=-effective_scheduled_for`

Concrete examples:
- `GET /api/v1/notifications/app/?status=scheduled&has_quiet_period_shift=true`
- `GET /api/v1/notifications/app/?ordering=-effective_scheduled_for`

User-side full listing:
- `GET /api/v1/notifications/`
- supports `application_id`
- supports `status`
- supports `effective_scheduled_from`
- supports `effective_scheduled_to`
- supports `has_quiet_period_shift=true|false`
- supports `ordering=effective_scheduled_for` or `ordering=-effective_scheduled_for`

Concrete examples:
- `GET /api/v1/notifications/?application_id=12&status=scheduled&has_quiet_period_shift=true`
- `GET /api/v1/notifications/?ordering=-effective_scheduled_for`

## Statuses

Main statuses exposed to frontend:
- `draft`: created but not queued yet
- `scheduled`: waiting for `scheduled_for`
- `queued`: accepted by backend queue
- `processing`: currently being delivered
- `sent`: completed successfully
- `partial`: partially delivered
- `failed`: delivery failed

Frontend implications:
- show edit/delete actions only for future notifications
- do not expose manual send for a notification still scheduled in the future
- display `effective_scheduled_for` as the operational send date when present
- surface backend `code` values directly for operational errors

## Error contract

Simple errors:

```json
{
  "code": "some_error_code",
  "detail": "Readable message"
}
```

Validation errors:

```json
{
  "code": "validation_error",
  "detail": "Validation error.",
  "errors": {
    "field_name": [
      "..."
    ]
  }
}
```
