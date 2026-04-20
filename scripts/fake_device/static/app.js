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
