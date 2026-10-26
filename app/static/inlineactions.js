/* The small page actions that used to be written as onclick= and onchange= on elements, as data attributes read here, so the site can forbid inline event handlers (Content-Security-Policy). */
(function () {
  function byId(i) { return document.getElementById(i); }
  document.addEventListener("click", function (e) {
    var t = e.target.closest ? e.target.closest("[data-fill-id],[data-select-on-click],[data-copy-from],[data-details-open]") : null;
    if (!t) return;
    if (t.hasAttribute("data-select-on-click") && t.select) t.select();
    if (t.hasAttribute("data-fill-id")) {
      var d = byId(t.getAttribute("data-open-id")); if (d) d.open = true;
      var f = byId(t.getAttribute("data-fill-id")); if (f) f.value = t.getAttribute("data-fill-value") || "";
      var g = byId(t.getAttribute("data-focus-id")); if (g) g.focus();
    }
    if (t.hasAttribute("data-copy-from") && navigator.clipboard) {
      var src = byId(t.getAttribute("data-copy-from"));
      if (src) navigator.clipboard.writeText(src.textContent.trim()).then(function () { t.textContent = t.getAttribute("data-copied") || "Copied"; });
    }
    if (t.hasAttribute("data-details-open")) {
      var on = t.getAttribute("data-open-all") === "1";
      document.querySelectorAll(t.getAttribute("data-details-open")).forEach(function (x) { x.open = on; });
    }
  });
  document.addEventListener("change", function (e) {
    var t = e.target;
    if (!t || !t.hasAttribute) return;
    if (t.hasAttribute("data-submit-on-change") && t.form) t.form.submit();
    if (t.hasAttribute("data-nav-checked")) {
      var go = t.getAttribute("data-nav-checked") + (t.checked ? t.getAttribute("data-nav-checked-query") || "" : "");
      var u = null;
      try { u = new URL(go, location.origin); } catch (err) { u = null; }
      if (u && u.origin === location.origin) location.assign(u.pathname + u.search + u.hash);   // only a page on this site
    }
    if (t.hasAttribute("data-toggle-target")) {
      var v = byId(t.getAttribute("data-toggle-target")), on = (t.getAttribute("data-needs") || "").split(",").indexOf(t.value) >= 0;
      if (v) { v.disabled = !on; if (v.parentNode) v.parentNode.style.opacity = on ? 1 : .4; }
    }
  });
  document.addEventListener("submit", function (e) { if (e.target && e.target.hasAttribute && e.target.hasAttribute("data-no-submit")) e.preventDefault(); });
})();
