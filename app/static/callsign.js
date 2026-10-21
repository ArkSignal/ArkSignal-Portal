/* Call sign information wherever a call sign appears. An element with data-call (a link, a cell) gets the licensee's name, class and place as its hover text, fetched once when the pointer or focus first
   arrives (never on page load, so a long list asks for nothing). An element with the class cs-license and data-call is filled in at once ("Name, Class, City ST"). Both read /api/callsign/CALL.json.
   Public: ArkCall.link(call) -> html for a call sign in a script-built popup; ArkCall.enhance(root) after adding elements. */
(function () {
  "use strict";
  var cache = {}, RE = /^[A-Z]{1,2}[0-9][A-Z]{1,3}$/;
  function base(c) { return String(c || "").toUpperCase().split("-")[0].split("/")[0].replace(/[^A-Z0-9]/g, ""); }
  function ask(call) {
    call = base(call);
    if (!RE.test(call)) return Promise.resolve(null);
    if (!cache[call]) cache[call] = fetch("/api/callsign/" + encodeURIComponent(call) + ".json").then(function (r) { return r.ok ? r.json() : {}; }).then(function (d) { return d.callsign || null; }).catch(function () { return null; });
    return cache[call];
  }
  function text(c) { return c ? [c.name, c.oper_class, c.place].filter(Boolean).join(", ") + (c.status && c.status !== "Valid" ? " (" + c.status + ")" : "") : ""; }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); }
  function hover(el) {
    if (el.__cs) return; el.__cs = 1;
    var go = function () { ask(el.getAttribute("data-call")).then(function (c) { if (c && !el.title) el.title = text(c); }); };
    el.addEventListener("mouseenter", go, {once: true}); el.addEventListener("focus", go, {once: true}); el.addEventListener("touchstart", go, {once: true, passive: true});
  }
  function enhance(root) {
    (root || document).querySelectorAll("[data-call]").forEach(function (el) {
      if (el.classList.contains("cs-license")) {
        if (el.__cs) return; el.__cs = 1;
        var call = base(el.getAttribute("data-call"));
        if (!RE.test(call)) { el.textContent = "not a US call sign"; return; }
        ask(call).then(function (c) { el.innerHTML = c ? esc(text(c)) + ' &middot; <a href="/callsign?call=' + encodeURIComponent(call) + '">License details</a>' : 'No US license found &middot; <a href="/callsign?call=' + encodeURIComponent(call) + '">Look up</a>'; });
      } else hover(el);
    });
  }
  // ArkCall.tag(el, call): the text of a card title or popup heading gets the licensee as its hover text (blank call = none)
  function tag(el, call) { if (!el) return; el.title = ""; el.__cs = 0; if (call) el.setAttribute("data-call", call); else el.removeAttribute("data-call"); if (call) enhance(el.parentNode || el); }
  window.ArkCall = {tag: tag, ask: ask, enhance: enhance, link: function (call) { var b = base(call); return RE.test(b) ? '<a class="cs" data-call="' + esc(call) + '" href="/callsign?call=' + encodeURIComponent(b) + '">' + esc(call) + "</a>" : esc(call); }};
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { enhance(); }); else enhance();
})();
