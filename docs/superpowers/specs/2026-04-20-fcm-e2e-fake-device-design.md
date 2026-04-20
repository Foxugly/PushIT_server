# FCM E2E fake device — design

**Date:** 2026-04-20
**Status:** Draft — awaiting user review
**Scope:** Add a Python/browser-based "fake device" that registers a real FCM token and receives real push notifications from PushIT, without needing a smartphone or native app.

## Motivation

PushIT delivers notifications to Android/iOS devices via Firebase Cloud Messaging (FCM). Today there is no way to verify the full FCM delivery path end-to-end without a real mobile device. Mock-based testing (`FCM_SERVICE_ACCOUNT_PATH` empty, handled in `notifications/push.py`) covers the server-side lifecycle but never exercises Firebase's actual push delivery.

The goal is a lightweight tool that:

- **(A) Lets a developer manually verify** FCM delivery works end-to-end: create a notification in PushIT and see it pop up.
- **(C) Acts as a diagnostic tool** kept around to debug FCM issues (token, received payloads, service worker state).
- **(B) Enables future automated E2E assertion** (pytest polls a local endpoint to assert the notification arrived). Not built now, but architecturally enabled.

## Non-goals

- Replacing existing mock-based tests. The mock path stays.
- Production use. The tool runs on `localhost` for dev/debug only.
- Cross-browser testing matrix. Target Chrome/Edge (stable Web Push support).
- Native (Android/iOS) parity. The web push delivery path is similar enough to validate server-side correctness, but platform-specific rendering differences are out of scope.
- Automated pytest E2E. Only the hook (`/received` endpoint) is delivered; a real test comes in a later PR.

## Assumptions & prerequisites

- Firebase Web App is already registered in the Firebase console, with config (`apiKey`, `authDomain`, `projectId`, `messagingSenderId`, `appId`) and a VAPID key. Confirmed by user.
- The existing Firebase service account (`pushit-dcf8a-firebase-adminsdk-fbsvc-*.json`) is used by the Django server and unaffected.
- `django-cors-headers` is already installed and configured (`CORS_ALLOWED_ORIGINS` in `config/settings/base.py`). The fake device origin will be appended to this list via env var.
- `DevicePlatform` choices are `android` / `ios` (no `web`). The fake device registers as `android` — FCM's send path (`notifications/push.py::_send_fcm`) does not branch on platform, so this is acceptable without a schema migration.

## Architecture

```
scripts/fake_device/
├── server.py                       # Flask app — serves static files + /received endpoint
├── static/
│   ├── index.html                  # UI: token, app_token input, received list
│   └── app.js                      # Firebase Web SDK init, getToken, onMessage, auto-register
├── firebase-messaging-sw.js        # Service Worker (background messages). Served from "/" root.
├── config.example.json             # Template for Firebase web config + VAPID key
├── config.json                     # User-filled (gitignored)
├── test_server.py                  # Flask server unit tests
└── README.md                       # Setup + usage + smoke test checklist
```

`config.json` is added to `.gitignore`. The Firebase Web config contains public client identifiers (not secrets strictly speaking) but we still avoid committing them to keep the repo clean of project-specific identifiers.

## Components

### `server.py` (Flask, ~60 LOC)

Endpoints:

- `GET /` → `static/index.html`
- `GET /app.js` → `static/app.js`
- `GET /firebase-messaging-sw.js` → served from root (FCM requires the SW to be at site root to control the whole scope)
- `GET /firebase-config.json` → reads `config.json`, returns as JSON (so the JS/SW can fetch config at runtime rather than hardcoding it)
- `POST /received` → `{title, body, data, received_at, mode}` stored in in-memory list
- `GET /received` → JSON array of received notifications (for pytest / debug)
- `DELETE /received` → clears the list

CLI args:

- `--port` (default `8765`)
- `--api-base` (default `http://127.0.0.1:8000/api/v1`) — injected into `index.html` so the JS knows where PushIT is
- `--config` (default `./config.json`)

Startup behavior: if `config.json` missing or malformed, exit 1 with a clear message pointing to `config.example.json`.

### `static/index.html` (~80 LOC, vanilla)

- App token input (prefilled from `?app_token=` query string)
- "Register" button → triggers token fetch + device linking
- FCM token display with copy button
- Status area (permission state, registered device id)
- Live list of received notifications (title, body, timestamp, mode fg/bg)
- "Clear" button → `DELETE /received`

### `static/app.js` (~100 LOC)

- `fetch('/firebase-config.json')` → init Firebase app + messaging
- `navigator.serviceWorker.register('/firebase-messaging-sw.js')`
- `Notification.requestPermission()` → `getToken({vapidKey, serviceWorkerRegistration})`
- On "Register" click: `POST {api-base}/devices/link/` with `X-App-Token` header, body `{device_name: "Fake Web Device", platform: "android", push_token: <fcm_token>}`
- `onMessage(payload)` (foreground) → append to UI + `POST /received` with `mode: "foreground"`
- `BroadcastChannel('fake-device')` listener → append to UI when SW forwards a background message

### `firebase-messaging-sw.js` (~40 LOC)

- Imports Firebase SW libs from Google CDN
- Fetches `/firebase-config.json` at install time and initializes Firebase
- `onBackgroundMessage` → `self.registration.showNotification()` + `BroadcastChannel('fake-device').postMessage(payload)` + `fetch('/received', {method: 'POST', ...})` with `mode: "background"`

### `config.example.json`

```json
{
  "apiKey": "...",
  "authDomain": "pushit-dcf8a.firebaseapp.com",
  "projectId": "pushit-dcf8a",
  "messagingSenderId": "...",
  "appId": "...",
  "vapidKey": "..."
}
```

## Data flow

### 1. Page bootstrap
```
Browser → GET /                       (index.html)
       → GET /app.js
       → GET /firebase-config.json    (server reads local config.json)
       → register SW '/firebase-messaging-sw.js'
       → Notification.requestPermission()
       → getToken({vapidKey}) → FCM token
```

### 2. Auto-registration of fake device
```
Browser JS ──POST http://127.0.0.1:8000/api/v1/devices/link/
             X-App-Token: apt_xxx
             { device_name: "Fake Web Device",
               platform: "android",
               push_token: <fcm_token> }
         ← 200 { device_id, ... }
```
CORS preflight (`OPTIONS`) succeeds because `http://localhost:8765` is added to `CORS_ALLOWED_ORIGINS` via env var.

### 3. Foreground reception (tab active)
```
FCM → browser → onMessage(payload)
             → append to UI list
             → POST http://localhost:8765/received
                { title, body, data, received_at, mode: "foreground" }
```

### 4. Background reception (tab inactive/minimized)
```
FCM → browser → SW.onBackgroundMessage(payload)
             → self.registration.showNotification()       (OS-level notif)
             → BroadcastChannel('fake-device').postMessage(payload)
             → fetch POST /received { ..., mode: "background" }
```
The page, if still open, also appends via `BroadcastChannel`. If the page is closed, only `/received` and the OS notification record the event.

### 5. Pytest mode (future bonus B)
```
pytest → (assume Flask server and browser page are already running — user setup)
       → DELETE http://localhost:8765/received
       → POST /api/v1/notifications/ + /send/   (via existing API)
       → poll GET http://localhost:8765/received until match or timeout (10s)
       → assert title/body/data
```
Only the hook is delivered now; the actual pytest test is deferred.

## Error handling

### JS / Page
- **Permission denied**: red banner "Notifications refused — re-enable in browser settings". No retry loop.
- **`getToken()` failure** (bad VAPID key, SW not registered, bad Firebase config): show exception in page + console + "Retry" button. Do not attempt device registration.
- **`POST /devices/link/` failure**: show status code + body (most likely 401 on bad `app_token`). No retry, let user fix and click again.
- **Missing `app_token`**: disable "Register" button, show "Provide an app token".

### Service Worker
- **Malformed Firebase config**: SW logs error but stays registered (so the browser doesn't keep the previous broken SW). Page detects `navigator.serviceWorker.controller === null` after a timeout and shows a warning.
- **`fetch('/received')` fails in SW** (Flask server stopped): swallowed silently — the OS notification has already been shown; only the Flask-side log is lost.

### Flask server
- **`config.json` missing or malformed**: refuse to start, exit 1 with a clear message.
- **Port occupied**: let Flask raise its native error. No magic retry.
- **`/received` with malformed payload**: return 400 + `{error: "..."}`, don't crash.

### PushIT side (no change)
- FCM sending to a revoked/invalid token → `InvalidPushTokenError` (already handled in `notifications/push.py`). The fake device would simply "not receive anything" — the README documents that re-registering is needed if the FCM token changes.

Philosophy: **fail fast and loudly, show the error, let the human react.** This is a dev tool, not production.

## Testing

### Server tests (`scripts/fake_device/test_server.py`)

Unit tests using Flask's test client:

- `test_get_index_returns_html` — `GET /` returns 200 + `text/html`
- `test_firebase_config_endpoint_returns_json` — `GET /firebase-config.json` returns 200 + JSON
- `test_firebase_config_missing_exits_at_startup` — launching `server.py` without `config.json` fails cleanly (exit 1)
- `test_post_received_stores_and_get_returns` — POST a notification, GET returns the list
- `test_delete_received_clears_list` — DELETE empties the list
- `test_post_received_rejects_malformed_payload` — payload missing `title` → 400

### No JS / SW tests
Out of scope — would require a JS test harness disproportionate to the tool's purpose. Functional validation comes from manual usage (A+C).

### Manual smoke test (in README)

Checklist the developer runs once after setup:

1. `python scripts/fake_device/server.py` → page opens at `http://localhost:8765`
2. FCM token is displayed on the page
3. Device appears in PushIT (`GET /api/v1/devices/` as the owner)
4. Run `full_flow.py` (or create a notification targeting that device) → notification appears in the page (foreground)
5. Minimize the tab, trigger another notification → OS notification shows + entry appears in `/received`

### Dependencies & CI

The repo currently has only `requirements.txt` (no dev split). We introduce `requirements-dev.txt` containing `flask` (and pinning pytest versions already used). Prod deployment keeps using `requirements.txt` unchanged. The tests under `scripts/fake_device/` are not part of the main pytest run for the Django app; they can be invoked explicitly as `pytest scripts/fake_device/ -q`. This avoids pulling Flask into prod and avoids coupling server-side test failures to an unrelated tool.

## Known limitations / decisions

1. **Token freshness**: FCM tokens can rotate (permission re-granted, browser data cleared). Each page load re-registers and creates a new `Device` row. No dedup. Accepted — it's dev.
2. **Platform=`android`**: the fake device lies about its platform. Acceptable because FCM send is platform-agnostic at the provider call site.
3. **Single-user in-memory `/received`**: the Flask process keeps notifications in a Python list, reset on restart. No persistence. Fine for a dev tool.
4. **No auth on the Flask server**: assumed safe because it only binds to `localhost`. Document this in the README.
5. **Service Worker config leak**: the SW fetches `/firebase-config.json` at runtime. This is served by the local Flask server only — the config never leaves `localhost`.

## Out of scope (possible future work)

- Automated Playwright-based E2E test using the `/received` hook.
- Adding a `web` value to `DevicePlatform` choices (would require a small migration).
- Multiple concurrent fake devices (today: one browser tab = one device).
- Persistent `/received` log (SQLite file).
- HTTPS/production hosting of the fake device page.
