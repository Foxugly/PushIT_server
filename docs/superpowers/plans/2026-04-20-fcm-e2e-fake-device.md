# FCM E2E fake device — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a browser-based fake device under `scripts/fake_device/` that registers a real FCM token with PushIT and displays incoming notifications, enabling E2E verification of the FCM delivery path without a smartphone.

**Architecture:** A small Flask server serves a static HTML/JS page + Firebase messaging Service Worker. The page uses the Firebase Web SDK to obtain a real FCM token, auto-registers itself as a Device in PushIT via `POST /api/v1/devices/link/`, and records incoming notifications (foreground via `onMessage`, background via the SW). The Flask server additionally exposes `/received` endpoints so tests or humans can inspect what arrived.

**Tech Stack:** Python 3.12, Flask 3.x, Firebase Web SDK (via CDN), vanilla JS, Service Worker API.

**Related spec:** `docs/superpowers/specs/2026-04-20-fcm-e2e-fake-device-design.md`

---

## Prerequisites (human, one-time, out of code)

The developer running this plan must already have:
- A registered Firebase Web App in the `pushit-dcf8a` Firebase project
- The Web config (`apiKey`, `authDomain`, `projectId`, `messagingSenderId`, `appId`)
- A VAPID key generated in Firebase console -> Project settings -> Cloud Messaging -> Web Push certificates

These go into `scripts/fake_device/config.json` at step `Task 7`.

---

## File structure

Files created by this plan:

```
scripts/fake_device/
|-- server.py                       # Flask app (load_config, create_app, main)
|-- static/
|   |-- index.html                  # UI
|   `-- app.js                      # Firebase Web SDK client
|-- firebase-messaging-sw.js        # Service Worker (must be served from root "/")
|-- config.example.json             # Template
|-- test_server.py                  # Flask unit tests
`-- README.md                       # Setup + usage

requirements-dev.txt                # New dev-only deps (flask)
.gitignore                          # Add scripts/fake_device/config.json
```

No modifications to Django app code. CORS setup is runtime-only (env var `CORS_ALLOWED_ORIGINS`), documented in README.

---

## Task 1: Scaffold directory structure and dev dependencies

**Files:**
- Create: `scripts/fake_device/` (directory)
- Create: `scripts/fake_device/static/` (directory)
- Create: `requirements-dev.txt`
- Modify: `.gitignore` (append rule for `config.json`)

- [ ] **Step 1: Create the directory structure**

Run:
```bash
mkdir -p scripts/fake_device/static
```

- [ ] **Step 2: Create `requirements-dev.txt`**

Create `requirements-dev.txt` with the following content:

```
-r requirements.txt
flask>=3.0,<4
```

- [ ] **Step 3: Add config.json ignore rule to `.gitignore`**

Append to `.gitignore`:

```
# Local-only Firebase Web config for scripts/fake_device/
scripts/fake_device/config.json
```

- [ ] **Step 4: Install the dev dependency locally**

Run:
```bash
pip install -r requirements-dev.txt
```

Expected: Flask 3.x installed. Other deps already satisfied.

- [ ] **Step 5: Commit**

```bash
git add requirements-dev.txt .gitignore
git commit -m "chore: add dev requirements file and ignore fake_device config"
```

---

## Task 2: Write failing Flask server tests (TDD)

**Files:**
- Create: `scripts/fake_device/test_server.py`

Tests drive the server API: `load_config()` for startup validation, `create_app()` factory for route testing with Flask's test client.

- [ ] **Step 1: Create `scripts/fake_device/test_server.py` with the full test suite**

```python
import json
from pathlib import Path

import pytest

from scripts.fake_device.server import create_app, load_config


VALID_CONFIG = {
    "apiKey": "AIzaTEST",
    "authDomain": "pushit-dcf8a.firebaseapp.com",
    "projectId": "pushit-dcf8a",
    "messagingSenderId": "1234567890",
    "appId": "1:1234567890:web:abc",
    "vapidKey": "BTESTVAPIDKEY",
}


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(VALID_CONFIG))
    return path


@pytest.fixture
def client(config_file: Path):
    app = create_app(config_path=config_file, api_base="http://127.0.0.1:8000/api/v1")
    app.config["TESTING"] = True
    return app.test_client()


def test_load_config_returns_dict(config_file: Path):
    config = load_config(config_file)
    assert config["projectId"] == "pushit-dcf8a"
    assert config["vapidKey"] == "BTESTVAPIDKEY"


def test_load_config_missing_file_exits(tmp_path: Path):
    missing = tmp_path / "nope.json"
    with pytest.raises(SystemExit) as exc_info:
        load_config(missing)
    assert exc_info.value.code == 1


def test_load_config_malformed_json_exits(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    with pytest.raises(SystemExit) as exc_info:
        load_config(bad)
    assert exc_info.value.code == 1


def test_get_index_returns_html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.content_type.startswith("text/html")


def test_get_firebase_config_returns_json(client):
    response = client.get("/firebase-config.json")
    assert response.status_code == 200
    assert response.is_json
    body = response.get_json()
    assert body["projectId"] == "pushit-dcf8a"


def test_get_firebase_config_includes_api_base(client):
    response = client.get("/firebase-config.json")
    body = response.get_json()
    assert body["_apiBase"] == "http://127.0.0.1:8000/api/v1"


def test_service_worker_served_from_root(client):
    response = client.get("/firebase-messaging-sw.js")
    assert response.status_code == 200
    assert response.content_type.startswith(("application/javascript", "text/javascript"))


def test_post_received_stores_notification(client):
    payload = {
        "title": "Hello",
        "body": "World",
        "data": {"foo": "bar"},
        "received_at": "2026-04-20T10:00:00Z",
        "mode": "foreground",
    }
    post = client.post("/received", json=payload)
    assert post.status_code == 201

    get = client.get("/received")
    assert get.status_code == 200
    items = get.get_json()
    assert len(items) == 1
    assert items[0]["title"] == "Hello"
    assert items[0]["mode"] == "foreground"


def test_post_received_rejects_payload_without_title(client):
    response = client.post("/received", json={"body": "no title"})
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_post_received_rejects_non_json(client):
    response = client.post("/received", data="not json", content_type="text/plain")
    assert response.status_code == 400


def test_delete_received_clears_list(client):
    client.post(
        "/received",
        json={"title": "A", "body": "B", "data": {}, "received_at": "t", "mode": "foreground"},
    )
    delete = client.delete("/received")
    assert delete.status_code == 204
    get = client.get("/received")
    assert get.get_json() == []
```

- [ ] **Step 2: Run the tests to confirm they fail (import error)**

Run:
```bash
pytest scripts/fake_device/test_server.py -q
```

Expected: collection error -- `ModuleNotFoundError: No module named 'scripts.fake_device.server'` (or similar import failure). This is the expected failing state before implementation.

- [ ] **Step 3: Commit**

```bash
git add scripts/fake_device/test_server.py
git commit -m "test: add failing tests for fake_device Flask server"
```

---

## Task 3: Implement `server.py` to make tests pass

**Files:**
- Create: `scripts/fake_device/__init__.py` (empty, enables package import)
- Create: `scripts/__init__.py` if missing (empty)
- Create: `scripts/fake_device/server.py`

- [ ] **Step 1: Create empty `__init__.py` files for package import**

Run:
```bash
touch scripts/__init__.py scripts/fake_device/__init__.py
```

Verify `scripts/full_flow.py` still works as a standalone script -- it uses `if __name__ == "__main__"` so adding `__init__.py` is harmless.

- [ ] **Step 2: Create `scripts/fake_device/server.py`**

```python
"""Flask server for the PushIT fake device diagnostic tool."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from flask import Flask, Response, abort, jsonify, request, send_from_directory


REQUIRED_CONFIG_KEYS = (
    "apiKey",
    "authDomain",
    "projectId",
    "messagingSenderId",
    "appId",
    "vapidKey",
)


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        sys.stderr.write(
            f"fake_device: config file not found at {path}. "
            f"Copy config.example.json to config.json and fill in your Firebase Web config.\n"
        )
        raise SystemExit(1)

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"fake_device: config file {path} is not valid JSON: {exc}\n")
        raise SystemExit(1) from exc

    missing = [k for k in REQUIRED_CONFIG_KEYS if k not in data]
    if missing:
        sys.stderr.write(
            f"fake_device: config {path} is missing required keys: {', '.join(missing)}\n"
        )
        raise SystemExit(1)

    return data


def create_app(config_path: Path, api_base: str) -> Flask:
    here = Path(__file__).resolve().parent
    static_dir = here / "static"
    sw_path = here / "firebase-messaging-sw.js"

    app = Flask(__name__, static_folder=None)

    firebase_config = load_config(config_path)
    received: list[dict[str, Any]] = []

    @app.get("/")
    def index() -> Response:
        return send_from_directory(static_dir, "index.html")

    @app.get("/app.js")
    def app_js() -> Response:
        return send_from_directory(static_dir, "app.js")

    @app.get("/firebase-messaging-sw.js")
    def service_worker() -> Response:
        if not sw_path.exists():
            abort(404)
        response = send_from_directory(sw_path.parent, sw_path.name)
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.get("/firebase-config.json")
    def firebase_config_json() -> Response:
        payload = dict(firebase_config)
        payload["_apiBase"] = api_base
        return jsonify(payload)

    @app.post("/received")
    def post_received() -> tuple[Response, int]:
        if not request.is_json:
            return jsonify({"error": "expected application/json"}), 400

        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict) or "title" not in payload:
            return jsonify({"error": "missing required field 'title'"}), 400

        received.append(payload)
        app.logger.info("fake_device_received: %s", payload.get("title"))
        return jsonify({"stored": True}), 201

    @app.get("/received")
    def get_received() -> Response:
        return jsonify(received)

    @app.delete("/received")
    def delete_received() -> tuple[str, int]:
        received.clear()
        return "", 204

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="PushIT fake device Flask server")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--api-base",
        default="http://127.0.0.1:8000/api/v1",
        help="Base URL of the PushIT API (used by the page for /devices/link/).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parent / "config.json",
        help="Path to the Firebase Web config JSON.",
    )
    args = parser.parse_args()

    app = create_app(config_path=args.config, api_base=args.api_base)
    print(f"fake_device: serving on http://{args.host}:{args.port}")
    print(f"fake_device: PushIT API base is {args.api_base}")
    print(f"fake_device: open http://{args.host}:{args.port}/?app_token=apt_xxx")
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the tests to verify they pass**

Run:
```bash
pytest scripts/fake_device/test_server.py -q
```

Expected: all 11 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add scripts/__init__.py scripts/fake_device/__init__.py scripts/fake_device/server.py
git commit -m "feat: add fake_device Flask server"
```

---

## Task 4: Create the `index.html` page

**Files:**
- Create: `scripts/fake_device/static/index.html`

The page is intentionally vanilla HTML + inline CSS, no framework.

- [ ] **Step 1: Create `scripts/fake_device/static/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>PushIT Fake Device</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 860px; margin: 2em auto; padding: 0 1em; color: #222; }
  h1 { margin-bottom: 0.2em; }
  .sub { color: #666; margin-top: 0; }
  .card { border: 1px solid #ddd; border-radius: 8px; padding: 1em; margin: 1em 0; background: #fafafa; }
  .row { display: flex; gap: 0.5em; align-items: center; }
  input[type=text] { flex: 1; padding: 0.5em; font-family: monospace; font-size: 13px; }
  button { padding: 0.5em 1em; cursor: pointer; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  code { background: #eef; padding: 2px 4px; border-radius: 3px; word-break: break-all; }
  .status.ok { color: #060; }
  .status.err { color: #b00; }
  .status.warn { color: #a60; }
  ul.received { list-style: none; padding: 0; }
  ul.received li { border-left: 3px solid #88f; padding: 0.5em 0.75em; margin: 0.5em 0; background: #fff; }
  ul.received li.background { border-left-color: #f80; }
  .ts { color: #888; font-size: 12px; }
  .mode { font-size: 11px; text-transform: uppercase; padding: 2px 6px; border-radius: 3px; background: #eef; margin-left: 0.5em; }
  .mode.background { background: #fe8; }
</style>
</head>
<body>
<h1>PushIT Fake Device</h1>
<p class="sub">Browser-based FCM receiver for end-to-end testing without a smartphone.</p>

<div class="card">
  <h3>1. App token</h3>
  <div class="row">
    <input id="app-token" type="text" placeholder="apt_..." />
    <button id="register-btn">Register device</button>
  </div>
  <p id="register-status" class="status"></p>
</div>

<div class="card">
  <h3>2. FCM token</h3>
  <p id="token-status" class="status">Waiting for notification permission...</p>
  <div class="row">
    <code id="fcm-token">-</code>
    <button id="copy-token-btn" disabled>Copy</button>
  </div>
</div>

<div class="card">
  <h3>3. Received notifications</h3>
  <div class="row">
    <button id="clear-btn">Clear</button>
    <span id="received-count" class="status">0 received</span>
  </div>
  <ul id="received-list" class="received"></ul>
</div>

<script type="module" src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Verify the page is served**

Start the server (with a real config.json copied from Task 7 OR create a temporary one with placeholder values for this smoke check):

```bash
python scripts/fake_device/server.py --port 8765
```

Open `http://localhost:8765/` in a browser. Expected: the HTML renders with three cards. The FCM/notification flow will fail until `app.js` exists (Task 5).

Stop the server (Ctrl+C).

- [ ] **Step 3: Commit**

```bash
git add scripts/fake_device/static/index.html
git commit -m "feat: add fake_device index.html"
```

---

## Task 5: Implement the Firebase Web SDK client (`app.js`)

**Files:**
- Create: `scripts/fake_device/static/app.js`

Note: the received list uses `replaceChildren()` and DOM element construction -- never `innerHTML` with interpolated content -- to avoid any XSS exposure when rendering notification `title`/`body` strings.

- [ ] **Step 1: Create `scripts/fake_device/static/app.js`**

```javascript
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.13.2/firebase-app.js";
import {
  getMessaging,
  getToken,
  onMessage,
} from "https://www.gstatic.com/firebasejs/10.13.2/firebase-messaging.js";

const els = {
  appToken: document.getElementById("app-token"),
  registerBtn: document.getElementById("register-btn"),
  registerStatus: document.getElementById("register-status"),
  tokenStatus: document.getElementById("token-status"),
  fcmToken: document.getElementById("fcm-token"),
  copyTokenBtn: document.getElementById("copy-token-btn"),
  clearBtn: document.getElementById("clear-btn"),
  receivedCount: document.getElementById("received-count"),
  receivedList: document.getElementById("received-list"),
};

const state = {
  fcmToken: null,
  apiBase: null,
  received: [],
};

function setStatus(el, text, cls) {
  el.textContent = text;
  el.className = `status ${cls || ""}`.trim();
}

function prefillAppToken() {
  const qs = new URLSearchParams(window.location.search);
  const token = qs.get("app_token");
  if (token) {
    els.appToken.value = token;
  }
}

function buildReceivedItem(item) {
  const li = document.createElement("li");
  li.className = item.mode === "background" ? "background" : "";

  const title = document.createElement("strong");
  title.textContent = item.title || "(no title)";
  li.appendChild(title);

  const modeTag = document.createElement("span");
  modeTag.className = `mode ${item.mode || ""}`;
  modeTag.textContent = item.mode || "?";
  li.appendChild(modeTag);

  const body = document.createElement("div");
  body.textContent = item.body || "";
  li.appendChild(body);

  const ts = document.createElement("div");
  ts.className = "ts";
  ts.textContent = item.received_at || "";
  li.appendChild(ts);

  return li;
}

function renderReceived() {
  els.receivedCount.textContent = `${state.received.length} received`;
  const children = state.received
    .slice()
    .reverse()
    .map(buildReceivedItem);
  els.receivedList.replaceChildren(...children);
}

function addReceived(item) {
  state.received.push(item);
  renderReceived();
}

async function postReceived(item) {
  try {
    await fetch("/received", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(item),
    });
  } catch (e) {
    console.warn("failed to POST /received", e);
  }
}

async function main() {
  prefillAppToken();

  let configResp;
  try {
    configResp = await fetch("/firebase-config.json");
  } catch (e) {
    setStatus(els.tokenStatus, `Failed to load Firebase config: ${e.message}`, "err");
    return;
  }
  const config = await configResp.json();
  state.apiBase = config._apiBase;

  let swRegistration;
  try {
    swRegistration = await navigator.serviceWorker.register("/firebase-messaging-sw.js");
  } catch (e) {
    setStatus(els.tokenStatus, `Service Worker registration failed: ${e.message}`, "err");
    return;
  }

  const firebaseApp = initializeApp({
    apiKey: config.apiKey,
    authDomain: config.authDomain,
    projectId: config.projectId,
    messagingSenderId: config.messagingSenderId,
    appId: config.appId,
  });
  const messaging = getMessaging(firebaseApp);

  let permission;
  try {
    permission = await Notification.requestPermission();
  } catch (e) {
    setStatus(els.tokenStatus, `Permission request error: ${e.message}`, "err");
    return;
  }
  if (permission !== "granted") {
    setStatus(els.tokenStatus, "Notifications denied. Re-enable in browser settings.", "err");
    return;
  }

  try {
    state.fcmToken = await getToken(messaging, {
      vapidKey: config.vapidKey,
      serviceWorkerRegistration: swRegistration,
    });
  } catch (e) {
    setStatus(els.tokenStatus, `getToken() failed: ${e.message}`, "err");
    return;
  }

  els.fcmToken.textContent = state.fcmToken;
  els.copyTokenBtn.disabled = false;
  setStatus(els.tokenStatus, "FCM token acquired.", "ok");

  onMessage(messaging, (payload) => {
    const item = {
      title: payload.notification?.title || payload.data?.title || "(no title)",
      body: payload.notification?.body || payload.data?.body || "",
      data: payload.data || {},
      received_at: new Date().toISOString(),
      mode: "foreground",
    };
    addReceived(item);
    postReceived(item);
  });

  const channel = new BroadcastChannel("fake-device");
  channel.onmessage = (event) => {
    addReceived(event.data);
  };
}

els.copyTokenBtn.addEventListener("click", () => {
  if (state.fcmToken) {
    navigator.clipboard.writeText(state.fcmToken);
  }
});

els.clearBtn.addEventListener("click", async () => {
  state.received = [];
  renderReceived();
  try {
    await fetch("/received", { method: "DELETE" });
  } catch (e) {
    console.warn("failed to DELETE /received", e);
  }
});

els.registerBtn.addEventListener("click", async () => {
  const appToken = els.appToken.value.trim();
  if (!appToken) {
    setStatus(els.registerStatus, "Enter an app token first.", "err");
    return;
  }
  if (!state.fcmToken) {
    setStatus(els.registerStatus, "No FCM token yet -- wait for permission.", "err");
    return;
  }
  setStatus(els.registerStatus, "Registering...", "warn");
  let resp;
  try {
    resp = await fetch(`${state.apiBase}/devices/link/`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-App-Token": appToken,
      },
      body: JSON.stringify({
        device_name: "Fake Web Device",
        platform: "android",
        push_token: state.fcmToken,
      }),
    });
  } catch (e) {
    setStatus(els.registerStatus, `Network error: ${e.message}`, "err");
    return;
  }
  const body = await resp.json().catch(() => ({}));
  if (resp.ok) {
    setStatus(
      els.registerStatus,
      `Registered as device ${body.device_id}.`,
      "ok",
    );
  } else {
    setStatus(
      els.registerStatus,
      `Registration failed (${resp.status}): ${JSON.stringify(body)}`,
      "err",
    );
  }
});

main();
```

- [ ] **Step 2: Commit**

```bash
git add scripts/fake_device/static/app.js
git commit -m "feat: add fake_device Firebase Web SDK client"
```

---

## Task 6: Implement the Service Worker (`firebase-messaging-sw.js`)

**Files:**
- Create: `scripts/fake_device/firebase-messaging-sw.js` (lives at the package root, NOT inside `static/`, because the Flask server serves it from `/` to control the whole origin's scope)

- [ ] **Step 1: Create `scripts/fake_device/firebase-messaging-sw.js`**

```javascript
importScripts("https://www.gstatic.com/firebasejs/10.13.2/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/10.13.2/firebase-messaging-compat.js");

async function init() {
  const resp = await fetch("/firebase-config.json");
  const config = await resp.json();
  firebase.initializeApp({
    apiKey: config.apiKey,
    authDomain: config.authDomain,
    projectId: config.projectId,
    messagingSenderId: config.messagingSenderId,
    appId: config.appId,
  });

  const messaging = firebase.messaging();

  messaging.onBackgroundMessage((payload) => {
    const title = payload.notification?.title || payload.data?.title || "PushIT";
    const body = payload.notification?.body || payload.data?.body || "";

    self.registration.showNotification(title, {
      body,
      data: payload.data || {},
    });

    const item = {
      title,
      body,
      data: payload.data || {},
      received_at: new Date().toISOString(),
      mode: "background",
    };

    try {
      const channel = new BroadcastChannel("fake-device");
      channel.postMessage(item);
    } catch (e) {
      // ignore
    }

    fetch("/received", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(item),
    }).catch(() => {});
  });
}

init().catch((e) => {
  console.error("fake_device SW init failed:", e);
});
```

- [ ] **Step 2: Verify the SW is served from root**

Start the server:
```bash
python scripts/fake_device/server.py --port 8765
```

Then:
```bash
curl -I http://localhost:8765/firebase-messaging-sw.js
```

Expected: `200 OK` with `Service-Worker-Allowed: /` header. Stop the server.

- [ ] **Step 3: Commit**

```bash
git add scripts/fake_device/firebase-messaging-sw.js
git commit -m "feat: add fake_device Firebase messaging service worker"
```

---

## Task 7: Create the Firebase config example template

**Files:**
- Create: `scripts/fake_device/config.example.json`

- [ ] **Step 1: Create `scripts/fake_device/config.example.json`**

```json
{
  "apiKey": "REPLACE_WITH_FIREBASE_WEB_API_KEY",
  "authDomain": "pushit-dcf8a.firebaseapp.com",
  "projectId": "pushit-dcf8a",
  "messagingSenderId": "REPLACE_WITH_SENDER_ID",
  "appId": "REPLACE_WITH_APP_ID",
  "vapidKey": "REPLACE_WITH_VAPID_KEY"
}
```

- [ ] **Step 2: Verify the real config.json is gitignored**

Run:
```bash
cp scripts/fake_device/config.example.json scripts/fake_device/config.json
git status
```

Expected: `config.json` does NOT appear in `git status` output (it's ignored).

- [ ] **Step 3: Commit the example template only**

```bash
git add scripts/fake_device/config.example.json
git commit -m "feat: add fake_device Firebase config template"
```

---

## Task 8: Write the README

**Files:**
- Create: `scripts/fake_device/README.md`

- [ ] **Step 1: Create `scripts/fake_device/README.md`**

````markdown
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

### 2. Get an app token

Log into PushIT, create an application, and copy its `app_token` (starts with `apt_`).

### 3. Open the page

```
http://localhost:8765/?app_token=apt_xxx
```

- Accept the notification permission prompt.
- Wait for the FCM token to appear.
- Click **Register device**. Watch for "Registered as device ...".

### 4. Send a test notification

Use any API client; for example:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/notifications/ \
  -H "Content-Type: application/json" \
  -H "X-App-Token: apt_xxx" \
  -d '{"title": "Hello", "message": "From PushIT", "device_ids": [<device_id>]}'

curl -X POST http://127.0.0.1:8000/api/v1/notifications/<id>/send/ \
  -H "X-App-Token: apt_xxx"
```

The notification should appear within a few seconds in the page (foreground) or as an OS notification (background).

## Smoke test checklist (manual)

Run this once after setup to confirm everything is wired correctly:

- [ ] `python scripts/fake_device/server.py` -- server starts, prints "serving on http://127.0.0.1:8765"
- [ ] Open `http://localhost:8765/?app_token=apt_xxx` -- FCM token is displayed on the page
- [ ] Click **Register device** -- status becomes "Registered as device ..."
- [ ] `GET http://127.0.0.1:8000/api/v1/devices/` lists the new "Fake Web Device"
- [ ] Send a notification targeting that device -- it appears in the page (foreground mode)
- [ ] Minimize the tab, send another notification -- an OS notification is shown AND a `background` entry appears in `GET http://localhost:8765/received`

## Troubleshooting

- **"Service Worker registration failed"**: make sure you are on `localhost` or HTTPS. `file://` does not work.
- **"getToken() failed"**: check the VAPID key in `config.json`, and that FCM is enabled on the Firebase project.
- **"Registration failed (401)"**: the app token is invalid. Regenerate via `POST /api/v1/apps/<id>/regenerate-token/`.
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
````

- [ ] **Step 2: Commit**

```bash
git add scripts/fake_device/README.md
git commit -m "docs: add fake_device README with setup and smoke test"
```

---

## Task 9: End-to-end manual smoke test

**Files:** none modified -- this is a validation-only task.

This task is executed by a human (or agent with a browser). Do not mark complete without running it.

- [ ] **Step 1: Fill `config.json` with real Firebase Web values**

Follow the README "Firebase Web App" instructions to get the real config, then:

```bash
cp scripts/fake_device/config.example.json scripts/fake_device/config.json
# edit config.json with real values
```

- [ ] **Step 2: Ensure Django runs with FCM enabled**

In `.env` confirm:
- `FCM_SERVICE_ACCOUNT_PATH=<path to pushit-dcf8a-firebase-adminsdk-fbsvc-*.json>`
- `CORS_ALLOWED_ORIGINS=http://localhost:8000,http://localhost:8765`

Start Django:
```bash
python manage.py runserver
```

- [ ] **Step 3: Confirm Celery mode**

In DEV, `CELERY_TASK_ALWAYS_EAGER=True` is set, so tasks run inline and a worker is not needed. If you see scheduled notifications not firing, start one:

```bash
celery -A config worker -Q pushit -l info
```

- [ ] **Step 4: Start the fake device server**

```bash
python scripts/fake_device/server.py --port 8765
```

- [ ] **Step 5: Register a PushIT app and copy its token**

Via `full_flow.py` or manual API calls:
```bash
python scripts/full_flow.py
```

Copy the `app_token` from the output (you can read it back via `GET /api/v1/apps/<id>/` if needed).

- [ ] **Step 6: Open the page and register**

Open `http://localhost:8765/?app_token=apt_xxx` in Chrome or Edge.
Accept the notification permission prompt.
Click **Register device**. Confirm "Registered as device N".

- [ ] **Step 7: Send a foreground notification**

Via an API client, create and send a notification targeting that new device ID.
Expected: within a few seconds, the notification appears in the page under "Received notifications" with mode `foreground`.

- [ ] **Step 8: Send a background notification**

Switch to another tab or minimize the fake device window. Create and send another notification.
Expected: an OS-level notification pops up, AND `curl http://localhost:8765/received` shows an entry with `mode: "background"`.

- [ ] **Step 9: Clean up**

```bash
curl -X DELETE http://localhost:8765/received
```

Stop the fake device server and Django dev server.

- [ ] **Step 10: Final commit of anything left over (if any)**

If the smoke test revealed a bug that required a fix, commit the fix. Otherwise, nothing to do here.

---

## Done

At this point you have:
- A `scripts/fake_device/` tool that can receive real FCM pushes from PushIT in a browser.
- A unit-tested Flask server exposing the `/received` hook for future pytest automation.
- Documentation and a smoke test that proves the end-to-end FCM path works.

The `/received` endpoint is the seam for a future pytest-based E2E test (bonus B in the spec); that test is out of scope for this plan.
