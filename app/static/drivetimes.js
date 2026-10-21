/* Drive times for every map. Click a point on the map (switch it on under Drive Times in the tools), or search a city or an address, and the hospitals nearest by road are listed in order of
   drive time (near-ties by trauma level, then services), with rings of 15, 30, 60 and 90 minutes (Settings > Routing) drawn around the place. All of it is OUR OWN ESTIMATE from typical road speeds
   (the network's routing server, OpenStreetMap roads): no traffic, no lights, not an ambulance's real route. The page gives its map to ArkDrive.init; the One Search (static/mapsearch.js) and
   the hospital popups call ArkDrive.at(lat, lon, label) and ArkDrive.ringLayer(rings).
   Public:  ArkDrive.init({map})   ArkDrive.at(lat, lon, label)   ArkDrive.search(text)   ArkDrive.ringLayer(rings, opts)   ArkDrive.clear()   ArkDrive.colors   */
(function () {
  "use strict";
  var COL = ["#2ee59d", "#ffd400", "#ff7a1a", "#ff2d55", "#c58bff"];
  var map = null, ptG = null, hosG = null, ringG = null, on = false, seq = 0, rowsData = [], lastNote = "";
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]; }); }
  function pref(k, v) { try { if (v === undefined) return localStorage.getItem("lv." + k); localStorage.setItem("lv." + k, v); } catch (e) { return null; } }
  function colorFor(all, m) { var s = all.slice().sort(function (a, b) { return a - b; }); return COL[Math.min(Math.max(0, s.indexOf(m)), COL.length - 1)]; }
  function dist(km) { return window.ArkUnits ? ArkUnits.text.dist(km) : Math.round(km * 0.621) + " mi"; }

  // The rings of one place as a layer: thick solid lines in a color for each time, a stronger fill toward the middle, and a "30 min" label on each.
  function ringLayer(rings, o) {
    o = o || {};
    var ms = Object.keys(rings || {}).map(Number).sort(function (a, b) { return b - a; }), g = L.layerGroup();
    if (map && !map.getPane("drrings")) { map.createPane("drrings"); map.getPane("drrings").style.zIndex = 430; map.getPane("drrings").style.pointerEvents = "none"; }
    ms.forEach(function (m) {
      var c = colorFor(ms, m), poly = rings[m], top = poly[0];
      poly.forEach(function (p) { if (p[0] > top[0]) top = p; });
      L.polygon(poly, {pane: "drrings", color: c, weight: o.weight || 4, opacity: .95, fillColor: c, fillOpacity: o.fill == null ? .13 : o.fill, interactive: false}).addTo(g);
      if (o.labels !== false)
        L.marker(top, {pane: "drrings", interactive: false, keyboard: false, icon: L.divIcon({className: "", iconSize: [0, 0], html: '<span class="dr-lab" style="background:' + c + '">' + m + " min</span>"})}).addTo(g);
    });
    rl.push(g); g.on("add", sync); g.on("remove", sync);   // the button below appears while any of these is on the map
    return g;
  }
  var rl = [], btn = null;
  function active() { return rl.some(function (g) { return map && map.hasLayer(g); }) || !!(ptG || hosG || ringG) || (panel() && panel().style.display === "block"); }
  function sync() { if (btn) btn.style.display = active() ? "block" : "none"; }
  // one obvious button on every map while drive times or rings are showing: it takes them all away (and tells the page, so its own "rings" choice goes back to none)
  function clearAll() {
    rl.forEach(function (g) { if (map && map.hasLayer(g)) map.removeLayer(g); });
    clear();
    document.dispatchEvent(new CustomEvent("ark-drive-cleared"));
    sync();
  }

  function panel() { return $("lv-drive"); }
  function show(html) { var p = panel(); if (!p) return; p.innerHTML = html; p.style.display = "block"; sync(); }
  function clear() {
    seq++;
    [ptG, hosG, ringG].forEach(function (g) { if (g && map) map.removeLayer(g); });
    ptG = hosG = ringG = null; rowsData = [];
    if (panel()) { panel().style.display = "none"; panel().innerHTML = ""; }
    sync();
  }
  function badge(h) {
    var lvl = {I: 1, II: 2, III: 3, IV: 4, V: 5}[h.trauma];
    return '<span class="hico ' + (lvl ? "t" + lvl : h.emergency ? "ed" : "noed") + '" style="width:20px;height:20px;font-size:' + (lvl ? 10 : 15) + 'px;flex:none">' + (h.trauma || "+") + "</span>";
  }
  function render(label, lat, lon, d, ringsWanted) {
    var rows = (d.hospitals || []).filter(function (r) { return r.name !== label; }), mins = Object.keys(d.rings || {}).map(Number).sort(function (a, b) { return a - b; });
    var h = '<header><div><b>Drive Times From</b><div class="dr-where">' + esc(label) + '</div></div><button class="btn small neutral" type="button" id="dr-close" style="margin:0">Close</button></header>';
    if (mins.length) h += '<div class="dr-key">' + mins.map(function (m) { return '<span><i style="background:' + colorFor(mins, m) + '"></i>' + m + " min</span>"; }).join("") + "</div>";
    else if (ringsWanted && !d.note) h += '<div class="dr-note">Working out the rings...</div>';
    if (d.note) h += '<div class="dr-note">' + esc(d.note) + "</div>";
    h += '<div class="dr-list">' + (rows.length ? rows.map(function (r, i) {
      var meta = [r.city ? r.city + (r.state ? ", " + r.state : "") : "", r.trauma ? "Trauma level " + r.trauma : r.emergency ? "Emergency" : "", r.services.length ? r.services.length + (r.services.length === 1 ? " service" : " services") : "", r.road_km != null ? dist(r.road_km) + " by road" : dist(r.km) + " away in a straight line"].filter(Boolean).join(" · ");
      return '<div class="dr-row" data-i="' + i + '" tabindex="0"><span class="dr-n">' + (i + 1) + "</span>" + badge(r) + '<span class="dr-name"><b>' + esc(r.name) + '</b><small>' + esc(meta) + (r.approx ? " · approximate position" : "") + "</small></span>" +
        '<span class="dr-min">' + (r.minutes == null ? "no road" : minText(r.minutes)) + "</span></div>";
    }).join("") : '<div class="dr-note">' + (d.enabled === false ? "" : "No hospitals could be timed from here.") + "</div>") + "</div>";
    h += '<div class="dr-foot">Our own estimate from typical road speeds: no traffic, lights or weather, and not an ambulance\'s real route.</div>';
    show(h);
    $("dr-close").onclick = clear;
    rowsData = rows;
  }
  function minText(m) { return m < 90 ? Math.round(m) + " min" : Math.floor(m / 60) + " h " + Math.round(m % 60) + " min"; }

  function at(lat, lon, label) {
    if (!map) return;
    clear();
    var me = ++seq, name = label || (lat.toFixed(3) + ", " + lon.toFixed(3));
    ptG = L.layerGroup([L.marker([lat, lon], {keyboard: false, zIndexOffset: 900, icon: L.divIcon({className: "", iconSize: [26, 26], iconAnchor: [13, 13], html: '<div class="dr-pin"></div>'})}).bindTooltip(esc(name), {direction: "top", offset: [0, -12]})]).addTo(map);
    show('<header><div><b>Drive Times From</b><div class="dr-where">' + esc(name) + '</div></div><button class="btn small neutral" type="button" id="dr-close" style="margin:0">Close</button></header><div class="dr-note">Asking the routing server...</div>');
    $("dr-close").onclick = clear;
    var q = "lat=" + lat.toFixed(5) + "&lon=" + lon.toFixed(5);
    fetch("/api/drivetimes.json?" + q).then(function (r) { return r.json().then(function (j) { j._status = r.status; return j; }); }).then(function (d) {
      if (me !== seq) return;
      if (d._status === 429) d.note = d.note || "Too many questions just now; wait a moment.";
      render(name, lat, lon, d, d.enabled !== false && d._status !== 429);
      drawHospitals();
      if (d.enabled === false || d._status === 429 || !(d.hospitals || []).length) return;
      return fetch("/api/drivetimes.json?" + q + "&rings=1").then(function (r) { return r.json(); }).then(function (d2) {
        if (me !== seq) return;
        if (d2.rings && Object.keys(d2.rings).length) {
          ringG = ringLayer(d2.rings).addTo(map);
          var ms = Object.keys(d2.rings).map(Number).sort(function (a, b) { return b - a; });
          map.fitBounds(L.latLngBounds(d2.rings[ms[0]]).pad(.04), {maxZoom: 11, padding: [20, 20]});
        }
        render(name, lat, lon, d2, false);
        drawHospitals();
      });
    }).catch(function () { if (me === seq) show('<header><div><b>Drive Times From</b><div class="dr-where">' + esc(name) + '</div></div><button class="btn small neutral" type="button" id="dr-close" style="margin:0">Close</button></header><div class="dr-note">Drive times could not be loaded.</div>'); $("dr-close") && ($("dr-close").onclick = clear); });
  }
  function drawHospitals() {
    if (hosG) map.removeLayer(hosG);
    hosG = L.layerGroup().addTo(map);
    rowsData.forEach(function (r, i) {
      if (r.minutes == null || i >= 10) return;
      L.marker([r.lat, r.lon], {keyboard: false, zIndexOffset: 700, icon: L.divIcon({className: "", iconSize: [22, 22], iconAnchor: [11, 11], html: '<div class="dr-rank">' + (i + 1) + "</div>"})})
        .bindTooltip("<b>" + esc(r.name) + "</b> · " + minText(r.minutes), {direction: "top", offset: [0, -10]}).on("click", function () { focus(i); }).addTo(hosG);
    });
  }
  function focus(i) {
    var r = rowsData[i]; if (!r) return;
    map.setView([r.lat, r.lon], Math.max(map.getZoom(), 12));
    L.popup().setLatLng([r.lat, r.lon]).setContent("<b>" + esc(r.name) + "</b><br>" + (r.minutes == null ? "No road route" : minText(r.minutes) + " by road (an estimate)") + (r.trauma ? " · Trauma level " + esc(r.trauma) : "") + '<br><a href="/hospitals/' + encodeURIComponent(r.id) + '">Hospital page</a>').openOn(map);
  }

  // search: a place or an address typed in the one search box (or any text)
  function search(text) {
    var me = ++seq; text = (text || "").trim(); if (text.length < 2) return;
    show('<header><div><b>Drive Times From</b><div class="dr-where">' + esc(text) + '</div></div><button class="btn small neutral" type="button" id="dr-close" style="margin:0">Close</button></header><div class="dr-note">Looking for that place...</div>');
    $("dr-close").onclick = clear;
    fetch("/api/geocode.json?q=" + encodeURIComponent(text)).then(function (r) { return r.json(); }).then(function (d) {
      if (me !== seq) return;
      var res = d.results || [];
      if (!res.length) { show('<header><div><b>Drive Times From</b><div class="dr-where">' + esc(text) + '</div></div><button class="btn small neutral" type="button" id="dr-close" style="margin:0">Close</button></header><div class="dr-note">' + esc(d.error ? "Too many questions just now; wait a moment." : "No city or address found for that.") + "</div>"); $("dr-close").onclick = clear; return; }
      if (res.length === 1) { at(res[0].lat, res[0].lon, res[0].label); return; }
      show('<header><div><b>Which place?</b></div><button class="btn small neutral" type="button" id="dr-close" style="margin:0">Close</button></header><div class="dr-list">' + res.map(function (x, i) {
        return '<div class="dr-row" data-pick="' + i + '" tabindex="0"><span class="dr-name"><b>' + esc(x.label) + "</b><small>" + esc(x.kind === "address" ? "Address" : x.kind === "zip" ? "ZIP code" : "City or town") + "</small></span></div>";
      }).join("") + "</div>");
      $("dr-close").onclick = clear;
      panel().onclick = function (e) { var el = e.target.closest("[data-pick]"); if (el) { var x = res[parseInt(el.getAttribute("data-pick"), 10)]; panel().onclick = null; at(x.lat, x.lon, x.label); } };
    }).catch(function () { if (me === seq) { show('<div class="dr-note">The search could not be reached.</div>'); } });
  }

  function init(o) {
    map = o.map;
    if (!btn || !btn.isConnected) {
      btn = document.createElement("button"); btn.type = "button"; btn.id = "dr-clearmap"; btn.textContent = "Clear Drive Times and Rings"; btn.setAttribute("aria-label", "Clear the drive times and rings from the map");
      btn.style.cssText = "display:none;position:absolute;left:50%;bottom:36px;transform:translateX(-50%);z-index:1400;padding:10px 20px;border:2px solid #fff;border-radius:999px;background:#d23a3a;color:#fff;font:700 .95rem/1 Inter,sans-serif;cursor:pointer;box-shadow:0 2px 14px rgba(0,0,0,.6)";
      btn.addEventListener("click", function (e) { e.stopPropagation(); clearAll(); });
      map.getContainer().appendChild(btn); L.DomEvent.disableClickPropagation(btn);
    }
    var box = $("dr-on");
    var KEY = window.ARK_DRIVE_KEY || "drive";   // a map can keep its own choice (the Trauma Map starts with the click off)
    on = pref(KEY) === "1"; if (box) box.checked = on;
    var sec = box && box.closest("details"); if (!box || (sec && sec.style.display === "none")) on = false;   // the section is not offered on this map type (Settings > Map Sections By Map Type)
    function mode() { map.getContainer().style.cursor = on ? "crosshair" : ""; var b = document.querySelector('[data-fb="drive"]'); if (b) b.textContent = on ? "On" : ""; }
    mode();
    if (box) box.addEventListener("change", function () { on = box.checked; pref(KEY, on ? "1" : "0"); mode(); });
    // a click only picks the place: a small pin and a button; the drive times are asked for with the button
    var pick = null;
    map.on("click", function (e) {
      if (!on) return;
      if (pick) map.removeLayer(pick);
      var ll = e.latlng, div = document.createElement("div");
      div.innerHTML = "<b>" + ll.lat.toFixed(4) + ", " + ll.lng.toFixed(4) + '</b><br><button class="btn small" type="button" style="margin:6px 6px 0 0">Show Drive Times From Here</button><button class="btn small neutral" type="button" data-loc="1" style="margin:6px 0 0">From My Location</button>';
      div.querySelector("button").addEventListener("click", function () { map.closePopup(); if (pick) { map.removeLayer(pick); pick = null; } at(ll.lat, ll.lng, ""); });
      div.querySelector("[data-loc]").addEventListener("click", function () { map.closePopup(); if (pick) { map.removeLayer(pick); pick = null; } var h = document.getElementById("dr-here"); if (h) h.click(); });   // asks the browser for where you are
      pick = L.popup({autoClose: true, closeOnClick: false}).setLatLng(ll).setContent(div).openOn(map);
    });
    var p = panel();
    if (p) {
      p.addEventListener("click", function (e) { var el = e.target.closest(".dr-row[data-i]"); if (el) focus(parseInt(el.getAttribute("data-i"), 10)); });
      p.addEventListener("keydown", function (e) { if (e.key === "Enter") { var el = e.target.closest(".dr-row"); if (el) el.click(); } });
    }
  }
  window.ArkDrive = {clearAll: clearAll, init: init, at: at, search: search, ringLayer: ringLayer, clear: clear, colors: COL, minText: minText, on: function () { return on; }};
})();
