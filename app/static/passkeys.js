/* Passkeys (WebAuthn) in the browser: sign in with one (the login page's button) and add one (My Account > Passkeys). The server does every check; this only talks to the browser's credential manager.
   The choices and messages are plain text in the page, and a browser without passkey support just does not show the buttons. */
(function () {
  function b64d(s) { s = String(s || "").replace(/-/g, "+").replace(/_/g, "/"); while (s.length % 4) s += "="; var bin = atob(s), a = new Uint8Array(bin.length); for (var i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i); return a.buffer; }
  function b64e(buf) { var a = new Uint8Array(buf), s = ""; for (var i = 0; i < a.length; i++) s += String.fromCharCode(a[i]); return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); }
  function post(url, body) {
    return fetch(url, {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json", "X-CSRFToken": window.CSRF_TOKEN || "", "X-Requested-With": "XMLHttpRequest"}, body: JSON.stringify(body || {})}).then(function (r) { return r.json().catch(function () { return {ok: false, message: "The site did not answer properly."}; }); });
  }
  var supported = !!(window.PublicKeyCredential && navigator.credentials && navigator.credentials.create);
  function say(id, text, ok) { var e = document.getElementById(id); if (e) { e.textContent = text; e.style.color = ok ? "var(--acc)" : "var(--danger, #ff6b6b)"; } }
  function why(err) {
    var n = err && err.name || "";
    if (n === "NotAllowedError") return "That was cancelled, timed out, or the phone or browser refused it (" + n + "). On a phone: check the device has a screen lock set, that Google Password Manager (or your passkey app) is on, then try again.";
    if (n === "InvalidStateError") return "This device already has a passkey for you here.";
    if (n === "SecurityError") return "The browser would not use a passkey for this address (" + n + "). Passkeys belong to one site address: use the site's current address, and add a new passkey here if the old one was made under another address.";
    if (n === "NotSupportedError") return "This device or browser cannot make or use this kind of passkey (" + n + "): it may have no screen lock, or passkeys are switched off for the browser.";
    return "Your browser could not do that" + (n ? " (" + n + ")" : "") + ": " + (err && err.message ? err.message : "unknown reason") + ".";
  }

  // ---- sign in
  var login = document.getElementById("pk-login");
  var conditional = null;   // an open "passkeys in the user name box" request, stopped when the button is used
  function finish(cred) {
    var a = cred.response;
    return post("/login/passkey/verify", {id: cred.id, clientDataJSON: b64e(a.clientDataJSON), authenticatorData: b64e(a.authenticatorData), signature: b64e(a.signature), next: new URLSearchParams(location.search).get("next") || ""}).then(function (r) {
      if (r.ok) { location.href = r.next || "/"; } else { say("pk-msg", r.message, false); }
    });
  }
  if (login && supported && window.PublicKeyCredential.isConditionalMediationAvailable) {   // passkeys offered in the user name box's own suggestions (the way Chrome on a phone likes best)
    window.PublicKeyCredential.isConditionalMediationAvailable().then(function (yes) {
      if (!yes || !document.getElementById("username")) return;
      conditional = new AbortController();
      post("/login/passkey/options").then(function (r) {
        if (!r.ok) return;
        var o = r.options;
        return navigator.credentials.get({mediation: "conditional", signal: conditional.signal, publicKey: {challenge: b64d(o.challenge), rpId: o.rpId, timeout: o.timeout, userVerification: o.userVerification, allowCredentials: []}});
      }).then(function (cred) { if (cred) return finish(cred); }).catch(function () {});
    }).catch(function () {});
  }
  if (login) {
    if (!supported) { login.closest(".pk-box").style.display = "none"; }
    else login.addEventListener("click", function () {
      if (conditional) { conditional.abort(); conditional = null; }
      login.disabled = true; say("pk-msg", "", true);
      post("/login/passkey/options").then(function (r) {
        if (!r.ok) throw {message: r.message};
        var o = r.options;
        return navigator.credentials.get({publicKey: {challenge: b64d(o.challenge), rpId: o.rpId, timeout: o.timeout, userVerification: o.userVerification, allowCredentials: []}});
      }).then(function (cred) {
        var a = cred.response;
        return post("/login/passkey/verify", {id: cred.id, clientDataJSON: b64e(a.clientDataJSON), authenticatorData: b64e(a.authenticatorData), signature: b64e(a.signature), next: new URLSearchParams(location.search).get("next") || ""});
      }).then(function (r) {
        if (r.ok) { location.href = r.next || "/"; } else { say("pk-msg", r.message, false); login.disabled = false; }
      }).catch(function (e) { say("pk-msg", why(e), false); login.disabled = false; });
    });
  }

  // ---- add a passkey
  var add = document.getElementById("pk-add");
  if (add) {
    if (!supported) { say("pk-msg", "This browser cannot make passkeys. Try a current version of Chrome, Edge, Safari or Firefox.", false); add.disabled = true; }
    else add.addEventListener("click", function () {
      add.disabled = true; say("pk-msg", "", true);
      var name = (document.getElementById("pk-name") || {}).value || "";
      post("/my/passkeys/options").then(function (r) {
        if (!r.ok) throw {message: r.message};
        var o = r.options;
        return navigator.credentials.create({publicKey: {challenge: b64d(o.challenge), rp: o.rp, user: {id: b64d(o.user.id), name: o.user.name, displayName: o.user.displayName}, pubKeyCredParams: o.pubKeyCredParams, timeout: o.timeout,
          attestation: o.attestation, authenticatorSelection: o.authenticatorSelection, excludeCredentials: (o.excludeCredentials || []).map(function (c) { return {type: c.type, id: b64d(c.id)}; })}});
      }).then(function (cred) {
        var a = cred.response;
        return post("/my/passkeys/register", {name: name, clientDataJSON: b64e(a.clientDataJSON), attestationObject: b64e(a.attestationObject), transports: a.getTransports ? a.getTransports() : []});
      }).then(function (r) {
        if (r.html) { var l = document.getElementById("passkeys-list"); if (l) l.outerHTML = r.html; }
        say("pk-msg", r.message || "", !!r.ok); add.disabled = false; if (r.ok && document.getElementById("pk-name")) document.getElementById("pk-name").value = "";
      }).catch(function (e) { say("pk-msg", why(e), false); add.disabled = false; });
    });
  }
})();
