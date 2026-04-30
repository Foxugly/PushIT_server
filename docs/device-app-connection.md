# Device app connection workflow

This document describes how a client application registers a logged-in device so it can receive Firebase Cloud Messaging notifications from PushIT.

## Overview

A device must be authenticated as a PushIT user before it can be linked to an application.

The app token does not replace user authentication. It only identifies the PushIT application the device wants to subscribe to.

Required sequence:

1. Log in with user credentials and obtain a JWT access token.
2. Obtain a Firebase Cloud Messaging push token on the device.
3. Identify the authenticated device and read the applications already linked to it.
4. If needed, link the authenticated device to a new PushIT application with an app token.
5. Receive notifications through Firebase using the registered push token.

## Base URL

Development API base URL:

```text
http://127.0.0.1:8000/api/v1
```

Production should use the deployed PushIT API origin.

## 1. User login

Endpoint:

```http
POST /api/v1/auth/login/
Content-Type: application/json
```

Request:

```json
{
  "email": "user@example.com",
  "password": "VeryStr0ngPassword123!"
}
```

Successful response:

```json
{
  "access": "jwt_access_token",
  "refresh": "jwt_refresh_token",
  "user": {
    "id": 1,
    "email": "user@example.com",
    "username": "user",
    "userkey": "usr_xxxxxxxxxxxx",
    "is_active": true,
    "language": "FR"
  }
}
```

Store `access` for authenticated API calls. Use:

```http
Authorization: Bearer jwt_access_token
```

Use `refresh` with `/api/v1/auth/refresh/` when the access token expires.

## 2. Get an app token

The app token is created by the PushIT application owner when creating or regenerating a PushIT application.

Initial app creation:

```http
POST /api/v1/apps/
Authorization: Bearer jwt_access_token
Content-Type: application/json
```

Request:

```json
{
  "name": "My App"
}
```

Response contains the raw app token once:

```json
{
  "id": 10,
  "name": "My App",
  "app_token_prefix": "apt_12345678",
  "app_token": "apt_1234567890abcdef...",
  "is_active": true
}
```

If the raw token is lost, regenerate it:

```http
POST /api/v1/apps/{app_id}/regenerate-token/
Authorization: Bearer jwt_access_token
```

The token starts with `apt_`.

## 3. Obtain the FCM push token

The client app must obtain a Firebase Cloud Messaging token using the platform Firebase SDK.

The backend expects that token as `push_token`.

Supported platform values today:

```text
android
ios
```

For the browser fake device, the tool currently registers as `android` because there is no `web` platform value yet.

## 4. Identify the authenticated device

Call this endpoint after login and after obtaining the FCM token.

It creates or updates the device for the authenticated user and returns the active PushIT applications already linked to this device.

Endpoint:

```http
POST /api/v1/devices/identify/
Authorization: Bearer jwt_access_token
Content-Type: application/json
```

Request:

```json
{
  "device_name": "Samsung S24",
  "platform": "android",
  "push_token": "fcm_registration_token"
}
```

Response for a new device:

```json
{
  "status": "ok",
  "device_id": 42,
  "device_created": true,
  "linked_applications": []
}
```

Response for a known device already linked to apps:

```json
{
  "status": "ok",
  "device_id": 42,
  "device_created": false,
  "linked_applications": [
    {
      "id": 10,
      "name": "My App",
      "description": "Production alerts",
      "is_active": true,
      "linked_at": "2026-04-30T10:15:00Z"
    }
  ]
}
```

Use `linked_applications` to decide whether the user already receives notifications for the desired app.

If the same FCM `push_token` was previously associated with another PushIT user, the backend assigns the device to the current authenticated user and clears the previous active app links before returning the response. This prevents one user from seeing another user's device/app associations.

## 5. Link the authenticated device to a new app

Endpoint:

```http
POST /api/v1/devices/link/
Authorization: Bearer jwt_access_token
Content-Type: application/json
```

Request:

```json
{
  "app_token": "apt_1234567890abcdef...",
  "device_name": "Samsung S24",
  "platform": "android",
  "push_token": "fcm_registration_token"
}
```

Successful response:

```json
{
  "status": "ok",
  "device_id": 42,
  "device_created": true,
  "link_created": true,
  "application_id": 10
}
```

Meaning:

- `device_id`: PushIT device id.
- `device_created`: `true` when this push token created a new device.
- `link_created`: `true` when this is the first link between this device and app.
- `application_id`: PushIT application identified by the app token.

The backend stores:

- the authenticated user on the `Device`;
- the FCM token on the `Device`;
- a `DeviceApplicationLink` between the device and the PushIT application.

After a successful link, the app can call `/api/v1/devices/identify/` again to refresh the list of linked applications.

## 6. Receive notifications

After linking, the device is eligible to receive notifications for that application.

When a notification is sent to the application, PushIT resolves active linked devices and sends the notification through Firebase to each device `push_token`.

The app does not poll PushIT to receive the push. It listens through Firebase Cloud Messaging.

## Token refresh and re-identification

The client should call `/api/v1/devices/identify/` again when:

- the FCM token changes;
- the app is reinstalled;
- the user logs in on a new device;
- notification permissions are reset;
- the app wants to refresh `last_seen_at`.

The identify endpoint is idempotent by `push_token`: it updates the existing device metadata and returns the current linked apps.

Call `/api/v1/devices/link/` only when the user provides a new `app_token` or explicitly wants to reconnect a previously inactive app link.

## Auth rules

Device identification requires:

- a valid user JWT in `Authorization: Bearer ...`.

Device linking requires both:

- a valid user JWT in `Authorization: Bearer ...`;
- a valid app token in the JSON payload.

`X-App-Token` is still used for server-to-server application endpoints such as:

```http
POST /api/v1/notifications/app/create/
GET /api/v1/notifications/app/
```

Do not use `X-App-Token` alone to link a device.

## Common errors

Missing or invalid user JWT:

```json
{
  "code": "not_authenticated",
  "detail": "Authentication credentials were not provided."
}
```

Missing app token:

```json
{
  "code": "app_token_missing",
  "detail": "Missing app token."
}
```

Invalid app token format:

```json
{
  "code": "app_token_invalid_format",
  "detail": "Invalid app token format."
}
```

Inactive application:

```json
{
  "code": "app_token_inactive",
  "detail": "Inactive application."
}
```

Revoked token:

```json
{
  "code": "app_token_revoked",
  "detail": "App token has been revoked."
}
```

Invalid payload:

```json
{
  "code": "validation_error",
  "detail": "Validation error.",
  "errors": {
    "push_token": [
      "Ensure this field has at least 20 characters."
    ]
  }
}
```

## Minimal curl example

Login:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"VeryStr0ngPassword123!"}'
```

Identify device:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/devices/identify/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer jwt_access_token" \
  -d '{
    "device_name": "Samsung S24",
    "platform": "android",
    "push_token": "fcm_registration_token"
  }'
```

Link device to an app:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/devices/link/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer jwt_access_token" \
  -d '{
    "app_token": "apt_1234567890abcdef...",
    "device_name": "Samsung S24",
    "platform": "android",
    "push_token": "fcm_registration_token"
  }'
```

## Mobile implementation checklist

- [ ] User can log in and store `access` / `refresh`.
- [ ] App refreshes the access token when needed.
- [ ] App asks for notification permission.
- [ ] App obtains an FCM registration token.
- [ ] App calls `/api/v1/devices/identify/` with Bearer JWT and FCM token.
- [ ] App displays or stores the returned `linked_applications`.
- [ ] App receives or scans the PushIT `app_token`.
- [ ] App calls `/api/v1/devices/link/` with Bearer JWT and JSON `app_token`.
- [ ] App re-identifies when FCM rotates the token.
- [ ] App handles foreground and background FCM messages.
- [ ] App surfaces API error `code` values for support/debugging.
