/* "Show my location" on every map: a blue dot and a circle for how well the device knows where it is, kept up to date while the box in Display and View is ticked. The position is read by the browser
   only and drawn on this page: it is never sent to the server. Public: ArkLocate.init({map})  ArkLocate.center()  */
(function () {
  "use strict";
  var map = null, layer = null, watchId = null, wantCenter = false, last = null, waiting = [], btn = null;
  function $(id) { return document.getElementById(id); }
  function pref(k, v) { try { if (v === undefined) return localStorage.getItem("lv." + k); localStorage.setItem("lv." + k, v); } catch (e) { return null; } }
  function note(t) { var n = $("my-loc-note"); if (n) n.textContent = t || ""; }
  function draw(p) {
    var ll = [p.coords.latitude, p.coords.longitude], acc = Math.max(5, p.coords.accuracy || 0); last = ll;
    if (!layer) layer = L.layerGroup().addTo(map);
    layer.clearLayers();
    L.circle(ll, {radius: acc, color: "#4fb3ff", weight: 1, fillColor: "#4fb3ff", fillOpacity: .12, interactive: false}).addTo(layer);
    L.circleMarker(ll, {radius: 8, color: "#fff", weight: 3, fillColor: "#2f7fff", fillOpacity: 1}).addTo(layer).bindTooltip("You are here (within about " + (window.ArkUnits ? ArkUnits.text.alt(acc, 0) : Math.round(acc) + " m") + ")", {direction: "top", offset: [0, -8]});
    note("Accurate to about " + (window.ArkUnits ? ArkUnits.text.alt(acc, 0) : Math.round(acc) + " m") + ".");
    if (wantCenter) { wantCenter = false; map.setView(ll, Math.max(map.getZoom(), 11)); }   // only when the person asked to go there: a map that opens already showing the dot never moves
    while (waiting.length) waiting.shift()(ll);
    syncBtn();
  }
  function refuse(text) { $("my-loc").checked = false; pref("myloc", "0"); stop(); note(text); }   // the box goes back to unticked and the reason stays on screen
  function start() {
    if (!navigator.geolocation) { refuse("This browser cannot tell where you are."); return; }
    if (window.isSecureContext === false) { refuse("Your browser only shares where you are with secure (https) addresses, and this address is not one. Open the site by its https address."); return; }
    note("Looking for you...");
    watchId = navigator.geolocation.watchPosition(draw, function (e) { waiting = []; if (e.code === 1) refuse("Location is blocked for this site in your browser (look for the location icon in the address bar and allow it)."); else note("Your position could not be found yet; trying again."); },
      {enableHighAccuracy: true, maximumAge: 15000, timeout: 30000});
  }
  function syncBtn() { if (btn) { btn.classList.toggle("on", !!last); btn.style.color = last ? "#2f7fff" : ""; } }
  function stop() { if (watchId != null) navigator.geolocation.clearWatch(watchId); watchId = null; wantCenter = false; waiting = []; if (layer) { map.removeLayer(layer); layer = null; } last = null; note(""); syncBtn(); }
  function init(o) {
    map = o.map; var box = $("my-loc"), go = $("my-loc-go"); if (!box) return;
    box.checked = pref("myloc") === "1";
    // the bullseye button on the map, like other sites: one tap shows you and goes there, another tap goes there again
    var Ctl = L.Control.extend({options: {position: "bottomright"}, onAdd: function () {
      var c = L.DomUtil.create("div", "leaflet-bar lv-locate"); btn = L.DomUtil.create("a", "", c); btn.href = "#"; btn.role = "button"; btn.setAttribute("aria-label", "Show my location and go there"); btn.title = "Show my location and go there";
      btn.innerHTML = '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><circle cx="12" cy="12" r="7.5" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="12" r="2.6" fill="currentColor"/><path d="M12 1.5v4M12 18.5v4M1.5 12h4M18.5 12h4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
      L.DomEvent.on(btn, "click", function (e) { L.DomEvent.stop(e); if (last && box.checked) map.setView(last, Math.max(map.getZoom(), 12)); else { wantCenter = true; if (!box.checked) { box.checked = true; box.dispatchEvent(new Event("change")); } } });
      L.DomEvent.disableClickPropagation(c); return c; }});
    new Ctl().addTo(map);
    var dh = $("dr-here"); if (dh) dh.addEventListener("click", function () {
      function go(ll) { if (window.ArkDrive) ArkDrive.at(ll[0], ll[1], "My location"); }
      if (last && box.checked) go(last); else { waiting.push(go); if (!box.checked) { box.checked = true; box.dispatchEvent(new Event("change")); } }
    });
    box.addEventListener("change", function () { pref("myloc", box.checked ? "1" : "0"); if (box.checked) start(); else stop(); });
    if (go) go.addEventListener("click", function () { if (last) map.setView(last, Math.max(map.getZoom(), 12)); else { wantCenter = true; if (!box.checked) { box.checked = true; box.dispatchEvent(new Event("change")); } } });
    if (box.checked) start();
  }
  window.ArkLocate = {init: init, center: function () { if (last) map.setView(last, Math.max(map.getZoom(), 12)); }};
})();
