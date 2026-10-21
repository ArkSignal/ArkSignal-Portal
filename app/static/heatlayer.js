/* The portal's own heat map of past traffic: where aircraft flew, for any period, from the hub's history (/api/history/heat.json adds up each UTC day on a grid that follows the zoom).
   var H = ArkHeat.create(map, {range: fn -> {from: Date, to: Date}, altitude: fn -> {min: feet|null, max: feet|null}, onState: fn(state)})
   H.show(true|false)   draw or remove it;  H.refresh()   fetch again (after the period, altitude or map changed);  H.legend()   html of the color bar;  H.style({radius, strength})
   state = {loading, days, found, asked, cells, truncated, error}. Days arrive one at a time and are drawn as they come, the newest first. Hover and click go to what is below (the layer is only a drawing). */
(function () {
  "use strict";
  var RAMP = [[0, [20, 60, 200, 0]], [.15, [30, 110, 235, 150]], [.35, [30, 200, 220, 190]], [.55, [90, 220, 90, 215]], [.75, [250, 220, 40, 235]], [.9, [255, 130, 30, 245]], [1, [240, 40, 40, 255]]];
  var LUT = (function () {   // 256 colors from a faint blue to red
    var out = [];
    for (var i = 0; i < 256; i++) {
      var t = i / 255, j = 1; while (j < RAMP.length - 1 && t > RAMP[j][0]) j++;
      var a = RAMP[j - 1], b = RAMP[j], f = (t - a[0]) / ((b[0] - a[0]) || 1), c = [];
      for (var k = 0; k < 4; k++) c.push(Math.round(a[1][k] + (b[1][k] - a[1][k]) * f));
      out.push(c);
    }
    return out;
  })();

  function utcDay(d) { return d.toISOString().slice(0, 10); }

  function create(map, o) {
    var pane = map.getPane("heathist") || map.createPane("heathist"), cv = document.createElement("canvas"), blob = document.createElement("canvas"), on = false, seq = 0, ctl = null;
    pane.style.zIndex = 405; pane.style.pointerEvents = "none"; cv.style.cssText = "position:absolute;left:0;top:0;pointer-events:none"; pane.appendChild(cv);
    var cells = {}, cell = 0, z = 8, opt = {radius: 2.2, strength: 1}, state = {loading: false, days: 0, found: 0, asked: 0, cells: 0, truncated: false, error: ""}, timer = null;
    function say() { if (o.onState) o.onState(state); }
    function place() { L.DomUtil.setPosition(cv, map.containerPointToLayerPoint([0, 0])); }
    function size() { var s = map.getSize(); if (cv.width !== s.x || cv.height !== s.y) { cv.width = s.x; cv.height = s.y; } place(); }

    function draw() {
      size();
      var ctx = cv.getContext("2d"), w = cv.width, h = cv.height; ctx.clearRect(0, 0, w, h);
      var keys = Object.keys(cells); if (!on || !keys.length) { return; }
      var max = 1, i; keys.forEach(function (k) { if (cells[k][2] > max) max = cells[k][2]; });
      blob.width = w; blob.height = h;
      var b = blob.getContext("2d"), px = cell / 360 * 256 * Math.pow(2, map.getZoom()), r = Math.max(3, px * opt.radius), lm = Math.log(1 + max);
      b.clearRect(0, 0, w, h); b.globalCompositeOperation = "lighter";
      var stamp = document.createElement("canvas"); stamp.width = stamp.height = Math.ceil(r * 2); var sc = stamp.getContext("2d"), g2 = sc.createRadialGradient(r, r, 0, r, r, r);
      g2.addColorStop(0, "rgba(255,255,255,1)"); g2.addColorStop(1, "rgba(255,255,255,0)"); sc.fillStyle = g2; sc.fillRect(0, 0, stamp.width, stamp.height);
      keys.forEach(function (k) {
        var c = cells[k], p = map.latLngToContainerPoint([c[0], c[1]]);
        if (p.x < -r || p.y < -r || p.x > w + r || p.y > h + r) return;
        b.globalAlpha = Math.min(1, Math.log(1 + c[2]) / lm * .13 * opt.strength + .008);
        b.drawImage(stamp, p.x - r, p.y - r);
      });
      var img = b.getImageData(0, 0, w, h), d = img.data;
      for (i = 0; i < d.length; i += 4) {   // the strength of the white gathered at a point becomes a color
        var a = d[i + 3]; if (!a) continue;
        var col = LUT[Math.min(255, a)]; d[i] = col[0]; d[i + 1] = col[1]; d[i + 2] = col[2]; d[i + 3] = col[3];
      }
      ctx.putImageData(img, 0, 0);
    }

    function days() {
      var rg = o.range(), out = [], t = new Date(Date.UTC(rg.from.getUTCFullYear(), rg.from.getUTCMonth(), rg.from.getUTCDate()));
      while (t <= rg.to && out.length < 400) { out.push(new Date(t)); t = new Date(t.getTime() + 86400000); }
      return {list: out.reverse(), from: rg.from, to: rg.to};   // the newest day first, so the part people look at most appears first
    }
    function refresh() {
      clearTimeout(timer); timer = setTimeout(go, 150);
    }
    async function go() {
      if (!on) return;
      var me = ++seq; if (ctl) ctl.abort(); ctl = window.AbortController ? new AbortController() : null;
      var dd = days(), alt = o.altitude(), b = map.getBounds().pad(0.1), zz = Math.max(3, Math.min(13, Math.round(map.getZoom())));
      z = zz; cells = {}; cell = 360 / (256 * Math.pow(2, z)) * 4;
      state = {loading: true, days: dd.list.length, found: 0, asked: 0, cells: 0, truncated: false, error: ""}; say(); draw();
      for (var i = 0; i < dd.list.length; i++) {
        var day = utcDay(dd.list[i]), q = new URLSearchParams({day: day, z: z, south: b.getSouth(), west: b.getWest(), north: b.getNorth(), east: b.getEast()});
        if (alt.min != null) q.set("amin", alt.min); if (alt.max != null) q.set("amax", alt.max);
        if (i === 0) q.set("until", Math.floor(dd.to.getTime() / 1000)); if (i === dd.list.length - 1) q.set("since", Math.floor(dd.from.getTime() / 1000));
        if (dd.list.length === 1) { q.set("since", Math.floor(dd.from.getTime() / 1000)); q.set("until", Math.floor(dd.to.getTime() / 1000)); }
        try {
          var r = await fetch("/api/history/heat.json?" + q.toString(), {signal: ctl ? ctl.signal : undefined, cache: "no-store"});
          if (me !== seq) return;
          if (!r.ok) throw new Error("HTTP " + r.status);
          var j = await r.json(); if (me !== seq) return;
          state.found += j.found; state.asked += j.asked; state.truncated = state.truncated || j.truncated;
          j.cells.forEach(function (c) { var k = c[0] + "," + c[1]; if (cells[k]) cells[k][2] += c[2]; else cells[k] = c; });
          state.cells = Object.keys(cells).length; draw(); say();
        } catch (e) {
          if (e && e.name === "AbortError") return;
          if (me !== seq) return;
          state.error = "The history could not be loaded."; break;
        }
      }
      if (me === seq) { state.loading = false; if (!state.found && !state.error) state.error = "The hub has no history for this period."; say(); }
    }
    function show(v) {
      on = !!v; pane.style.display = on ? "" : "none";
      if (on) refresh(); else { seq++; if (ctl) ctl.abort(); cells = {}; draw(); state.loading = false; say(); }
    }
    map.on("zoomend", function () { if (on) draw(); });   // at once from what is kept, then again from fresh numbers
    map.on("moveend zoomend", function () { if (on) refresh(); });
    map.on("resize", function () { if (on) draw(); });
    pane.style.display = "none";
    return {show: show, refresh: refresh, style: function (s) { opt.radius = s.radius || opt.radius; opt.strength = s.strength || opt.strength; if (on) draw(); },
            legend: function () { return '<div class="heatbar" style="height:10px;border-radius:5px;background:linear-gradient(90deg,rgba(30,110,235,.6),rgb(30,200,220),rgb(90,220,90),rgb(250,220,40),rgb(255,130,30),rgb(240,40,40))"></div><div style="display:flex;justify-content:space-between;font-size:.74rem" class="dim"><span>Fewer flights</span><span>More flights</span></div>'; },
            state: function () { return state; }, cells: function () { return Object.keys(cells).length; }};
  }
  window.ArkHeat = {create: create};
})();
