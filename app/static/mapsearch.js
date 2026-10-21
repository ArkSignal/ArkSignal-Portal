/* One search for every map. Attaches to the map's search box (#q): after two letters it asks /api/search.json and lists airports, places, hospitals, repeaters, APRS stations,
   balloons and counties under the box. Picking one flies the map there (window.arkMap, set by each map page), marks the place for a few seconds and offers the page for it.
   The box keeps doing its own job on each map (filtering the aircraft or the list); this only adds the suggestions. */
(function () {
  "use strict";
  var inp = document.getElementById("q");
  if (!inp || inp.dataset.msReady) return;
  inp.dataset.msReady = "1";
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]; }); }
  var box = document.createElement("div");
  box.id = "ms-box"; box.setAttribute("role", "listbox"); box.style.display = "none";
  var host = inp.parentNode;
  host.style.position = host.style.position || "relative";
  inp.parentNode.parentNode.insertBefore(box, inp.parentNode.nextSibling);
  var items = [], hi = -1, timer = null, seq = 0, mark = null;
  function hide() { box.style.display = "none"; hi = -1; }
  function paint() {
    if (!items.length) { hide(); return; }
    var last = "";
    box.innerHTML = items.map(function (it, i) {
      var head = it.type !== last ? '<div class="ms-h">' + esc(it.type) + "</div>" : ""; last = it.type;
      return head + '<div class="ms-i' + (i === hi ? " on" : "") + '" role="option" data-i="' + i + '"><b>' + esc(it.label) + "</b>" + (it.sub ? ' <span class="dim">' + esc(it.sub) + "</span>" : "") + "</div>";
    }).join("");
    box.style.display = "block";
  }
  function go(it) {
    hide();
    var typed = inp.value;
    function release() { skipNext = true; inp.value = ""; inp.dispatchEvent(new Event("input", {bubbles: true})); }   // a picked place is a place to go to, never a filter: the page's own filter on this text is cleared
    if (it.drive) { if (window.ArkDrive) ArkDrive.search(it.drive); release(); return; }
    if (it.lat != null) release();
    var map = window.arkMap;
    if (it.lat != null && map) {
      map.setView([it.lat, it.lon], it.zoom || 12);
      if (mark) { map.removeLayer(mark); mark = null; }
      mark = L.layerGroup([L.circleMarker([it.lat, it.lon], {radius: 18, color: "#ffffff", weight: 3, fillColor: "#3ee6a8", fillOpacity: .3, interactive: false}),
        L.marker([it.lat, it.lon], {interactive: false, keyboard: false, icon: L.divIcon({className: "", iconSize: [0, 0], html: '<span class="ms-lab">' + esc(it.label) + "</span>"})})]).addTo(map);
      setTimeout(function () { if (mark && map) { map.removeLayer(mark); mark = null; } }, 12000);
    }
    if (it.href && (it.lat == null || !map)) location.href = it.href;
    else if (it.href) {
      var note = document.getElementById("ms-note") || document.createElement("div");
      note.id = "ms-note"; note.className = "ms-note"; note.innerHTML = esc(it.label) + ' &middot; <a href="' + esc(it.href) + '">Open its page</a>' + (it.lat != null && window.ArkDrive ? ' &middot; <a href="#" id="ms-drive">Drive times from here</a>' : "") + (it.lat != null ? ' &middot; <a href="/weather/place?lat=' + it.lat.toFixed(4) + "&lon=" + it.lon.toFixed(4) + "&q=" + encodeURIComponent(it.label) + '">Weather here</a>' : "");
      var dl = note.querySelector("#ms-drive"); if (dl) dl.addEventListener("click", function (e) { e.preventDefault(); ArkDrive.at(it.lat, it.lon, it.label); });
      box.parentNode.insertBefore(note, box.nextSibling);
      setTimeout(function () { if (note.parentNode) note.parentNode.removeChild(note); }, 12000);
    }
  }
  function ask() {
    var t = inp.value.trim(); if (t.length < 2) { items = []; hide(); return; }
    var me = ++seq;
    fetch("/api/search.json?q=" + encodeURIComponent(t)).then(function (r) { return r.json(); }).then(function (d) { if (me === seq) { items = (d.results || []).concat(window.ArkDrive && t.length >= 3 ? [{type: "Drive Times", label: "Drive times from “" + t + "”", sub: "city or address", drive: t}] : []); hi = -1; paint(); } }).catch(function () {});
  }
  var skipNext = false;
  inp.addEventListener("input", function () { if (skipNext) { skipNext = false; clearTimeout(timer); items = []; hide(); return; } clearTimeout(timer); timer = setTimeout(ask, 250); });
  inp.addEventListener("keydown", function (e) {
    if (box.style.display === "none") return;
    if (e.key === "ArrowDown") { hi = Math.min(items.length - 1, hi + 1); paint(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { hi = Math.max(0, hi - 1); paint(); e.preventDefault(); }
    else if (e.key === "Enter" && hi >= 0) { go(items[hi]); e.preventDefault(); }
    else if (e.key === "Escape") hide();
  });
  box.addEventListener("mousedown", function (e) { var el = e.target.closest(".ms-i"); if (el) { e.preventDefault(); go(items[parseInt(el.getAttribute("data-i"), 10)]); } });
  document.addEventListener("click", function (e) { if (!box.contains(e.target) && e.target !== inp) hide(); });
})();
