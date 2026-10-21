/* A map page fits the window: the map takes the height that is left between the top of the page and the footer, so the whole page is the height of the window, the footer is always in view and there is
   nothing to scroll. It follows the window being resized, the footer changing height (a seasonal greeting) and the menu wrapping onto another line. Not in full screen, which has its own sizing. */
(function () {
  // a map that opens on a whole area (Arkansas) was fitted to the size it had before this script sized it: remember the last fitBounds a page made on its own, and make it again when the map's size changes
  // and the visitor has not moved the map yet
  if (window.L && L.Map && !L.Map.prototype._arkFitWrapped) {
    var fb = L.Map.prototype.fitBounds;
    L.Map.prototype._arkFitWrapped = true;
    L.Map.prototype.fitBounds = function (b, o) {
      if (!this._arkUser && !this._arkRefitting) {   // only the page's own opening fit of a wide area (a whole state), made in its first seconds
        var bb = L.latLngBounds(b); this._arkBorn = this._arkBorn || Date.now();
        if (Date.now() - this._arkBorn < 4000 && bb.getNorth() - bb.getSouth() > 1) { this._arkFit = [b, o]; this._arkFitAt = Date.now(); } else this._arkFit = null;
      }
      return fb.call(this, b, o);
    };
  }
  // any other move the page makes by itself afterwards (a flight, a place, a link's own view) replaces the opening fit: it is not made again
  if (window.L && L.Map && !L.Map.prototype._arkMovesWrapped) {
    L.Map.prototype._arkMovesWrapped = true;
    ["setView", "flyTo", "panTo", "setZoom", "flyToBounds"].forEach(function (m) {
      var f = L.Map.prototype[m]; if (!f) return;
      L.Map.prototype[m] = function () { if (!this._arkRefitting && this._arkFit && Date.now() - (this._arkFitAt || 0) > 50) this._arkFit = null; return f.apply(this, arguments); };
    });
  }
  function watchUser(map) {
    if (!map || map._arkWatched) return; map._arkWatched = true;
    var c = map.getContainer(), mark = function () { map._arkUser = true; };
    ["mousedown", "wheel", "touchstart", "keydown", "dblclick"].forEach(function (ev) { c.addEventListener(ev, mark, {passive: true}); });
  }
  function refit(map) {
    if (!map || map._arkUser || !map._arkFit) return;
    if (/[?&](zoom|lat)=/.test(location.search)) return;   // a link that names its view keeps it
    var sz = map.getSize(); if (sz.x < 100 || sz.y < 100) return;   // a map that is not on the screen yet cannot be fitted
    map._arkRefitting = true;
    try { map.invalidateSize({animate: false}); fb2(map, map._arkFit); } finally { map._arkRefitting = false; }
  }
  function fb2(map, a) { L.Map.prototype.fitBounds.call(map, a[0], Object.assign({}, a[1] || {}, {animate: false})); }
  var lv = document.getElementById("lv");
  if (!lv || lv.getAttribute("data-fit")) return;
  lv.setAttribute("data-fit", "1");
  // the height of the screen as it is right now (100dvh follows a phone browser's bars as they slide away or come back); a hidden element of that height is the way to read it
  var probe = document.createElement("div"); probe.setAttribute("aria-hidden", "true"); probe.style.cssText = "position:fixed;left:0;top:0;width:0;height:100vh;height:100dvh;visibility:hidden;pointer-events:none"; document.body.appendChild(probe);
  function fit() {
    if (lv.classList.contains("lv-full") || document.fullscreenElement) { lv.style.height = ""; return; }
    lv.style.height = "";   // the map's height is the rest of the screen by CSS (mapshell.css, main.wide #lv): nothing is measured here
    own = true; window.dispatchEvent(new Event("resize")); own = false;   // the map measures itself again (the flag keeps this from calling fit again)
    if (window.arkMap) { watchUser(window.arkMap); if (Math.abs(lv.offsetHeight - lastH) > 4) refit(window.arkMap); }
    lastH = lv.offsetHeight;
  }
  var t = null, own = false, lastH = 0;
  function soon() { clearTimeout(t); t = setTimeout(fit, 60); }
  window.addEventListener("resize", function () { if (!own) soon(); });
  if (window.visualViewport) window.visualViewport.addEventListener("resize", function () { if (!own) soon(); });
  if (window.ResizeObserver) { var ro = new ResizeObserver(soon); ro.observe(lv); if (document.querySelector("footer")) ro.observe(document.querySelector("footer")); var nav = document.getElementById("mainnav"); if (nav) ro.observe(nav); }
  document.addEventListener("adsb-prefs", function () { setTimeout(soon, 200); });
  var lastProbe = probe.offsetHeight;
  setInterval(function () { if (document.hidden) return; var h = probe.offsetHeight; if (h !== lastProbe) { lastProbe = h; soon(); } }, 600);   // a phone's browser bars come and go without a resize event
  fit(); setTimeout(fit, 400); setTimeout(fit, 1500);
})();
