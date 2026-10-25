/* Notifications for this device: makes (or removes) the browser's push subscription and tells the site. Used by My Account > Community Settings and the Chat page. */
window.ArkPush = (function () {
  var csrf = "";
  function supported() { return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window; }
  function bytes(b64) {
    var pad = "=".repeat((4 - b64.length % 4) % 4), raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/")), out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }
  function post(url, body) {
    return fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify(body || {}) }).then(function (r) { return r.json().catch(function () { return { ok: false, message: "The site did not answer." }; }); });
  }
  function current() {
    if (!supported()) return Promise.resolve(null);
    return navigator.serviceWorker.getRegistration("/").then(function (reg) { return reg ? reg.pushManager.getSubscription() : null; });
  }
  function status() {
    if (!supported()) return Promise.resolve({ state: "unsupported" });
    if (Notification.permission === "denied") return Promise.resolve({ state: "denied" });
    return current().then(function (sub) { return { state: sub ? "on" : "off" }; });
  }
  function enable() {
    if (!supported()) return Promise.resolve({ ok: false, message: "This browser cannot show notifications from a closed page." });
    return Notification.requestPermission().then(function (p) {
      if (p !== "granted") return { ok: false, message: "The browser did not allow notifications. Allow them for this site in the browser's site settings, then try again." };
      return navigator.serviceWorker.register("/sw.js", { scope: "/" }).then(function () { return navigator.serviceWorker.ready; }).then(function (reg) {
        return fetch("/push/key", { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (k) {
          return reg.pushManager.getSubscription().then(function (s) { return s || reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: bytes(k.key) }); });
        });
      }).then(function (sub) { return post("/my/push/subscribe", { subscription: sub.toJSON(), device: navigator.userAgent }); });
    }).catch(function (e) { return { ok: false, message: "Notifications could not be turned on (" + (e && e.name || "error") + ")." }; });
  }
  function disable() {
    return current().then(function (sub) {
      var ep = sub ? sub.endpoint : "";
      return post("/my/push/unsubscribe", { endpoint: ep }).then(function (r) { return (sub ? sub.unsubscribe() : Promise.resolve()).then(function () { return r; }); });
    }).catch(function () { return { ok: false, message: "Could not turn notifications off." }; });
  }
  return { init: function (o) { csrf = (o && o.csrf) || ""; }, supported: supported, status: status, enable: enable, disable: disable, test: function () { return post("/my/push/test"); } };
})();
