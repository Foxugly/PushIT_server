importScripts("https://www.gstatic.com/firebasejs/10.13.2/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/10.13.2/firebase-messaging-compat.js");

// FIREBASE_CONFIG is injected synchronously at the top of this file by the
// Flask server (see scripts/fake_device/server.py). This keeps all event
// handler registrations synchronous during the SW's initial evaluation,
// which Chrome requires for push/notificationclick/pushsubscriptionchange.
firebase.initializeApp(FIREBASE_CONFIG);

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
    // ignore — BroadcastChannel not supported in this environment
  }

  fetch("/received", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(item),
  }).catch(() => {});
});
