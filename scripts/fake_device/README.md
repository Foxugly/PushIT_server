# PushIT fake device

Browser-based fake device for end-to-end testing of the FCM delivery path without a smartphone.

## What it does

- Obtains a real FCM token via the Firebase Web SDK in your browser.
- Auto-registers itself in PushIT as a `Device` via `POST /api/v1/devices/link/`.
- Receives real push notifications:
  - **Foreground** (tab active): displayed in the page via `onMessage`.
  - **Background** (tab minimized): shown as an OS-level notification via the Service Worker.
- Logs every received notification to `GET /received` so tests or humans can inspect.

## Setup (one-time)

### 1. Firebase Web App

In the Firebase console for the `pushit-dcf8a` project:

1. Project settings -> General -> Your apps -> Add Web App. Copy the config object.
2. Project settings -> Cloud Messaging -> Web configuration -> Generate a VAPID key.

### 2. Local config

```bash
cp scripts/fake_device/config.example.json scripts/fake_device/config.json
```

Edit `config.json` with the values from step 1. This file is gitignored.

### 3. Install dev dependencies

```bash
pip install -r requirements-dev.txt
```

### 4. Allow CORS from localhost:8765 in Django

Add the fake device origin to your `.env`:

```
CORS_ALLOWED_ORIGINS=http://localhost:8000,http://localhost:8765
```

Restart the Django dev server so the new env var is picked up.

### 5. FCM service account in Django

Make sure `FCM_SERVICE_ACCOUNT_PATH` in your `.env` points to the real
`pushit-dcf8a-firebase-adminsdk-fbsvc-*.json` file. Without it, PushIT
uses the mock push provider and no real FCM delivery happens.

## Usage

### 1. Start the fake device server

```bash
python scripts/fake_device/server.py --port 8765
```

### 2. Get user credentials and an app token

Create or reuse a PushIT user account. Then create an application and copy its
`app_token` (starts with `apt_`).

### 3. Open the page

```
http://localhost:8765/?app_token=apt_xxx
```

- Accept the notification permission prompt.
- Wait for the FCM token to appear.
- Log in with the PushIT user credentials.
- Click **Register device**. Watch for "Registered as device ...".

### 4. Send a test notification

Use any API client; for example:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/notifications/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{"title": "Hello", "message": "From PushIT", "device_ids": [<device_id>]}'

curl -X POST http://127.0.0.1:8000/api/v1/notifications/<id>/send/ \
  -H "Authorization: Bearer <access_token>"
```

The notification should appear within a few seconds in the page (foreground) or as an OS notification (background).

## Smoke test checklist (manual)

Run this once after setup to confirm everything is wired correctly:

- [ ] `python scripts/fake_device/server.py` -- server starts, prints "serving on http://127.0.0.1:8765"
- [ ] Open `http://localhost:8765/?app_token=apt_xxx` -- FCM token is displayed on the page
- [ ] Log in with a PushIT user
- [ ] Click **Register device** -- status becomes "Registered as device ..."
- [ ] `GET http://127.0.0.1:8000/api/v1/devices/` lists the new "Fake Web Device"
- [ ] Send a notification targeting that device -- it appears in the page (foreground mode)
- [ ] Minimize the tab, send another notification -- an OS notification is shown AND a `background` entry appears in `GET http://localhost:8765/received`

## Troubleshooting

- **"Service Worker registration failed"**: make sure you are on `localhost` or HTTPS. `file://` does not work.
- **"getToken() failed"**: check the VAPID key in `config.json`, and that FCM is enabled on the Firebase project.
- **"Registration failed (401)"**: log in first, then verify the app token is valid. Regenerate via `POST /api/v1/apps/<id>/regenerate-token/` if needed.
- **"Registration failed (CORS)"**: add `http://localhost:8765` to `CORS_ALLOWED_ORIGINS` in your Django `.env` and restart.
- **No notifications arrive**: verify `FCM_SERVICE_ACCOUNT_PATH` is set in Django's `.env` (otherwise mock provider is used).
- **FCM token changed**: happens after browser data clear or permission reset. Re-click **Register device** to re-link.

## Running tests

```bash
pytest scripts/fake_device/test_server.py -q
```

The fake device tests are not included in the main CI run -- they exercise the Flask helper only.

## Known limitations

- Each page reload re-registers a new `Device` in PushIT (no dedup). Accepted in dev.
- The fake device declares `platform: "android"` (no `web` option in `DevicePlatform` today).
- Received notifications are kept in-memory in the Flask process only; restart clears them.
- Server binds to `localhost` -- do not expose to the network.
