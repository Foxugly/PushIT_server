# Prompt: PushIT Kotlin Multiplatform Client

## Context

I have an existing backend API called **PushIT Server** — a Django REST API that manages push notifications via Firebase Cloud Messaging. The backend is fully functional and deployed. I need to build a **Kotlin Multiplatform (KMP)** mobile client that works on Android and iOS.

The full OpenAPI schema is available at `http://127.0.0.1:8000/api/schema/` (or attached as `schema.yaml`).

## Goal

Build a KMP mobile app (Web + Android + iOS) that:

1. **Registers a user** and authenticates via JWT
2. **Receives push notifications** via Firebase Cloud Messaging
3. **Sends the FCM device token** to the PushIT backend
4. **Displays received notifications** in a list

## Backend API — Key Endpoints

Base URL: `http://127.0.0.1:8000/api/v1/`

### Authentication (JWT)

- `POST /auth/register/` — `{"email", "username", "password"}` → `201` with user profile
- `POST /auth/login/` — `{"email", "password"}` → `200` with `{"access", "refresh", "user"}`
- `POST /auth/refresh/` — `{"refresh"}` → `200` with `{"access"}`
- `POST /auth/logout/` — `{"refresh"}` → `204`
- `GET /auth/me/` — returns current user profile

All authenticated endpoints use `Authorization: Bearer <access_token>`.

### Device Registration (App Token auth)

- `POST /devices/link/` — links a device to an application
  - Header: `X-App-Token: apt_...` (server-to-server token, but also used by the mobile app to self-register)
  - Body: `{"push_token": "<FCM registration token>", "platform": "android"|"ios", "device_name": "Samsung S24"}`
  - Response: `{"status": "ok", "device_id": 1, "device_created": true, "link_created": true, "application_id": 1}`

### Notifications (read-only for the mobile app)

- `GET /notifications/` — list notifications (JWT auth, paginated)
  - Query params: `?application_id=`, `?status=`, `?page=`
  - Response: `{"count", "next", "previous", "results": [...]}`
- `GET /notifications/{id}/` — notification detail
- `GET /notifications/stats/` — notification count by status

### Application Management (for the admin/dashboard screens)

- `GET /apps/` — list user's applications
- `POST /apps/` — create application → returns `app_token` (show once)
- `PATCH /apps/{id}/` — update name, description, webhook_url
- `GET /apps/{id}/` — application detail

## Architecture Requirements

### Kotlin Multiplatform

- **Shared module** (`shared/`): API client, data models, ViewModels (or equivalent)
- **Android app** (`androidApp/`): Jetpack Compose UI, Firebase Messaging integration
- **iOS app** (`iosApp/`): SwiftUI, Firebase Messaging integration

### Networking

- Use **Ktor** for HTTP client (KMP-compatible)
- Store JWT tokens securely (Android Keystore / iOS Keychain)
- Auto-refresh access token when expired (401 → refresh → retry)

### Firebase Cloud Messaging

- **Android**: Add `google-services.json` from Firebase Console, implement `FirebaseMessagingService` to capture `onNewToken()` and `onMessageReceived()`
- **iOS**: Add `GoogleService-Info.plist`, register for remote notifications, implement `UNUserNotificationCenterDelegate`
- On token refresh, call `POST /devices/link/` with the new token

### Screens (minimal MVP)

1. **Login / Register** — email + password form, stores JWT tokens
2. **Notification List** — paginated list from `GET /notifications/`, pull-to-refresh
3. **Notification Detail** — title, message, status, timestamps
4. **Settings** — show current user, app token info, logout button

### Data Flow

```
App Start
  → Check stored JWT → valid? → go to Notification List
                      → expired? → try refresh → fail? → Login screen

Login/Register
  → POST /auth/login/ or /auth/register/
  → Store access + refresh tokens
  → Request FCM token from Firebase SDK
  → POST /devices/link/ with X-App-Token header
  → Navigate to Notification List

FCM Token Refresh (onNewToken)
  → POST /devices/link/ with new push_token

Push Notification Received
  → Display system notification
  → If app is in foreground, refresh notification list
```

## Firebase Setup

The app and the backend share the **same Firebase project**:
- Backend uses the **service account JSON** (`FCM_SERVICE_ACCOUNT_PATH`) to send notifications
- Mobile apps use **google-services.json** (Android) / **GoogleService-Info.plist** (iOS) to receive them

## What NOT to Build

- No notification creation from the mobile app (that's done via the web dashboard or API)
- No quiet period management from mobile
- No template management from mobile
- No admin features — this is a notification receiver app

## Technical Stack

- Kotlin 2.x, KMP
- Ktor for networking
- Kotlinx.serialization for JSON
- Jetpack Compose (Android), SwiftUI (iOS)
- Firebase Cloud Messaging SDK
- Gradle with version catalogs

## Deliverables

1. Working KMP project with shared networking layer
2. Android app that receives push notifications and registers with PushIT
3. iOS app (same features)
4. Web app (same features)
5. README with setup instructions (Firebase config, backend URL)
