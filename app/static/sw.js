/* The site's service worker: it exists to show notifications that arrive by Web Push (new chat messages while the site is closed) and to open the chat when one is clicked. It caches nothing and changes no page. */
self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });

self.addEventListener("push", function (e) {
  var d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = { title: "ArkSignal", body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "ArkSignal", {
    body: d.body || "", tag: d.tag || undefined, renotify: !!d.tag, data: { url: d.url || "/chat/" },
    icon: "/apple-touch-icon.png", badge: "/favicon-32.png"
  }));
});

self.addEventListener("notificationclick", function (e) {
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || "/chat/";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (list) {
    for (var i = 0; i < list.length; i++) {   // a chat window that is already open is used; any other page is left alone
      if (new URL(list[i].url).pathname.indexOf("/chat") === 0 && "focus" in list[i]) { list[i].navigate(url); return list[i].focus(); }
    }
    return self.clients.openWindow(url);
  }));
});
