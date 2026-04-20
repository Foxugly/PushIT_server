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
